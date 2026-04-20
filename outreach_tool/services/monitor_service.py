import email
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

from .app_data import _ensure_app_dirs
from .database import connect_master, init_master_schema, lookup_contact_master, mark_replied_everywhere
from .email_service import get_imap
from .phone_service import resolve_phone_location_label

def send_discord_alert(webhook_url, contact, reply_snippet, location: str):
    full_name, number, email_addr = contact
    payload = {
        "embeds": [{
            "title": "📬 Reply Received!",
            "color": 0x00C896,
            "fields": [
                {"name": "👤 Name", "value": full_name or "Unknown", "inline": True},
                {"name": "📧 Email", "value": email_addr, "inline": False},
                {"name": "📞 Phone", "value": number or "N/A", "inline": True},
                {"name": "🌍 Location", "value": location or "Unknown", "inline": False},
                {"name": "💬 Reply Preview", "value": reply_snippet[:300] or "—", "inline": False},
            ],
            "timestamp": datetime.utcnow().isoformat()
        }]
    }
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        return r.status_code == 204
    except Exception as e:
        return False

def extract_sender_email(from_header):
    match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', from_header)
    return match.group(0).lower() if match else None

def run_reply_monitor(
    webhook_url,
    check_interval,
    log_fn,
    stop_event,
    seen_ids: set,
    account: Optional[Dict[str, str]] = None,
    accounts: Optional[List[Dict[str, str]]] = None,
):
    _ensure_app_dirs()
    conn = connect_master()
    init_master_schema(conn)
    monitor_accounts = [acc for acc in (accounts or ([] if account is None else [account])) if isinstance(acc, dict)]
    if not monitor_accounts:
        raise ValueError("No inbox account was provided for reply monitoring.")
    log_fn(f"👀 Reply monitor started for {len(monitor_accounts)} inbox(es) (lookup: master tool_leads.db).")
    while not stop_event.is_set():
        try:
            for account_item in monitor_accounts:
                if stop_event.is_set():
                    break
                account_name = account_item.get("label") or account_item.get("smtp_user") or "Email account"
                account_key = account_item.get("id") or account_item.get("smtp_user") or account_name
                imap = get_imap(account_item)
                try:
                    imap.select("INBOX")
                    _, data = imap.search(None, "ALL")
                    msg_ids = data[0].split()
                    for mid in msg_ids:
                        seen_key = (account_key, mid)
                        if seen_key in seen_ids:
                            continue
                        seen_ids.add(seen_key)
                        _, msg_data = imap.fetch(mid, "(RFC822)")
                        raw = msg_data[0][1]
                        msg = email.message_from_bytes(raw)
                        from_h = msg.get("From", "")
                        sender = extract_sender_email(from_h)
                        if not sender:
                            continue
                        contact = lookup_contact_master(sender)
                        if contact:
                            location = resolve_phone_location_label(conn, contact[1])
                            body_text = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/plain":
                                        body_text = part.get_payload(decode=True).decode(errors="ignore")
                                        break
                            else:
                                body_text = msg.get_payload(decode=True).decode(errors="ignore")
                            log_fn(f"📬 Reply from {sender} ({contact[0]}) [{location}] via {account_name} — alerting Discord!")
                            ok = send_discord_alert(webhook_url, contact, body_text, location)
                            if ok:
                                mark_replied_everywhere(sender)
                                log_fn(f"   ✅ Discord notified for {sender}")
                            else:
                                log_fn(f"   ❌ Discord webhook failed for {sender}")
                finally:
                    imap.logout()
        except Exception as e:
            log_fn(f"⚠️ Monitor error: {e}")
        for _ in range(check_interval):
            if stop_event.is_set(): break
            time.sleep(1)
    log_fn("👀 Reply monitor stopped.")
    try:
        conn.close()
    except Exception:
        pass
