"""ygo_paths.py  --  where every folder is. All the other files ask this one, so a folder only has to be renamed HERE.

    ygo_randomizer/                 ROOT        (the folder you open; has the launcher)
    ├── Yu-Gi-Oh Randomizer.pyw
    ├── web/                                    the website (app.py) and the offline version (offline.py)
    ├── forbidden lists/            FORBIDDEN   banlists
    └── program/                    PROGRAM     all the .py files (this file lives here)
        ├── data/                   DATA        cards.csv, Stats.csv, banlist staples, wiki notes ...
        └── backups/                BACKUPS     old cards.csv / Stats.csv from each update
"""
from pathlib import Path                                    # file paths

PROGRAM = Path(__file__).resolve().parent          # the 'program' folder (where this file is)
ROOT = PROGRAM.parent                              # the folder above it
DATA = PROGRAM / "data"                            # every data file
BACKUPS = PROGRAM / "backups"                      # the updater's backups
FORBIDDEN = ROOT / "forbidden lists"               # banlists

for folder in (DATA, BACKUPS, FORBIDDEN):          # make sure they all exist (does nothing if they do)
    folder.mkdir(parents=True, exist_ok=True)               # (makes any missing parent folders too)

