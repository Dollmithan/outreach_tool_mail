import email as _email_module
import json
import logging
import os
import sys
import tempfile
import threading
import uuid
from collections import deque
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from outreach_tool.services import app_data, config_service, database
from outreach_tool.services.email_service import get_imap, send_email
from outreach_tool.services.monitor_service import extract_sender_email, send_discord_alert
from outreach_tool.services.outreach_service import get_unsent, mark_sent, personalize
from outreach_tool.services.phone_service import resolve_phone_location_label

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "outreach-pro-secret-change-me-2024")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"

# ── Log buffer ─────────────────────────────────────────────────────────────────
_log_buffer = deque(maxlen=600)
_log_lock = threading.Lock()


def _log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    with _log_lock:
        _log_buffer.append({"time": ts, "message": str(msg), "level": level})


_SKIP_LOGGERS = {"werkzeug", "urllib3", "charset_normalizer", "flask", "engineio", "socketio"}


class _WebLogHandler(logging.Handler):
    def emit(self, record):
        if record.name in _SKIP_LOGGERS or record.name.startswith("werkzeug"):
            return
        _log(self.format(record), record.levelname)


_handler = _WebLogHandler()
_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.INFO)

# ── Service state (no threads — browser drives timing) ─────────────────────────
# outreach_state keys: enabled, working_path, daily_limit, delay_min, delay_max,
#                      sender_accounts, sender_sent_counts
_outreach_state: dict = {}

# monitor_state keys: enabled, accounts, check_interval, seen_ids
_monitor_state: dict = {}


# ── Auth ───────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def index():
    return redirect(url_for("dashboard") if session.get("logged_in") else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        error = "Invalid credentials."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


# ── Logs API (polling — no SSE needed) ────────────────────────────────────────
@app.route("/api/logs")
@login_required
def get_logs():
    since = request.args.get("since", 0, type=int)
    with _log_lock:
        buf = list(_log_buffer)
    return jsonify({"logs": buf[since:], "total": len(buf)})


@app.route("/api/logs", methods=["DELETE"])
@login_required
def clear_logs():
    with _log_lock:
        _log_buffer.clear()
    return jsonify({"ok": True})


# ── Settings / Accounts API ────────────────────────────────────────────────────
@app.route("/api/accounts")
@login_required
def get_accounts():
    return jsonify(config_service.get_email_accounts())


@app.route("/api/accounts", methods=["POST"])
@login_required
def add_account():
    data = request.get_json() or {}
    accounts = config_service.get_email_accounts()
    new_acct = {
        "id": uuid.uuid4().hex[:12],
        "label": data.get("label", "New Account"),
        "smtp_host": data.get("smtp_host", "smtp.alexhost.com"),
        "smtp_port": data.get("smtp_port", "465"),
        "smtp_user": data.get("smtp_user", ""),
        "smtp_password": data.get("smtp_password", ""),
        "display_name": data.get("display_name", ""),
        "imap_host": data.get("imap_host", "imap.alexhost.com"),
        "imap_port": data.get("imap_port", "993"),
        "outreach_subject": data.get("outreach_subject", ""),
        "outreach_body": data.get("outreach_body", ""),
    }
    accounts.append(new_acct)
    config_service.save_email_accounts(accounts)
    _log(f"Added account: {new_acct['label']}")
    return jsonify(new_acct)


@app.route("/api/accounts/<account_id>", methods=["PUT"])
@login_required
def update_account(account_id):
    data = request.get_json() or {}
    accounts = config_service.get_email_accounts()
    for i, acct in enumerate(accounts):
        if acct["id"] == account_id:
            for key in (
                "label", "smtp_host", "smtp_port", "smtp_user", "smtp_password",
                "display_name", "imap_host", "imap_port", "outreach_subject", "outreach_body",
            ):
                if key in data:
                    accounts[i][key] = data[key]
            config_service.save_email_accounts(accounts)
            _log(f"Updated account: {accounts[i]['label']}")
            return jsonify(accounts[i])
    return jsonify({"error": "Account not found"}), 404


@app.route("/api/accounts/<account_id>", methods=["DELETE"])
@login_required
def delete_account(account_id):
    accounts = config_service.get_email_accounts()
    filtered = [a for a in accounts if a["id"] != account_id]
    if len(filtered) == len(accounts):
        return jsonify({"error": "Account not found"}), 404
    config_service.save_email_accounts(filtered)
    _log(f"Removed account: {account_id}")
    return jsonify({"ok": True})


@app.route("/api/discord")
@login_required
def get_discord():
    return jsonify({"webhook": config_service.cfg("DISCORD", "webhook", "")})


@app.route("/api/discord", methods=["POST"])
@login_required
def save_discord():
    data = request.get_json() or {}
    config_service.save_config({"DISCORD": {"webhook": data.get("webhook", "")}})
    _log("Discord webhook saved.")
    return jsonify({"ok": True})


# ── Database / Imports API ─────────────────────────────────────────────────────
@app.route("/api/imports")
@login_required
def list_imports():
    imports = database.list_imports()
    result = []
    for imp in imports:
        entry = dict(imp)
        try:
            entry["stats"] = database.get_stats_for_import(imp["id"])
        except Exception:
            entry["stats"] = {"total": 0, "sent": 0, "replied": 0, "left": 0}
        result.append(entry)
    return jsonify(result)


@app.route("/api/imports/db", methods=["POST"])
@login_required
def import_db_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".db"):
        return jsonify({"error": "File must be a .db file"}), 400
    label = (request.form.get("label") or "").strip() or secure_filename(f.filename)
    tmp = os.path.join(tempfile.gettempdir(), f"upload_{uuid.uuid4().hex}.db")
    try:
        f.save(tmp)
        import_id, working_path = database.import_user_database(tmp, label)
        stats = database.get_stats_for_import(import_id)
        _log(f"Imported database: {label} ({stats['total']} leads)")
        return jsonify({"id": import_id, "label": label, "stats": stats})
    except Exception as e:
        _log(f"DB import failed: {e}", "ERROR")
        return jsonify({"error": str(e)}), 400
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


