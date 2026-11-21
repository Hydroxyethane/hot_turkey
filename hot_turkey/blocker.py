"""Blocking engine: hosts-file website blocking and app blocking.

Website blocking rewrites the Windows hosts file (requires write access;
run as administrator for a reliable block). App blocking polls running
processes and kills those whose name matches the blocked list - no
installer or admin rights required.

Design notes:
  * Website blocking is idempotent: applying a block always strips our
    existing marker block and re-adds the current list.
  * Restoring the file only removes our marker block; the user's own
    entries are never touched.
  * On first write a backup of the original hosts file is kept in the
    app data folder.
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional

from hot_turkey.config import (
    app_data_dir,
    normalize_app,
    normalize_site,
)

MARKER_START = "# >>> Hot Turkey block start >>>"
MARKER_END = "# <<< Hot Turkey block end <<<"

BACKUP_FILENAME = "hosts.backup"


def hosts_file_path() -> Path:
    if os.name != "nt":
        raise RuntimeError("Website blocking requires Windows (hosts file).")
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / r"System32" / "drivers" / "etc" / "hosts"


def blocked_domains(sites: Iterable[str]) -> List[str]:
    """Normalized, deduplicated domain list for the hosts block.

    For each site we cover the bare host, ``www.`` and ``m.`` variants, so
    the common subdomains are caught even though hosts files have no wildcards.
    """
    domains: set[str] = set()
    for raw in sites:
        site = normalize_site(raw)
        if not site:
            continue
        domains.add(site)
        domains.add(f"www.{site}")
        domains.add(f"m.{site}")
    return sorted(domains)


def hosts_block_text(sites: Iterable[str]) -> str:
    domains = blocked_domains(sites)
    if not domains:
        return ""
    lines = [MARKER_START] + [f"127.0.0.1 {d}" for d in domains] + [MARKER_END]
    return "\n".join(lines) + "\n"


def strip_own_block(text: str) -> str:
    """Remove our marker block (and stray markers) from hosts text."""
    out: list[str] = []
    skipping = False
    for line in text.splitlines():
        if MARKER_START in line:
            skipping = True
            continue
        if MARKER_END in line:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    # collapse 3+ consecutive blank lines into one, trim edges
    cleaned: list[str] = []
    blanks = 0
    for line in out:
        if line.strip() == "":
            blanks += 1
            if blanks >= 2:
                continue
            cleaned.append("")
        else:
            blanks = 0
            cleaned.append(line)
    return "\n".join(cleaned).strip("\n") + "\n" if cleaned else ""


def _write_text(path: Path, text: str) -> None:
    newline = "\r\n" if os.name == "nt" else "\n"
    path.write_text(text, encoding="utf-8", newline=newline)


def _ensure_backup(path: Path) -> None:
    try:
        if not path.exists():
            return
        backup = app_data_dir() / BACKUP_FILENAME
        if backup.exists():
            return
        app_data_dir().mkdir(parents=True, exist_ok=True)
        backup.write_bytes(path.read_bytes())
    except OSError:
        pass


def needs_write(original: str, new: str) -> bool:
    """True when the file content must change (ignore trailing-newline noise)."""
    return original.strip("\n") != new.strip("\n")


def apply_host_block(sites: Iterable[str], path: Optional[Path] = None) -> int:
    """Write the block to the hosts file. Returns number of domains blocked.

    Idempotent: the previous block is stripped before re-adding.
    Raises RuntimeError/OSError on failure (e.g. missing admin rights).
    """
    path = Path(path) if path else hosts_file_path()
    sites = list(sites)
    original = ""
    if path.exists():
        try:
            original = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise RuntimeError(f"cannot read hosts file: {exc}") from exc

    domains = blocked_domains(sites)
    cleaned = strip_own_block(original)

    content = ""
    if domains:
        block = hosts_block_text(sites)
        content = cleaned
        if content and not content.endswith("\n\n"):
            content = content.rstrip("\n") + "\n\n"
        content += block
    else:
        content = cleaned

    if not needs_write(original, content):
        return len(domains)

    if domains:
        _ensure_backup(path)

    try:
        _write_text(path, content)
    except OSError as exc:
        raise RuntimeError(f"cannot write hosts file (run as administrator?): {exc}") from exc
    return len(domains)


def remove_host_block(path: Optional[Path] = None) -> bool:
    """Strip our block from the hosts file. Returns True if anything changed."""
    path = Path(path) if path else hosts_file_path()
    if not path.exists():
        return False
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeError(f"cannot read hosts file: {exc}") from exc
    cleaned = strip_own_block(original)
    if cleaned == original:
        return False
    try:
        _write_text(path, cleaned)
    except OSError as exc:
        raise RuntimeError(f"cannot write hosts file: {exc}") from exc
    return True


# --- app blocking ------------------------------------------------------------

_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def _running_processes() -> list[dict]:
    """Return [{'pid': int, 'name': str}, ...] for running processes."""
    if os.name != "nt":
        raise RuntimeError("App blocking requires Windows (tasklist).")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", _NO_WINDOW)
    out = subprocess.check_output(
        ["tasklist", "/fo", "csv", "/nh"],
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        timeout=15,
    )
    rows: list[dict] = []
    for parts in csv.reader(out.splitlines()):
        if len(parts) < 2:
            continue
        name = parts[0].strip().lower()
        pid_raw = parts[1].strip().split("\x00")[0]
        m = re.match(r"\d+", pid_raw)
        if not m or not name:
            continue
        rows.append({"pid": int(m.group()), "name": name})
    return rows


def _kill_pid(pid: int) -> bool:
    if pid == os.getpid():
        return False  # never kill ourselves
    if os.name != "nt":
        return False
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", _NO_WINDOW)
    try:
        subprocess.check_call(
            ["taskkill", "/F", "/PID", str(pid)],
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def kill_apps(apps: Iterable[str]) -> List[str]:
    """Kill every running process whose name is in ``apps``.

    Returns the sorted list of exe names that were killed.
    """
    wanted = {normalize_app(a) for a in apps if normalize_app(a)}
    if not wanted:
        return []
    killed: set[str] = set()
    for proc in _running_processes():
        name = normalize_app(proc["name"])
        if name in wanted and _kill_pid(proc["pid"]):
            killed.add(name)
    return sorted(killed)
