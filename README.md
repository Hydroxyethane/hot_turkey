# Hot Turkey

A Cold Turkey–style blocker for **Windows 11**. It locks down time by
blocking websites (via the hosts file) and apps (via a process-kill loop)
for a chosen duration — or automatically on a schedule — and can keep
blocking after a restart.

> Hot Turkey does **not** pretend to be Cold Turkey. It is an independent,
> open-source-style blocker with the same core ideas: timed blocks,
> schedules, password-protected stop, and Windows startup support.

## Features

- **Timed blocks** — 15 min / 30 min / 1 h / 2 h / 4 h / custom (`1:30`, `45m`, `120`)
- **Schedules** — block automatically Mon–Sun, overnight windows included
- **Website blocking** — rewrites `C:\Windows\System32\drivers\etc\hosts`
  (`www.` and `m.` subdomains covered), strips only its own marker block,
  and restores the file on stop
- **App blocking** — polls running processes every 1.5 s and kills any whose
  name is on the blocked list (`kill steam.exe`, even if it relaunches)
- **Autostart** — one toggle in the app or `install.bat` to launch at sign-in
- **Anti-bypass** — optional block-stop password (set in Settings)
- **Session restore** — an active block survives reopening the app

## Requirements

- Windows 11
- Python 3.11+ ([python.org](https://www.python.org/downloads/) — check
  **“Add python.exe to PATH”**). Tkinter ships with the python.org installer.

No other dependencies — stdlib only.

## Quick start

1. Install Python 3.11+ (with “Add python.exe to PATH”).
2. Double-click **`install.bat`** — creates a Desktop shortcut and enables
   autostart. (Or just run `run_hot_turkey.bat` and turn on autostart from
   the app later.)
3. Launch from the desktop shortcut — you’ll see the dark GUI.
4. Click a time preset (e.g. **1 hour**), or set **Websites** / **Apps** /
   **Schedules** first, then block.
5. To stop a block, click **Stop Block** (asks for the password if you set
   one in Settings).

### Website blocking needs admin rights

Editing the hosts file requires administrative access. Hot Turkey can:

- run normally and block **apps** (no rights needed) — website blocking will
  show a warning; or
- **Relaunch as administrator** (Settings tab → “Relaunch as administrator”,
  or right-click the desktop shortcut → **Run as administrator**).

The **Scheduler** blocks at startup even if you close the app (autostart).

### Optional: password-protected stop

Settings tab → *Block-stop password*. Once set, stopping the block or quitting
Hot Turkey requires typing it — the same idea as Cold Turkey’s key-bypass.
The password is stored as plain text in your per-user config file
(`%APPDATA%\HotTurkey\config.json`, your own account) — it is a deterrent,
not a security perimeter.

## Configuration file

`%APPDATA%\HotTurkey\config.json`:

```json
{
  "sites": ["youtube.com", "reddit.com"],
  "apps": ["steam.exe", "discord.exe"],
  "schedules": [
    { "enabled": true, "days": [0, 1, 2, 3, 4], "start": "17:00", "end": "22:00" }
  ],
  "quit_password": "",
  "autostart": false
}
```

`days` uses Python weekday numbers (0 = Monday … 6 = Sunday).
`start > end` means an overnight window (e.g. `22:00`–`07:00`).

An `hosts.backup` copy is kept next to it (useful if you ever need the
pre-block hosts file). **Do not** hand-edit `hosts` while a block is active —
the file is rewritten under your feet.

## Building an .exe

```
build_exe.bat
```

Uses PyInstaller (`pip install pyinstaller`) to produce
`dist\Hot Turkey.exe` — a single windowed executable. Distribute the exe and
run it as administrator.

## Architecture

```
hot_turkey.py          entry point (pythonw → no console)
hot_turkey/
  config.py            JSON load/save, normalization, validation, state file
  scheduler.py         pure schedule matching (incl. overnight windows)
  blocker.py           hosts file rewriting + process kill loop
  autostart.py         per-user Run key (HKCU, no admin needed)
  gui.py               Tkinter dark GUI (windows/apps/schedules/settings)
tests/                 unit tests (stdlib unittest)
install.bat / .ps1     desktop shortcut + autostart
uninstall.bat          removes shortcut + autostart entry
build_exe.bat          PyInstaller .exe builder
run_hot_turkey.bat     no-console launcher
```

Design notes:

- The monitor thread only touches the hosts file and running processes; the
  UI stays on the Tk mainloop. All GUI updates flow through a queue.
- Hosts blocking is idempotent: applying a block always strips the previous
  marker block and re-adds the current list.
- Scheduler blocking is re-evaluated every 15 s; stale sessions are restored
  from `state.json` on relaunch.
- Quitting stops the active block (with password confirmation if one is set)
  — a block never outlives the app unintentionally. Startup + schedules
  cover the “block across reboots” case.

## Development

```
python -m unittest discover -s tests -t . -v
```

The pure logic (config, scheduler, hosts text handling) is platform
independent and runs anywhere; the hosts-file and process-kill parts are
Windows-only and raise/skip cleanly elsewhere.

## License

MIT
