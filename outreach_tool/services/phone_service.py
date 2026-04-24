import re
from typing import Optional

import requests

from .supabase_client import sb_select, sb_upsert


def _sanitize_phone_country_key(raw_number) -> str:
    return re.sub(r"\D+", "", str(raw_number or ""))


def _country_code_to_label(cc: str) -> str:
    cc = (cc or "").strip().upper()
    _MAP = {
        "GB": "England", "CA": "Canada", "US": "United States",
        "AU": "Australia", "FR": "France", "DE": "Germany",
        "ES": "Spain", "IT": "Italy", "NL": "Netherlands", "BE": "Belgium",
    }
    return _MAP.get(cc, cc or "Unknown")


def _build_phone_candidates(raw_number: str, digits_key: str):
    s = (raw_number or "").strip()
    s2 = re.sub(r"[^\d+]", "", s)
    if s2.startswith("00"):
        s2 = "+" + s2[2:]
    candidates = []
    if s2.startswith("+") and s2 not in candidates:
        candidates.append(s2)
    if digits_key:
        candidates.append("+" + digits_key)
        if len(digits_key) == 10:
            candidates.append("+1" + digits_key)
            candidates.append("+44" + digits_key)
        elif len(digits_key) == 9:
            candidates.append("+44" + digits_key)
    seen = set()
    out = []
    for c in candidates:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _call_phone_api(raw_number: str, digits_key: str) -> str:
    """Call libphonenumberapi.com and return the country code string, or empty string."""
    api_base = "https://libphonenumberapi.com/api/phone-numbers/"
    for cand in _build_phone_candidates(raw_number, digits_key)[:2]:
        try:
            url = api_base + requests.utils.quote(cand, safe="")
            r = requests.get(url, timeout=3)
            if r.status_code != 200:
                continue
            cc = (r.json().get("country") or "").strip()
            if cc:
                return cc
        except Exception:
            continue
    return ""


def resolve_phone_location_label(
    raw_number: str,
    *,
    use_persistent_cache: bool = True,
    session_cache: Optional[dict] = None,
) -> str:
    """
    Resolve a phone number to a country label.

    use_persistent_cache=True  → check Supabase cache first; write result back on miss
    use_persistent_cache=False → skip Supabase entirely (no read, no write)
    session_cache (dict)       → temporary in-call cache; populated/checked before Supabase
    """
    key = _sanitize_phone_country_key(raw_number)
    if not key:
        return "Unknown"

    # 1. Session cache (fastest — no I/O)
    if session_cache is not None and key in session_cache:
        return session_cache[key]

    # 2. Persistent Supabase cache
    if use_persistent_cache:
        try:
            rows = sb_select("phone_country_cache", filters={"number_sanitized": key})
            if rows and rows[0].get("country_label"):
                label = rows[0]["country_label"]
                if session_cache is not None:
                    session_cache[key] = label
                return label
        except Exception:
            pass

    # 3. External API call
    resolved_country = _call_phone_api(raw_number, key)
    label = _country_code_to_label(resolved_country)

    # 4. Write to persistent cache (only when enabled)
    if use_persistent_cache:
        try:
            sb_upsert("phone_country_cache", {
                "number_sanitized": key,
                "country_code": resolved_country or "",
                "country_label": label,
            })
        except Exception:
            pass

    # 5. Write to session cache
    if session_cache is not None:
        session_cache[key] = label

    return label
