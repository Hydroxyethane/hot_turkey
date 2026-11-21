"""Hot Turkey: configuration management.

Platform independent. Config lives in a per-user folder:

  * Windows:  %APPDATA%\\HotTurkey\\config.json
  * otherwise: ~/.config/hot_turkey/config.json  (dev / tests)

A malformed config is never fatal: it is backed up and replaced with
defaults, so the app always starts in a sane state.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

APP_NAME = "Hot Turkey"
APP_DIR_NAME = "HotTurkey"
CONFIG_FILENAME = "config.json"
STATE_FILENAME = "state.json"

DEFAULT_SITES = [
    "youtube.com",
    "reddit.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "facebook.com",
    "tiktok.com",
    "twitch.tv",
    "netflix.com",
    "tumblr.com",
]

DEFAULT_APPS = [
    "steam.exe",
    "discord.exe",
    "epicgameslauncher.exe",
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "opera.exe",
    "brave.exe",
]

DEFAULT_SCHEDULES: list[dict] = [
    {"enabled": False, "days": [0, 1, 2, 3, 4], "start": "17:00", "end": "22:00"},
]

DEFAULT_CONFIG = {
    "version": 1,
    "sites": list(DEFAULT_SITES),
    "apps": list(DEFAULT_APPS),
    "schedules": [dict(s) for s in DEFAULT_SCHEDULES],
    "quit_password": "",  # optional; empty = plain confirmation dialog
    "autostart": False,
}

# --- path helpers -----------------------------------------------------------


def app_data_dir() -> Path:
    """Directory holding the config file for this platform."""
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if not base:
            base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return Path(base) / APP_DIR_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return Path(base) / "hot_turkey"


def config_path() -> Path:
    return app_data_dir() / CONFIG_FILENAME


def state_path() -> Path:
    return app_data_dir() / STATE_FILENAME


# --- normalizers / validators ------------------------------------------------

_DOMAIN_RE = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
_APP_RE = re.compile(r"^[a-z0-9 ._\-]+$")


def normalize_site(value: Any) -> str:
    """Normalize a site entry to a bare lowercase host (e.g. 'youtube.com').

    Accepts full URLs, 'www.', trailing slashes and whitespace. Returns
    an empty string when the input cannot be a bare domain.
    """
    if not isinstance(value, str):
        return ""
    s = value.strip().lower()
    if not s:
        return ""
    s = s.removeprefix("http://").removeprefix("https://")
    s = s.removeprefix("www.")
    s = s.split("/")[0].split("?")[0].split("#")[0].split(":")[0]
    return s if _DOMAIN_RE.match(s) else ""


def normalize_app(value: Any) -> str:
    """Normalize an app entry to a lowercase exe name (e.g. 'steam.exe').

    Accepts a bare name ('steam'), an exe name ('Steam.exe') or a path
    ('D:\\Games\\Steam\\steam.exe'). Returns '' when unusable.
    """
    if not isinstance(value, str):
        return ""
    s = value.strip().lower()
    if not s:
        return ""
    s = s.replace("/", "\\").split("\\")[-1]
    if not s.endswith(".exe"):
        s = s + ".exe"
    if not _APP_RE.match(s):
        return ""
    return s


def parse_hhmm(value: Any) -> tuple[int, int]:
    """Parse 'HH:MM' -> (hour, minute). Raises ValueError on bad input."""
    if not isinstance(value, str):
        raise ValueError(f"expected 'HH:MM' string, got {value!r}")
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
    if not m:
        raise ValueError(f"invalid time {value!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        raise ValueError(f"invalid time {value!r}")
    return h, mi


def normalize_schedule(raw: Any) -> Optional[dict]:
    """Normalize one schedule entry; returns None if it is unusable."""
    if not isinstance(raw, dict):
        return None
    try:
        start = raw.get("start")
        end = raw.get("end")
        parse_hhmm(start)
        parse_hhmm(end)
        days = {int(d) for d in raw.get("days", [])}
        days = {d for d in days if 0 <= d <= 6}
        if not days:
            return None
        return {
            "enabled": bool(raw.get("enabled", False)),
            "days": sorted(days),
            "start": f"{parse_hhmm(start)[0]:02d}:{parse_hhmm(start)[1]:02d}",
            "end": f"{parse_hhmm(end)[0]:02d}:{parse_hhmm(end)[1]:02d}",
        }
    except (TypeError, ValueError):
        return None


def normalize_config(raw: Any) -> dict:
    """Coerce arbitrary input into a valid config dict (never raises)."""
    cfg = dict(DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return cfg

    sites = [normalize_site(v) for v in raw.get("sites", [])] if isinstance(raw.get("sites"), list) else []
    sites = list(dict.fromkeys(s for s in sites if s))
    cfg["sites"] = sites

    apps = [normalize_app(v) for v in raw.get("apps", [])] if isinstance(raw.get("apps"), list) else []
    apps = list(dict.fromkeys(a for a in apps if a))
    cfg["apps"] = apps

    schedules = []
    for s in raw.get("schedules", []) if isinstance(raw.get("schedules"), list) else []:
        ns = normalize_schedule(s)
        if ns:
            schedules.append(ns)
    cfg["schedules"] = schedules

    password = raw.get("quit_password")
    cfg["quit_password"] = password if isinstance(password, str) else ""
    cfg["autostart"] = bool(raw.get("autostart", False))
    return cfg


# --- load / save -------------------------------------------------------------


def load_config(path: Optional[Path] = None) -> dict:
    """Load config from disk, falling back to defaults (and writing them)."""
    path = Path(path) if path else config_path()
    if not path.exists():
        cfg = normalize_config(None)
        try:
            save_config(cfg, path)
        except OSError:
            pass
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        # Never brick the app on a corrupt file: back it up and start fresh.
        try:
            backup = path.with_suffix(".broken.json")
            shutil.copy2(path, backup)
        except OSError:
            pass
        cfg = normalize_config(None)
        try:
            save_config(cfg, path)
        except OSError:
            pass
        return cfg
    return normalize_config(raw)


def save_config(cfg: dict, path: Optional[Path] = None) -> None:
    path = Path(path) if path else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalize_config(cfg), indent=2), encoding="utf-8")


# --- active-session state ----------------------------------------------------


def load_state(path: Optional[Path] = None) -> Optional[dict]:
    path = Path(path) if path else state_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("mode") not in ("timed", "unlimited", "schedule"):
        return None
    if "started_at" not in raw:
        return None
    return raw


def save_state(state: dict, path: Optional[Path] = None) -> None:
    path = Path(path) if path else state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def clear_state(path: Optional[Path] = None) -> None:
    path = Path(path) if path else state_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
