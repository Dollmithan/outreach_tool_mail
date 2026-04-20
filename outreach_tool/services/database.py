import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from openpyxl import load_workbook

from .app_data import _ensure_app_dirs, get_app_data_dir, get_master_db_path

def connect_master():
    path = get_master_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

def init_master_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_path TEXT NOT NULL,
            working_path TEXT NOT NULL UNIQUE,
            label TEXT,
            imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            full_name TEXT,
            number TEXT,
            sent INTEGER NOT NULL DEFAULT 0,
            replied INTEGER NOT NULL DEFAULT 0,
            replied_at TEXT,
            UNIQUE(import_id, email),
            FOREIGN KEY (import_id) REFERENCES imports(id)
        );
        CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);

        -- Cache phone->country so we don't repeatedly call the external API.
        CREATE TABLE IF NOT EXISTS phone_country_cache (
            number_sanitized TEXT PRIMARY KEY,
            country_code TEXT NOT NULL,
            country_label TEXT NOT NULL,
            checked_at TEXT NOT NULL
        );
    """)
    conn.commit()

def ensure_contact_sent_column(conn):
    try:
        conn.execute("ALTER TABLE contacts ADD COLUMN sent INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass

def ensure_contact_replied_columns(conn):
    try:
        conn.execute("ALTER TABLE contacts ADD COLUMN replied INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE contacts ADD COLUMN replied_at TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass

def ensure_contact_tracking_columns(conn):
    """Original lead .db files may lack sent/replied; add columns on the working copy only."""
    ensure_contact_sent_column(conn)
    ensure_contact_replied_columns(conn)

def normalize_email(addr):
    return (addr or "").strip().lower()

def register_working_database(original_path, working_path, label=None):
    """Register an existing SQLite working file in the master DB and sync leads (no file copy)."""
    _ensure_app_dirs()
    original_path = os.path.abspath(original_path)
    working_path = os.path.abspath(working_path)
    if not os.path.isfile(working_path):
        raise FileNotFoundError(f"Working database missing: {working_path}")
    conn_m = connect_master()
    init_master_schema(conn_m)
    lab = label or os.path.basename(original_path)
    ins = conn_m.execute(
        "INSERT INTO imports (original_path, working_path, label, imported_at) VALUES (?,?,?,?)",
        (original_path, working_path, lab, datetime.now().isoformat()),
    )
    import_id = ins.lastrowid
    _sync_working_file_to_master(conn_m, working_path, import_id)
    conn_m.commit()
    conn_m.close()
    return import_id, working_path
def import_user_database(original_path, label=None):
    """Copy the user's .db to the tool data dir and register it in the master DB."""
    _ensure_app_dirs()
    original_path = os.path.abspath(original_path)
    if not os.path.isfile(original_path):
        raise FileNotFoundError(f"Not a file: {original_path}")
    token = uuid.uuid4().hex[:12]
    base = os.path.splitext(os.path.basename(original_path))[0]
    working_path = os.path.join(get_app_data_dir(), "working_copies", f"{base}_{token}.db")
    shutil.copy2(original_path, working_path)
    return register_working_database(original_path, working_path, label)

def _excel_header_norm(cell):
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell).strip().lower())

_EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-']+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.IGNORECASE)

def _is_email_like(val) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    if not s or " " in s or "@" not in s:
        return False
    return _EMAIL_RE.match(s) is not None

def _digits_only(val) -> str:
    if val is None:
        return ""
    return re.sub(r"\D+", "", str(val))

def _is_phone_like(val) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    if not s:
        return False
    if "@" in s:
        return False
    d = _digits_only(s)
    return len(d) >= 7

def _excel_cell_to_int01(val, default=0):
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, (int, float)):
        return 1 if int(val) != 0 else 0
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "y", "x"):
        return 1
    if s in ("0", "false", "no", "n"):
        return 0
    return default

