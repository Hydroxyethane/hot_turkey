"""Startup (autostart) management via the Windows Run key.

Adds/removes a per-user Run entry so Hot Turkey launches at sign-in without
needing administrator rights. Uses ``pythonw.exe`` (when available) so no
console window pops up at startup.
"""

from __future__ import annotations

import os
import sys

RUN_KEY_SUBPATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
KEY_NAME = "HotTurkey"


def preferred_interpreter(exe: str | None = None) -> str:
    """Return the exe to launch with (prefer pythonw.exe to avoid a console)."""
    exe = exe or sys.executable
    if not exe:
        return "pythonw.exe"
    if exe.lower().endswith("pythonw.exe"):
        return exe
    sibling = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(sibling):
        return sibling
    return exe


def autostart_command(script_path: str, exe: str | None = None) -> str:
    """Build the full command line that starts the app."""
    interpreter = preferred_interpreter(exe)
    script = os.path.abspath(script_path)
    return f'"{interpreter}" "{script}"'


def _winreg():
    if os.name != "nt":
        raise RuntimeError("Autostart is only available on Windows.")
    import winreg

    return winreg


def autostart_enabled() -> bool:
    """True when the Run entry exists (does not validate the command)."""
    if os.name != "nt":
        return False
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_SUBPATH, 0, winreg.KEY_READ
        ) as key:
            value, _ = winreg.QueryValueEx(key, KEY_NAME)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool, script_path: str) -> None:
    """Enable or disable the per-user autostart Run entry."""
    winreg = _winreg()
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        RUN_KEY_SUBPATH,
        0,
        winreg.KEY_READ | winreg.KEY_SET_VALUE,
    ) as key:
        if enabled:
            winreg.SetValueEx(
                key, KEY_NAME, 0, winreg.REG_SZ, autostart_command(script_path)
            )
        else:
            try:
                winreg.DeleteValue(key, KEY_NAME)
            except OSError:
                pass