@app.route("/api/imports/excel", methods=["POST"])
@login_required
def import_excel_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        return jsonify({"error": "File must be .xlsx or .xlsm"}), 400
    label = (request.form.get("label") or "").strip() or secure_filename(f.filename)
    tmp = os.path.join(tempfile.gettempdir(), f"upload_{uuid.uuid4().hex}{ext}")
    try:
        f.save(tmp)
        import_id, working_path = database.import_excel_as_leads(tmp, label)
        stats = database.get_stats_for_import(import_id)
        _log(f"Imported Excel: {label} ({stats['total']} leads)")
        return jsonify({"id": import_id, "label": label, "stats": stats})
    except Exception as e:
        _log(f"Excel import failed: {e}", "ERROR")
        return jsonify({"error": str(e)}), 400
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


@app.route("/api/imports/<int:import_id>", methods=["DELETE"])
@login_required
def delete_import(import_id):
    try:
        database.remove_import(import_id)
        _log(f"Removed import #{import_id}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/imports/<int:import_id>/stats")
@login_required
def import_stats(import_id):
    try:
        return jsonify(database.get_stats_for_import(import_id))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/imports/<int:import_id>/locations")
@login_required
def import_locations(import_id):
    import sqlite3 as _sq
    wp = database.get_working_path_for_import(import_id)
    if not wp or not os.path.isfile(wp):
        return jsonify({"error": "Working database not found"}), 404
    try:
        conn_w = _sq.connect(wp)
        numbers = [r[0] for r in conn_w.execute(
            "SELECT number FROM contacts WHERE number IS NOT NULL AND trim(number) != ''"
        ).fetchall()]
        conn_w.close()
        conn_m = database.connect_master()
        database.init_master_schema(conn_m)
        counts: dict = {}
        for num in numbers:
            loc = resolve_phone_location_label(conn_m, num)
            if loc:
                counts[loc] = counts.get(loc, 0) + 1
        conn_m.close()
        return jsonify({"locations": [
            {"country": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True)
        ]})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/imports/refresh", methods=["POST"])
@login_required
def refresh_imports():
    imports = database.list_imports()
    for imp in imports:
        try:
            database.resync_import_from_working(imp["id"])
        except Exception as e:
            _log(f"Resync failed for #{imp['id']}: {e}", "WARNING")
    _log(f"Refreshed {len(imports)} import(s).")
    return jsonify({"ok": True, "count": len(imports)})


# ── Outreach API ───────────────────────────────────────────────────────────────
def _build_weighted_senders(sender_accounts_raw):
    weighted = []
    for raw in (sender_accounts_raw or []):
        try:
            w = float(raw.get("weight", 0))
        except Exception:
            w = 0.0
        if w > 0:
            weighted.append({
                "id": raw.get("id", ""),
                "label": raw.get("label") or raw.get("smtp_user") or "Account",
                "weight": w,
                "account": raw,
            })
    if weighted:
        return weighted
    default = config_service.get_default_email_account()
    if default:
        return [{"id": default.get("id", ""), "label": default.get("label") or "Account",
                 "weight": 100.0, "account": default}]
    return []