def _score_excel_columns(header_vals, sample_rows, max_cols: int):
    """
    Identify email/phone/name columns even when headers don't match exactly.
    Uses a weighted score from header keywords and sample cell values.
    """
    headers = [_excel_header_norm(c) for c in (header_vals or ())]
    idx = {
        "full_name": None,
        "first_name": None,
        "last_name": None,
        "email": None,
        "number": None,
        "sent": None,
        "replied": None,
    }

    # Known status columns, when explicitly named
    for i, h in enumerate(headers[:max_cols]):
        if not h:
            continue
        if idx["sent"] is None and h == "sent":
            idx["sent"] = i
        if idx["replied"] is None and h in ("replied", "reply"):
            idx["replied"] = i
        if idx["first_name"] is None and (
            (("first" in h) and ("name" in h))
            or h in ("firstname", "given name", "givenname", "given")
        ):
            idx["first_name"] = i
        if idx["last_name"] is None and (
            (("last" in h) and ("name" in h))
            or h in ("lastname", "surname", "family name", "familyname", "last")
        ):
            idx["last_name"] = i
        if idx["full_name"] is None and (("full" in h) and ("name" in h)):
            idx["full_name"] = i

    email_scores = [0.0] * max_cols
    phone_scores = [0.0] * max_cols
    name_scores = [0.0] * max_cols

    for i in range(max_cols):
        h = headers[i] if i < len(headers) else ""
        if any(x in h for x in ("email", "e-mail", "e mail", "mail")):
            email_scores[i] += 3.0
        if any(x in h for x in ("phone", "mobile", "tel", "telephone", "cell", "whatsapp")) or h == "number":
            phone_scores[i] += 3.0
        if any(x in h for x in ("full name", "fullname", "contact name", "client name", "lead name")) or h in ("name", "contact"):
            name_scores[i] += 2.0
        if idx.get("first_name") == i:
            name_scores[i] += 1.5
        if idx.get("last_name") == i:
            name_scores[i] += 1.5

    for row in sample_rows or ():
        if not row:
            continue
        for i in range(max_cols):
            val = row[i] if i < len(row) else None
            if _is_email_like(val):
                email_scores[i] += 1.0
            if _is_phone_like(val):
                phone_scores[i] += 1.0
            if val is not None:
                s = str(val).strip()
                if s and not _is_email_like(s):
                    d = _digits_only(s)
                    if len(d) <= 2 and len(s) >= 3:
                        name_scores[i] += 0.2

    def best_index(scores):
        best_i, best_v = None, -1e9
        for i, v in enumerate(scores):
            if v > best_v:
                best_i, best_v = i, v
        return best_i, best_v

    email_i, email_v = best_index(email_scores)
    phone_i, phone_v = best_index(phone_scores)

    # Avoid mixing email + phone: ensure different columns
    if email_i is not None and phone_i is not None and email_i == phone_i:
        if email_v >= phone_v:
            phone_scores[email_i] = -1e9
            phone_i, phone_v = best_index(phone_scores)
        else:
            email_scores[phone_i] = -1e9
            email_i, email_v = best_index(email_scores)

    idx["email"] = email_i if (email_i is not None and email_v >= 2.0) else None
    idx["number"] = phone_i if (phone_i is not None and phone_v >= 2.0) else None

    forbidden = {idx["email"], idx["number"], idx["sent"], idx["replied"]}
    best_name_i, best_name_v = None, -1e9
    for i, v in enumerate(name_scores):
        if i in forbidden:
            continue
        if v > best_name_v:
            best_name_i, best_name_v = i, v
    if best_name_i is not None and best_name_v > 0.5:
        idx["full_name"] = best_name_i
    else:
        for i in range(max_cols):
            if i not in forbidden:
                idx["full_name"] = i
                break

    return idx

def _map_excel_columns(header_vals, sample_rows):
    """Map header row to column indices: full_name/first/last_name, email, number, sent, replied."""
    max_cols = max(len(header_vals or ()), max((len(r) for r in (sample_rows or ())), default=0), 0)
    max_cols = min(max_cols, 80)
    idx = _score_excel_columns(header_vals or (), sample_rows or (), max_cols)

    # Conservative fallback to A/B/C only if sample suggests column B is emails
    if idx["email"] is None and max_cols >= 3:
        col1_em = 0
        for r in (sample_rows or ())[:20]:
            if len(r) > 1 and _is_email_like(r[1]):
                col1_em += 1
        if col1_em >= 3:
            idx["full_name"], idx["email"], idx["number"] = 0, 1, 2
            if max_cols >= 5:
                idx["sent"], idx["replied"] = 3, 4
            elif max_cols >= 4:
                idx["sent"] = 3

    return idx

