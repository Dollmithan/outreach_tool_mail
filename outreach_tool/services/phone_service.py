import re
from datetime import datetime
from threading import Lock
from typing import Optional

import phonenumbers
import requests
from phonenumbers import NumberParseException, PhoneNumberType
from phonenumbers.geocoder import description_for_number

_phone_geo_cache_lock = Lock()
_phone_geo_cache = {}

def _sanitize_phone_country_key(raw_number) -> str:
    """Cache key: digits only (no '+' / dashes / spaces)."""
    return re.sub(r"\D+", "", str(raw_number or ""))

def _country_code_to_label(cc: str) -> str:
    cc = (cc or "").strip().upper()
    if cc == "GB":
        return "England"
    if cc == "CA":
        return "Canada"
    if cc == "US":
        return "United States"
    if cc == "AU":
        return "Australia"
    if cc == "FR":
        return "France"
    if cc == "DE":
        return "Germany"
    if cc == "ES":
        return "Spain"
    if cc == "IT":
        return "Italy"
    if cc == "NL":
        return "Netherlands"
    if cc == "BE":
        return "Belgium"
    return cc or "Unknown"

def _build_phone_candidates_for_api(raw_number: str, digits_key: str):
    """
    Build candidate phone strings for libphonenumberapi.com.
    We try explicit '+' first, then common country-prefix variants for national numbers.
    """
    s = (raw_number or "").strip()
    if not s and not digits_key:
        return []
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

    # Deduplicate preserving order
    seen = set()
    out = []
    for c in candidates:
        c = c.strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out

def resolve_phone_location_label(conn_m, raw_number: str) -> str:
    """
    Resolve phone -> country label using the persistent cache.
    If not cached, call libphonenumberapi.com once and store the result.
    """
    key = _sanitize_phone_country_key(raw_number)
    if not key:
        return "Unknown"

    try:
        row = conn_m.execute(
            "SELECT country_label FROM phone_country_cache WHERE number_sanitized=?",
            (key,),
        ).fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass

    api_base = "https://libphonenumberapi.com/api/phone-numbers/"
    resolved_country = ""
    candidates = _build_phone_candidates_for_api(raw_number, key)

    # Try candidates until one returns a country code.
    for cand in candidates[:6]:
        try:
            url = api_base + requests.utils.quote(cand, safe="")
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            cc = (data.get("country") or "").strip()
            if cc:
                resolved_country = cc
                break
        except Exception:
            continue

    label = _country_code_to_label(resolved_country)
    # Cache even unknown to avoid repeated external calls for the same key.
    try:
        conn_m.execute(
            "INSERT OR REPLACE INTO phone_country_cache (number_sanitized, country_code, country_label, checked_at) VALUES (?,?,?,?)",
            (key, resolved_country or "", label, datetime.utcnow().isoformat()),
        )
        conn_m.commit()
    except Exception:
        try:
            conn_m.rollback()
        except Exception:
            pass
    return label
