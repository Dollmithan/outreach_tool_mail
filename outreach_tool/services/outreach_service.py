import os
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from .app_data import append_outreach_history_entry, load_outreach_history
from .config_service import get_default_email_account
from .database import (
    connect_master,
    ensure_contact_tracking_columns,
    get_import_id_for_working_path,
    init_master_schema,
    normalize_email,
)
from .email_service import send_email
from .phone_service import resolve_phone_location_label


def load_db_contacts(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    ensure_contact_tracking_columns(conn)
    cur.execute("SELECT full_name, number, email FROM contacts WHERE email IS NOT NULL AND trim(email) != ''")
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_sent(db_path, email_addr):
    """Update the working clone only (never the user's original file). Also sync master.leads."""
    db_path = os.path.abspath(db_path)
    em = normalize_email(email_addr)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    ensure_contact_tracking_columns(conn)
    cur.execute("UPDATE contacts SET sent=1 WHERE lower(trim(email))=?", (em,))
    conn.commit()
    conn.close()
    iid = get_import_id_for_working_path(db_path)
    if iid is None:
        return
    conn_m = connect_master()
    init_master_schema(conn_m)
    conn_m.execute(
        "UPDATE leads SET sent=1 WHERE import_id=? AND email=?",
        (iid, em),
    )
    conn_m.commit()
    conn_m.close()


def get_unsent(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    ensure_contact_tracking_columns(conn)
    cur.execute(
        "SELECT full_name, number, email FROM contacts WHERE email IS NOT NULL AND trim(email) != '' "
        "AND (sent IS NULL OR sent=0)"
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def personalize(template, name):
    first = name.split()[0] if name else "there"
    return template.replace("{name}", first).replace("{full_name}", name or "there")


def _build_sender_accounts(
    sender_accounts: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
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


def _append_history(db_path, name, number, email_addr, location, sender_info, sender_account):
    append_outreach_history_entry({
        "name": name or "",
        "email": email_addr,
        "number": number or "",
        "location": location,
        "sender_label": sender_info["label"],
        "sender_email": sender_account.get("smtp_user", ""),
        "working_path": db_path,
    })


def _run_sequential_outreach(
    contacts,
    db_path,
    subject,
    body_template,
    daily_limit,
    delay_min,
    delay_max,
    log_fn,
    stop_event,
    conn_m,
    sent_today,
    weighted_accounts,
    status_fn=None,
    history_update_fn=None,
):
    sender_sent_counts = {item["id"]: 0 for item in weighted_accounts}
    for name, number, email_addr in contacts:
        if stop_event.is_set():
            log_fn("Outreach stopped.")
            break
        if sent_today >= daily_limit:
            log_fn(f"Daily limit of {daily_limit} reached. Will continue tomorrow.")
            break

        sender_info = max(
            weighted_accounts,
            key=lambda item: (item["weight"] * (sent_today + 1)) - sender_sent_counts[item["id"]],
        )
        sender_account = sender_info["account"]
        subject_template = sender_account.get("outreach_subject") or subject
        body_template_for_account = sender_account.get("outreach_body") or body_template
        body = personalize(body_template_for_account, name or "")
        subj = personalize(subject_template, name or "")
        location = resolve_phone_location_label(conn_m, number)
        log_fn(
            f"Location: {location} | Sending to {email_addr} via "
            f"{sender_info['label']} ({sender_account.get('smtp_user', '')})"
        )
        if status_fn:
            try:
                status_fn(0)
            except Exception:
                pass

        ok = send_email(email_addr, subj, body, log_fn=log_fn, account=sender_account)
        if ok:
            mark_sent(db_path, email_addr)
            sent_today += 1
            sender_sent_counts[sender_info["id"]] += 1
            _append_history(db_path, name, number, email_addr, location, sender_info, sender_account)
            if history_update_fn:
                try:
                    history_update_fn()
                except Exception:
                    pass
            log_fn(f"Sent to {email_addr} ({location})")
        else:
            log_fn(f"Failed to {email_addr} ({location})")

        wait = random.randint(delay_min, delay_max)
        log_fn(f"Next email in {wait}s... ({sent_today}/{daily_limit} today)")
        for remaining in range(wait, 0, -1):
            if stop_event.is_set():
                break
            if status_fn:
                try:
                    status_fn(remaining)
                except Exception:
                    pass
            time.sleep(1)
    return sent_today


def _run_parallel_outreach(
    contacts,
    db_path,
    subject,
    body_template,
    daily_limit,
    delay_min,
    delay_max,
    log_fn,
    stop_event,
    weighted_accounts,
    sent_today_start,
    history_update_fn=None,
):
    if not contacts:
        return sent_today_start

    contacts_lock = threading.Lock()
    state_lock = threading.Lock()
    next_contact_idx = 0
    sent_today = sent_today_start
    in_flight = 0

    def worker(sender_info):
        nonlocal next_contact_idx, sent_today, in_flight
        sender_account = sender_info["account"]
        conn_local = connect_master()
        init_master_schema(conn_local)
        try:
            while not stop_event.is_set():
                with state_lock:
                    if (sent_today + in_flight) >= daily_limit:
                        break
                    in_flight += 1
                with contacts_lock:
                    if next_contact_idx >= len(contacts):
                        with state_lock:
                            in_flight -= 1
                        break
                    name, number, email_addr = contacts[next_contact_idx]
                    next_contact_idx += 1

                try:
                    subject_template = sender_account.get("outreach_subject") or subject
                    body_template_for_account = sender_account.get("outreach_body") or body_template
                    body = personalize(body_template_for_account, name or "")
                    subj = personalize(subject_template, name or "")
                    location = resolve_phone_location_label(conn_local, number)
                    log_fn(
                        f"Location: {location} | Sending to {email_addr} via "
                        f"{sender_info['label']} ({sender_account.get('smtp_user', '')})"
                    )

                    ok = send_email(email_addr, subj, body, log_fn=log_fn, account=sender_account)
                    if ok:
                        mark_sent(db_path, email_addr)
                        with state_lock:
                            sent_today += 1
                            sent_now = sent_today
                        _append_history(db_path, name, number, email_addr, location, sender_info, sender_account)
                        if history_update_fn:
                            try:
                                history_update_fn()
                            except Exception:
                                pass
                        log_fn(f"Sent to {email_addr} ({location}) via {sender_info['label']}")
                        if sent_now >= daily_limit:
                            log_fn(f"Daily limit of {daily_limit} reached. Stopping active senders.")
                            stop_event.set()
                            break
                    else:
                        log_fn(f"Failed to {email_addr} ({location}) via {sender_info['label']}")
                finally:
                    with state_lock:
                        in_flight -= 1

                wait = random.randint(delay_min, delay_max)
                with state_lock:
                    sent_snapshot = min(sent_today, daily_limit)
                log_fn(f"{sender_info['label']} cooldown: {wait}s ({sent_snapshot}/{daily_limit} today)")
                for _ in range(wait):
                    if stop_event.is_set():
                        break
                    time.sleep(1)
        finally:
            try:
                conn_local.close()
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=max(1, len(weighted_accounts))) as executor:
        futures = [executor.submit(worker, sender_info) for sender_info in weighted_accounts]
        for future in futures:
            future.result()
    return sent_today


def run_outreach(
    db_path,
    subject,
    body_template,
    daily_limit,
    delay_min,
    delay_max,
    log_fn,
    stop_event,
    status_fn=None,
    sender_accounts: Optional[List[Dict[str, Any]]] = None,
    history_update_fn=None,
    send_simultaneously: bool = False,
):
    contacts = get_unsent(db_path)
    log_fn(f"Unsent contacts found: {len(contacts)}")

    weighted_accounts = _build_sender_accounts(sender_accounts)
    if not weighted_accounts:
        log_fn("No email account is configured for outreach.")
        return

    conn_m = connect_master()
    init_master_schema(conn_m)
    history = load_outreach_history()
    sent_today = history.get("total_sent", 0)
    if sent_today:
        log_fn(f"Persisted outreach history found for today: {sent_today} already sent.")
    if sent_today >= daily_limit:
        log_fn(f"Daily limit of {daily_limit} already reached for today. Reset history to start over.")
        if history_update_fn:
            try:
                history_update_fn()
            except Exception:
                pass
        try:
            conn_m.close()
        except Exception:
            pass
        return

    if send_simultaneously and len(weighted_accounts) > 1:
        log_fn(
            f"Simultaneous mailbox mode enabled. {len(weighted_accounts)} mailboxes will send in parallel with independent cooldowns."
        )
        try:
            conn_m.close()
        except Exception:
            pass
        sent_today = _run_parallel_outreach(
            contacts,
            db_path,
            subject,
            body_template,
            daily_limit,
            delay_min,
            delay_max,
            log_fn,
            stop_event,
            weighted_accounts,
            sent_today_start=sent_today,
            history_update_fn=history_update_fn,
        )
    else:
        sent_today = _run_sequential_outreach(
            contacts,
            db_path,
            subject,
            body_template,
            daily_limit,
            delay_min,
            delay_max,
            log_fn,
            stop_event,
            conn_m,
            sent_today,
            weighted_accounts,
            status_fn=status_fn,
            history_update_fn=history_update_fn,
        )
        try:
            conn_m.close()
        except Exception:
            pass

    log_fn(f"Outreach session done. Sent {sent_today} emails today.")