def write_excel_to_contacts_sqlite(excel_path, sqlite_path):
    """
    Scan all sheets to find the best match.
    Assumes row 1 is a header row, but header text can vary widely.
    Writes contacts table: full_name, number, email, sent, replied, replied_at.
    Returns number of rows inserted (rows with non-empty email).
    """
    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise RuntimeError(
            "The openpyxl package is required for Excel import. Install with: pip install openpyxl"
        ) from e
    ext = os.path.splitext(excel_path)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        raise ValueError(
            "Supported Excel types: .xlsx / .xlsm. Save older .xls files as .xlsx in Excel."
        )
    if os.path.isfile(sqlite_path):
        os.remove(sqlite_path)
    conn = None
    wb = None
    try:
        conn = sqlite3.connect(sqlite_path)
        conn.execute(
            """
            CREATE TABLE contacts (
                full_name TEXT,
                number TEXT,
                email TEXT,
                sent INTEGER DEFAULT 0,
                replied INTEGER DEFAULT 0,
                replied_at TEXT
            )
            """
        )
        wb = load_workbook(excel_path, read_only=True, data_only=True)
        best = None  # (score, ws, header_vals, sample_rows, col)

        for ws in wb.worksheets:
            rows_iter = ws.iter_rows(values_only=True)
            try:
                header_vals = next(rows_iter)
            except StopIteration:
                continue

            sample_rows = []
            for _ in range(50):
                try:
                    r = next(rows_iter)
                except StopIteration:
                    break
                sample_rows.append(r)

            col = _map_excel_columns(header_vals, sample_rows)
            if col.get("email") is None:
                continue

            email_hits = 0
            phone_hits = 0
            for r in sample_rows:
                if not r:
                    continue
                ix_email = col.get("email")
                if ix_email is not None and ix_email < len(r) and _is_email_like(r[ix_email]):
                    email_hits += 1
                ix_phone = col.get("number")
                if ix_phone is not None and ix_phone < len(r) and _is_phone_like(r[ix_phone]):
                    phone_hits += 1

            # Strongly prefer lots of email-like values; phone is a secondary tie-breaker
            score = (email_hits * 10.0) + (phone_hits * 1.0)
            if best is None or score > best[0]:
                best = (score, ws, header_vals, sample_rows, col)

        if best is None:
            raise ValueError(
                "Could not find an Email column in any sheet. "
                "Make sure row 1 contains headers (or at least that one sheet has email-like values)."
            )

        _, ws, header_vals, sample_rows, col = best
        inserted = 0
        batch = []
        def process_row(row):
            nonlocal inserted
            if row is None:
                return

            def gv(key):
                ix = col[key]
                if ix is None or ix >= len(row):
                    return None
                return row[ix]

            email_raw = gv("email")
            em = normalize_email(email_raw or "")
            if not em:
                return
            first = gv("first_name")
            last = gv("last_name")
            if (first is not None and str(first).strip() != "") or (last is not None and str(last).strip() != ""):
                first_s = str(first).strip() if first is not None else ""
                last_s = str(last).strip() if last is not None else ""
                full_name = (first_s + (" " if (first_s and last_s) else "") + last_s).strip()
            else:
                fn = gv("full_name")
                full_name = (str(fn).strip() if fn is not None else "") or ""
            num = gv("number")
            number = (str(num).strip() if num is not None else "") or ""
            sent = _excel_cell_to_int01(gv("sent"), 0)
            replied = _excel_cell_to_int01(gv("replied"), 0)
            rep_at = None
            batch.append((full_name, number, em, sent, replied, rep_at))
            inserted += 1

        # Process the sampled rows (already collected during scan)
        for row in sample_rows:
            process_row(row)

        # Then process remaining rows from the chosen worksheet
        rows_iter = ws.iter_rows(values_only=True)
        try:
            _ = next(rows_iter)  # header row
        except StopIteration:
            rows_iter = iter(())
        # Discard the same number of sampled rows so we don't double-import
        for _ in range(len(sample_rows)):
            try:
                _ = next(rows_iter)
            except StopIteration:
                break

        for row in rows_iter:
            process_row(row)
        if batch:
            conn.executemany(
                "INSERT INTO contacts (full_name, number, email, sent, replied, replied_at) VALUES (?,?,?,?,?,?)",
                batch,
            )
        conn.commit()
        return inserted
    except Exception:
        if conn:
            conn.close()
            conn = None
        try:
            if os.path.isfile(sqlite_path):
                os.remove(sqlite_path)
        except OSError:
            pass
        raise
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass
        if conn is not None:
            conn.close()