@app.route("/api/outreach/config")
@login_required
def get_outreach_config():
    c = config_service.load_config()

    def _og(key, fb=""):
        return c.get("OUTREACH", key, fallback=fb) if c.has_section("OUTREACH") else fb

    try:
        sender_mix = json.loads(_og("sender_mix_json", "{}"))
    except Exception:
        sender_mix = {}

    accounts = config_service.get_email_accounts()
    for acct in accounts:
        acct["weight"] = int(sender_mix.get(acct["id"], 0))

    imports = database.list_imports()
    return jsonify({
        "working_path": _og("working_path"),
        "daily_limit": int(_og("daily_limit", "100")),
        "delay_min": int(_og("delay_min", "120")),
        "delay_max": int(_og("delay_max", "300")),
        "accounts": accounts,
        "imports": [
            {"id": i["id"], "label": i.get("label", ""), "working_path": i["working_path"]}
            for i in imports
        ],
    })


@app.route("/api/outreach/config", methods=["POST"])
@login_required
def save_outreach_config():
    data = request.get_json() or {}
    sender_mix = {a["id"]: int(a.get("weight", 0)) for a in data.get("accounts", []) if "id" in a}
    config_service.save_config({
        "OUTREACH": {
            "working_path": data.get("working_path", ""),
            "daily_limit": str(int(data.get("daily_limit", 100))),
            "delay_min": str(int(data.get("delay_min", 120))),
            "delay_max": str(int(data.get("delay_max", 300))),
            "sender_mix_json": json.dumps(sender_mix),
        }
    })
    incoming = data.get("accounts", [])
    if incoming:
        existing = config_service.get_email_accounts()
        for inc in incoming:
            for ex in existing:
                if ex["id"] == inc.get("id"):
                    for k in ("outreach_subject", "outreach_body"):
                        if k in inc:
                            ex[k] = inc[k]
        config_service.save_email_accounts(existing)
    return jsonify({"ok": True})


@app.route("/api/outreach/start", methods=["POST"])
@login_required
def start_outreach():
    data = request.get_json() or {}
    c = config_service.load_config()

    def _og(key, fb=""):
        return c.get("OUTREACH", key, fallback=fb) if c.has_section("OUTREACH") else fb

    db_path = data.get("working_path") or _og("working_path")
    if not db_path or not os.path.isfile(db_path):
        return jsonify({"error": "No valid database selected for outreach"}), 400

    daily_limit = int(data.get("daily_limit", int(_og("daily_limit", "100"))))
    delay_min   = int(data.get("delay_min",   int(_og("delay_min",   "120"))))
    delay_max   = int(data.get("delay_max",   int(_og("delay_max",   "300"))))

    try:
        sender_mix = json.loads(_og("sender_mix_json", "{}"))
    except Exception:
        sender_mix = {}

    all_accounts = config_service.get_email_accounts()
    sender_accounts = [{**a, "weight": int(sender_mix.get(a["id"], 0))}
                       for a in all_accounts if int(sender_mix.get(a["id"], 0)) > 0]
    if not sender_accounts:
        sender_accounts = [{**a, "weight": 100} for a in all_accounts]

    _outreach_state.update({
        "enabled": True,
        "working_path": db_path,
        "daily_limit": daily_limit,
        "delay_min": delay_min,
        "delay_max": delay_max,
        "sender_accounts": sender_accounts,
        "sender_sent_counts": {},
    })
    _log(f"Outreach started — limit={daily_limit}, delay={delay_min}–{delay_max}s")
    return jsonify({"ok": True, "delay_min": delay_min, "delay_max": delay_max})


@app.route("/api/outreach/stop", methods=["POST"])
@login_required
def stop_outreach():
    _outreach_state["enabled"] = False
    _log("Outreach stopped.")
    return jsonify({"ok": True})


