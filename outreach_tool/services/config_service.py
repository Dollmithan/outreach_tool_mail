import configparser
import os
import uuid
from typing import Any, Dict, List, Optional

from outreach_tool.constants import EMAIL_ACCOUNT_SECTION_PREFIX
from .app_data import get_config_file_path

def load_config():
    # interpolation=None so email templates can contain "%" without errors
    c = configparser.ConfigParser(interpolation=None)
    config_path = get_config_file_path()
    if os.path.exists(config_path):
        c.read(config_path, encoding="utf-8")
    return c

def save_config(data: dict):
    c = load_config()
    for section, values in data.items():
        if not c.has_section(section):
            c.add_section(section)
        for k, v in values.items():
            c.set(section, k, str(v))
    config_path = get_config_file_path()
    tmp_path = config_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        c.write(f)
    if os.path.exists(config_path):
        os.remove(config_path)
    os.rename(tmp_path, config_path)

def cfg(section, key, fallback=""):
    c = load_config()
    if not c.has_section(section):
        return fallback
    return c.get(section, key, fallback=fallback)


def _email_account_section(account_id: str) -> str:
    return f"{EMAIL_ACCOUNT_SECTION_PREFIX}{(account_id or '').strip()}"


def _email_account_from_section(
    c: configparser.ConfigParser, section: str, account_id: str
) -> Dict[str, str]:
    def _get(key: str, fallback: str = "") -> str:
        return c.get(section, key, fallback=fallback).strip()

    smtp_user = _get("smtp_user")
    label = _get("label") or smtp_user or "Email account"
    return {
        "id": account_id,
        "label": label,
        "smtp_host": _get("smtp_host", "smtp.alexhost.com"),
        "smtp_port": _get("smtp_port", "465"),
        "smtp_user": smtp_user,
        "smtp_password": _get("smtp_password"),
        "display_name": _get("display_name"),
        "imap_host": _get("imap_host", "imap.alexhost.com"),
        "imap_port": _get("imap_port", "993"),
        "outreach_subject": _get("outreach_subject"),
        "outreach_body": _get("outreach_body"),
    }


def get_email_accounts(c: Optional[configparser.ConfigParser] = None) -> List[Dict[str, str]]:
    c = c or load_config()
    accounts: List[Dict[str, str]] = []
    ordered_sections = sorted(
        [sec for sec in c.sections() if sec.startswith(EMAIL_ACCOUNT_SECTION_PREFIX)]
    )
    for section in ordered_sections:
        account_id = section[len(EMAIL_ACCOUNT_SECTION_PREFIX):].strip()
        if not account_id:
            continue
        accounts.append(_email_account_from_section(c, section, account_id))

    if accounts:
        return accounts

    # Backward compatibility with the old single-account layout.
    smtp_user = c.get("SMTP", "user", fallback="").strip()
    smtp_password = c.get("SMTP", "password", fallback="").strip()
    smtp_host = c.get("SMTP", "host", fallback="smtp.alexhost.com").strip() or "smtp.alexhost.com"
    smtp_port = c.get("SMTP", "port", fallback="465").strip() or "465"
    display_name = c.get("SMTP", "display_name", fallback="").strip()
    imap_host = c.get("IMAP", "host", fallback="imap.alexhost.com").strip() or "imap.alexhost.com"
    imap_port = c.get("IMAP", "port", fallback="993").strip() or "993"
    if any([smtp_user, smtp_password, display_name, smtp_host, imap_host]):
        return [{
            "id": "default",
            "label": smtp_user or "Default account",
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_user": smtp_user,
            "smtp_password": smtp_password,
            "display_name": display_name,
            "imap_host": imap_host,
            "imap_port": imap_port,
            "outreach_subject": "",
            "outreach_body": "",
        }]
    return []


def get_default_email_account(
    c: Optional[configparser.ConfigParser] = None,
    account_id: str = "",
) -> Optional[Dict[str, str]]:
    c = c or load_config()
    accounts = get_email_accounts(c)
    if not accounts:
        return None
    wanted = (account_id or c.get("EMAIL_ACCOUNTS", "default_account_id", fallback="")).strip()
    for account in accounts:
        if account["id"] == wanted:
            return account
    return accounts[0]


def save_email_accounts(accounts: List[Dict[str, Any]], default_account_id: str = "") -> None:
    c = load_config()
    for section in list(c.sections()):
        if section.startswith(EMAIL_ACCOUNT_SECTION_PREFIX):
            c.remove_section(section)

    if not c.has_section("EMAIL_ACCOUNTS"):
        c.add_section("EMAIL_ACCOUNTS")

    normalized_accounts: List[Dict[str, str]] = []
    for raw in accounts:
        account_id = (raw.get("id") or uuid.uuid4().hex[:12]).strip()
        section = _email_account_section(account_id)
        if not c.has_section(section):
            c.add_section(section)
        account = {
            "id": account_id,
            "label": str(raw.get("label", "")).strip(),
            "smtp_host": str(raw.get("smtp_host", "smtp.alexhost.com")).strip() or "smtp.alexhost.com",
            "smtp_port": str(raw.get("smtp_port", "465")).strip() or "465",
            "smtp_user": str(raw.get("smtp_user", "")).strip(),
            "smtp_password": str(raw.get("smtp_password", "")).strip(),
            "display_name": str(raw.get("display_name", "")).strip(),
            "imap_host": str(raw.get("imap_host", "imap.alexhost.com")).strip() or "imap.alexhost.com",
            "imap_port": str(raw.get("imap_port", "993")).strip() or "993",
            "outreach_subject": str(raw.get("outreach_subject", "")).strip(),
            "outreach_body": str(raw.get("outreach_body", "")).rstrip("\n"),
        }
        if not account["label"]:
            account["label"] = account["smtp_user"] or f"Account {len(normalized_accounts) + 1}"
        for key in (
            "label",
            "smtp_host",
            "smtp_port",
            "smtp_user",
            "smtp_password",
            "display_name",
            "imap_host",
            "imap_port",
            "outreach_subject",
            "outreach_body",
        ):
            c.set(section, key, account[key])
        normalized_accounts.append(account)

    default_id = (default_account_id or "").strip()
    if not default_id and normalized_accounts:
        default_id = normalized_accounts[0]["id"]
    c.set("EMAIL_ACCOUNTS", "default_account_id", default_id)

    if not c.has_section("SMTP"):
        c.add_section("SMTP")
    if not c.has_section("IMAP"):
        c.add_section("IMAP")
    primary = get_default_email_account(c, default_id)
    if primary:
        c.set("SMTP", "host", primary["smtp_host"])
        c.set("SMTP", "port", primary["smtp_port"])
        c.set("SMTP", "user", primary["smtp_user"])
        c.set("SMTP", "password", primary["smtp_password"])
        c.set("SMTP", "display_name", primary["display_name"])
        c.set("IMAP", "host", primary["imap_host"])
        c.set("IMAP", "port", primary["imap_port"])

    config_path = get_config_file_path()
    tmp_path = config_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        c.write(f)
    if os.path.exists(config_path):
        os.remove(config_path)
    os.rename(tmp_path, config_path)
