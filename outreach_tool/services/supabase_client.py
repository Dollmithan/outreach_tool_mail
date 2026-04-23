"""
Thin wrapper around the Supabase PostgREST API using `requests`.
No supabase-py dependency — keeps the requirement list minimal.
"""
import os
import requests as _req

_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
_KEY = os.environ.get("SUPABASE_KEY") or ""


def _headers(prefer: str = "") -> dict:
    h = {
        "apikey": _KEY,
        "Authorization": f"Bearer {_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _table(name: str) -> str:
    return f"{_URL}/rest/v1/{name}"


def _fmt(val) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def sb_select(table, filters=None, columns="*", order=None, limit=None, extra=None):
    """
    SELECT rows.
    filters: {col: val}          → col=eq.val
    extra:   {col: "neq.false"}  → raw PostgREST operator string
    """
    params = {"select": columns}
    for col, val in (filters or {}).items():
        params[col] = f"eq.{_fmt(val)}"
    for col, expr in (extra or {}).items():
        params[col] = expr
    if order:
        params["order"] = order
    if limit is not None:
        params["limit"] = limit
    r = _req.get(_table(table), headers=_headers(), params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def sb_count(table, filters=None, extra=None) -> int:
    params = {"select": "*", "limit": "0"}
    for col, val in (filters or {}).items():
        params[col] = f"eq.{_fmt(val)}"
    for col, expr in (extra or {}).items():
        params[col] = expr
    r = _req.get(_table(table), headers=_headers("count=exact"), params=params, timeout=20)
    r.raise_for_status()
    cr = r.headers.get("content-range", "")
    if not cr or "/" not in cr:
        return 0
    total = cr.split("/")[-1]
    return int(total) if total.isdigit() else 0


def sb_insert(table, data, upsert=False):
    """INSERT one row (dict) or many (list). Returns list of created rows."""
    prefer = "resolution=merge-duplicates,return=representation" if upsert else "return=representation"
    r = _req.post(_table(table), headers=_headers(prefer), json=data, timeout=30)
    r.raise_for_status()
    return r.json()


def sb_upsert(table, data):
    return sb_insert(table, data, upsert=True)


def sb_update(table, data, filters=None, extra=None):
    params = {}
    for col, val in (filters or {}).items():
        params[col] = f"eq.{_fmt(val)}"
    for col, expr in (extra or {}).items():
        params[col] = expr
    r = _req.patch(_table(table), headers=_headers("return=representation"),
                   json=data, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def sb_delete(table, filters=None, extra=None):
    params = {}
    for col, val in (filters or {}).items():
        params[col] = f"eq.{_fmt(val)}"
    for col, expr in (extra or {}).items():
        params[col] = expr
    if not params:
        return  # refuse to delete everything accidentally
    r = _req.delete(_table(table), headers=_headers(), params=params, timeout=20)
    r.raise_for_status()


def sb_batch_insert(table, rows, chunk=500):
    """Insert rows in chunks to stay within request-size limits."""
    for i in range(0, len(rows), chunk):
        sb_insert(table, rows[i:i + chunk])


def sb_batch_upsert(table, rows, chunk=500):
    """Upsert rows in chunks to stay within request-size limits."""
    for i in range(0, len(rows), chunk):
        sb_upsert(table, rows[i:i + chunk])
