#!/usr/bin/env python3
"""Hot Turkey — Cold Turkey-style blocker for Windows 11.

Launch with ``pythonw hot_turkey.py`` (no console window) or via the
``run_hot_turkey.bat`` launcher.
"""

import sys

from hot_turkey.gui import HotTurkeyApp


def main():
    try:
        app = HotTurkeyApp()
        app.run()
    except RuntimeError as exc:
        # Tkinter missing or other startup failure: explain and exit.
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Hot Turkey", str(exc))
            root.destroy()
        except Exception:
            pass
        try:
            print(f"Hot Turkey: {exc}", file=sys.stderr)
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
