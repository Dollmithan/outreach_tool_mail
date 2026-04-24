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
from outreach_tool.services.outreach_service import personalize
from outreach_tool.services.phone_service import resolve_phone_location_label
from outreach_tool.services.supabase_client import sb_batch_insert, sb_batch_upsert, sb_insert, sb_select, sb_upsert

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
_outreach_state: dict = {}
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


# ── Logs API ──────────────────────────────────────────────────────────────────
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
        entry = {"id": imp["id"], "label": imp.get("label", ""), "imported_at": imp.get("imported_at", "")}
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
        import_id, _ = database.import_user_database(tmp, label)
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
        import_id, _ = database.import_excel_as_leads(tmp, label)
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
    try:
        rows = database.get_numbers_for_import(import_id)
        numbers = [r["number"] for r in rows if r.get("number") and str(r.get("number", "")).strip()]
        session_cache: dict = {}
        counts: dict = {}
        for num in numbers:
            loc = resolve_phone_location_label(num, session_cache=session_cache)
            if loc and loc != "Unknown":
                counts[loc] = counts.get(loc, 0) + 1
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
        "import_id": _og("import_id"),
        "daily_limit": int(_og("daily_limit", "100")),
        "delay_min": int(_og("delay_min", "120")),
        "delay_max": int(_og("delay_max", "300")),
        "accounts": accounts,
        "imports": [{"id": i["id"], "label": i.get("label", "")} for i in imports],
    })


@app.route("/api/outreach/config", methods=["POST"])
@login_required
def save_outreach_config():
    data = request.get_json() or {}
    sender_mix = {a["id"]: int(a.get("weight", 0)) for a in data.get("accounts", []) if "id" in a}
    config_service.save_config({
        "OUTREACH": {
            "import_id": str(data.get("import_id", "")),
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

    import_id_str = str(data.get("import_id") or _og("import_id") or "").strip()
    if not import_id_str:
        return jsonify({"error": "No import selected for outreach"}), 400
    try:
        import_id = int(import_id_str)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid import ID"}), 400

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
        "import_id": import_id,
        "daily_limit": daily_limit,
        "delay_min": delay_min,
        "delay_max": delay_max,
        "sender_accounts": sender_accounts,
        "sender_sent_counts": {},
        "current_action": "starting...",
    })

    sender_mix_save = {a["id"]: int(a.get("weight", 0)) for a in data.get("accounts", []) if "id" in a}
    config_service.save_config({
        "OUTREACH": {
            "import_id": str(import_id),
            "daily_limit": str(daily_limit),
            "delay_min": str(delay_min),
            "delay_max": str(delay_max),
            "sender_mix_json": json.dumps(sender_mix_save),
        }
    })

    _log(f"Outreach started — limit={daily_limit}, delay={delay_min}–{delay_max}s")
    return jsonify({"ok": True, "delay_min": delay_min, "delay_max": delay_max})


@app.route("/api/outreach/stop", methods=["POST"])
@login_required
def stop_outreach():
    _outreach_state["enabled"] = False
    _outreach_state["current_action"] = ""
    _log("Outreach stopped.")
    return jsonify({"ok": True})


@app.route("/api/outreach/tick", methods=["POST"])
@login_required
def outreach_tick():
    """Send one email. Called by the browser on each timer tick."""
    if not _outreach_state.get("enabled"):
        return jsonify({"ok": False, "reason": "stopped"})

    import_id     = _outreach_state.get("import_id")
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

    if not import_id:
        _outreach_state["enabled"] = False
        return jsonify({"ok": False, "reason": "no_database"})

    contacts = database.get_unsent(import_id)
    if not contacts:
        _outreach_state["enabled"] = False
        _log("No more unsent contacts. Outreach complete.")
        return jsonify({"ok": False, "reason": "no_contacts", "sent_today": sent_today})

    weighted = _build_weighted_senders(sender_accts)
    if not weighted:
        _outreach_state["enabled"] = False
        return jsonify({"ok": False, "reason": "no_accounts"})

    name, number, email_addr = contacts[0]

    sender_info = max(
        weighted,
        key=lambda s: (s["weight"] * (sent_today + 1)) - sent_counts.get(s["id"], 0),
    )
    acct = sender_info["account"]

    subj = personalize(acct.get("outreach_subject") or "", name or "")
    body = personalize(acct.get("outreach_body") or "", name or "")

    # Check Supabase persistent cache for phone location; call API once if missing
    location = resolve_phone_location_label(number)

    _log(f"Sending to {email_addr} ({location}) via {sender_info['label']}…")
    ok = send_email(email_addr, subj, body, log_fn=_log, account=acct)

    if ok:
        database.mark_sent(import_id, email_addr)
        sent_today += 1
        sent_counts[sender_info["id"]] = sent_counts.get(sender_info["id"], 0) + 1
        app_data.append_outreach_history_entry({
            "name": name or "",
            "email": email_addr,
            "number": number or "",
            "location": location,
            "sender_label": sender_info["label"],
            "sender_email": acct.get("smtp_user", ""),
        })
        _log(f"Sent to {email_addr} ({location}) via {sender_info['label']}")
    else:
        _log(f"Failed to send to {email_addr}", "ERROR")

    remaining = len(contacts) - 1
    done = remaining == 0 or sent_today >= daily_limit
    
    import random
    next_delay_sec = random.randint(delay_min, delay_max)
    next_delay_ms = next_delay_sec * 1000

    if done:
        _outreach_state["enabled"] = False
        _outreach_state["current_action"] = ""
        _log(f"Outreach session done. {sent_today} emails sent today.")
    else:
        action_str = f"waiting {next_delay_sec}s..."
        _outreach_state["current_action"] = action_str
        _log(f"[outreach] {action_str}")

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
        "next_delay_ms": next_delay_ms,
        "current_action": _outreach_state.get("current_action", "")
    })


