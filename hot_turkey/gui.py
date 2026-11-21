"""Hot Turkey GUI (Tkinter).

Cold Turkey-style blocker for Windows 11:
  * Website blocking via the Windows hosts file
  * App blocking via a process-kill loop
  * Timed sessions, unlimited sessions and daily schedules
  * Optional password-protected stop (anti-bypass)
  * Start-with-Windows autostart (per-user Run key)

Threading: a background thread runs the app-blocking sweep; the Tk
mainloop stays on the user's thread. All GUI updates flow through a
queue drained by the periodic refresh.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, ttk

    TK_AVAILABLE = True
except ImportError:  # e.g. minimal Linux boxes; the app targets Windows 11
    tk = None
    ttk = None
    messagebox = None
    TK_AVAILABLE = False

from hot_turkey import autostart, blocker
from hot_turkey.config import (
    app_data_dir,
    clear_state,
    load_config,
    load_state,
    normalize_app,
    normalize_site,
    parse_hhmm,
    save_config,
    save_state,
)
from hot_turkey.scheduler import any_schedule_active, describe_schedule

try:
    import ctypes
except ImportError:  # pragma: no cover
    ctypes = None

SCRIPT_PATH = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else Path("hot_turkey.py")

# --- theme --------------------------------------------------------------------
BG = "#14161f"
PANEL = "#1e2233"
PANEL_ALT = "#262b3d"
LINE = "#2c3247"
FG = "#e8eaf2"
MUTED = "#9aa3bb"
ACCENT = "#ff7a3d"
ACCENT_DARK = "#e05a1d"
GOOD = "#3ecf8e"
BAD = "#ff5a5a"
WARN = "#ffc857"

LOG_MAX_LINES = 400


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class InstanceGuard:
    """Stops two instances from fighting over the same hosts file."""

    def __init__(self, name: str):
        self._mutex = None
        self.already_running = False
        if os.name == "nt" and ctypes is not None:
            k32 = ctypes.windll.kernel32
            self._mutex = k32.CreateMutexW(None, False, f"HotTurkey_{name}")
            # ERROR_ALREADY_EXISTS == 183
            self.already_running = k32.GetLastError() == 183

    def release(self):
        if self._mutex is not None and ctypes is not None:
            try:
                ctypes.windll.kernel32.CloseHandle(self._mutex)
            except Exception:
                pass
            self._mutex = None


def parse_duration(text):
    """Parse '45m', '2h', '1:30' or bare minutes -> seconds (None on failure)."""
    s = (text or "").strip().lower()
    if not s:
        return None
    try:
        if ":" in s:
            parts = s.split(":")
            if len(parts) != 2:
                return None
            h, m = int(parts[0]), int(parts[1])
            if h > 99 or m > 59:
                return None
            return h * 3600 + m * 60
        if s.endswith("m"):
            return int(s[:-1]) * 60
        if s.endswith("h"):
            return int(s[:-1]) * 3600
        return int(s) * 60
    except (ValueError, TypeError):
        return None


def format_remaining(seconds: float) -> str:
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _default_sites():
    from hot_turkey.config import DEFAULT_SITES

    return list(DEFAULT_SITES)


def _default_apps():
    from hot_turkey.config import DEFAULT_APPS

    return list(DEFAULT_APPS)


class HotTurkeyApp:
    """Owns config, session state, the monitor thread and the Tk UI."""

    def __init__(self):
        if not TK_AVAILABLE:
            raise RuntimeError(
                "Tkinter is not available. On Windows 11 install Python 3.11+ "
                "from python.org (it bundles Tkinter)."
            )
        self.events: queue.Queue = queue.Queue()
        self.config = load_config()
        self.state: dict | None = None
        self.hosts_ok = False
        self._stop_monitor = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._last_schedule_check = 0.0
        self._kill_log_last: dict[str, float] = {}
        self._guard = InstanceGuard("v1")

        self.root = tk.Tk()
        self.root.title("Hot Turkey — Cold Turkey-style blocker")
        self.root.geometry("980x780")
        self.root.minsize(860, 660)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_quit)
        self._set_window_icon()

        if self._guard.already_running:
            messagebox.showerror(
                "Hot Turkey",
                "Hot Turkey is already running.\n"
                "Stop the existing instance first (or close its window).",
            )
            self.root.destroy()
            self._guard.release()
            sys.exit(1)

        self._build_ui()
        self._cleanup_hosts_on_start()
        self._restore_or_schedule_start()
        self._tick()

    def _set_window_icon(self):
        icon = Path(__file__).resolve().parent.parent / "assets" / "hot_turkey.ico"
        try:
            if icon.exists():
                self.root.iconbitmap(str(icon))
        except Exception:
            pass  # icon is cosmetic; never block startup on it

    # -- UI construction -----------------------------------------------------

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                        padding=(18, 10), font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", PANEL_ALT)],
                  foreground=[("selected", FG)])
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), background=ACCENT,
                        foreground="#1a0d06", borderwidth=0, padding=(14, 8))
        style.map("Accent.TButton", background=[("active", ACCENT_DARK)],
                  foreground=[("disabled", MUTED)])
        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"), background=BAD,
                        foreground="#2b0d0d", borderwidth=0, padding=(14, 8))
        style.map("Danger.TButton", background=[("active", "#d94a4a")],
                  foreground=[("disabled", MUTED)])
        style.configure("TButton", font=("Segoe UI", 10, "bold"), background=PANEL_ALT,
                        foreground=FG, borderwidth=0, padding=(14, 8))
        style.map("TButton", background=[("active", "#333a55"), ("disabled", PANEL)],
                  foreground=[("disabled", MUTED)])
        style.configure("Light.TButton", font=("Segoe UI", 10), background=PANEL_ALT,
                        foreground=FG, borderwidth=0, padding=(12, 6))
        style.map("Light.TButton", background=[("active", "#333a55")],
                  foreground=[("disabled", MUTED)])
        style.configure("TEntry", font=("Segoe UI", 10), fieldbackground=PANEL_ALT,
                        foreground=FG, borderwidth=0, insertcolor=FG, padding=(8, 6))
        style.configure("TProgressbar", thickness=10, background=ACCENT,
                        troughcolor=PANEL_ALT, borderwidth=0, lightcolor=ACCENT,
                        darkcolor=ACCENT)
        style.configure("TCheckbutton", background=BG, foreground=FG,
                        font=("Segoe UI", 10), padding=(0, 0))
        style.map("TCheckbutton", background=[("active", BG)], foreground=[("disabled", MUTED)])

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        self._build_header()
        self._build_countdown()
        self._build_tabs()
        self._build_log()

    # --- header --------------------------------------------------------------

    def _build_header(self):
        header = ttk.Frame(self.root, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        header.columnconfigure(0, weight=1)

        title_box = ttk.Frame(header, style="Card.TFrame")
        title_box.grid(row=0, column=0, sticky="w", padx=(16, 0), pady=(12, 10))
        tk.Label(title_box, text="HOT TURKEY", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(title_box, text="Cold Turkey-style blocker for Windows 11",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        self._admin_badge = tk.Label(
            header,
            text="Administrator" if is_admin() else "Standard user",
            bg=(GOOD if is_admin() else WARN), fg="#1a0d06",
            font=("Segoe UI", 8, "bold"), padx=10, pady=3,
        )
        self._admin_badge.grid(row=0, column=1, padx=(8, 16), sticky="ne", pady=(14, 0))

        self._quit_btn = ttk.Button(header, text="Quit", command=self._on_quit)
        self._quit_btn.grid(row=1, column=1, padx=(0, 16), sticky="e", pady=(0, 12))

    # --- countdown -----------------------------------------------------------

    def _build_countdown(self):
        card = ttk.Frame(self.root, style="Card.TFrame")
        card.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        card.columnconfigure(0, weight=1)

        inner = ttk.Frame(card, style="Card.TFrame")
        inner.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 4))

        self._status_pill = tk.Label(
            inner, text="IDLE", bg=PANEL, fg=GOOD,
            font=("Segoe UI", 10, "bold"), padx=12, pady=3,
        )
        self._status_pill.grid(row=0, column=0, sticky="w")

        self._countdown = tk.Label(
            inner, text="READY", bg=PANEL, fg=FG,
            font=("Segoe UI", 34, "bold"), anchor="w",
        )
        self._countdown.grid(row=1, column=0, sticky="w", pady=(4, 2))

        self._countdown_sub = tk.Label(
            inner, text="Block sites and apps below to lock down time.",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w",
        )
        self._countdown_sub.grid(row=2, column=0, sticky="w")

        self._progress = ttk.Progressbar(card, mode="determinate", maximum=100)
        self._progress.grid(row=1, column=0, sticky="ew", padx=18, pady=(4, 0))
        self._progress.grid_remove()

        buttons = ttk.Frame(card, style="Card.TFrame")
        buttons.grid(row=2, column=0, sticky="ew", padx=18, pady=(10, 8))
        buttons.columnconfigure(6, weight=1)
        presets = [
            ("15 min", 15 * 60),
            ("30 min", 30 * 60),
            ("1 hour", 60 * 60),
            ("2 hours", 2 * 3600),
            ("4 hours", 4 * 3600),
        ]
        for i, (label, seconds) in enumerate(presets):
            ttk.Button(buttons, text=label, style="Accent.TButton",
                       command=lambda s=seconds: self._start_timed(s)).grid(
                row=0, column=i, padx=(0, 6))
        self._custom_entry = ttk.Entry(buttons, width=12)
        self._custom_entry.grid(row=0, column=5, padx=(8, 6))
        self._custom_entry.bind("<Return>", lambda _e: self._start_custom())
        ttk.Button(buttons, text="Block", style="Accent.TButton",
                   command=self._start_custom).grid(row=0, column=6, padx=(0, 6))
        ttk.Button(buttons, text="Stop Block", style="Danger.TButton",
                   command=self._stop_block_with_guard).grid(row=0, column=7)

        self._custom_hint = ttk.Label(buttons, text="Custom: HH:MM (like 1:30) or minutes",
                                      style="TLabel")
        self._custom_hint.grid(row=1, column=0, columnspan=8, sticky="w", pady=(6, 0))

    # --- tabs ----------------------------------------------------------------

    def _build_tabs(self):
        self._notebook = ttk.Notebook(self.root)
        self._notebook.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 8))

        self._sites_tab = ttk.Frame(self._notebook, style="TFrame")
        self._apps_tab = ttk.Frame(self._notebook, style="TFrame")
        self._schedule_tab = ttk.Frame(self._notebook, style="TFrame")
        self._settings_tab = ttk.Frame(self._notebook, style="TFrame")
        self._notebook.add(self._sites_tab, text="Websites")
        self._notebook.add(self._apps_tab, text="Apps")
        self._notebook.add(self._schedule_tab, text="Schedules")
        self._notebook.add(self._settings_tab, text="Settings")

        self._build_list_tab(self._sites_tab, "site")
        self._build_list_tab(self._apps_tab, "app")
        self._build_schedules_tab(self._schedule_tab)
        self._build_settings_tab(self._settings_tab)

    def _build_list_tab(self, parent, kind):
        """kind: 'site' or 'app' — shared layout for the two list tabs."""
        left = ttk.Frame(parent, style="TFrame")
        left.pack(side="left", fill="both", expand=True, padx=(16, 8), pady=16)

        listbox = tk.Listbox(
            left, bg=PANEL, fg=FG, selectbackground=ACCENT, selectforeground="#1a0d06",
            borderwidth=0, highlightthickness=0, font=("Segoe UI", 10),
            relief="flat", activestyle="none", height=12,
        )
        scroll = ttk.Scrollbar(left, orient="vertical", command=listbox.yview)
        listbox.config(yscrollcommand=scroll.set)
        listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        right = ttk.Frame(parent, style="TFrame")
        right.pack(side="left", fill="y", padx=(0, 16), pady=(16, 0))

        entry = ttk.Entry(right, width=24)
        entry.pack(fill="x", pady=(0, 8))
        if kind == "site":
            entry.bind("<Return>", lambda _e: self._add_site())
            ttk.Button(right, text="Add", style="Accent.TButton",
                       command=self._add_site).pack(fill="x", pady=(0, 6))
            ttk.Button(right, text="Remove", command=self._remove_site).pack(fill="x", pady=(0, 6))
            ttk.Button(right, text="Reset", style="Light.TButton",
                       command=self._reset_sites).pack(fill="x")
            self._site_entry = entry
            self._site_listbox = listbox
        else:
            entry.bind("<Return>", lambda _e: self._add_app())
            ttk.Button(right, text="Add", style="Accent.TButton",
                       command=self._add_app).pack(fill="x", pady=(0, 6))
            ttk.Button(right, text="Remove", command=self._remove_app).pack(fill="x", pady=(0, 6))
            ttk.Button(right, text="Reset", style="Light.TButton",
                       command=self._reset_apps).pack(fill="x")
            self._app_entry = entry
            self._app_listbox = listbox

        hint = ("Sites are blocked in the Windows hosts file (127.0.0.1). "
                "Handles www. and m. subdomains; requires administrator rights."
                if kind == "site" else
                "Enter the exe name, e.g. steam.exe. Apps are killed whenever they run — "
                "checked every 1.5 seconds.")
        under = ttk.Frame(parent, style="TFrame")
        under.pack(fill="x", padx=20)
        ttk.Label(under, text=hint, style="TLabel", wraplength=860).pack(anchor="w", pady=(0, 8))

        if kind == "site":
            self._refresh_site_list()
        else:
            self._refresh_app_list()

    def _build_schedules_tab(self, parent):
        header = ttk.Frame(parent, style="TFrame")
        header.pack(fill="x", padx=16, pady=(12, 4))
        ttk.Label(
            header,
            text="Block automatically during these windows. Overnight windows "
            "(e.g. 22:00–07:00) work too.",
            style="TLabel", wraplength=860,
        ).pack(anchor="w", pady=(0, 8))
        ttk.Button(
            header, text="+ Add schedule", style="Accent.TButton",
            command=self._add_schedule,
        ).pack(anchor="w")

        self._schedule_rows = ttk.Frame(parent, style="TFrame")
        self._schedule_rows.pack(fill="both", expand=True, padx=16, pady=(8, 16))
        self._refresh_schedules_tab()

    def _build_settings_tab(self, parent):
        wrap = ttk.Frame(parent, style="TFrame")
        wrap.pack(fill="x", padx=20, pady=18)

        ttk.Label(wrap, text="Autostart", style="TLabel",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self._autostart_var = tk.BooleanVar(value=autostart.autostart_enabled())
        self._autostart_check = ttk.Checkbutton(
            wrap, text="Start Hot Turkey with Windows at sign-in",
            variable=self._autostart_var, command=self._on_autostart_toggle,
        )
        self._autostart_check.pack(anchor="w", pady=(8, 18))

        ttk.Label(wrap, text="Block-stop password", style="TLabel",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(wrap, text="If set, stopping the block (or quitting) requires this password "
                  "— the Cold Turkey anti-bypass trick. Plain text, not a security perimeter.",
                  style="TLabel", wraplength=860).pack(anchor="w", pady=(4, 8))
        pw_row = ttk.Frame(wrap, style="TFrame")
        pw_row.pack(anchor="w")
        self._pw_entry = ttk.Entry(pw_row, show="*", width=36)
        self._pw_entry.pack(side="left", padx=(0, 10))
        self._pw_entry.bind("<Return>", lambda _e: self._set_password())
        ttk.Button(pw_row, text="Set password", command=self._set_password).pack(side="left", padx=(0, 6))
        ttk.Button(pw_row, text="Clear", style="Light.TButton",
                   command=self._clear_password).pack(side="left")

        ttk.Label(wrap, text="Housekeeping", style="TLabel",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(18, 4))
        ttk.Label(wrap, text="The hosts file can be restored at any time — your own entries "
                  "are left untouched.", style="TLabel", wraplength=860).pack(anchor="w")
        house = ttk.Frame(wrap, style="TFrame")
        house.pack(anchor="w", pady=(8, 0))
        ttk.Button(house, text="Clean up hosts file", command=self._clean_hosts_now).pack(side="left", padx=(0, 8))
        ttk.Button(house, text="Open app data folder", style="Light.TButton",
                   command=self._open_app_data).pack(side="left")

        if not is_admin():
            self._admin_row = ttk.Frame(wrap, style="TFrame")
            self._admin_row.pack(anchor="w", pady=(18, 0))
            ttk.Label(self._admin_row, text="Website blocking needs administrator rights (hosts file):",
                      style="TLabel").pack(side="left", padx=(0, 10))
            ttk.Button(self._admin_row, text="Relaunch as administrator",
                       command=self._relaunch_as_admin).pack(side="left")

        info = (f"Python: {sys.executable}\n"
                f"Config: {app_data_dir()}\n"
                f"Admin: {'yes' if is_admin() else 'no'}")
        tk.Label(wrap, text=info, justify="left", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(16, 0))

    def _build_log(self):
        frame = ttk.Frame(self.root, style="Card.TFrame")
        frame.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 12))
        frame.columnconfigure(0, weight=1)

        tk.Label(frame, text="ACTIVITY LOG", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))

        self._log_text = tk.Text(
            frame, height=7, bg=PANEL_ALT, fg=FG, borderwidth=0,
            highlightthickness=0, font=("Consolas", 9), wrap="word",
            state="disabled", spacing1=2, spacing3=2,
        )
        self._log_text.grid(row=1, column=0, sticky="ew", padx=14, pady=(4, 12))

    # -- configuration list tabs ---------------------------------------------

    def _refresh_site_list(self):
        self._site_listbox.delete(0, "end")
        for site in self.config["sites"]:
            self._site_listbox.insert("end", site)

    def _add_site(self):
        raw = self._site_entry.get()
        site = normalize_site(raw)
        if not site:
            self._log(f"Could not read site from {raw!r}", kind="warn")
            return
        if site in self.config["sites"]:
            self._log(f"{site} is already blocked", kind="warn")
            return
        self.config["sites"].append(site)
        self._site_entry.delete(0, "end")
        self._refresh_site_list()
        self._save_and_reapply()
        self._log(f"Added site {site}")

    def _remove_site(self):
        sel = self._site_listbox.curselection()
        if not sel:
            return
        site = self.config["sites"].pop(sel[0])
        self._refresh_site_list()
        self._save_and_reapply()
        self._log(f"Removed site {site}")

    def _reset_sites(self):
        self.config["sites"] = _default_sites()
        self._refresh_site_list()
        self._save_and_reapply()
        self._log("Sites reset to default list")

    def _refresh_app_list(self):
        self._app_listbox.delete(0, "end")
        for app in self.config["apps"]:
            self._app_listbox.insert("end", app)

    def _add_app(self):
        raw = self._app_entry.get()
        app = normalize_app(raw)
        if not app:
            self._log(f"Could not read app from {raw!r}", kind="warn")
            return
        if app in self.config["apps"]:
            self._log(f"{app} is already blocked", kind="warn")
            return
        self.config["apps"].append(app)
        self._app_entry.delete(0, "end")
        self._refresh_app_list()
        self._save_and_reapply()
        self._log(f"Added app {app}")

    def _remove_app(self):
        sel = self._app_listbox.curselection()
        if not sel:
            return
        app = self.config["apps"].pop(sel[0])
        self._refresh_app_list()
        self._save_and_reapply()
        self._log(f"Removed app {app}")

    def _reset_apps(self):
        self.config["apps"] = _default_apps()
        self._refresh_app_list()
        self._save_and_reapply()
        self._log("Apps reset to default list")

    # --- schedules ------------------------------------------------------------

    def _refresh_schedules_tab(self):
        for widget in self._schedule_rows.winfo_children():
            widget.destroy()
        if not self.config["schedules"]:
            ttk.Label(self._schedule_rows,
                      text="No schedules yet. Click “+ Add schedule” to create one.",
                      style="TLabel").pack(anchor="w", pady=8)
            return
        for idx, schedule in enumerate(self.config["schedules"]):
            self._build_schedule_row(idx, schedule)

    def _build_schedule_row(self, idx: int, schedule: dict):
        row = ttk.Frame(self._schedule_rows, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 8))
        row.columnconfigure(0, weight=1)

        var = tk.BooleanVar(value=bool(schedule.get("enabled", False)))

        def toggle_enabled():
            self.config["schedules"][idx]["enabled"] = var.get()
            self._save_and_reapply()
            self._refresh_schedules_tab()

        chk = ttk.Checkbutton(row, text="", variable=var, command=toggle_enabled)
        chk.grid(row=0, column=0, padx=(12, 12), pady=12, sticky="w")

        day_frame = ttk.Frame(row, style="Card.TFrame")
        day_frame.grid(row=0, column=1, padx=(0, 12), pady=10)
        for day in range(7):
            active = day in schedule.get("days", [])
            btn = tk.Label(day_frame, text=DAY_LABELS[day][:2],
                           bg=(ACCENT if active else PANEL_ALT),
                           fg=("#1a0d06" if active else MUTED),
                           font=("Segoe UI", 8, "bold"), padx=6, pady=3, cursor="hand2")
            btn.pack(side="left", padx=2)

            def bind_day(d=day, b=btn):
                def fn(_e=None):
                    sched = self.config["schedules"][idx]
                    days = set(sched.get("days", []))
                    if d in days:
                        days.discard(d)
                    else:
                        days.add(d)
                    sched["days"] = sorted(days)
                    b.config(bg=(ACCENT if d in days else PANEL_ALT),
                             fg=("#1a0d06" if d in days else MUTED))
                    self._save_and_reapply()
                b.bind("<Button-1>", fn)

            bind_day()

        start_var = tk.StringVar(value=schedule.get("start", "17:00"))
        end_var = tk.StringVar(value=schedule.get("end", "22:00"))

        def on_time(*_args):
            sched = self.config["schedules"][idx]
            try:
                parse_hhmm(start_var.get())
                parse_hhmm(end_var.get())
            except ValueError:
                self._log("Scheduled times must be HH:MM", kind="warn")
                return
            sched["start"] = start_var.get().strip()
            sched["end"] = end_var.get().strip()
            self._save_and_reapply()

        time_frame = ttk.Frame(row, style="Card.TFrame")
        time_frame.grid(row=0, column=2, pady=10)
        ttk.Label(time_frame, text="from", style="TLabel").pack(side="left", padx=(0, 4))
        ttk.Entry(time_frame, textvariable=start_var, width=6).pack(side="left")
        ttk.Label(time_frame, text="until", style="TLabel").pack(side="left", padx=(8, 4))
        ttk.Entry(time_frame, textvariable=end_var, width=6).pack(side="left")
        start_var.trace_add("write", on_time)
        end_var.trace_add("write", on_time)

        ttk.Button(row, text="Remove", style="Light.TButton",
                   command=lambda i=idx: self._remove_schedule(i)).grid(
            row=0, column=3, padx=(12, 14), pady=10, sticky="e")

    def _add_schedule(self):
        self.config["schedules"].append(
            {"enabled": True, "days": [0, 1, 2, 3, 4], "start": "17:00", "end": "22:00"}
        )
        self._refresh_schedules_tab()
        self._save_and_reapply()
        self._log("Schedule added")

    def _remove_schedule(self, idx: int):
        schedule = self.config["schedules"].pop(idx)
        if self.state and self.state.get("source") == "schedule":
            self._disengage("schedule removed")
        self._refresh_schedules_tab()
        self._save_and_reapply()
        self._log(f"Schedule removed ({describe_schedule(schedule)})")

    # --- settings --------------------------------------------------------------

    def _on_autostart_toggle(self):
        try:
            autostart.set_autostart(self._autostart_var.get(), str(SCRIPT_PATH))
            self.config["autostart"] = self._autostart_var.get()
            save_config(self.config)
            self._log("Autostart " + ("enabled" if self._autostart_var.get() else "disabled"))
        except Exception as exc:
            self._autostart_var.set(not self._autostart_var.get())  # revert on failure
            self._log(f"Autostart toggle failed: {exc}", kind="warn")

    def _set_password(self):
        pw = self._pw_entry.get()
        if not pw:
            self._log("Enter a password (or use Clear)", kind="warn")
            return
        self.config["quit_password"] = pw
        save_config(self.config)
        self._pw_entry.delete(0, "end")
        self._log("Block-stop password set")

    def _clear_password(self):
        self.config["quit_password"] = ""
        save_config(self.config)
        self._pw_entry.delete(0, "end")
        self._log("Block-stop password cleared")

    def _clean_hosts_now(self):
        try:
            if self.state:
                self._disengage("hosts cleanup")
                self._log("Hosts file cleaned (active block was also stopped)")
            else:
                changed = blocker.remove_host_block()
                self._log("Hosts file cleaned" if changed else "Hosts file already clean")
        except Exception as exc:
            self._log(f"Hosts cleanup failed: {exc}", kind="warn")

    def _open_app_data(self):
        path = app_data_dir()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(path))  # noqa: S606 - user-requested folder
            else:
                subprocess.Popen(["xdg-open", str(path)])  # noqa: S603
        except Exception as exc:
            self._log(f"Could not open folder: {exc}", kind="warn")

    def _relaunch_as_admin(self):
        answer = messagebox.askyesnocancel(
            "Relaunch as administrator",
            "Hot Turkey will close and reopen with administrator rights so "
            "website blocking can work.\n\nA UAC prompt will appear.",
        )
        if answer is not True:
            return
        if os.name != "nt" or ctypes is None:
            self._log("Administrator relaunch is only available on Windows", kind="warn")
            return
        try:
            exe = autostart.preferred_interpreter(sys.executable)
            params = f'"{SCRIPT_PATH}"'
            res = ctypes.windll.shell32.ShellExecuteW(0, "runas", exe, params, None, 1)
            if res <= 32:
                self._log(f"UAC declined or failed (code {res})", kind="warn")
                return
        except Exception as exc:
            self._log(f"Could not relaunch as administrator: {exc}", kind="warn")
            return
        # The elevated copy relaunches and re-engages the same session.
        self._guard.release()
        self.root.destroy()
        os._exit(0)

    # -- persistence ----------------------------------------------------------

    def _save_and_reapply(self):
        try:
            save_config(self.config)
        except Exception as exc:
            self._log(f"Could not save config: {exc}", kind="warn")
        if self.state:
            self._apply_hosts()
            try:
                killed = blocker.kill_apps(self.config["apps"])
                for name in killed:
                    self._log(f"Killed {name} (list changed while blocked)")
            except Exception as exc:
                self._log(f"App sweep failed: {exc}", kind="warn")

    def _apply_hosts(self):
        if os.name != "nt":
            self.hosts_ok = False
            return
        try:
            n = blocker.apply_host_block(self.config["sites"])
            self.hosts_ok = True
            self._log(f"Website block applied — {n} domains")
        except Exception as exc:
            self.hosts_ok = False
            self._log(f"Website blocking unavailable: {exc}", kind="warn")

    def _cleanup_hosts_on_start(self):
        if os.name != "nt":
            return
        try:
            blocker.remove_host_block()
        except Exception:
            pass  # a missing/unreadable hosts file must not stop the app

    def _restore_or_schedule_start(self):
        # 1) restore an unexpired manual session from state.json
        state = load_state()
        if state and state.get("source") in ("manual", "restored"):
            mode = state.get("mode")
            ends_at = state.get("ends_at")
            if mode == "timed":
                remaining = None
                if ends_at and ends_at > time.time():
                    remaining = ends_at - time.time()
                if remaining and remaining > 0:
                    self._engage("restored", "timed", remaining)
                    self._log("Restored an active timed block")
            elif mode == "unlimited":
                self._engage("restored", "unlimited")
                self._log("Restored an active block")
        # 2) if not started yet, engage whatever schedules say
        if not self.state and any_schedule_active(self.config["schedules"]):
            self._engage("schedule", "unlimited")
            self._log("Schedule active — block engaged")

    # -- session control ------------------------------------------------------

    def _engage(self, source: str, mode: str, duration: float | None = None):
        self._stop_monitor()
        now = time.time()
        ends_at = (now + duration) if duration else None
        self.state = {
            "mode": mode,
            "source": source,
            "started_at": now,
            "ends_at": ends_at,
        }
        if source in ("manual", "restored"):
            save_state(self.state)
        self.hosts_ok = False
        self._apply_hosts()
        self._start_monitor()
        self._refresh_ui()

    def _disengage(self, reason: str = "manual"):
        if not self.state:
            return
        self._stop_monitor()
        try:
            blocker.remove_host_block()
            self._log("Hosts file restored")
        except Exception as exc:
            self._log(f"Hosts restore failed: {exc}", kind="warn")
        clear_state()
        self.state = None
        self.hosts_ok = False
        self._refresh_ui()
        self._log(f"Block stopped ({reason})")

    def _start_timed(self, duration: int):
        if duration <= 0:
            return
        minutes = duration // 60
        self._engage("manual", "timed", duration)
        self._log(f"Block started — {minutes} minute{'s' if minutes != 1 else ''}")

    def _start_custom(self):
        duration = parse_duration(self._custom_entry.get())
        if duration is None or duration <= 0:
            self._log("Enter a duration like 45m, 1:30 or 120 (minutes)", kind="warn")
            return
        self._custom_entry.delete(0, "end")
        self._engage("manual", "timed", duration)
        self._log(f"Block started — {duration // 60} minute{'s' if duration // 60 != 1 else ''}")

    def _start_monitor(self):
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _stop_monitor(self):
        self._stop_monitor.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=2)
            self._monitor_thread = None

    def _monitor_loop(self):
        last_hosts_apply = 0.0
        try:
            while not self._stop_monitor.is_set():
                try:
                    killed = blocker.kill_apps(self.config["apps"])
                    for name in killed:
                        self._log_kill(name)
                except Exception as exc:
                    self._log(f"App sweep failed: {exc}", kind="warn")
                now = time.time()
                if now - last_hosts_apply > 10:
                    try:
                        blocker.apply_host_block(self.config["sites"])
                    except Exception as exc:
                        self._log(f"Hosts re-apply failed: {exc}", kind="warn")
                    last_hosts_apply = now
                time.sleep(1.5)
        except Exception:
            traceback.print_exc()

    def _log_kill(self, name: str):
        now = time.time()
        last = self._kill_log_last.get(name, 0.0)
        if now - last < 5:
            return
        self._kill_log_last[name] = now
        self.events.put(("log", f"[{datetime.now().strftime('%H:%M:%S')}] Killed {name}"))

    # -- password guard --------------------------------------------------------

    def _require_password(self, action: str) -> bool:
        password = self.config.get("quit_password", "")
        if not password:
            return True
        entered = _ask_password(self.root, action, password)
        return entered is not None

    # -- stop / quit ------------------------------------------------------------

    def _stop_block_with_guard(self):
        if not self.state:
            self._log("Block is not active", kind="warn")
            return
        if not self._require_password("stop this block"):
            return
        self._disengage("stopped by user")

    def _on_quit(self):
        if not self.state:
            if messagebox.askyesno("Quit Hot Turkey", "Quit Hot Turkey?"):
                self._shutdown()
            return
        if not self._require_password("stop the active block"):
            return
        if messagebox.askyesno(
            "Stop the block?",
            "An active block will stop when you quit.\n\nStop it and quit?",
        ):
            self._disengage("quit")
            self._shutdown()

    def _shutdown(self):
        try:
            self._stop_monitor()
        except Exception:
            pass
        try:
            self.root.destroy()
        finally:
            self._guard.release()
            sys.exit(0)

    # -- refresh ----------------------------------------------------------------

    def _refresh_ui(self):
        if not self.root.winfo_exists():
            return
        state = self.state
        now = time.time()

        if not state:
            self._status_pill.config(text="IDLE", fg=GOOD)
            self._countdown.config(text="READY")
            self._countdown_sub.config(
                text="Sites and apps are not blocked right now."
                + (" Configure schedules below to lock down automatically."
                   if any(s.get("enabled") for s in self.config["schedules"]) else "")
            )
            self._progress.grid_remove()
            return

        self._status_pill.config(text="BLOCKED", fg=ACCENT)
        mode = state["mode"]
        remaining_text = ""
        if mode == "timed" and state.get("ends_at"):
            remaining = state["ends_at"] - now
            if remaining <= 0:
                self._disengage("time finished")
                return
            remaining_text = format_remaining(remaining)
            self._countdown.config(text=remaining_text)
            total = max(1.0, state["ends_at"] - state["started_at"])
            fraction = remaining / total
            self._progress.grid()
            self._progress.configure(value=max(0.0, min(100.0, fraction * 100)))
        else:
            self._countdown.config(text="UNLIMITED")
            self._progress.grid_remove()

        source = state["source"]
        sub = {"manual": "Manual block", "restored": "Restored block",
               "schedule": "Schedule"}.get(source, "Blocked")
        count_sites = len(self.config["sites"])
        count_apps = len(self.config["apps"])
        hosts_note = "" if self.hosts_ok else " — websites NOT blocked (needs admin)"
        if mode == "timed":
            self._countdown_sub.config(
                text=f"{sub} — {remaining_text} remaining · {count_sites} sites · {count_apps} apps{hosts_note}"
            )
        else:
            self._countdown_sub.config(
                text=f"{sub} — unlimited · {count_sites} sites · {count_apps} apps{hosts_note}"
            )

    # -- main loop ----------------------------------------------------------------

    def _tick(self):
        if not self.root.winfo_exists():
            return
        while True:
            try:
                kind, message = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._append_log(message)

        if self.state and self.state.get("mode") == "timed" and self.state.get("ends_at"):
            if time.time() >= self.state["ends_at"]:
                self._disengage("time finished")

        now = time.time()
        if now - self._last_schedule_check > 15:
            self._last_schedule_check = now
            blocked_now = any_schedule_active(self.config["schedules"])
            if blocked_now and not self.state:
                self._engage("schedule", "unlimited")
                self._log("Schedule active — block engaged")
            elif not blocked_now and self.state and self.state.get("source") == "schedule":
                self._disengage("schedule ended")

        self._refresh_ui()
        self.root.after(500, self._tick)

    def _append_log(self, message: str):
        self._log_text.config(state="normal")
        self._log_text.insert("end", message + "\n")
        self._log_text.see("end")
        lines = int(self._log_text.index("end-1c").split(".")[0])
        if lines > LOG_MAX_LINES:
            self._log_text.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
        self._log_text.config(state="disabled")

    def _log(self, message: str, kind: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = "!!" if kind == "warn" else ""
        self.events.put(("log", f"[{timestamp}] {prefix} {message}"))

    def run(self):
        self._log("Hot Turkey started — ready.")
        self.root.mainloop()


def _ask_password(parent, action: str, expected: str):
    """Modal dialog asking for the password; returns True only on a match."""
    result = {}

    def finish(entered: str | None):
        result["value"] = entered
        dlg.destroy()

    dlg = tk.Toplevel(parent)
    dlg.title("Password required")
    dlg.resizable(False, False)
    dlg.grab_set()
    dlg.transient(parent)
    dlg.configure(bg=BG)

    tk.Label(dlg, text="Password required to", bg=BG, fg=FG,
             font=("Segoe UI", 10)).pack(pady=(16, 2), padx=24)
    tk.Label(dlg, text=action, bg=BG, fg=ACCENT,
             font=("Segoe UI", 11, "bold")).pack(padx=24)
    entry = ttk.Entry(dlg, show="*", width=36)
    entry.pack(pady=14, padx=24)
    entry.focus_set()

    buttons = ttk.Frame(dlg, style="TFrame")
    buttons.pack(pady=(0, 18))
    ttk.Button(buttons, text="OK", style="Accent.TButton",
               command=lambda: finish(entry.get())).pack(side="left", padx=6)
    ttk.Button(buttons, text="Cancel", style="Light.TButton",
               command=lambda: finish(None)).pack(side="left", padx=6)
    entry.bind("<Return>", lambda _e: finish(entry.get()))
    dlg.bind("<Escape>", lambda _e: finish(None))

    parent.wait_window(dlg)
    return result.get("value") == expected
