import random
import time
from typing import Any, Dict, List, Optional


def personalize(template, name):
    first = name.split()[0] if name else "there"
    return template.replace("{name}", first).replace("{full_name}", name or "there")


def _build_sender_accounts(
    sender_accounts: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    from .config_service import get_default_email_account
    weighted_accounts: List[Dict[str, Any]] = []
    for raw in sender_accounts or []:
        try:
            weight = float(raw.get("weight", 0))
        except Exception:
            weight = 0.0
        if weight <= 0:
            continue
        weighted_accounts.append({
            "id": raw.get("id", ""),
            "label": raw.get("label") or raw.get("smtp_user") or "Email account",
            "weight": weight,
            "account": raw,
        })

    if weighted_accounts:
        return weighted_accounts

    default_account = get_default_email_account()
    if not default_account:
        return []
    return [{
        "id": default_account.get("id", ""),
        "label": default_account.get("label") or default_account.get("smtp_user") or "Email account",
        "weight": 100.0,
        "account": default_account,
    }]