@app.route("/api/outreach/tick", methods=["POST"])
@login_required
def outreach_tick():
    """Send one email. Called by the browser on each timer tick."""
    if not _outreach_state.get("enabled"):
        return jsonify({"ok": False, "reason": "stopped"})

    db_path       = _outreach_state.get("working_path", "")
    daily_limit   = int(_outreach_state.get("daily_limit", 100))
    delay_min     = int(_outreach_state.get("delay_min", 120))
    delay_max     = int(_outreach_state.get("delay_max", 300))
    sender_accts  = _outreach_state.get("sender_accounts", [])
    sent_counts   = _outreach_state.setdefault("sender_sent_counts", {})

    history    = app_data.load_outreach_history()
    sent_today = history.get("total_sent", 0)

    if sent_today >= daily_limit:
        _outreach_state["enabled"] = False
        _log(f"Daily limit of {daily_limit} reached. Outreach complete.")
        return jsonify({"ok": False, "reason": "daily_limit_reached",
                        "sent_today": sent_today, "daily_limit": daily_limit})

    if not db_path or not os.path.isfile(db_path):
        _outreach_state["enabled"] = False
        return jsonify({"ok": False, "reason": "no_database"})

    contacts = get_unsent(db_path)
    if not contacts:
        _outreach_state["enabled"] = False
        _log("No more unsent contacts. Outreach complete.")
        return jsonify({"ok": False, "reason": "no_contacts", "sent_today": sent_today})

    weighted = _build_weighted_senders(sender_accts)
    if not weighted:
        _outreach_state["enabled"] = False
        return jsonify({"ok": False, "reason": "no_accounts"})

    name, number, email_addr = contacts[0]

    # Same weighted-rotation logic as the original sequential outreach
    sender_info = max(
        weighted,
        key=lambda s: (s["weight"] * (sent_today + 1)) - sent_counts.get(s["id"], 0),
    )
    acct = sender_info["account"]

    subj = personalize(acct.get("outreach_subject") or "", name or "")
    body = personalize(acct.get("outreach_body") or "", name or "")

    conn_m = database.connect_master()
    database.init_master_schema(conn_m)
    location = resolve_phone_location_label(conn_m, number)
    conn_m.close()

    _log(f"Sending to {email_addr} ({location}) via {sender_info['label']}…")
    ok = send_email(email_addr, subj, body, log_fn=_log, account=acct)

    if ok:
        mark_sent(db_path, email_addr)
        sent_today += 1
        sent_counts[sender_info["id"]] = sent_counts.get(sender_info["id"], 0) + 1
        app_data.append_outreach_history_entry({
            "name": name or "",
            "email": email_addr,
            "number": number or "",
            "location": location,
            "sender_label": sender_info["label"],
            "sender_email": acct.get("smtp_user", ""),
            "working_path": db_path,
        })
        _log(f"Sent to {email_addr} ({location}) via {sender_info['label']}")
    else:
        _log(f"Failed to send to {email_addr}", "ERROR")

    remaining = len(contacts) - 1
    done = remaining == 0 or sent_today >= daily_limit
    if done:
        _outreach_state["enabled"] = False
        _log(f"Outreach session done. {sent_today} emails sent today.")

    return jsonify({
        "ok": ok,
        "sent_today": sent_today,
        "daily_limit": daily_limit,
        "delay_min": delay_min,
        "delay_max": delay_max,
        "remaining": remaining,
        "done": done,
        "contact": {"name": name or "", "email": email_addr, "location": location},
        "sender": sender_info["label"],
    })


@app.route("/api/outreach/status")
@login_required
def outreach_status_api():
    h = app_data.load_outreach_history()
    return jsonify({
        "running": bool(_outreach_state.get("enabled")),
        "sent_today": h.get("total_sent", 0),
        "daily_limit": _outreach_state.get("daily_limit", 100),
    })


@app.route("/api/outreach/history")
@login_required
def outreach_history():
    return jsonify(app_data.list_outreach_history_days())


@app.route("/api/outreach/history/reset", methods=["POST"])
@login_required
def reset_outreach_history():
    app_data.reset_outreach_history()
    _log("Today's outreach history reset.")
    return jsonify({"ok": True})


# ── Monitor API ────────────────────────────────────────────────────────────────
@app.route("/api/monitor/config")
@login_required
def get_monitor_config():
    c = config_service.load_config()

    def _mg(key, fb):
        return c.get("MONITOR", key, fallback=fb) if c.has_section("MONITOR") else fb

    try:
        account_ids = json.loads(_mg("account_ids_json", "[]"))
    except Exception:
        account_ids = []
    return jsonify({
        "check_interval": int(_mg("check_interval", "120")),
        "account_ids": account_ids,
        "accounts": config_service.get_email_accounts(),
    })


@app.route("/api/monitor/config", methods=["POST"])
@login_required
def save_monitor_config():
    data = request.get_json() or {}
    config_service.save_config({
        "MONITOR": {
            "check_interval": str(int(data.get("check_interval", 120))),
            "account_ids_json": json.dumps(data.get("account_ids", [])),
        }
    })
    return jsonify({"ok": True})


