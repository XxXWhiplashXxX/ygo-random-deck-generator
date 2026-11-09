"""Double-click to start the offline website (web/offline.py) on Windows.

.pyw files may be tied to an old Python, so instead of running the site here, this finds a Python
through the Windows Python Launcher (py.exe) that has the website's packages installed, and starts
the site with it in its own console window.
"""
import subprocess                                # runs py.exe
from pathlib import Path                         # file paths

ROOT = Path(__file__).resolve().parent           # the project folder (where this file is)
OFFLINE = ROOT / "web" / "offline.py"            # the offline website

# Which Pythons to try, best first: 3.12 (the recommended one), then the newest Python 3 installed.
CANDIDATES = (["py", "-3.12"], ["py", "-3"])                # 'py -3.12' first, then 'py -3'
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)      # quick checks run without flashing a console
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)  # the site itself gets a console: logs + close to stop
INSTALL_HINT = "py -3 -m pip install -r web/requirements.txt"  # the command to show when packages are missing


def show_error(message):                                    # function: show an error box
    """A small pop-up error box (a .pyw has no console to print to)."""
    import tkinter as tk                                    # the small window toolkit built into Python
    from tkinter import messagebox                          # its message boxes
    root = tk.Tk()                                          # a hidden main window (a box needs one)
    root.withdraw()                              # hide the empty main window, show only the box
    messagebox.showerror("Yu-Gi-Oh! Randomizer", message)   # show the box
    root.destroy()                                          # close everything


def has_packages(python):                                   # function: does this Python have the website's packages?
    """True if this Python exists and can import the website's packages."""
    try:
        result = subprocess.run(python + ["-c", "import fastapi, uvicorn"], cwd=str(ROOT),  # try importing them with that Python...
                                capture_output=True, timeout=30, creationflags=NO_WINDOW)  # ...without a console window, 30 seconds at most
        return result.returncode == 0            # 0 = the import worked
    except (OSError, subprocess.TimeoutExpired):  # py.exe missing, or it hung
        return False


def main():                                                 # function: find a Python and start the offline site
    try:                                                    # (try it)
        subprocess.run(["py", "--version"], capture_output=True, timeout=30, creationflags=NO_WINDOW)  # is the Windows Python Launcher there?
    except (OSError, subprocess.TimeoutExpired):            # not found:
        show_error("The Windows Python Launcher (py.exe) was not found.\n\n"  # tell the user what to install
                   "Install Python from python.org (it includes the launcher), then run in PowerShell:\n"
                   f"{INSTALL_HINT}")
        return                                              # stop
    for python in CANDIDATES:                               # each Python to try
        if has_packages(python):                            # it has the packages:
            subprocess.Popen(python + [str(OFFLINE)], cwd=str(ROOT), creationflags=NEW_CONSOLE)  # start the offline site in its own console window
            return
    show_error("No installed Python has the website's packages yet.\n\n"
               "Open PowerShell in the project folder and run:\n"
               f"{INSTALL_HINT}\n\nThen double-click this file again.")


if __name__ == "__main__":
    main()