@app.route("/api/outreach/status")
@login_required
def outreach_status_api():
    h = app_data.load_outreach_history()
    return jsonify({
        "running": bool(_outreach_state.get("enabled")),
        "outreach_action": _outreach_state.get("current_action", ""),
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

    interval    = int(data.get("check_interval", int(_mg("check_interval", "120"))))
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

    config_service.save_config({
        "MONITOR": {
            "check_interval": str(interval),
            "account_ids_json": json.dumps(account_ids),
        }
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

    # ── Phase 1: Collect all new replies (fetch bodies eagerly while IMAP is open) ──
    pending = []
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
                    pending.append({
                        "acct_key": acct_key,
                        "mid_str": mid_str,
                        "sender": sender,
                        "contact": contact,
                        "reply_date": reply_date,
                        "body_text": body_text,
                        "receiver": acct.get("smtp_user") or acct_name,
                        "acct_name": acct_name,
                    })
            finally:
                imap.logout()
        except Exception as e:
            _log(f"Monitor check error ({acct_name}): {e}", "ERROR")

    # ── Phase 2: Smart phone-location caching based on reply count ──────────────
    reply_count = len(pending)
    # 1 reply → call API once, skip cache entirely (no read, no write)
    # >1 replies → use a temporary in-memory session cache; clear it when done
    no_cache     = reply_count == 1
    session_cache: dict = {} if reply_count > 1 else None  # type: ignore[assignment]

    # ── Phase 3: Process replies ─────────────────────────────────────────────────
    replies_found = 0
    for reply in pending:
        contact  = reply["contact"]
        location = resolve_phone_location_label(
            contact[1],
            use_persistent_cache=not no_cache,
            session_cache=session_cache,
        )
        _log(f"Reply from {reply['sender']} ({contact[0]}) [{location}] — alerting Discord!")
        ok = send_discord_alert(
            webhook, contact, reply["body_text"], location,
            reply["reply_date"], reply["receiver"], _log,
        )
        if ok:
            database.mark_replied_everywhere(reply["sender"])
            database.record_reply_processed(reply["acct_key"], reply["mid_str"])
            replies_found += 1
            _log(f"Discord notified for {reply['sender']}")
        else:
            _log(f"Discord webhook failed for {reply['sender']}", "ERROR")

    # Clear session cache after processing
    if session_cache is not None:
        session_cache.clear()

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
        "outreach_action": _outreach_state.get("current_action", ""),
        "monitor_running":  bool(_monitor_state.get("enabled")),
        "sent_today":  h.get("total_sent", 0),
        "daily_limit": _outreach_state.get("daily_limit", 100),
    })


# ── Migration endpoint ─────────────────────────────────────────────────────────
@app.route("/api/migrate", methods=["POST"])
@login_required
def migrate_local_to_supabase():
    """
    One-time migration of local SQLite/JSON/INI data to Supabase.
    Safe to call multiple times (config + phone cache are upserted; a guard
    in app_config prevents duplicate outreach_history / import rows).
    """
    result = {
        "config_migrated": False,
        "accounts_migrated": 0,
        "history_entries_migrated": 0,
        "imports_migrated": 0,
        "leads_migrated": 0,
        "phone_cache_migrated": 0,
        "skipped": [],
    }

    # ── 1. Local config.ini ───────────────────────────────────────────────────
    local_config_candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_data", "config.ini"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "outreach_tool_data", "config.ini"),
    ]
    for local_config_path in local_config_candidates:
        if not os.path.exists(local_config_path):
            continue
        try:
            import configparser
            c = configparser.ConfigParser(interpolation=None)
            c.read(local_config_path, encoding="utf-8")

            for section in ("DISCORD", "OUTREACH", "MONITOR"):
                if c.has_section(section):
                    for key, val in c.items(section):
                        try:
                            sb_upsert("app_config", {"key": f"{section}.{key}", "value": val})
                        except Exception:
                            pass

            for sec in c.sections():
                if sec.startswith("EMAIL_ACCOUNT:"):
                    acct_id = sec[len("EMAIL_ACCOUNT:"):].strip()
                    row = {
                        "id": acct_id,
                        "label": c.get(sec, "label", fallback=""),
                        "smtp_host": c.get(sec, "smtp_host", fallback="smtp.alexhost.com"),
                        "smtp_port": c.get(sec, "smtp_port", fallback="465"),
                        "smtp_user": c.get(sec, "smtp_user", fallback=""),
                        "smtp_password": c.get(sec, "smtp_password", fallback=""),
                        "display_name": c.get(sec, "display_name", fallback=""),
                        "imap_host": c.get(sec, "imap_host", fallback="imap.alexhost.com"),
                        "imap_port": c.get(sec, "imap_port", fallback="993"),
                        "outreach_subject": c.get(sec, "outreach_subject", fallback=""),
                        "outreach_body": c.get(sec, "outreach_body", fallback=""),
                        "weight": 0,
                        "sort_order": result["accounts_migrated"],
                    }
                    try:
                        sb_upsert("email_accounts", row)
                        result["accounts_migrated"] += 1
                    except Exception:
                        pass

            result["config_migrated"] = True
            _log(f"Config migrated from {local_config_path}")
        except Exception as e:
            _log(f"Config migration error: {e}", "WARNING")
        break  # only process the first found config

    # ── 2. Local outreach_history.json ────────────────────────────────────────
    history_candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_data", "outreach_history.json"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "outreach_tool_data", "outreach_history.json"),
    ]
    for history_path in history_candidates:
        if not os.path.exists(history_path):
            continue
        try:
            # Guard: skip if Supabase already has history rows
            existing_count = len(sb_select("outreach_history", columns="id", limit=1))
            if existing_count > 0:
                result["skipped"].append("outreach_history (already exists)")
                break
            with open(history_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            days = raw.get("days", {}) if isinstance(raw, dict) else {}
            batch = []
            for day_key, day_data in days.items():
                for entry in (day_data.get("entries") or []):
                    if not isinstance(entry, dict):
                        continue
                    batch.append({
                        "date": day_key,
                        "name": entry.get("name", ""),
                        "email": entry.get("email", ""),
                        "number": entry.get("number", ""),
                        "location": entry.get("location", ""),
                        "sender_label": entry.get("sender_label", ""),
                        "sender_email": entry.get("sender_email", ""),
                    })
            if batch:
                sb_batch_insert("outreach_history", batch)
                result["history_entries_migrated"] = len(batch)
            _log(f"Outreach history migrated: {len(batch)} entries")
        except Exception as e:
            _log(f"History migration error: {e}", "WARNING")
        break

    # ── 3. Local tool_leads.db ────────────────────────────────────────────────
    master_db_candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_data", "tool_leads.db"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "outreach_tool_data", "tool_leads.db"),
    ]
    for master_db in master_db_candidates:
        if not os.path.exists(master_db):
            continue
        try:
            import sqlite3
            conn = sqlite3.connect(master_db)
            conn.row_factory = sqlite3.Row

            # Guard: skip imports if Supabase already has some
            existing_imports = len(sb_select("imports", columns="id", limit=1))
            if existing_imports > 0:
                result["skipped"].append("imports/leads (already exists)")
            else:
                local_imports = conn.execute(
                    "SELECT id, label, working_path FROM imports"
                ).fetchall()
                for imp in local_imports:
                    local_id = imp["id"]
                    label = imp["label"] or f"Import #{local_id}"
                    try:
                        sb_result = sb_insert("imports", {"label": label})
                        sb_import_id = sb_result[0]["id"]
                        result["imports_migrated"] += 1
                    except Exception as e:
                        _log(f"Import row error ({label}): {e}", "WARNING")
                        continue

                    leads = conn.execute(
                        """
                        SELECT full_name, number, email,
                               COALESCE(sent, 0), COALESCE(replied, 0),
                               replied_at, sent_at
                        FROM leads WHERE import_id = ?
                        AND email IS NOT NULL AND TRIM(COALESCE(email, '')) != ''
                        """,
                        (local_id,),
                    ).fetchall()

                    if leads:
                        batch = []
                        seen_emails: set = set()
                        for lead in leads:
                            e = (lead["email"] or "").strip().lower()
                            if not e or e in seen_emails:
                                continue
                            seen_emails.add(e)
                            batch.append({
                                "import_id": sb_import_id,
                                "email": e,
                                "full_name": lead["full_name"] or "",
                                "number": lead["number"] or "",
                                "sent": bool(lead[3]),
                                "replied": bool(lead[4]),
                                "sent_at": lead["sent_at"],
                                "replied_at": lead["replied_at"],
                            })
                        try:
                            sb_batch_insert("leads", batch)
                            result["leads_migrated"] += len(batch)
                        except Exception as e:
                            _log(f"Leads batch error for '{label}': {e}", "WARNING")

            # Migrate phone_country_cache (idempotent upsert)
            try:
                cache_rows = conn.execute(
                    "SELECT number_sanitized, country_code, country_label FROM phone_country_cache"
                ).fetchall()
                if cache_rows:
                    batch = [
                        {
                            "number_sanitized": r["number_sanitized"],
                            "country_code": r["country_code"] or "",
                            "country_label": r["country_label"] or "",
                        }
                        for r in cache_rows
                    ]
                    sb_batch_upsert("phone_country_cache", batch)
                    result["phone_cache_migrated"] = len(batch)
                    _log(f"Phone cache migrated: {len(batch)} entries")
            except Exception as e:
                _log(f"Phone cache migration error: {e}", "WARNING")

            conn.close()
            _log(f"Master DB migrated from {master_db}")
        except Exception as e:
            _log(f"Master DB migration error: {e}", "WARNING")
        break

    _log(f"Migration complete: {result}")
    return jsonify({"ok": True, "result": result})


if __name__ == "__main__":
    app_data._ensure_app_dirs()
    _log("OutreachPro Web starting on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
