import email
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

import requests

from .app_data import _ensure_app_dirs
from .database import connect_master, init_master_schema, lookup_contact_master, mark_replied_everywhere, is_reply_processed, record_reply_processed
from .email_service import get_imap
from .phone_service import resolve_phone_location_label

import json

def send_discord_alert(webhook_url, contact, reply_snippet, location: str, reply_time: str = None, receiver_email: str = None, log_fn=None):
    full_name, number, email_addr, sent_at = contact
    
    def clean(v, max_len=1024):
        s = str(v or "").strip()
        if len(s) > max_len:
            return s[:max_len-5] + "..."
        return s or "—"

    fields = [
        {"name": "👤 Name", "value": clean(full_name), "inline": True},
        {"name": "📧 From Email", "value": clean(email_addr), "inline": False},
        {"name": "📞 Phone", "value": clean(number), "inline": True},
        {"name": "🌍 Location", "value": clean(location), "inline": False},
    ]
    if receiver_email:
        fields.append({"name": "📥 Receiving Inbox", "value": clean(receiver_email), "inline": False})
    if sent_at:
        fields.append({"name": "📤 Sent At", "value": clean(sent_at), "inline": True})
    if reply_time:
        fields.append({"name": "📥 Replied", "value": clean(reply_time), "inline": True})
    
    # Discord field limit is exactly 1024. We must stay under it even with footnotes.
    has_attachment = len(reply_snippet) > 1000
    if has_attachment:
        preview = clean(reply_snippet[:900]) + "\n*(Full reply attached as message.txt)*"
        fields.append({"name": "💬 Reply Preview", "value": preview, "inline": False})
    else:
        fields.append({"name": "💬 Reply Content", "value": clean(reply_snippet), "inline": False})
    
    payload = {
        "embeds": [{
            "title": "📬 Reply Received!",
            "color": 0x00C896,
            "fields": fields,
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        }]
    }

    try:
        def _post_and_check(use_json=True):
            if use_json:
                r = requests.post(webhook_url, json=payload, timeout=15)
            else:
                # IMPORTANT: payload_json is a separate form field, NOT a file with None filename
                files = {"file": ("message.txt", reply_snippet.encode("utf-8", errors="replace"))}
                r = requests.post(webhook_url, data={"payload_json": json.dumps(payload)}, files=files, timeout=15)
            
            if r.status_code not in (200, 204):
                if log_fn:
                    log_fn(f"   ⚠️ Discord API rejection ({r.status_code}): {r.text}")
                    # Log total chars for debugging if character limits are suspected
                    total_chars = sum(len(f['name']) + len(f['value']) for f in fields)
                    log_fn(f"   (Internal Debug: Total embed chars: {total_chars})")
                return False
            return True

        if has_attachment:
            ok = _post_and_check(use_json=False)
            if not ok:
                # Fallback: Truncate further and send without file
                fields[-1]["value"] = clean(reply_snippet[:950]) + "\n*(Attachment failed)*"
                ok = _post_and_check(use_json=True)
            return ok
        else:
            return _post_and_check(use_json=True)

    except Exception as e:
        if log_fn:
            log_fn(f"   ⚠️ Webhook network error: {e}")
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
                        mid_str = mid.decode("utf-8") if isinstance(mid, bytes) else str(mid)
                        if is_reply_processed(account_key, mid_str):
                            continue
                        _, msg_data = imap.fetch(mid, "(RFC822)")
                        raw = msg_data[0][1]
                        msg = email.message_from_bytes(raw)
                        from_h = msg.get("From", "")
                        sender = extract_sender_email(from_h)
                        if not sender:
                            continue
                        reply_date = msg.get("Date") or datetime.utcnow().isoformat()
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
                            
                            # Safety cap for extremely large emails
                            if len(body_text) > 1_000_000:
                                body_text = body_text[:1_000_000] + "\n\n[... Message truncated due to size ...]"
                            
                            log_fn(f"📬 Reply from {sender} ({contact[0]}) [{location}] via {account_name} — alerting Discord!")
                            receiver = account_item.get("smtp_user") or account_name
                            ok = send_discord_alert(webhook_url, contact, body_text, location, reply_date, receiver, log_fn)
                            if ok:
                                mark_replied_everywhere(sender)
                                record_reply_processed(account_key, mid_str)
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