def import_excel_as_leads(original_path, label=None):
    """Convert Excel to a working .db in app data and register it (original Excel is never modified)."""
    _ensure_app_dirs()
    original_path = os.path.abspath(original_path)
    if not os.path.isfile(original_path):
        raise FileNotFoundError(f"Not a file: {original_path}")
    token = uuid.uuid4().hex[:12]
    base = os.path.splitext(os.path.basename(original_path))[0]
    working_path = os.path.join(get_app_data_dir(), "working_copies", f"{base}_excel_{token}.db")
    n = write_excel_to_contacts_sqlite(original_path, working_path)
    if n == 0:
        try:
            os.remove(working_path)
        except OSError:
            pass
        raise ValueError(
            "No rows with an email address were imported. "
            "Check the header row and that data starts on row 2."
        )
    return register_working_database(original_path, working_path, label)

def _sync_working_file_to_master(conn_m, working_path, import_id):
    """Upsert all contacts from a working clone into master.leads."""
    conn_w = sqlite3.connect(working_path)
    ensure_contact_tracking_columns(conn_w)
    cur = conn_w.cursor()
    cur.execute(
        """
        SELECT full_name, number, email,
               COALESCE(sent, 0) AS s,
               COALESCE(replied, 0) AS r,
               replied_at
        FROM contacts
        WHERE email IS NOT NULL AND TRIM(COALESCE(email, '')) != ''
        """
    )
    rows = cur.fetchall()
    conn_w.close()
    for full_name, number, email, sent, replied, rep_at in rows:
        e = normalize_email(email)
        if not e:
            continue
        conn_m.execute(
            """
            INSERT INTO leads (import_id, email, full_name, number, sent, replied, replied_at)
            VALUES (?,?,?,?,?,?, ?)
            ON CONFLICT(import_id, email) DO UPDATE SET
                full_name = excluded.full_name,
                number = excluded.number,
                sent = excluded.sent,
                replied = MAX(COALESCE(excluded.replied, 0), COALESCE(replied, 0)),
                replied_at = CASE
                    WHEN MAX(COALESCE(excluded.replied, 0), COALESCE(replied, 0)) != 0
                    THEN COALESCE(excluded.replied_at, replied_at)
                    ELSE NULL
                END
            """,
            (
                import_id,
                e,
                full_name,
                number,
                1 if sent else 0,
                1 if replied else 0,
                rep_at,
            ),
        )

