import os
import tempfile
from datetime import datetime

from .supabase_client import sb_delete, sb_insert, sb_select

# Keep a writable temp dir for file uploads (server-side only, not persisted)
_TEMP_DIR = None


def _get_temp_dir() -> str:
    global _TEMP_DIR
    if _TEMP_DIR:
        return _TEMP_DIR
    base = tempfile.gettempdir()
    candidate = os.path.join(base, "outreach_tool_uploads")
    os.makedirs(candidate, exist_ok=True)
    _TEMP_DIR = candidate
    return _TEMP_DIR


def _ensure_app_dirs():
    _get_temp_dir()


def get_app_data_dir() -> str:
    return _get_temp_dir()


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_outreach_history(day_key=None) -> dict:
    key = str(day_key or _today_key())
    try:
        rows = sb_select("outreach_history", filters={"date": key}, order="ts.asc")
    except Exception:
        rows = []
    return {
        "date": key,
        "total_sent": len(rows),
        "entries": [
            {
                "name": r.get("name", ""),
                "email": r.get("email", ""),
                "number": r.get("number", ""),
                "location": r.get("location", ""),
                "sender_label": r.get("sender_label", ""),
                "sender_email": r.get("sender_email", ""),
                "timestamp": (r.get("ts") or "")[:19].replace("T", " "),
            }
            for r in rows
        ],
    }


def list_outreach_history_days() -> list:
    try:
        rows = sb_select("outreach_history", columns="date", order="date.desc")
    except Exception:
        rows = []
    dates_seen = []
    dates_set: set = set()
    for r in rows:
        d = r.get("date", "")
        if d and d not in dates_set:
            dates_set.add(d)
            dates_seen.append(d)
    result = []
    for d in dates_seen[:3]:
        result.append(load_outreach_history(d))
    return result


def append_outreach_history_entry(entry: dict, day_key=None) -> dict:
    key = str(day_key or _today_key())
    try:
        sb_insert("outreach_history", {
            "date": key,
            "name": entry.get("name", ""),
            "email": entry.get("email", ""),
            "number": entry.get("number", ""),
            "location": entry.get("location", ""),
            "sender_label": entry.get("sender_label", ""),
            "sender_email": entry.get("sender_email", ""),
        })
    except Exception:
        pass
    return load_outreach_history(key)


def save_outreach_history(history: dict, day_key=None) -> dict:
    key = str(day_key or (history.get("date") if isinstance(history, dict) else None) or _today_key())
    return load_outreach_history(key)


def reset_outreach_history(day_key=None) -> dict:
    key = str(day_key or _today_key())
    try:
        sb_delete("outreach_history", filters={"date": key})
    except Exception:
        pass
    return {"date": key, "total_sent": 0, "entries": []}
