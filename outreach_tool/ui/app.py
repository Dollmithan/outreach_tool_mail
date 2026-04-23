import json
import logging
import os
import re
import sqlite3
import threading
import tkinter as tk
import uuid
import configparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any, Dict, List, Optional

import phonenumbers
import requests
from phonenumbers import NumberParseException, PhoneNumberType
from phonenumbers.geocoder import description_for_number

from ..services.app_data import _ensure_app_dirs, list_outreach_history_days, load_outreach_history, reset_outreach_history
from ..services.config_service import cfg, get_default_email_account, get_email_accounts, load_config, save_config, save_email_accounts
from ..services.database import (
    add_lead_to_import,
    connect_master,
    get_master_db_path,
    get_stats_for_import,
    get_working_path_for_import,
    import_excel_as_leads,
    import_user_database,
    init_master_schema,
    list_imports,
    remove_import,
    resync_import_from_working,
)
from ..services.outreach_service import run_outreach
from ..services.phone_service import _build_phone_candidates_for_api, _country_code_to_label, _sanitize_phone_country_key
from ..services.monitor_service import run_reply_monitor
from ..services.warmup_service import run_warmup

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OutreachPro — Scam Recovery Mailer")
        self.geometry("960x720")
        self.resizable(True, True)
        self.configure(bg="#0D0D0D")
        self._setup_styles()

        self.stop_warmup = threading.Event()
        self.stop_outreach = threading.Event()
        self.stop_monitor = threading.Event()
        self.seen_ids = set()
        self._email_accounts: List[Dict[str, str]] = []
        self._sender_share_vars: Dict[str, tk.StringVar] = {}
        self._monitor_account_vars: Dict[str, tk.BooleanVar] = {}
        self._selected_email_account_id = ""
        self._selected_outreach_template_account_id = ""
        self._outreach_paths = []  # parallel to outreach combobox labels
        self._outreach_busy = False
        self._outreach_thread = None
        self._monitor_busy = False
        self._monitor_thread = None
        self._db_loc_refresh_busy = False
        self._db_loc_refresh_thread = None

        _ensure_app_dirs()
        mc = connect_master()
        init_master_schema(mc)
        mc.close()

        self._build_ui()
        self._load_saved_config()
        self._refresh_import_lists(select_working=cfg("OUTREACH", "working_path", ""))
        self._refresh_outreach_history_view()
        self._save_after_id = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._wire_autosave_prefs()

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background="#0D0D0D", borderwidth=0)
        style.configure("TNotebook.Tab", background="#1A1A1A", foreground="#888", padding=[16,8],
                        font=("Courier New", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected","#00C896")], foreground=[("selected","#000")])
        style.configure("TFrame", background="#0D0D0D")
        style.configure("TLabel", background="#0D0D0D", foreground="#CCC", font=("Courier New", 10))
        style.configure("TEntry", fieldbackground="#1A1A1A", foreground="#00C896",
                        insertcolor="#00C896", font=("Courier New", 10))
        style.configure("TButton", background="#00C896", foreground="#000",
                        font=("Courier New", 10, "bold"), padding=[10,6])
        style.map("TButton", background=[("active","#00A07A")])
        style.configure("Danger.TButton", background="#FF4444", foreground="#FFF",
                        font=("Courier New", 10, "bold"), padding=[10,6])
        style.map("Danger.TButton", background=[("active","#CC2222")])
        style.configure("TSpinbox", fieldbackground="#1A1A1A", foreground="#00C896",
                        font=("Courier New", 10))
        style.configure("TCheckbutton", background="#0D0D0D", foreground="#CCC",
                        font=("Courier New", 10))
        style.configure("TCombobox", fieldbackground="#1A1A1A", foreground="#00C896",
                        bordercolor="#333", arrowcolor="#00C896", font=("Courier New", 10))

    def _lbl(self, parent, text, row, col, colspan=1, anchor="w"):
        ttk.Label(parent, text=text).grid(row=row, column=col, columnspan=colspan,
                                          sticky=anchor, padx=8, pady=4)

    def _entry(self, parent, row, col, width=35, show=None, colspan=1):
        e = ttk.Entry(parent, width=width, show=show)
        e.grid(row=row, column=col, columnspan=colspan, sticky="ew", padx=8, pady=4)
        return e

    def _spin(self, parent, row, col, from_, to, default):
        s = ttk.Spinbox(parent, from_=from_, to=to, width=10)
        s.set(default)
        s.grid(row=row, column=col, sticky="w", padx=8, pady=4)
        return s

    def _scroll_units_from_mousewheel(self, event) -> int:
        delta = getattr(event, "delta", 0)
        if delta:
            return int(-delta / 120) or (-1 if delta > 0 else 1)
        num = getattr(event, "num", None)
        if num == 4:
            return -1
        if num == 5:
            return 1
        return 0

    def _bind_scrollable_tab_mousewheel(self, canvas, widget):
        def _on_mousewheel(event):
            units = self._scroll_units_from_mousewheel(event)
            if units:
                canvas.yview_scroll(units, "units")

        def _bind_tree(node):
            node.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_mousewheel), add="+")
            node.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"), add="+")
            node.bind("<Button-4>", _on_mousewheel, add="+")
            node.bind("<Button-5>", _on_mousewheel, add="+")
            for child in node.winfo_children():
                _bind_tree(child)

        _bind_tree(widget)

    def _create_scrollable_tab(self, nb, title):
        outer = ttk.Frame(nb)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        nb.add(outer, text=title)

        canvas = tk.Canvas(
            outer,
            bg="#0D0D0D",
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        content = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def _sync_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_content_width(event):
            canvas.itemconfigure(window_id, width=event.width)

        content.bind("<Configure>", _sync_scrollregion, add="+")
        canvas.bind("<Configure>", _sync_content_width, add="+")
        self._bind_scrollable_tab_mousewheel(canvas, content)
        return content

    def _default_outreach_subject(self) -> str:
        return "Regarding your case - we may be able to help"

    def _default_outreach_body(self) -> str:
        return (
            "Hi {name},\n\n"
            "My name is [YOUR NAME] and I work with a team that specialises in helping victims of financial fraud recover their lost funds.\n\n"
            "We understand how distressing it can be to lose money to a scam, and we want you to know that recovery may be possible.\n\n"
            "If you'd like to learn more about your options, simply reply to this email and one of our specialists will reach out to you personally.\n\n"
            "There is no obligation, and your enquiry is completely confidential.\n\n"
            "Kind regards,\n[YOUR NAME]"
        )

    def _selected_email_account_index(self) -> int:
        for idx, account in enumerate(self._email_accounts):
            if account["id"] == self._selected_email_account_id:
                return idx
        return 0 if self._email_accounts else -1

    def _persist_email_account_editor(self):
        idx = self._selected_email_account_index()
        if idx < 0:
            return
        account = self._email_accounts[idx]
        account["label"] = self.account_label.get().strip() or self.account_smtp_user.get().strip() or f"Account {idx + 1}"
        account["smtp_host"] = self.account_smtp_host.get().strip() or "smtp.alexhost.com"
        account["smtp_port"] = self.account_smtp_port.get().strip() or "465"
        account["smtp_user"] = self.account_smtp_user.get().strip()
        account["smtp_password"] = self.account_smtp_pass.get().strip()
        account["display_name"] = self.account_display_name.get().strip()
        account["imap_host"] = self.account_imap_host.get().strip() or "imap.alexhost.com"
        account["imap_port"] = self.account_imap_port.get().strip() or "993"
        if self.account_listbox.size() > idx:
            self.account_listbox.delete(idx)
            self.account_listbox.insert(
                idx,
                f"{account.get('label') or account.get('smtp_user') or 'Email account'} | {account.get('smtp_user', '')}",
            )
            self.account_listbox.selection_clear(0, tk.END)
            self.account_listbox.selection_set(idx)
            self.account_listbox.activate(idx)

    def _on_email_account_editor_change(self, _event=None):
        self._persist_email_account_editor()
        self._persist_outreach_template_editor()
        self._schedule_prefs_save()

    def _selected_outreach_template_account_index(self) -> int:
        for idx, account in enumerate(self._email_accounts):
            if account["id"] == self._selected_outreach_template_account_id:
                return idx
        return 0 if self._email_accounts else -1

    def _persist_outreach_template_editor(self):
        idx = self._selected_outreach_template_account_index()
        if idx < 0:
            return
        account = self._email_accounts[idx]
        account["outreach_subject"] = self.outreach_subject.get().strip() or self._default_outreach_subject()
        account["outreach_body"] = self.outreach_body.get("1.0", "end").rstrip("\n") or self._default_outreach_body()

    def _refresh_outreach_template_editor(self, select_id: str = ""):
        if self._email_accounts:
            self._selected_outreach_template_account_id = (
                select_id
                or self._selected_outreach_template_account_id
                or self._email_accounts[0]["id"]
            )
        else:
            self._selected_outreach_template_account_id = ""

        labels = []
        ids = []
        for idx, account in enumerate(self._email_accounts):
            labels.append(account.get("label") or account.get("smtp_user") or f"Account {idx + 1}")
            ids.append(account["id"])
        self._outreach_template_account_ids = ids
        self.outreach_template_account["values"] = tuple(labels)

        idx = self._selected_outreach_template_account_index()
        if idx >= 0:
            account = self._email_accounts[idx]
            self.outreach_template_account.current(idx)
            self.outreach_subject.delete(0, tk.END)
            self.outreach_subject.insert(0, account.get("outreach_subject") or self._default_outreach_subject())
            self.outreach_body.delete("1.0", tk.END)
            self.outreach_body.insert("1.0", account.get("outreach_body") or self._default_outreach_body())
        else:
            self.outreach_template_account.set("")
            self.outreach_subject.delete(0, tk.END)
            self.outreach_body.delete("1.0", tk.END)

    def _on_outreach_template_account_select(self, _event=None):
        self._persist_outreach_template_editor()
        idx = self.outreach_template_account.current()
        if 0 <= idx < len(getattr(self, "_outreach_template_account_ids", [])):
            self._selected_outreach_template_account_id = self._outreach_template_account_ids[idx]
            self._refresh_outreach_template_editor(select_id=self._selected_outreach_template_account_id)
            self._schedule_prefs_save()

    def _on_outreach_template_change(self, _event=None):
        self._persist_outreach_template_editor()
        self._schedule_prefs_save()

    def _refresh_email_accounts_ui(self, select_id: str = "", preserve_editor: bool = False):
        if preserve_editor and self._email_accounts:
            select_id = select_id or self._selected_email_account_id
        elif self._email_accounts:
            select_id = select_id or self._selected_email_account_id or self._email_accounts[0]["id"]
        else:
            select_id = ""

        self._selected_email_account_id = select_id
        self.account_listbox.delete(0, tk.END)
        for account in self._email_accounts:
            self.account_listbox.insert(
                tk.END,
                f"{account.get('label') or account.get('smtp_user') or 'Email account'} | {account.get('smtp_user', '')}",
            )

        selected_idx = self._selected_email_account_index()
        if selected_idx >= 0:
            self.account_listbox.selection_clear(0, tk.END)
            self.account_listbox.selection_set(selected_idx)
            self.account_listbox.activate(selected_idx)
            account = self._email_accounts[selected_idx]
            for widget, value, default in (
                (self.account_label, account.get("label", ""), ""),
                (self.account_smtp_host, account.get("smtp_host", ""), "smtp.alexhost.com"),
                (self.account_smtp_port, account.get("smtp_port", ""), "465"),
                (self.account_smtp_user, account.get("smtp_user", ""), ""),
                (self.account_smtp_pass, account.get("smtp_password", ""), ""),
                (self.account_display_name, account.get("display_name", ""), ""),
                (self.account_imap_host, account.get("imap_host", ""), "imap.alexhost.com"),
                (self.account_imap_port, account.get("imap_port", ""), "993"),
            ):
                widget.delete(0, tk.END)
                widget.insert(0, value or default)
        else:
            for widget in (
                self.account_label,
                self.account_smtp_host,
                self.account_smtp_port,
                self.account_smtp_user,
                self.account_smtp_pass,
                self.account_display_name,
                self.account_imap_host,
                self.account_imap_port,
            ):
                widget.delete(0, tk.END)

        self._refresh_sender_mix_widgets()
        self._refresh_monitor_account_options()
        self._refresh_outreach_template_editor()

    def _on_email_account_select(self, _event=None):
        current = self.account_listbox.curselection()
        if not current:
            return
        self._persist_email_account_editor()
        idx = current[0]
        if 0 <= idx < len(self._email_accounts):
            self._selected_email_account_id = self._email_accounts[idx]["id"]
            self._refresh_email_accounts_ui(select_id=self._selected_email_account_id)

    def _add_email_account(self):
        self._persist_email_account_editor()
        account = {
            "id": uuid.uuid4().hex[:12],
            "label": f"Account {len(self._email_accounts) + 1}",
            "smtp_host": "smtp.alexhost.com",
            "smtp_port": "465",
            "smtp_user": "",
            "smtp_password": "",
            "display_name": "",
            "imap_host": "imap.alexhost.com",
            "imap_port": "993",
            "outreach_subject": self.outreach_subject.get().strip() or self._default_outreach_subject(),
            "outreach_body": self.outreach_body.get("1.0", "end").rstrip("\n") or self._default_outreach_body(),
        }
        self._email_accounts.append(account)
        self._refresh_email_accounts_ui(select_id=account["id"])
        self._refresh_outreach_template_editor(select_id=account["id"])
        self._schedule_prefs_save()

    def _remove_email_account(self):
        idx = self._selected_email_account_index()
        if idx < 0:
            return
        del self._email_accounts[idx]
        next_id = self._email_accounts[min(idx, len(self._email_accounts) - 1)]["id"] if self._email_accounts else ""
        self._selected_email_account_id = next_id
        self._refresh_email_accounts_ui(select_id=next_id)
        self._schedule_prefs_save()

    def _refresh_sender_mix_widgets(self):
        existing_values = {}
        for account_id, var in self._sender_share_vars.items():
            existing_values[account_id] = var.get()
        self._sender_share_vars = {}
        for child in self.sender_mix_frame.winfo_children():
            child.destroy()
        if not self._email_accounts:
            ttk.Label(self.sender_mix_frame, text="Add at least one email account in Settings.").grid(
                row=0, column=0, sticky="w", padx=8, pady=4
            )
            return

        total = 0.0
        for idx, account in enumerate(self._email_accounts):
            account_id = account["id"]
            value = existing_values.get(account_id)
            if value is None:
                value = "100" if idx == 0 and len(self._email_accounts) == 1 else "0"
            var = tk.StringVar(value=value)
            self._sender_share_vars[account_id] = var
            ttk.Label(
                self.sender_mix_frame,
                text=account.get("label") or account.get("smtp_user") or f"Account {idx + 1}",
            ).grid(row=idx, column=0, sticky="w", padx=8, pady=4)
            spin = ttk.Spinbox(
                self.sender_mix_frame,
                from_=0,
                to=100,
                width=8,
                textvariable=var,
            )
            spin.grid(row=idx, column=1, sticky="w", padx=8, pady=4)
            ttk.Label(self.sender_mix_frame, text="%").grid(row=idx, column=2, sticky="w", pady=4)
            spin.bind("<ButtonRelease-1>", self._schedule_prefs_save)
            spin.bind("<KeyRelease>", self._schedule_prefs_save)
            spin.bind("<FocusOut>", lambda _e: self._flush_prefs_save())
            try:
                total += float(var.get() or "0")
            except Exception:
                pass
        self.sender_mix_note.config(
            text="Percentages are normalized automatically at send time. Set one account to 100% to use only that inbox."
        )

    def _load_sender_mix(self, c: configparser.ConfigParser):
        raw = c.get("OUTREACH", "sender_mix_json", fallback="").strip()
        data = {}
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    data = {str(k): str(v) for k, v in loaded.items()}
            except Exception:
                data = {}
        if not data and self._email_accounts:
            first = self._email_accounts[0]["id"]
            data = {first: "100"}
        for account in self._email_accounts:
            value = data.get(account["id"])
            if value is not None and account["id"] in self._sender_share_vars:
                self._sender_share_vars[account["id"]].set(str(value))

    def _refresh_monitor_account_options(self):
        existing_values = {account_id: var.get() for account_id, var in self._monitor_account_vars.items()}
        self._monitor_account_vars = {}
        for child in self.monitor_accounts_frame.winfo_children():
            child.destroy()
        if not self._email_accounts:
            ttk.Label(self.monitor_accounts_frame, text="Add at least one email account in Settings.").grid(
                row=0, column=0, sticky="w", padx=8, pady=4
            )
            return

        raw_ids = cfg("MONITOR", "account_ids_json", "").strip()
        saved_ids = set()
        if raw_ids:
            try:
                parsed = json.loads(raw_ids)
                if isinstance(parsed, list):
                    saved_ids = {str(item) for item in parsed}
            except Exception:
                saved_ids = set()
        if not saved_ids:
            legacy_id = cfg("MONITOR", "account_id", "").strip()
            if legacy_id:
                saved_ids = {legacy_id}

        for idx, account in enumerate(self._email_accounts):
            account_id = account["id"]
            if account_id in existing_values:
                checked = existing_values[account_id]
            elif saved_ids:
                checked = account_id in saved_ids
            else:
                checked = idx == 0
            var = tk.BooleanVar(value=checked)
            self._monitor_account_vars[account_id] = var
            chk = ttk.Checkbutton(
                self.monitor_accounts_frame,
                text=account.get("label") or account.get("smtp_user") or f"Account {idx + 1}",
                variable=var,
                command=self._schedule_prefs_save,
            )
            chk.grid(row=idx, column=0, sticky="w", padx=8, pady=4)

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg="#0D0D0D")
        hdr.pack(fill="x", padx=20, pady=(16,4))
        hdr_status_fr = tk.Frame(hdr, bg="#0D0D0D")
        hdr_status_fr.pack(side="right", padx=(8, 0))
        self.hdr_monitor_status = tk.Label(
            hdr_status_fr,
            text="Reply monitor: Idle",
            bg="#0D0D0D",
            fg="#666666",
            font=("Courier New", 10, "bold"),
        )
        self.hdr_monitor_status.pack(anchor="e")
        self.hdr_outreach_status = tk.Label(
            hdr_status_fr,
            text="Outreach: Idle",
            bg="#0D0D0D",
            fg="#666666",
            font=("Courier New", 10, "bold"),
        )
        self.hdr_outreach_status.pack(anchor="e")
        tk.Label(hdr, text="OUTREACH", bg="#0D0D0D", fg="#00C896",
                 font=("Courier New", 22, "bold")).pack(side="left")
        tk.Label(hdr, text="PRO", bg="#0D0D0D", fg="#FFFFFF",
                 font=("Courier New", 22, "bold")).pack(side="left")
        tk.Label(hdr, text="  //  scam recovery mailer", bg="#0D0D0D", fg="#444",
                 font=("Courier New", 11)).pack(side="left", padx=8)

        sep = tk.Frame(self, bg="#00C896", height=1)
        sep.pack(fill="x", padx=20, pady=(0,8))

        self.status_bar = tk.Frame(self, bg="#141414", highlightthickness=1, highlightbackground="#2a2a2a")
        self.status_bar.pack(side="bottom", fill="x")
        self.status_outreach = tk.Label(
            self.status_bar,
            text="Outreach: Idle",
            bg="#141414",
            fg="#666666",
            font=("Courier New", 10),
            anchor="w",
        )
        self.status_outreach.pack(side="left", padx=(16, 8), pady=8)
        tk.Label(self.status_bar, text="·", bg="#141414", fg="#444444", font=("Courier New", 10)).pack(
            side="left", padx=4, pady=8
        )
        self.status_monitor = tk.Label(
            self.status_bar,
            text="Reply monitor: Idle",
            bg="#141414",
            fg="#666666",
            font=("Courier New", 10),
            anchor="w",
        )
        self.status_monitor.pack(side="left", padx=(8, 16), pady=8)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._on_notebook_tab_changed())

        self._build_settings_tab(self.notebook)
        self._build_database_tab(self.notebook)
        self._build_warmup_tab(self.notebook)
        self._build_outreach_tab(self.notebook)
        self._build_monitor_tab(self.notebook)
        self._build_log_tab(self.notebook)

    def _on_notebook_tab_changed(self):
        # Persist settings after edits.
        self._schedule_prefs_save()

    # -- SETTINGS ------------------------------
    def _build_settings_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="⚙  SETTINGS")
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=2)
        f.rowconfigure(1, weight=1)

        self._lbl(f,"--- Email Accounts ---",0,0,2)

        left = ttk.Frame(f)
        left.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        right = ttk.Frame(f)
        right.grid(row=1, column=1, sticky="nsew", padx=8, pady=4)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)

        self.account_listbox = tk.Listbox(
            left,
            height=10,
            bg="#1A1A1A",
            fg="#00C896",
            selectbackground="#00A07A",
            selectforeground="#000",
            font=("Courier New", 10),
            relief="flat",
            highlightthickness=0,
        )
        self.account_listbox.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.account_listbox.bind("<<ListboxSelect>>", self._on_email_account_select)

        ttk.Button(left, text="➕  Add account", command=self._add_email_account).grid(row=1, column=0, sticky="w", pady=8)
        ttk.Button(left, text="🗑  Remove account", style="Danger.TButton", command=self._remove_email_account).grid(row=1, column=1, sticky="e", pady=8)

        self._lbl(right,"Label:",0,0)
        self.account_label = self._entry(right,0,1,width=42)
        self._lbl(right,"SMTP Host:",1,0)
        self.account_smtp_host = self._entry(right,1,1)
        self.account_smtp_host.insert(0,"smtp.alexhost.com")
        self._lbl(right,"SMTP Port:",2,0)
        self.account_smtp_port = self._entry(right,2,1)
        self.account_smtp_port.insert(0,"465")
        self._lbl(right,"Email Address:",3,0)
        self.account_smtp_user = self._entry(right,3,1)
        self._lbl(right,"Password:",4,0)
        self.account_smtp_pass = self._entry(right,4,1,show="●")
        self._lbl(right,"Display Name:",5,0)
        self.account_display_name = self._entry(right,5,1)
        self._lbl(right,"IMAP Host:",6,0)
        self.account_imap_host = self._entry(right,6,1)
        self.account_imap_host.insert(0,"imap.alexhost.com")
        self._lbl(right,"IMAP Port:",7,0)
        self.account_imap_port = self._entry(right,7,1)
        self.account_imap_port.insert(0,"993")

        self._lbl(f,"--- Discord ---",2,0,2)
        self._lbl(f,"Webhook URL:",3,0)
        self.discord_url = self._entry(f,3,1,width=60)

        ttk.Button(f, text="💾  Save all preferences", command=self._save_settings).grid(
            row=4, column=0, columnspan=2, pady=16)
        self._lbl(
            f,
            "Preferences auto-save when you switch tabs, after edits (short delay), and when closing.",
            5,
            0,
            2,
        )

    # -- DATABASE ------------------------------
    def _build_database_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="🗄  DATABASE")
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        top = ttk.Frame(f)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        ttk.Button(top, text="➕  Import .db (saves a working copy)", command=self._import_database_dialog).pack(
            side="left", padx=4
        )
        ttk.Button(top, text="📊  Import Excel → .db", command=self._import_excel_dialog).pack(
            side="left", padx=4
        )
        ttk.Button(top, text="🔄  Refresh lists", command=lambda: self._refresh_import_lists()).pack(side="left", padx=4)
        ttk.Button(top, text="🗑  Remove selected import", style="Danger.TButton", command=self._remove_selected_import).pack(
            side="left", padx=4
        )

        self._lbl(f, "Imported lists (original file is never modified):", 1, 0, 2)
        lb_frame = tk.Frame(f, bg="#0D0D0D")
        lb_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=8, pady=4)
        f.rowconfigure(2, weight=1)
        self.db_imports_lb = tk.Listbox(
            lb_frame,
            height=8,
            bg="#1A1A1A",
            fg="#00C896",
            selectbackground="#00A07A",
            selectforeground="#000",
            font=("Courier New", 10),
            relief="flat",
            highlightthickness=0,
        )
        self.db_imports_lb.pack(fill="both", expand=True)
        self.db_imports_lb.bind("<<ListboxSelect>>", lambda _e: self._on_database_list_select())

        stat_fr = ttk.Frame(f)
        stat_fr.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        self.db_stat_total = ttk.Label(stat_fr, text="Leads: —")
        self.db_stat_total.pack(side="left", padx=12)
        self.db_stat_sent = ttk.Label(stat_fr, text="Sent: —")
        self.db_stat_sent.pack(side="left", padx=12)
        self.db_stat_replied = ttk.Label(stat_fr, text="Replied: —")
        self.db_stat_replied.pack(side="left", padx=12)
        self.db_stat_left = ttk.Label(stat_fr, text="Left (not sent): —")
        self.db_stat_left.pack(side="left", padx=12)

        stat_note = ttk.Frame(f)
        stat_note.grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))
        self.db_stat_note = ttk.Label(
            stat_note,
            text="",
            font=("Courier New", 9),
            foreground="#888",
        )
        self.db_stat_note.pack(side="left")

        self._lbl(f, "Leads by location (from phone number country code):", 5, 0, 2)

        # Compact inline summary only (no table/tree view).
        self.db_loc_summary = ttk.Label(
            f,
            text="",
            font=("Courier New", 9),
            foreground="#888",
            wraplength=740,
        )
        self.db_loc_summary.grid(row=6, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 0))

        self._lbl(
            f,
            "ℹ  .db imports are copied into the tool data folder; Excel is converted to a new\n"
            "   .db (row 1 = headers: Full name, Email, Number, Sent, Replied). Supported:\n"
            "   .xlsx / .xlsm. Outreach updates the working copy; reply monitor uses master DB.\n"
            "   'Leads' = `contacts` rows with a non-empty email.",
            10,
            0,
            2,
        )

        self._db_list_import_ids = []

    # -- WARMUP --------------------------------
    def _build_warmup_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="🔥  WARMUP")
        f.columnconfigure(1, weight=1)

        self._lbl(f,"Warmup Email List (one per line):",0,0,2)
        self.warmup_emails = tk.Text(f, height=6, bg="#1A1A1A", fg="#00C896",
                                     insertbackground="#00C896",
                                     font=("Courier New",10), relief="flat")
        self.warmup_emails.grid(row=1,column=0,columnspan=2,sticky="ew",padx=8,pady=4)

        self._lbl(f,"Emails to Send:",2,0)
        self.warmup_count = self._spin(f,2,1,1,500,30)
        self._lbl(f,"Min Delay (sec):",3,0)
        self.warmup_dmin = self._spin(f,3,1,10,600,60)
        self._lbl(f,"Max Delay (sec):",4,0)
        self.warmup_dmax = self._spin(f,4,1,10,600,180)

        bf = ttk.Frame(f)
        bf.grid(row=5,column=0,columnspan=2,pady=10)
        ttk.Button(bf, text="▶  Start Warmup", command=self._start_warmup).pack(side="left",padx=4)
        ttk.Button(bf, text="⏹  Stop", style="Danger.TButton", command=self._stop_warmup).pack(side="left",padx=4)

        self._lbl(f,"ℹ  Warmup sends natural-looking emails to trusted addresses to build\n"
                    "   your sender reputation before doing real outreach.",6,0,2)

    # -- OUTREACH ------------------------------
    def _build_outreach_tab(self, nb):
        f = self._create_scrollable_tab(nb, "📧  OUTREACH")
        f.columnconfigure(1, weight=1)

        self._lbl(f, "Outreach list (working copy):", 0, 0)
        row0 = ttk.Frame(f)
        row0.grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        self.outreach_import = ttk.Combobox(row0, width=52, state="readonly")
        self.outreach_import.pack(side="left")
        self.outreach_import.bind("<<ComboboxSelected>>", lambda _e: self._schedule_prefs_save())
        ttk.Button(row0, text="Import .db…", command=self._import_database_dialog).pack(side="left", padx=4)
        ttk.Button(row0, text="Import Excel…", command=self._import_excel_dialog).pack(side="left", padx=4)

        self._lbl(f, "Sender split by account:", 1, 0)
        self.sender_mix_frame = ttk.Frame(f)
        self.sender_mix_frame.grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        self.sender_mix_note = ttk.Label(
            f,
            text="",
            font=("Courier New", 9),
            foreground="#888",
            wraplength=740,
        )
        self.sender_mix_note.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))

        self._lbl(f, "Message account:", 3, 0)
        self.outreach_template_account = ttk.Combobox(f, width=40, state="readonly")
        self.outreach_template_account.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        self.outreach_template_account.bind("<<ComboboxSelected>>", self._on_outreach_template_account_select)

        self._lbl(f, "Subject Line:", 4, 0)
        self.outreach_subject = self._entry(f, 4, 1, width=60)
        self.outreach_subject.insert(0, self._default_outreach_subject())

        self._lbl(f, "Email Body (use {name} for first name):", 5, 0, 2)
        self.outreach_body = tk.Text(
            f,
            height=7,
            bg="#1A1A1A",
            fg="#00C896",
            insertbackground="#00C896",
            font=("Courier New", 10),
            relief="flat",
            wrap="word",
        )
        self.outreach_body.grid(row=6, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        self.outreach_body.insert("1.0", self._default_outreach_body())

        self._lbl(
            f,
            "Each sender account can use its own outreach message. Pick an account above to edit its template.",
            7,
            0,
            2,
        )

        self._lbl(f,"Daily Send Limit:",8,0)
        self.daily_limit = self._spin(f,8,1,1,1000,100)
        self._lbl(f,"Min Delay Between Emails (sec):",9,0)
        self.out_dmin = self._spin(f,9,1,30,3600,120)
        self._lbl(f,"Max Delay Between Emails (sec):",10,0)
        self.out_dmax = self._spin(f,10,1,30,3600,300)
        self.outreach_parallel_var = tk.BooleanVar(value=False)
        self.outreach_parallel_check = ttk.Checkbutton(
            f,
            text="Send from all enabled mailboxes at the same time",
            variable=self.outreach_parallel_var,
            command=self._schedule_prefs_save,
        )
        self.outreach_parallel_check.grid(row=11, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 2))

        bf = ttk.Frame(f)
        bf.grid(row=12,column=0,columnspan=2,pady=10)
        ttk.Button(bf, text="▶  Start Outreach", command=self._start_outreach).pack(side="left",padx=4)
        ttk.Button(bf, text="⏹  Stop", style="Danger.TButton", command=self._stop_outreach).pack(side="left",padx=4)

        # Dashboard / Progress Section
        dash = ttk.LabelFrame(f, text=" Dashboard & Progress ")
        dash.grid(row=13, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        dash.columnconfigure(0, weight=1)

        self.outreach_progress_var = tk.DoubleVar(value=0.0)
        self.outreach_progress_bar = ttk.Progressbar(dash, variable=self.outreach_progress_var, maximum=100)
        self.outreach_progress_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        self.outreach_history_summary = ttk.Label(
            dash,
            text="Initialising...",
            font=("Courier New", 10, "bold"),
            foreground="#00C896",
            wraplength=800
        )
        self.outreach_history_summary.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

        # Recent History Log
        hist_f = ttk.Frame(f)
        hist_f.grid(row=14, column=0, columnspan=2, sticky="nsew", padx=8, pady=(0, 8))
        f.rowconfigure(14, weight=1)
        
        hist_hdr = ttk.Frame(hist_f)
        hist_hdr.pack(fill="x", pady=(8, 4))
        ttk.Label(hist_hdr, text="Recent Outreach Activity (Last 3 Days)").pack(side="left")
        ttk.Button(hist_hdr, text="Reset Today's History", command=self._reset_outreach_history).pack(side="right")

        self.outreach_history_box = scrolledtext.ScrolledText(
            hist_f,
            height=8,
            bg="#0D0D0D",
            fg="#00C896",
            insertbackground="#00C896",
            font=("Courier New", 9),
            relief="flat",
            state="disabled",
            wrap="word",
        )
        self.outreach_history_box.pack(fill="both", expand=True)


    def _build_monitor_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="👀  REPLY MONITOR")
        f.columnconfigure(1, weight=1)

        self._lbl(f, "Lead lookup:", 0, 0)
        mp = get_master_db_path()
        self.master_db_label = ttk.Label(f, text=mp, font=("Courier New", 9))
        self.master_db_label.grid(row=0, column=1, sticky="w", padx=8, pady=4)

        self._lbl(f, "Inbox accounts:", 1, 0)
        self.monitor_accounts_frame = ttk.Frame(f)
        self.monitor_accounts_frame.grid(row=1, column=1, sticky="w", padx=8, pady=4)

        self._lbl(f,"Check Inbox Every (sec):",2,0)
        self.check_interval = self._spin(f,2,1,30,3600,120)

        bf = ttk.Frame(f)
        bf.grid(row=3,column=0,columnspan=2,pady=10)
        ttk.Button(bf, text="▶  Start Monitor", command=self._start_monitor).pack(side="left",padx=4)
        ttk.Button(bf, text="⏹  Stop", style="Danger.TButton", command=self._stop_monitor).pack(side="left",padx=4)

        self._lbl(
            f,
            "ℹ  The monitor uses the tool's master database (all imported leads). When a\n"
            "   reply matches an email there, it posts to Discord and marks the lead as\n"
            "   replied in the master database.",
            4,
            0,
            2,
        )

    # -- LEADS ---------------------------------
    def _build_leads_tab(self, nb):
        # Leads tab removed; location breakdown is now shown in the Database tab.
        return

    def _phone_location_label(self, raw_number: str, default_region_hint: Optional[str] = None) -> str:
        """
        Best-effort location label from phone number.

        If the number doesn't include an explicit country prefix (no '+' / '00'), we parse it
        using `default_region_hint` to avoid mis-interpreting country codes.
        """
        s = (raw_number or "").strip()
        if not s:
            return "Unknown"

        # Keep digits and leading '+' if present
        s2 = re.sub(r"[^\d+]", "", s)
        if not s2:
            return "Unknown"

        explicit = s2.startswith("+") or s2.startswith("00")
        if s2.startswith("00"):
            # Convert 00<countrycode> -> +<countrycode>
            s2 = "+" + s2[2:]

        digits = re.sub(r"\D+", "", s2)  # after normalization, remove any '+'
        if not digits:
            return "Unknown"

        try:
            if explicit:
                num = phonenumbers.parse(s2, None)
                if not phonenumbers.is_possible_number(num):
                    return "Unknown"
            else:
                # Parse as a national number using the hint, if we have one.
                if default_region_hint:
                    num = phonenumbers.parse(digits, default_region_hint)
                    if not phonenumbers.is_possible_number(num):
                        # Fallback: try interpreting it as E.164 (may be wrong, but better than Unknown)
                        num = phonenumbers.parse("+" + digits, None)
                else:
                    num = phonenumbers.parse(digits, None)
                    if not phonenumbers.is_possible_number(num):
                        num = phonenumbers.parse("+" + digits, None)

                if not phonenumbers.is_possible_number(num):
                    return "Unknown"

            region = phonenumbers.region_code_for_number(num) or "Unknown"
        except Exception:
            return "Unknown"

        if region == "GB":
            return "England"
        if region == "FR":
            return "France"
        if region == "US":
            return "United States"
        if region == "CA":
            return "Canada"
        if region == "AU":
            return "Australia"
        if region == "DE":
            return "Germany"
        if region == "ES":
            return "Spain"
        if region == "IT":
            return "Italy"
        if region == "NL":
            return "Netherlands"
        if region == "BE":
            return "Belgium"
        return region

    def _clear_db_location_breakdown(self):
        try:
            self.db_loc_summary.config(text="")
        except Exception:
            pass

    def _refresh_db_location_breakdown(self, import_id):
        """Show location summary for the selected import (working .db).

        Uses libphonenumberapi.com to resolve phone -> country, and caches results
        in the master DB so the API is only called once per phone number.
        """
        if self._db_loc_refresh_busy:
            return

        self._db_loc_refresh_busy = True
        try:
            self.db_loc_summary.config(text="Loading location counts...")
        except Exception:
            pass

        def worker():
            counts = {}
            try:
                wp = get_working_path_for_import(import_id)
                if not wp or not os.path.isfile(wp):
                    raise FileNotFoundError("Working DB missing")

                # Load all phone numbers from the selected working DB.
                conn_w = sqlite3.connect(wp)
                ensure_contact_tracking_columns(conn_w)
                rows = conn_w.execute(
                    "SELECT number FROM contacts WHERE number IS NOT NULL AND TRIM(COALESCE(number,'')) != ''"
                ).fetchall()
                conn_w.close()

                # Prepare deduped list of phones for API caching.
                def sanitize_key(v):
                    return re.sub(r"\D+", "", str(v or ""))

                key_to_sample_raw = {}
                keys_in_file = set()
                key_freq = {}
                for r in rows:
                    raw = r[0]
                    key = sanitize_key(raw)
                    if not key:
                        continue
                    keys_in_file.add(key)
                    if key not in key_to_sample_raw:
                        key_to_sample_raw[key] = raw
                    key_freq[key] = key_freq.get(key, 0) + 1

                # Determine a lightweight default region hint (only for constructing candidate API inputs).
                default_region_hint = None
                try:
                    hint_counts = {}
                    candidate_regions = ["CA", "GB", "US", "AU", "FR", "DE", "ES", "IT", "NL", "BE"]
                    # Use a small sample for speed.
                    for key in list(keys_in_file)[:500]:
                        try:
                            digits = key
                            for cand in candidate_regions:
                                num = phonenumbers.parse(digits, cand)
                                if phonenumbers.is_possible_number(num):
                                    region = phonenumbers.region_code_for_number(num) or None
                                    if region:
                                        hint_counts[region] = hint_counts.get(region, 0) + 1
                        except Exception:
                            continue
                    if hint_counts:
                        default_region_hint = max(hint_counts.items(), key=lambda kv: kv[1])[0]
                except Exception:
                    default_region_hint = None

                # Open master DB and read cache for the phones we need.
                conn_m = connect_master()
                init_master_schema(conn_m)

                def label_from_country_code(cc: str) -> str:
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

                cache_labels = {}  # key -> label

                keys_list = list(keys_in_file)
                chunk_size = 500
                for i in range(0, len(keys_list), chunk_size):
                    chunk = keys_list[i:i + chunk_size]
                    if not chunk:
                        continue
                    q_marks = ",".join(["?"] * len(chunk))
                    rows_cache = conn_m.execute(
                        f"SELECT number_sanitized, country_label FROM phone_country_cache WHERE number_sanitized IN ({q_marks})",
                        tuple(chunk),
                    ).fetchall()
                    for k, lab in rows_cache:
                        cache_labels[k] = lab

                # Call API for any cache misses.
                missing_keys = [k for k in keys_in_file if k not in cache_labels]

                api_base = "https://libphonenumberapi.com/api/phone-numbers/"

                def build_api_candidates(sample_raw, digits_key):
                    # Return a list of phone strings to try (include '+').
                    s = str(sample_raw or "").strip()
                    s2 = re.sub(r"[^\d+]", "", s)
                    if s2.startswith("00"):
                        s2 = "+" + s2[2:]
                    candidates = []

                    if s2.startswith("+"):
                        candidates.append(s2)
                    # If digits already include country code (e.g. +1..., +44..., or US/CA '1' prefix in national form)
                    if len(digits_key) >= 11 and digits_key.startswith("1"):
                        candidates.append("+" + digits_key)

                    if default_region_hint in ("CA", "GB"):
                        # If we have a national number without country prefix, prepend the expected calling code.
                        if default_region_hint == "CA" and (len(digits_key) == 10 or not digits_key.startswith("1")):
                            candidates.append("+1" + digits_key[-10:])
                        if default_region_hint == "GB" and (len(digits_key) == 10 or not digits_key.startswith("44")):
                            candidates.append("+44" + digits_key[-10:])

                    # If the number looks like a national-format number (no +), try common candidates.
                    # This improves CA/GB disambiguation when we can't infer a country prefix.
                    if len(digits_key) == 10:
                        candidates.append("+1" + digits_key)
                        candidates.append("+44" + digits_key)
                    elif len(digits_key) == 9:
                        # Some UK formats are 9 digits after stripping leading 0.
                        candidates.append("+44" + digits_key)

                    # Final fallback: try interpreting as international by direct '+digits'.
                    candidates.append("+" + digits_key)

                    # Deduplicate while preserving order.
                    seen = set()
                    out = []
                    for c in candidates:
                        c = c.strip()
                        if not c or c in seen:
                            continue
                        seen.add(c)
                        out.append(c)
                    return out

                headers = {"User-Agent": "OutreachPro/1.0"}
                # Real-time progress + counts:
                # - Start from cached labels
                # - Count unresolved phones as "Pending"
                counts = {}
                pending_label = "Pending"
                pending_total = 0
                for k, freq in key_freq.items():
                    lab = cache_labels.get(k)
                    if lab:
                        counts[lab] = counts.get(lab, 0) + freq
                    else:
                        pending_total += freq
                if pending_total:
                    counts[pending_label] = pending_total

                total_missing = len(missing_keys)
                done_missing = 0
                counts_lock = Lock()
                cache_inserts = []  # (number_sanitized, country_code, country_label, checked_at)

                def set_summary_text(extra_prefix: str = ""):
                    try:
                        items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
                        top_items = items[:12]
                        rest = len(items) - len(top_items)
                        summary = ", ".join([f"{loc} ({cnt})" for loc, cnt in top_items])
                        if rest > 0:
                            summary = summary + f", +{rest} more"
                        if extra_prefix:
                            summary = f"{extra_prefix} {summary}".strip()
                        return summary
                    except Exception:
                        return ""

                def resolve_one(key: str):
                    sample_raw = key_to_sample_raw.get(key, "")
                    digits_key = key
                    candidates = build_api_candidates(sample_raw, digits_key)
                    resolved_country = None
                    for cand in candidates:
                        try:
                            url = api_base + requests.utils.quote(cand, safe="")
                            r = requests.get(url, headers=headers, timeout=15)
                            if r.status_code != 200:
                                continue
                            data = r.json()
                            cc = (data.get("country") or "").strip()
                            if cc:
                                resolved_country = cc
                                break
                        except Exception:
                            continue
                    country_label = label_from_country_code(resolved_country) if resolved_country else "Unknown"
                    return key, resolved_country or "", country_label

                # Kick off a UI ticker that updates while we work.
                def ui_tick():
                    if not self._db_loc_refresh_busy:
                        return
                    with counts_lock:
                        prefix = f"Resolving {done_missing}/{total_missing}..."
                        live = set_summary_text(extra_prefix=prefix)
                    try:
                        self.db_loc_summary.config(text=live)
                    except Exception:
                        pass
                    self.after(350, ui_tick)

                self.after(0, ui_tick)

                if missing_keys:
                    with ThreadPoolExecutor(max_workers=10) as ex:
                        futures = [ex.submit(resolve_one, k) for k in missing_keys]
                        for fut in as_completed(futures):
                            try:
                                key, cc, lab = fut.result()
                            except Exception:
                                continue
                            freq = key_freq.get(key, 0)
                            with counts_lock:
                                done_missing += 1
                                # Move this key's rows from Pending -> resolved label
                                if pending_total and counts.get(pending_label, 0) > 0:
                                    counts[pending_label] = max(0, counts.get(pending_label, 0) - freq)
                                    if counts[pending_label] == 0:
                                        counts.pop(pending_label, None)
                                counts[lab] = counts.get(lab, 0) + freq
                            cache_labels[key] = lab
                            cache_inserts.append((key, cc, lab, datetime.utcnow().isoformat()))

                            # Flush cache inserts in batches so SQLite writes stay safe/fast.
                            if len(cache_inserts) >= 200:
                                try:
                                    conn_m.executemany(
                                        "INSERT OR REPLACE INTO phone_country_cache (number_sanitized, country_code, country_label, checked_at) VALUES (?,?,?,?)",
                                        cache_inserts,
                                    )
                                    conn_m.commit()
                                    cache_inserts.clear()
                                except Exception:
                                    conn_m.rollback()

                # Final cache flush
                if cache_inserts:
                    try:
                        conn_m.executemany(
                            "INSERT OR REPLACE INTO phone_country_cache (number_sanitized, country_code, country_label, checked_at) VALUES (?,?,?,?)",
                            cache_inserts,
                        )
                        conn_m.commit()
                    except Exception:
                        conn_m.rollback()

                # Final summary (no prefix)
                with counts_lock:
                    summary = set_summary_text()

                try:
                    conn_m.close()
                except Exception:
                    pass

            except Exception:
                summary = ""

            def apply_ui():
                try:
                    self.db_loc_summary.config(text=summary)
                except Exception:
                    pass
                self._db_loc_refresh_busy = False

            self.after(0, apply_ui)

        self._db_loc_refresh_thread = threading.Thread(target=worker, daemon=True)
        self._db_loc_refresh_thread.start()

    # -- LOG -----------------------------------
    def _build_log_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="📋  LOG")
        self.log_box = scrolledtext.ScrolledText(
            f, bg="#0D0D0D", fg="#00C896", insertbackground="#00C896",
            font=("Courier New", 9), relief="flat", state="disabled"
        )
        self.log_box.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Button(f, text="🗑  Clear Log", command=self._clear_log).pack(pady=4)

    # -- ACTIONS -------------------------------
    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        def _do():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"[{ts}] {msg}\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(0, _do)

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0","end")
        self.log_box.configure(state="disabled")

    def _refresh_outreach_history_view(self):
        today_history = load_outreach_history()
        history_days = list_outreach_history_days()
        total_sent = today_history.get("total_sent", 0)
        limit_value = 100 # default fallback
        try:
            limit_value = int(self.daily_limit.get())
        except Exception:
            limit_value = 100
        
        remaining = max(limit_value - total_sent, 0)
        all_days_total = sum(day.get("total_sent", 0) for day in history_days)
        
        # Update Progress Bar
        self.outreach_progress_bar.configure(maximum=max(limit_value, 1))
        self.outreach_progress_var.set(float(min(total_sent, limit_value)))
        
        self.outreach_history_summary.config(
            text=(
                f"Today ({today_history.get('date', 'N/A')}): {total_sent} / {limit_value} emails sent. "
                f"{remaining} remaining today. "
                f"({all_days_total} sent in last {len(history_days)} days)"
            )
        )


        lines = []
        for day in history_days:
            day_entries = day.get("entries", [])
            lines.append(f"=== {day.get('date', '')} | Sent: {day.get('total_sent', 0)} ===")
            sender_totals = {}
            for entry in day_entries:
                sender_email = (entry.get("sender_email") or "").strip()
                sender_label = (entry.get("sender_label") or "").strip()
                sender_key = sender_email or sender_label or "Email account"
                sender_totals[sender_key] = sender_totals.get(sender_key, 0) + 1
            if sender_totals:
                summary = ", ".join(
                    f"{sender}: {count}"
                    for sender, count in sorted(sender_totals.items(), key=lambda item: (-item[1], item[0].lower()))
                )
                lines.append(f"By mailbox: {summary}")
            for entry in reversed(day_entries):
                timestamp = entry.get("timestamp", "")
                name = (entry.get("name") or "").strip() or "Unknown"
                email_addr = entry.get("email", "")
                location = entry.get("location", "Unknown")
                sender_label = entry.get("sender_label", "Email account")
                sender_email = entry.get("sender_email", "")
                sender_text = sender_label if not sender_email else f"{sender_label} ({sender_email})"
                lines.append(f"{timestamp} | {name} <{email_addr}> | {location} | via {sender_text}")
            if day_entries:
                lines.append("")
        if not lines:
            lines.append("No outreach history has been saved yet.")

        self.outreach_history_box.configure(state="normal")
        self.outreach_history_box.delete("1.0", "end")
        self.outreach_history_box.insert("1.0", "\n".join(lines))
        self.outreach_history_box.configure(state="disabled")

    def _schedule_outreach_history_refresh(self):
        self.after(0, self._refresh_outreach_history_view)

    def _reset_outreach_history(self):
        if self._outreach_busy:
            messagebox.showwarning("Outreach", "Stop outreach before resetting today's history.")
            return
        reset_outreach_history()
        self._refresh_outreach_history_view()
        self.log("Today's outreach history was reset.")

    def _save_all_ui_prefs(self):
        """Write every tab’s fields to config.ini (same file as SMTP / Discord)."""
        self._persist_email_account_editor()
        self._persist_outreach_template_editor()
        default_account_id = self._selected_email_account_id or (self._email_accounts[0]["id"] if self._email_accounts else "")
        save_email_accounts(self._email_accounts, default_account_id=default_account_id)
        sender_mix = {account_id: var.get() for account_id, var in self._sender_share_vars.items()}
        monitor_account_ids = [
            account_id
            for account_id, var in self._monitor_account_vars.items()
            if var.get()
        ]
        save_config({
            "DISCORD": {
                "webhook": self.discord_url.get(),
            },
            "WARMUP": {
                "emails": self.warmup_emails.get("1.0", "end").rstrip("\n"),
                "count": self.warmup_count.get(),
                "delay_min": self.warmup_dmin.get(),
                "delay_max": self.warmup_dmax.get(),
            },
            "OUTREACH": {
                "working_path": self._get_outreach_working_path(),
                "subject": self.outreach_subject.get(),
                "body": self.outreach_body.get("1.0", "end").rstrip("\n"),
                "daily_limit": self.daily_limit.get(),
                "delay_min": self.out_dmin.get(),
                "delay_max": self.out_dmax.get(),
                "send_simultaneously": "1" if self.outreach_parallel_var.get() else "0",
                "sender_mix_json": json.dumps(sender_mix),
            },
            "MONITOR": {
                "check_interval": self.check_interval.get(),
                "account_id": monitor_account_ids[0] if monitor_account_ids else "",
                "account_ids_json": json.dumps(monitor_account_ids),
            },
        })

    def _schedule_prefs_save(self, _event=None):
        if self._save_after_id is not None:
            self.after_cancel(self._save_after_id)
        self._save_after_id = self.after(900, self._flush_prefs_save)

    def _flush_prefs_save(self):
        self._save_after_id = None
        try:
            self._save_all_ui_prefs()
        except Exception:
            pass

    def _wire_autosave_prefs(self):
        deb = self._schedule_prefs_save
        flush = self._flush_prefs_save
        for w in (
            self.account_label,
            self.account_smtp_host,
            self.account_smtp_port,
            self.account_smtp_user,
            self.account_smtp_pass,
            self.account_display_name,
            self.account_imap_host,
            self.account_imap_port,
        ):
            w.bind("<KeyRelease>", self._on_email_account_editor_change)
            w.bind("<FocusOut>", lambda _e: flush())
        for w in (
            self.discord_url,
        ):
            w.bind("<KeyRelease>", deb)
            w.bind("<FocusOut>", lambda _e: flush())
        self.warmup_emails.bind("<KeyRelease>", deb)
        self.warmup_emails.bind("<FocusOut>", lambda _e: flush())
        self.outreach_subject.bind("<KeyRelease>", self._on_outreach_template_change)
        self.outreach_subject.bind("<FocusOut>", lambda _e: flush())
        self.outreach_body.bind("<KeyRelease>", self._on_outreach_template_change)
        self.outreach_body.bind("<FocusOut>", lambda _e: flush())
        for w in (
            self.warmup_count,
            self.warmup_dmin,
            self.warmup_dmax,
            self.daily_limit,
            self.out_dmin,
            self.out_dmax,
            self.check_interval,
        ):
            w.bind("<ButtonRelease-1>", deb)
            w.bind("<KeyRelease>", deb)
            w.bind("<FocusOut>", lambda _e: flush())
        self.daily_limit.bind("<ButtonRelease-1>", lambda _e: self._refresh_outreach_history_view(), add="+")
        self.daily_limit.bind("<KeyRelease>", lambda _e: self._refresh_outreach_history_view(), add="+")
        self.daily_limit.bind("<FocusOut>", lambda _e: self._refresh_outreach_history_view(), add="+")

    def _on_close(self):
        try:
            if self._save_after_id is not None:
                self.after_cancel(self._save_after_id)
                self._save_after_id = None
            self._save_all_ui_prefs()
        except Exception:
            pass
        self.destroy()

    def _save_settings(self):
        try:
            self._save_all_ui_prefs()
        except Exception as ex:
            messagebox.showerror("Save failed", str(ex))
            return
        messagebox.showinfo("Saved", "All preferences were saved.")

    def _load_saved_config(self):
        c = load_config()
        legacy_subject = c.get("OUTREACH", "subject", fallback=self._default_outreach_subject()).strip() or self._default_outreach_subject()
        legacy_body = c.get("OUTREACH", "body", fallback=self._default_outreach_body()).rstrip("\n") or self._default_outreach_body()
        self._email_accounts = get_email_accounts(c)
        if not self._email_accounts:
            self._email_accounts = [{
                "id": uuid.uuid4().hex[:12],
                "label": "Account 1",
                "smtp_host": "smtp.alexhost.com",
                "smtp_port": "465",
                "smtp_user": "",
                "smtp_password": "",
                "display_name": "",
                "imap_host": "imap.alexhost.com",
                "imap_port": "993",
                "outreach_subject": legacy_subject,
                "outreach_body": legacy_body,
            }]
        for account in self._email_accounts:
            account["outreach_subject"] = (account.get("outreach_subject") or legacy_subject).strip() or self._default_outreach_subject()
            account["outreach_body"] = (account.get("outreach_body") or legacy_body).rstrip("\n") or self._default_outreach_body()
        self._selected_email_account_id = (
            c.get("EMAIL_ACCOUNTS", "default_account_id", fallback="").strip()
            or self._email_accounts[0]["id"]
        )
        self._selected_outreach_template_account_id = self._selected_email_account_id
        self._refresh_email_accounts_ui(select_id=self._selected_email_account_id)

        self.discord_url.delete(0, tk.END)
        self.discord_url.insert(0, cfg("DISCORD", "webhook"))

        if c.has_section("WARMUP"):
            if c.has_option("WARMUP", "emails"):
                self.warmup_emails.delete("1.0", tk.END)
                self.warmup_emails.insert("1.0", c.get("WARMUP", "emails"))
            if c.has_option("WARMUP", "count"):
                self.warmup_count.set(c.get("WARMUP", "count"))
            if c.has_option("WARMUP", "delay_min"):
                self.warmup_dmin.set(c.get("WARMUP", "delay_min"))
            if c.has_option("WARMUP", "delay_max"):
                self.warmup_dmax.set(c.get("WARMUP", "delay_max"))

        if c.has_section("OUTREACH"):
            if c.has_option("OUTREACH", "daily_limit"):
                self.daily_limit.set(c.get("OUTREACH", "daily_limit"))
            if c.has_option("OUTREACH", "delay_min"):
                self.out_dmin.set(c.get("OUTREACH", "delay_min"))
            if c.has_option("OUTREACH", "delay_max"):
                self.out_dmax.set(c.get("OUTREACH", "delay_max"))
            self.outreach_parallel_var.set(c.getboolean("OUTREACH", "send_simultaneously", fallback=False))
            self._load_sender_mix(c)
        self._refresh_outreach_template_editor(select_id=self._selected_outreach_template_account_id)

        if not c.has_section("OUTREACH"):
            self._load_sender_mix(c)

        if c.has_section("MONITOR"):
            if c.has_option("MONITOR", "check_interval"):
                self.check_interval.set(c.get("MONITOR", "check_interval"))
            self._refresh_monitor_account_options()

    def _get_outreach_working_path(self):
        idx = self.outreach_import.current()
        if idx < 0 or idx >= len(self._outreach_paths):
            return ""
        return self._outreach_paths[idx]

    def _refresh_import_lists(self, select_working=""):
        preserve = (select_working or self._get_outreach_working_path() or "").strip()
        sel = self.db_imports_lb.curselection()
        preserve_iid = self._db_list_import_ids[sel[0]] if sel and self._db_list_import_ids else None
        imports = list_imports()
        for r in imports:
            try:
                resync_import_from_working(r["id"])
            except Exception:
                pass
        imports = list_imports()
        self._db_list_import_ids = [r["id"] for r in imports]
        self.db_imports_lb.delete(0, tk.END)
        for r in imports:
            self.db_imports_lb.insert(
                tk.END,
                f"#{r['id']} — {r['label']}  |  {r['imported_at'][:19]}",
            )
        labels = []
        paths = []
        for r in imports:
            labels.append(f"#{r['id']} — {r['label']}")
            paths.append(r["working_path"])
        self._outreach_paths = paths
        self.outreach_import["values"] = tuple(labels)
        if paths:
            pick = -1
            if preserve:
                ap = os.path.abspath(preserve)
                for i, p in enumerate(paths):
                    if os.path.abspath(p) == ap:
                        pick = i
                        break
            if pick < 0:
                pick = 0
            self.outreach_import.current(pick)
        else:
            self.outreach_import.set("")
        if self.db_imports_lb.size() > 0:
            pick = 0
            if preserve_iid is not None:
                for j, xid in enumerate(self._db_list_import_ids):
                    if xid == preserve_iid:
                        pick = j
                        break
            self.db_imports_lb.selection_set(pick)
            self.db_imports_lb.see(pick)
        self._on_database_list_select()

    def _import_database_dialog(self):
        p = filedialog.askopenfilename(filetypes=[("SQLite DB", "*.db"), ("All files", "*.*")])
        if not p:
            return
        try:
            iid, wp = import_user_database(p)
            self.log(f"📥 Imported database → working copy id {iid} (original left unchanged).")
            self._refresh_import_lists(select_working=wp)
            self._save_all_ui_prefs()
            messagebox.showinfo(
                "Imported",
                "A working copy was saved in the tool data folder.\n"
                "Your original file was not modified.\n\n"
                f"Import id: {iid}",
            )
        except PermissionError as ex:
            messagebox.showerror(
                "Import failed (permission denied)",
                f"{ex}\n\nTry moving the source .db to a normal folder you own, "
                "or run the app from a writable location.",
            )
        except Exception as ex:
            messagebox.showerror("Import failed", str(ex))

    def _import_excel_dialog(self):
        p = filedialog.askopenfilename(
            filetypes=[
                ("Excel .xlsx", "*.xlsx"),
                ("Excel .xlsm", "*.xlsm"),
                ("All files", "*.*"),
            ]
        )
        if not p:
            return
        try:
            iid, wp = import_excel_as_leads(p)
            try:
                st = get_stats_for_import(iid)
                n = st.get("total_rows", 0)
            except Exception:
                n = 0
            self.log(f"📊 Imported Excel as working database id {iid} (contacts rows: {n}).")
            self._refresh_import_lists(select_working=wp)
            self._save_all_ui_prefs()
            messagebox.showinfo(
                "Excel imported",
                "The spreadsheet was converted to SQLite and added to your lists.\n"
                "Your original Excel file was not modified.\n\n"
                f"Import id: {iid}\n\n"
                "Tip: row 1 should name columns (e.g. Full name, Email, Number, Sent, Replied).",
            )
        except PermissionError as ex:
            messagebox.showerror(
                "Import failed (permission denied)",
                str(ex),
            )
        except Exception as ex:
            messagebox.showerror("Excel import failed", str(ex))

    def _remove_selected_import(self):
        sel = self.db_imports_lb.curselection()
        if not sel:
            messagebox.showwarning("Remove import", "Select a list in the box first.")
            return
        iid = self._db_list_import_ids[sel[0]]
        wp = get_working_path_for_import(iid)
        if not messagebox.askyesno(
            "Remove import",
            "Remove this list from the tool and delete its working copy?\n\n"
            "Your original source .db file is not changed.",
        ):
            return
        prev_outreach = self._get_outreach_working_path()
        try:
            remove_import(iid)
        except Exception as ex:
            messagebox.showerror("Remove failed", str(ex))
            return
        self.log(f"🗑 Removed import #{iid} from tool.")
        next_sel = "" if (prev_outreach and wp and os.path.abspath(prev_outreach) == os.path.abspath(wp)) else prev_outreach
        self._refresh_import_lists(select_working=next_sel)
        self._save_all_ui_prefs()

    def _on_database_list_select(self):
        sel = self.db_imports_lb.curselection()
        if not sel:
            self.db_stat_total.config(text="Leads (with email): —")
            self.db_stat_sent.config(text="Sent: —")
            self.db_stat_replied.config(text="Replied: —")
            self.db_stat_left.config(text="Left (not sent): —")
            self.db_stat_note.config(text="")
            self._clear_db_location_breakdown()
            return
        iid = self._db_list_import_ids[sel[0]]
        st = get_stats_for_import(iid)
        self.db_stat_total.config(text=f"Leads (with email): {st['total']}")
        self.db_stat_sent.config(text=f"Sent: {st['sent']}")
        self.db_stat_replied.config(text=f"Replied: {st['replied']}")
        self.db_stat_left.config(text=f"Left (not sent): {st['left']}")
        self.db_stat_note.config(
            text=(
                f"All rows in table contacts: {st['total_rows']} · "
                f"Skipped (NULL / blank email): {st['no_email']}"
            )
        )
        self._refresh_db_location_breakdown(iid)

    def _add_lead_from_database_tab(self):
        sel = self.db_imports_lb.curselection()
        if not sel:
            messagebox.showerror("Error", "Select an imported list in the list above.")
            return
        keep_import_id = self._db_list_import_ids[sel[0]]
        try:
            add_lead_to_import(
                keep_import_id,
                self.db_add_name.get().strip(),
                self.db_add_phone.get().strip(),
                self.db_add_email.get().strip(),
            )
        except Exception as ex:
            messagebox.showerror("Could not add lead", str(ex))
            return
        self.db_add_name.delete(0, tk.END)
        self.db_add_phone.delete(0, tk.END)
        self.db_add_email.delete(0, tk.END)
        wp = self._get_outreach_working_path()
        self._refresh_import_lists(select_working=wp)
        for idx, iid in enumerate(self._db_list_import_ids):
            if iid == keep_import_id:
                self.db_imports_lb.selection_set(idx)
                self.db_imports_lb.see(idx)
                break
        self._on_database_list_select()
        messagebox.showinfo("Added", "Lead added to the working copy and master database.")

    def _start_warmup(self):
        emails = [e for e in self.warmup_emails.get("1.0","end").strip().splitlines() if e.strip()]
        if not emails:
            messagebox.showerror("Error","Enter at least one warmup email address.")
            return
        self.stop_warmup.clear()
        t = threading.Thread(target=run_warmup, args=(
            emails,
            int(self.warmup_count.get()),
            int(self.warmup_dmin.get()),
            int(self.warmup_dmax.get()),
            self.log,
            self.stop_warmup
        ), daemon=True)
        t.start()

    def _stop_warmup(self):
        self.stop_warmup.set()

    def _apply_outreach_indicator(self, running: bool):
        """Main thread only. Updates header + bottom bar; keeps busy flag in sync."""
        self._outreach_busy = running
        if running:
            text = "Outreach: RUNNING"
            fg = "#FFB86C"
        else:
            text = "Outreach: Idle"
            fg = "#666666"
        self.hdr_outreach_status.config(text=text, fg=fg)
        self.status_outreach.config(text=text, fg=fg)

    def _set_outreach_countdown(self, seconds_left: int):
        """Main thread only. Updates OUTREACH label with seconds until next send."""
        if not self._outreach_busy:
            return
        try:
            secs = int(seconds_left)
        except Exception:
            secs = 0
        if secs > 0:
            text = f"Outreach: RUNNING ({secs}s)"
        else:
            text = "Outreach: RUNNING"
        fg = "#FFB86C"
        self.hdr_outreach_status.config(text=text, fg=fg)
        self.status_outreach.config(text=text, fg=fg)

    def _start_outreach(self):
        db = self._get_outreach_working_path()
        self._refresh_outreach_history_view()
        if not db or not os.path.exists(db):
            messagebox.showerror(
                "Error",
                "Import a .db from the Database tab (or use Import .db…) and select a list.",
            )
            return
        self._persist_outreach_template_editor()
        sender_accounts = []
        total_share = 0.0
        for account in self._email_accounts:
            raw_value = self._sender_share_vars.get(account["id"]).get() if account["id"] in self._sender_share_vars else "0"
            try:
                share = float(raw_value or "0")
            except Exception:
                share = 0.0
            if share <= 0:
                continue
            sender_accounts.append({**account, "weight": share})
            total_share += share
        if not sender_accounts:
            messagebox.showerror("Outreach", "Set at least one sender account above 0% in the Outreach tab.")
            return
        if abs(total_share - 100.0) > 0.01:
            self.log(f"ℹ Sender split totals {total_share:.2f}% — outreach will normalize it automatically.")
        if self._outreach_busy:
            messagebox.showwarning(
                "Outreach",
                "Outreach is already running. Use Stop or wait until it finishes.",
            )
            return
        thr = getattr(self, "_outreach_thread", None)
        if thr is not None and thr.is_alive():
            messagebox.showwarning(
                "Outreach",
                "Outreach is still finishing. Wait a moment before starting again.",
            )
            return
        self.stop_outreach.clear()
        self._apply_outreach_indicator(True)

        def worker():
            try:
                run_outreach(
                    db,
                    self.outreach_subject.get().strip() or self._default_outreach_subject(),
                    self.outreach_body.get("1.0", "end").strip() or self._default_outreach_body(),
                    int(self.daily_limit.get()),
                    int(self.out_dmin.get()),
                    int(self.out_dmax.get()),
                    self.log,
                    self.stop_outreach,
                    status_fn=lambda secs: self.after(0, lambda: self._set_outreach_countdown(secs)),
                    sender_accounts=sender_accounts,
                    history_update_fn=self._schedule_outreach_history_refresh,
                    send_simultaneously=self.outreach_parallel_var.get(),
                )
            finally:
                self.after(0, lambda: self._apply_outreach_indicator(False))
                self._schedule_outreach_history_refresh()

        self._outreach_thread = threading.Thread(target=worker, daemon=True)
        self._outreach_thread.start()

    def _stop_outreach(self):
        self.stop_outreach.set()
        if self._outreach_busy:
            self.log("⏹ Stop requested — outreach will halt after the current step (send or wait).")

    def _apply_monitor_indicator(self, running: bool):
        """Main thread only. Header + bottom bar for reply monitor."""
        self._monitor_busy = running
        if running:
            text = "Reply monitor: RUNNING"
            fg = "#88C0FF"
        else:
            text = "Reply monitor: Idle"
            fg = "#666666"
        self.hdr_monitor_status.config(text=text, fg=fg)
        self.status_monitor.config(text=text, fg=fg)

    def _start_monitor(self):
        webhook = cfg("DISCORD","webhook")
        if not webhook:
            messagebox.showerror("Error","Set your Discord webhook URL in the Settings tab first.")
            return
        monitor_accounts = [
            account
            for account in self._email_accounts
            if self._monitor_account_vars.get(account["id"]) and self._monitor_account_vars[account["id"]].get()
        ]
        if not monitor_accounts:
            default_account = get_default_email_account()
            if default_account:
                monitor_accounts = [default_account]
        if not monitor_accounts:
            messagebox.showerror("Reply monitor", "Add an inbox account in Settings first.")
            return
        if self._monitor_busy:
            messagebox.showwarning(
                "Reply monitor",
                "The monitor is already running. Use Stop or wait until it finishes.",
            )
            return
        mthr = getattr(self, "_monitor_thread", None)
        if mthr is not None and mthr.is_alive():
            messagebox.showwarning(
                "Reply monitor",
                "The monitor is still finishing. Wait a moment before starting again.",
            )
            return
        self.stop_monitor.clear()
        self._apply_monitor_indicator(True)
        interval = int(self.check_interval.get())

        def worker():
            try:
                run_reply_monitor(
                    webhook,
                    interval,
                    self.log,
                    self.stop_monitor,
                    self.seen_ids,
                    accounts=monitor_accounts,
                )
            finally:
                self.after(0, lambda: self._apply_monitor_indicator(False))

        self._monitor_thread = threading.Thread(target=worker, daemon=True)
        self._monitor_thread.start()

    def _stop_monitor(self):
        self.stop_monitor.set()
        if self._monitor_busy:
            self.log("⏹ Stop requested — reply monitor will stop after the current inbox pass / wait.")