def list_imports():
    conn = connect_master()
    init_master_schema(conn)
    rows = conn.execute(
        "SELECT id, original_path, working_path, label, imported_at FROM imports ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_working_path_for_import(import_id):
    conn = connect_master()
    init_master_schema(conn)
    row = conn.execute("SELECT working_path FROM imports WHERE id=?", (import_id,)).fetchone()
    conn.close()
    return row[0] if row else None

def get_stats_for_import(import_id):
    """Counts come from the working .db `contacts` table. 'total' = rows with usable email (same as outreach)."""
    wp = get_working_path_for_import(import_id)
    if not wp or not os.path.isfile(wp):
        return {
            "total": 0,
            "sent": 0,
            "replied": 0,
            "left": 0,
            "total_rows": 0,
            "no_email": 0,
        }
    conn = sqlite3.connect(wp)
    ensure_contact_tracking_columns(conn)
    cur = conn.cursor()
    total_rows = cur.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    base = "FROM contacts WHERE email IS NOT NULL AND TRIM(COALESCE(email, '')) != ''"
    total = cur.execute(f"SELECT COUNT(*) {base}").fetchone()[0]
    sent = cur.execute(
        f"SELECT COUNT(*) {base} AND COALESCE(sent, 0) != 0"
    ).fetchone()[0]
    replied = cur.execute(
        f"SELECT COUNT(*) {base} AND COALESCE(replied, 0) != 0"
    ).fetchone()[0]
    left = total - sent
    no_email = max(0, total_rows - total)
    conn.close()
    return {
        "total": total,
        "sent": sent,
        "replied": replied,
        "left": left,
        "total_rows": total_rows,
        "no_email": no_email,
    }

def get_import_id_for_working_path(working_path):
    working_path = os.path.abspath(working_path)
    conn = connect_master()
    init_master_schema(conn)
    row = conn.execute("SELECT id FROM imports WHERE working_path=?", (working_path,)).fetchone()
    conn.close()
    return row[0] if row else None

def add_lead_to_import(import_id, full_name, number, raw_email):
    email_n = normalize_email(raw_email)
    if not email_n:
        raise ValueError("Email is required.")
    conn = connect_master()
    init_master_schema(conn)
    row = conn.execute("SELECT working_path FROM imports WHERE id=?", (import_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError("Import not found.")
    working_path = row[0]
    conn.close()

    conn_w = sqlite3.connect(working_path)
    ensure_contact_tracking_columns(conn_w)
    conn_w.execute(
        "INSERT INTO contacts (full_name, number, email, sent, replied, replied_at) VALUES (?,?,?,0,0,NULL)",
        (full_name or "", number or "", email_n,),
    )
    conn_w.commit()
    conn_w.close()

    conn_m = connect_master()
    conn_m.execute(
        """
        INSERT INTO leads (import_id, email, full_name, number, sent, replied, replied_at)
        VALUES (?,?,?,?,0,0,NULL)
        ON CONFLICT(import_id, email) DO UPDATE SET
            full_name = excluded.full_name,
            number = excluded.number
        """,
        (import_id, email_n, full_name or "", number or ""),
    )
    conn_m.commit()
    conn_m.close()

def mark_replied_everywhere(email_addr):
    """Set replied on working .db row(s) and master leads (for sent matches)."""
    e = normalize_email(email_addr)
    if not e:
        return
    ts = datetime.now().isoformat()
    conn_m = connect_master()
    init_master_schema(conn_m)
    paths = conn_m.execute(
        """
        SELECT DISTINCT i.working_path FROM leads l
        JOIN imports i ON l.import_id = i.id
        WHERE l.email = ? AND l.sent = 1
        """,
        (e,),
    ).fetchall()
    for row in paths:
        wp = row[0]
        if not wp or not os.path.isfile(wp):
            continue
        cw = sqlite3.connect(wp)
        ensure_contact_tracking_columns(cw)
        cw.execute(
            "UPDATE contacts SET replied=1, replied_at=? WHERE lower(trim(email))=?",
            (ts, e),
        )
        cw.commit()
        cw.close()
    conn_m.execute(
        "UPDATE leads SET replied=1, replied_at=? WHERE email=? AND sent=1",
        (ts, e),
    )
    conn_m.commit()
    conn_m.close()

def remove_import(import_id):
    """Remove import record, master leads, and delete the working .db file."""
    conn = connect_master()
    init_master_schema(conn)
    row = conn.execute("SELECT working_path FROM imports WHERE id=?", (import_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError("Import not found.")
    wp = row[0]
    conn.execute("DELETE FROM leads WHERE import_id=?", (import_id,))
    conn.execute("DELETE FROM imports WHERE id=?", (import_id,))
    conn.commit()
    conn.close()
    if wp and os.path.isfile(wp):
        try:
            os.remove(wp)
        except OSError:
            pass

def resync_import_from_working(import_id):
    """Copy contacts from working .db into master.leads (webhook lookup)."""
    wp = get_working_path_for_import(import_id)
    if not wp or not os.path.isfile(wp):
        return
    conn_m = connect_master()
    init_master_schema(conn_m)
    _sync_working_file_to_master(conn_m, wp, import_id)
    conn_m.commit()
    conn_m.close()

def lookup_contact_master(email_addr):
    """Resolve lead for webhook: prefer a row we actually emailed."""
    e = normalize_email(email_addr)
    if not e:
        return None
    conn = connect_master()
    init_master_schema(conn)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT full_name, number, email FROM leads
        WHERE email=? AND sent=1
        ORDER BY id DESC LIMIT 1
        """,
        (e,),
    )
    row = cur.fetchone()
    if not row:
        cur.execute(
            """
            SELECT full_name, number, email FROM leads
            WHERE email=?
            ORDER BY id DESC LIMIT 1
            """,
            (e,),
        )
        row = cur.fetchone()
    conn.close()
    return tuple(row) if row else None
