import email
import imaplib
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Optional

from .config_service import cfg, get_default_email_account

def get_smtp(account: Optional[Dict[str, str]] = None):
    account = account or get_default_email_account() or {}
    host = (account.get("smtp_host") or cfg("SMTP", "host", "smtp.alexhost.com")).strip()
    port = int((account.get("smtp_port") or cfg("SMTP", "port", "465")).strip() or "465")
    return smtplib.SMTP_SSL(host, port)


def probe_rcpt_via_configured_smtp(
    email: str, mail_from: str, log_fn=None, account: Optional[Dict[str, str]] = None
) -> Optional[bool]:
    """
    If direct MX RCPT fails (common when outbound TCP/25 is blocked), run the same
    MAIL/RCPT sequence on the configured outbound SMTP (e.g. 465 SSL) after AUTH.
    """
    from .email_verification import _smtp_handshake_rcpt, _verification_log

    account = account or get_default_email_account() or {}
    user = (account.get("smtp_user") or cfg("SMTP", "user") or "").strip()
    password = account.get("smtp_password") or cfg("SMTP", "password") or ""
    if not user or not password:
        _verification_log(
            "   ⚠️ SMTP credentials incomplete — skip relay RCPT fallback",
            log_fn,
        )
        return None
    try:
        with get_smtp(account) as s:
            s.login(user, password)
            return _smtp_handshake_rcpt(s, email, mail_from)
    except Exception as ex:
        _verification_log(f"   ⚠️ Relay RCPT fallback error: {ex}", log_fn)
        return None


def get_imap(account: Optional[Dict[str, str]] = None):
    account = account or get_default_email_account() or {}
    host = (account.get("imap_host") or cfg("IMAP", "host", "imap.alexhost.com")).strip()
    port = int((account.get("imap_port") or cfg("IMAP", "port", "993")).strip() or "993")
    user = account.get("smtp_user") or cfg("SMTP", "user")
    password = account.get("smtp_password") or cfg("SMTP", "password")
    m = imaplib.IMAP4_SSL(host, port)
    m.login(user, password)
    return m

def _imap_append_to_sent(raw_msg: bytes, account: Optional[Dict[str, str]] = None):
    """
    Best-effort: append a copy to a Sent folder so you can verify the message left.
    Not all servers expose the same folder names; we try common ones.
    """
    folders = ["Sent", "Sent Items", "INBOX.Sent", "INBOX.Sent Items"]
    imap = get_imap(account)
    try:
        # Discover available mailboxes (optional, but helps)
        try:
            _, data = imap.list()
            if data:
                names = []
                for line in data:
                    if not line:
                        continue
                    s = line.decode(errors="ignore")
                    # mailbox name is after last quote or last space; keep it simple
                    m = re.search(r'"([^"]+)"\s*$', s)
                    if m:
                        names.append(m.group(1))
                    else:
                        parts = s.split()
                        if parts:
                            names.append(parts[-1])
                # Prefer any mailbox that contains "sent"
                sent_like = [n for n in names if "sent" in (n or "").lower()]
                folders = sent_like + folders
        except Exception:
            pass

        for f in folders:
            if not f:
                continue
            try:
                imap.append(f, None, None, raw_msg)
                return True, f
            except Exception:
                continue
        return False, "no sent folder worked"
    finally:
        try:
            imap.logout()
        except Exception:
            pass

def send_email(to_email, subject, body, log_fn=None, account: Optional[Dict[str, str]] = None):
    from .email_verification import check_email_syntax

    account = account or get_default_email_account() or {}
    user = (account.get("smtp_user") or cfg("SMTP","user")).strip()
    password = account.get("smtp_password") or cfg("SMTP","password")
    display_name = account.get("display_name") or cfg("SMTP","display_name",user)
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{display_name} <{user}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Message-ID"] = email.utils.make_msgid()
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.attach(MIMEText(body, "plain"))

    if not check_email_syntax(to_email):
        if log_fn:
            log_fn(f"❌ Failed {to_email}: invalid email syntax")
        return False

    try:
        with get_smtp(account) as s:
            s.login(user, password)
            refused = s.sendmail(user, [to_email], msg.as_string())
        if refused:
            # refused is a dict: {recipient: (code, message)}
            try:
                code, msg_txt = list(refused.values())[0]
                reason = f"SMTP refused recipient ({code}): {msg_txt}"
            except Exception:
                reason = f"SMTP refused recipient: {refused}"
            if log_fn:
                log_fn(f"❌ Failed {to_email}: {reason}")
            return False
        if log_fn:
            log_fn(f"✅ Sent to {to_email} (accepted by SMTP) — msgid {msg['Message-ID']}")
        # Best-effort: save a copy to Sent (helps debug “accepted but not received”)
        try:
            ok_sent, sent_folder = _imap_append_to_sent(msg.as_bytes(), account=account)
            if log_fn:
                if ok_sent:
                    log_fn(f"   📁 Saved a copy to IMAP folder: {sent_folder}")
                else:
                    log_fn(f"   ⚠️ Could not save to Sent: {sent_folder}")
        except Exception as ex:
            if log_fn:
                log_fn(f"   ⚠️ Could not save to Sent: {ex}")
        return True
    except Exception as e:
        if log_fn: log_fn(f"❌ Failed {to_email}: {e}")
        return False