@app.route("/api/monitor/start", methods=["POST"])
@login_required
def start_monitor():
    data = request.get_json() or {}
    webhook = config_service.cfg("DISCORD", "webhook", "")
    if not webhook:
        return jsonify({"error": "No Discord webhook configured in Settings"}), 400

    c = config_service.load_config()

    def _mg(key, fb):
        return c.get("MONITOR", key, fallback=fb) if c.has_section("MONITOR") else fb

    interval   = int(data.get("check_interval", int(_mg("check_interval", "120"))))
    account_ids = data.get("account_ids") or []
    if not account_ids:
        try:
            account_ids = json.loads(_mg("account_ids_json", "[]"))
        except Exception:
            account_ids = []

    all_accounts = config_service.get_email_accounts()
    accounts = [a for a in all_accounts if a["id"] in account_ids] if account_ids else all_accounts
    if not accounts:
        return jsonify({"error": "No accounts selected for monitoring"}), 400

    _monitor_state.update({
        "enabled": True,
        "accounts": accounts,
        "check_interval": interval,
        "seen_ids": set(),
    })
    _log(f"Monitor started — {len(accounts)} account(s), every {interval}s")
    return jsonify({"ok": True, "check_interval": interval})


@app.route("/api/monitor/stop", methods=["POST"])
@login_required
def stop_monitor():
    _monitor_state["enabled"] = False
    _log("Monitor stopped.")
    return jsonify({"ok": True})


@app.route("/api/monitor/tick", methods=["POST"])
@login_required
def monitor_tick():
    """Check all inboxes once. Called by the browser on each timer tick."""
    if not _monitor_state.get("enabled"):
        return jsonify({"ok": False, "reason": "stopped"})

    webhook  = config_service.cfg("DISCORD", "webhook", "")
    accounts = _monitor_state.get("accounts", [])
    seen_ids = _monitor_state.setdefault("seen_ids", set())

    replies_found = 0
    conn = database.connect_master()
    database.init_master_schema(conn)
    try:
        for acct in accounts:
            acct_name = acct.get("label") or acct.get("smtp_user") or "account"
            acct_key  = acct.get("id") or acct.get("smtp_user") or acct_name
            try:
                imap = get_imap(acct)
                try:
                    imap.select("INBOX")
                    _, data = imap.search(None, "ALL")
                    for mid in data[0].split():
                        seen_key = (acct_key, mid)
                        if seen_key in seen_ids:
                            continue
                        seen_ids.add(seen_key)
                        mid_str = mid.decode() if isinstance(mid, bytes) else str(mid)
                        if database.is_reply_processed(acct_key, mid_str):
                            continue
                        _, msg_data = imap.fetch(mid, "(RFC822)")
                        msg = _email_module.message_from_bytes(msg_data[0][1])
                        sender = extract_sender_email(msg.get("From", ""))
                        if not sender:
                            continue
                        reply_date = msg.get("Date") or datetime.utcnow().isoformat()
                        contact = database.lookup_contact_master(sender)
                        if not contact:
                            continue
                        location = resolve_phone_location_label(conn, contact[1])
                        if msg.is_multipart():
                            body_text = ""
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    body_text = part.get_payload(decode=True).decode(errors="ignore")
                                    break
                        else:
                            body_text = msg.get_payload(decode=True).decode(errors="ignore")
                        if len(body_text) > 1_000_000:
                            body_text = body_text[:1_000_000] + "\n\n[truncated]"
                        receiver = acct.get("smtp_user") or acct_name
                        _log(f"Reply from {sender} ({contact[0]}) [{location}] — alerting Discord!")
                        ok = send_discord_alert(webhook, contact, body_text, location,
                                                reply_date, receiver, _log)
                        if ok:
                            database.mark_replied_everywhere(sender)
                            database.record_reply_processed(acct_key, mid_str)
                            replies_found += 1
                            _log(f"Discord notified for {sender}")
                        else:
                            _log(f"Discord webhook failed for {sender}", "ERROR")
                finally:
                    imap.logout()
            except Exception as e:
                _log(f"Monitor check error ({acct_name}): {e}", "ERROR")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "replies_found": replies_found,
        "check_interval": _monitor_state.get("check_interval", 120),
    })


@app.route("/api/monitor/status")
@login_required
def monitor_status():
    return jsonify({"running": bool(_monitor_state.get("enabled"))})


# ── Global status ──────────────────────────────────────────────────────────────
@app.route("/api/status")
@login_required
def all_status():
    h = app_data.load_outreach_history()
    return jsonify({
        "outreach_running": bool(_outreach_state.get("enabled")),
        "monitor_running":  bool(_monitor_state.get("enabled")),
        "sent_today":  h.get("total_sent", 0),
        "daily_limit": _outreach_state.get("daily_limit", 100),
    })


if __name__ == "__main__":
    app_data._ensure_app_dirs()
    _log("OutreachPro Web starting on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
