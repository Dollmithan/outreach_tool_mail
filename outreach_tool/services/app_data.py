import json
import os
import threading
from datetime import datetime

from outreach_tool.constants import APP_DATA_NAME, APP_DIR, CONFIG_FILE_NAME

_APP_DATA_DIR = None
_OUTREACH_HISTORY_LOCK = threading.Lock()


def _resolve_app_data_dir():
    """
    Pick a writable app data location.
    1) Prefer project-local folder for portability.
    2) Fallback to %LOCALAPPDATA% on Windows if project folder is not writable.
    """
    local_candidate = os.path.join(APP_DIR, "app_data")
    fallback_base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    fallback_candidate = os.path.join(fallback_base, APP_DATA_NAME)
    for candidate in (local_candidate, fallback_candidate):
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, ".write_test")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return candidate
        except Exception:
            continue
    raise PermissionError(
        f"Cannot write app data in either '{local_candidate}' or '{fallback_candidate}'."
    )


def get_app_data_dir():
    global _APP_DATA_DIR
    if _APP_DATA_DIR:
        return _APP_DATA_DIR
    _APP_DATA_DIR = _resolve_app_data_dir()
    return _APP_DATA_DIR


def get_config_file_path():
    return os.path.join(get_app_data_dir(), CONFIG_FILE_NAME)


def _ensure_app_dirs():
    os.makedirs(os.path.join(get_app_data_dir(), "working_copies"), exist_ok=True)


def get_master_db_path():
    _ensure_app_dirs()
    return os.path.join(get_app_data_dir(), "tool_leads.db")


def get_outreach_history_path():
    _ensure_app_dirs()
    return os.path.join(get_app_data_dir(), "outreach_history.json")


def _today_key():
    return datetime.now().strftime("%Y-%m-%d")


def _empty_outreach_history(day_key=None):
    return {
        "date": day_key or _today_key(),
        "total_sent": 0,
        "entries": [],
    }


def _normalize_day_history(day_key, data):
    if not isinstance(data, dict):
        return _empty_outreach_history(day_key)
    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = []
    total_sent = data.get("total_sent", len(entries))
    try:
        total_sent = int(total_sent)
    except Exception:
        total_sent = len(entries)
    return {
        "date": day_key,
        "total_sent": max(total_sent, 0),
        "entries": [entry for entry in entries if isinstance(entry, dict)],
    }


def _read_outreach_history_store():
    path = get_outreach_history_path()
    if not os.path.exists(path):
        return {"days": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {"days": {}}

    if not isinstance(raw, dict):
        return {"days": {}}

    if "days" in raw and isinstance(raw["days"], dict):
        days = {}
        for day_key, day_data in raw["days"].items():
            days[str(day_key)] = _normalize_day_history(str(day_key), day_data)
        return {"days": days}

    legacy_day = str(raw.get("date") or _today_key())
    return {"days": {legacy_day: _normalize_day_history(legacy_day, raw)}}


def _write_outreach_history_store(store):
    payload = {"days": {}}
    if isinstance(store, dict):
        for day_key, day_data in (store.get("days") or {}).items():
            key = str(day_key)
            payload["days"][key] = _normalize_day_history(key, day_data)
    with open(get_outreach_history_path(), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return payload


def load_outreach_history(day_key=None):
    key = str(day_key or _today_key())
    with _OUTREACH_HISTORY_LOCK:
        store = _read_outreach_history_store()
    return _normalize_day_history(key, store["days"].get(key))


def list_outreach_history_days():
    with _OUTREACH_HISTORY_LOCK:
        store = _read_outreach_history_store()
    items = []
    for day_key in sorted(store["days"].keys(), reverse=True):
        items.append(_normalize_day_history(day_key, store["days"].get(day_key)))
    return items


def save_outreach_history(history, day_key=None):
    key = str(day_key or (history.get("date") if isinstance(history, dict) else None) or _today_key())
    with _OUTREACH_HISTORY_LOCK:
        store = _read_outreach_history_store()
        store["days"][key] = _normalize_day_history(key, history)
        _write_outreach_history_store(store)
        return store["days"][key]


def append_outreach_history_entry(entry, day_key=None):
    key = str(day_key or _today_key())
    with _OUTREACH_HISTORY_LOCK:
        store = _read_outreach_history_store()
        history = _normalize_day_history(key, store["days"].get(key))
        item = dict(entry or {})
        item.setdefault("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        history["entries"].append(item)
        history["total_sent"] = len(history["entries"])
        store["days"][key] = history
        _write_outreach_history_store(store)
        return store["days"][key]


def reset_outreach_history(day_key=None):
    key = str(day_key or _today_key())
    return save_outreach_history(_empty_outreach_history(key), day_key=key)
