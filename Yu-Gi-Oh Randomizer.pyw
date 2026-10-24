"""Double-click this file to start the Yu-Gi-Oh! Deck Randomizer.
(.pyw = Windows runs it without the black console window. Everything else lives in the 'program' folder.)"""
import sys                                                  # lets Python find the files in 'program'
import traceback                                            # turns an error into readable text
from pathlib import Path

PROGRAM = Path(__file__).resolve().parent / "program"       # the folder with all the .py files
sys.path.insert(0, str(PROGRAM))                            # 'import ygo_gui' now looks there

try:
    import ygo_gui                                          # the window
    ygo_gui.build_gui()                                     # open it (runs until you close the window)
except Exception:                                           # no console to print to, so show the error in a pop-up
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()                                         # no empty main window, just the message
    messagebox.showerror("Yu-Gi-Oh! Randomizer", "The randomizer could not start:\n\n" + traceback.format_exc()[-1500:])
