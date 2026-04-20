import logging
import re
import smtplib
import ssl
from typing import List, Optional, Tuple

import dns.resolver

from .email_service import probe_rcpt_via_configured_smtp

logger = logging.getLogger(__name__)

_VERIFY_EMAIL_SYNTAX_RE = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
    re.IGNORECASE,
)

def _verification_log(msg: str, log_fn) -> None:
    logger.info(msg)
    if log_fn:
        log_fn(msg)


def check_email_syntax(email: str) -> bool:
    s = (email or "").strip()
    if not s or " " in s or s.count("@") != 1:
        return False
    return _VERIFY_EMAIL_SYNTAX_RE.match(s) is not None


def list_mx_hosts(domain: str) -> List[str]:
    try:
        records = dns.resolver.resolve(domain, "MX")
        ordered = sorted(records, key=lambda r: (r.preference, str(r.exchange)))
        return [str(r.exchange).rstrip(".") for r in ordered]
    except Exception:
        return []


def _helo_domain(mail_from: str) -> str:
    return mail_from.rsplit("@", 1)[-1] if "@" in mail_from else "localhost"


def _rcpt_code_verdict(code: int) -> Optional[bool]:
    """True = accepted, False = permanent reject, None = inconclusive (e.g. greylist 4xx)."""
    if 200 <= code < 300:
        return True
    if 500 <= code < 600:
        return False
    return None


def _smtp_handshake_rcpt(
    smtp: smtplib.SMTP, email: str, mail_from: str
) -> Optional[bool]:
    helo = _helo_domain(mail_from)
    try:
        smtp.ehlo(helo)
    except smtplib.SMTPException:
        try:
            smtp.helo(helo)
        except smtplib.SMTPException:
            return None
    try:
        smtp.mail(mail_from)
    except smtplib.SMTPException:
        return None
    try:
        code, _ = smtp.rcpt(email)
        return _rcpt_code_verdict(code)
    except smtplib.SMTPRecipientsRefused:
        return False


def _mx_probe_attempt(email: str, mail_from: str, connect) -> Optional[bool]:
    smtp = None
    try:
        smtp = connect()
        return _smtp_handshake_rcpt(smtp, email, mail_from)
    except Exception:
        return None
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                try:
                    smtp.close()
                except Exception:
                    pass


def probe_rcpt_on_mx_multiport(
    email: str, mx_host: str, mail_from: str
) -> Optional[bool]:
    """
    Try several ports / TLS modes on the recipient MX (ISP often blocks only :25).
    True / False / None same as _smtp_handshake_rcpt; None if no strategy connected.
    """
    ctx = ssl.create_default_context()
    timeout = 12

    def plain25():
        s = smtplib.SMTP(timeout=timeout)
        s.connect(mx_host, 25)
        return s

    def starttls587():
        s = smtplib.SMTP(timeout=timeout)
        s.connect(mx_host, 587)
        s.ehlo()
        s.starttls(context=ctx)
        s.ehlo()
        return s

    def ssl465():
        return smtplib.SMTP_SSL(mx_host, 465, timeout=timeout, context=ctx)

    for label, factory in (
        ("25", plain25),
        ("587+STARTTLS", starttls587),
        ("465 SSL", ssl465),
    ):
        result = _mx_probe_attempt(email, mail_from, factory)
        if result is not None:
            return result
    return None


def verify_email(
    addr: str, log_fn=None, mail_from: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Pre-send: syntax, MX list, RCPT probe per MX (ports 25 / 587 / 465), then RCPT via
    your configured SMTP if direct MX is unreachable (e.g. outbound port 25 blocked).
    Inconclusive SMTP result is treated as valid (we still attempt delivery).
    Returns (ok_to_send, reason_for_failure_if_any).
    """
    email_raw = (addr or "").strip()
    _verification_log(f"📧 Verifying {email_raw!r} before send...", log_fn)

    if not check_email_syntax(email_raw):
        _verification_log("   ✗ Syntax check failed", log_fn)
        return False, "invalid email syntax"

    domain = email_raw.split("@", 1)[1]
    _verification_log(f"   Syntax OK — MX lookup for {domain!r}...", log_fn)
    mx_hosts = list_mx_hosts(domain)
    if not mx_hosts:
        _verification_log("   ✗ No MX records (or lookup failed)", log_fn)
        return False, "no MX records for recipient domain"

    from_addr = (mail_from or "").strip() or "verify@localhost"
    n = len(mx_hosts)
    for i, mx in enumerate(mx_hosts):
        _verification_log(
            f"   MX [{i + 1}/{n}] {mx} — RCPT probe (25 → 587+TLS → 465)...",
            log_fn,
        )
        smtp_result = probe_rcpt_on_mx_multiport(email_raw, mx, from_addr)
        if smtp_result is True:
            _verification_log("   ✓ SMTP RCPT accepted (2xx)", log_fn)
            return True, "ok"
        if smtp_result is False:
            _verification_log("   ✗ SMTP RCPT rejected — skipping send", log_fn)
            return False, "recipient rejected by recipient mail server (RCPT)"

    _verification_log(
        "   Direct MX not reachable or non-committal — RCPT via your SMTP (configured host)...",
        log_fn,
    )
    relay_result = probe_rcpt_via_configured_smtp(email_raw, from_addr, log_fn)
    if relay_result is True:
        _verification_log("   ✓ Relay SMTP accepted RCPT (2xx)", log_fn)
        return True, "ok"
    if relay_result is False:
        _verification_log("   ✗ Relay SMTP rejected RCPT — skipping send", log_fn)
        return False, "recipient rejected by your outbound mail server (RCPT)"

    _verification_log(
        "   ? Verification inconclusive — keeping recipient (will try outbound SMTP)",
        log_fn,
    )
    return True, "ok"
