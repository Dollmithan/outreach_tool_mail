import uuid
from typing import Any, Dict, List, Optional

from .supabase_client import sb_delete, sb_select, sb_upsert


class _SupabaseConfig:
    """Dict-like config wrapper for compatibility with existing code."""

    def __init__(self, data: dict):
        self._data = data

    def has_section(self, section: str) -> bool:
        return section.upper() in self._data

    def get(self, section: str, key: str, fallback: str = "") -> str:
        return self._data.get(section.upper(), {}).get(key, fallback)


def load_config() -> _SupabaseConfig:
    """Load all app_config rows from Supabase into a dict-like wrapper."""
    try:
        rows = sb_select("app_config")
    except Exception:
        rows = []
    data: dict = {}
    for row in rows:
        key_str = row.get("key", "")
        val = row.get("value", "")
        if "." in key_str:
            section, k = key_str.split(".", 1)
            section = section.upper()
            if section not in data:
                data[section] = {}
            data[section][k] = val
    return _SupabaseConfig(data)


def save_config(data: dict) -> None:
    """Upsert key/value pairs into app_config. data = {SECTION: {key: value}}."""
    for section, values in data.items():
        for k, v in values.items():
            sb_upsert("app_config", {"key": f"{section.upper()}.{k}", "value": str(v)})


def cfg(section: str, key: str, fallback: str = "") -> str:
    """Fetch a single config value from Supabase."""
    try:
        rows = sb_select("app_config", filters={"key": f"{section.upper()}.{key}"})
        return rows[0]["value"] if rows else fallback
    except Exception:
        return fallback


def _normalize_account(raw: dict) -> Dict[str, str]:
    return {
        "id": raw.get("id", ""),
        "label": raw.get("label") or raw.get("smtp_user") or "Email account",
        "smtp_host": raw.get("smtp_host") or "smtp.alexhost.com",
        "smtp_port": str(raw.get("smtp_port") or "465"),
        "smtp_user": raw.get("smtp_user", ""),
        "smtp_password": raw.get("smtp_password", ""),
        "display_name": raw.get("display_name", ""),
        "imap_host": raw.get("imap_host") or "imap.alexhost.com",
        "imap_port": str(raw.get("imap_port") or "993"),
        "outreach_subject": raw.get("outreach_subject", ""),
        "outreach_body": raw.get("outreach_body", ""),
    }


def get_email_accounts(c=None) -> List[Dict[str, str]]:
    try:
        rows = sb_select("email_accounts", order="sort_order.asc")
        return [_normalize_account(r) for r in rows]
    except Exception:
        return []


def get_default_email_account(c=None, account_id: str = "") -> Optional[Dict[str, str]]:
    accounts = get_email_accounts()
    if not accounts:
        return None
    if account_id:
        for a in accounts:
            if a["id"] == account_id:
                return a
    return accounts[0]


def save_email_accounts(accounts: List[Dict[str, Any]], default_account_id: str = "") -> None:
    """Upsert all accounts by id; delete ones not in the new list."""
    try:
        existing_rows = sb_select("email_accounts", columns="id")
        existing_ids = {r["id"] for r in existing_rows}
    except Exception:
        existing_ids = set()

    new_ids = {str(a.get("id") or "") for a in accounts if a.get("id")}

    for old_id in existing_ids - new_ids:
        try:
            sb_delete("email_accounts", filters={"id": old_id})
        except Exception:
            pass

    for i, raw in enumerate(accounts):
        acct_id = str(raw.get("id") or "").strip() or uuid.uuid4().hex[:12]
        row = {
            "id": acct_id,
            "label": str(raw.get("label", "")).strip() or str(raw.get("smtp_user", "")).strip() or f"Account {i + 1}",
            "smtp_host": str(raw.get("smtp_host", "smtp.alexhost.com")).strip() or "smtp.alexhost.com",
            "smtp_port": str(raw.get("smtp_port", "465")).strip() or "465",
            "smtp_user": str(raw.get("smtp_user", "")).strip(),
            "smtp_password": str(raw.get("smtp_password", "")).strip(),
            "display_name": str(raw.get("display_name", "")).strip(),
            "imap_host": str(raw.get("imap_host", "imap.alexhost.com")).strip() or "imap.alexhost.com",
            "imap_port": str(raw.get("imap_port", "993")).strip() or "993",
            "outreach_subject": str(raw.get("outreach_subject", "")).strip(),
            "outreach_body": str(raw.get("outreach_body", "")).rstrip("\n"),
            "weight": int(raw.get("weight") or 0),
            "sort_order": i,
        }
        sb_upsert("email_accounts", row)
