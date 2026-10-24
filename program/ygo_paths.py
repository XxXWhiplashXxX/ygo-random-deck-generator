"""ygo_paths.py  --  where every folder is. All the other files ask this one, so a folder only has to be renamed HERE.

    ygo_randomizer/                 ROOT        (the folder you open; has the launcher)
    ├── Yu-Gi-Oh Randomizer.pyw
    ├── decks/                      DECKS       exported decks
    ├── forbidden lists/            FORBIDDEN   banlists
    └── program/                    PROGRAM     all the .py files (this file lives here)
        ├── data/                   DATA        cards.csv, Stats.csv, settings, wiki notes ...
        └── backups/                BACKUPS     old cards.csv / Stats.csv from each update
"""
from pathlib import Path

PROGRAM = Path(__file__).resolve().parent          # the 'program' folder (where this file is)
ROOT = PROGRAM.parent                              # the folder above it
DATA = PROGRAM / "data"                            # every data file
BACKUPS = PROGRAM / "backups"                      # the updater's backups
DECKS = ROOT / "decks"                             # exported decks
FORBIDDEN = ROOT / "forbidden lists"               # banlists

for folder in (DATA, BACKUPS, DECKS, FORBIDDEN):   # make sure they all exist (does nothing if they do)
    folder.mkdir(parents=True, exist_ok=True)


def data_file(name: str) -> Path:
    """A file name typed in the window -> its full path. A bare name like 'cards.csv' means the data folder;
       a full path (picked with Browse...) is used as it is."""
    p = Path(str(name).strip())
    if p.is_absolute():
        return p
    full = DATA / p
    if not full.exists() and DATA.is_dir():                 # 'stats.csv' vs 'Stats.csv': find it whatever the capitals
        same = [f for f in DATA.iterdir() if f.name.lower() == p.name.lower()]
        if same:
            return same[0]
    return full
