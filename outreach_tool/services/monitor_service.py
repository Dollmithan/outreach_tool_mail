import email
import json
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

from .database import lookup_contact_master, mark_replied_everywhere, is_reply_processed, record_reply_processed
from .email_service import get_imap
from .phone_service import resolve_phone_location_label


def send_discord_alert(webhook_url, contact, reply_snippet, location: str, reply_time: str = None, receiver_email: str = None, log_fn=None):
    full_name, number, email_addr, sent_at = contact

    def clean(v, max_len=1024):
        s = str(v or "").strip()
        if len(s) > max_len:
            return s[:max_len - 5] + "..."
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
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }]
    }

    try:
        def _post_and_check(use_json=True):
            if use_json:
                r = requests.post(webhook_url, json=payload, timeout=15)
            else:
                files = {"file": ("message.txt", reply_snippet.encode("utf-8", errors="replace"))}
                r = requests.post(webhook_url, data={"payload_json": json.dumps(payload)}, files=files, timeout=15)
            if r.status_code not in (200, 204):
                if log_fn:
                    log_fn(f"   ⚠️ Discord API rejection ({r.status_code}): {r.text}")
                return False
            return True

        if has_attachment:
            ok = _post_and_check(use_json=False)
            if not ok:
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
