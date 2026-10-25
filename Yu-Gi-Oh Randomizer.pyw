"""Launch the offline web UI with Python 3.12, regardless of .pyw file associations."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OFFLINE = ROOT / "web" / "offline.py"


def show_error(message):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Yu-Gi-Oh! Randomizer", message)
    root.destroy()


def main():
    # On Windows, the .pyw file association may point at an older Python.
    # Explicitly use the Python Launcher to select the 3.12 environment that
    # has the packages from web/requirements.txt installed.
    command = ["py", "-3.12", str(OFFLINE)]
    try:
        # Keep a console for logs/errors and a simple way to stop the server.
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen(command, cwd=str(ROOT), creationflags=flags)
    except FileNotFoundError:
        show_error("Windows Python Launcher (py.exe) was not found.\n\n"
                   "Run this from PowerShell instead:\n"
                   "py -3.12 web/offline.py")
    except Exception as exc:
        show_error("Couldn't start the offline generator with Python 3.12.\n\n"
                   f"{exc}\n\n"
                   "Try from PowerShell: py -3.12 web/offline.py")


if __name__ == "__main__":
    main()
