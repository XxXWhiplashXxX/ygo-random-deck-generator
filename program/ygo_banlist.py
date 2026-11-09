"""ygo_banlist.py  --  reads the banlist files in the 'forbidden lists' folder.

A banlist file (.lflist.conf, the EDOPro / Omega format) holds one or more lists:
    !HAT (2014.04.01)           a list starts with '!' and its name
    #date 2014-04-01            (our extra info) the list's date ...
    #region TCG                 ... and whether TCG or OCG release dates count
    #pool Master Duel           (our extra info) only cards whose 'formats' column has this are allowed
    #copies 1                   (our extra info) every card's most copies (Highlander: 1)
    #exclude Link, Pendulum     (our extra info) cards with any of these traits are not allowed (Genesys)
    #points 100                 (our extra info) Genesys: the deck's points may not add up to more than this
    $whitelist                  (optional) ONLY the cards listed are allowed
    21044178 0 --Abyss Dweller  card Id, copies allowed (0 Forbidden, 1 Limited, 2 Semi-Limited), name
"""
from __future__ import annotations                          # lets type hints name classes that are defined further down the file

import re                                                   # regular expressions (text patterns)
from dataclasses import dataclass, field                    # tidy data containers
from pathlib import Path                                    # file paths
from typing import Optional                                 # Optional[X] = "an X or None"


@dataclass                                                  # makes a simple data container
class Banlist:                                              # class: one banlist
    name: str                                            # e.g. 'HAT (2014.04.01)'
    limits: dict = field(default_factory=dict)           # card Id -> copies allowed (0, 1, 2 ...)
    by_name: dict = field(default_factory=dict)          # card name (lowercase) -> copies allowed: for Ids we don't share
                                                         # (EDOPro gives pre-errata GOAT cards made-up Ids like 504700144)
    whitelist: bool = False                              # True: cards NOT listed are not allowed
    date: Optional[str] = None                           # '2014-04-01' (for the release-date card pool)
    region: str = "TCG"                                  # which release date counts: TCG or OCG
    pool: Optional[str] = None                           # e.g. 'Master Duel': only cards from that game / format
    copies: Optional[int] = None                         # most copies of ANY card (Highlander list: 1)
    exclude: list = field(default_factory=list)          # traits that are not allowed, e.g. ['link', 'pendulum']
    points: Optional[int] = None                         # Genesys point cap (None = not a Genesys list)


def _guess_date(name: str) -> Optional[str]:                # function: a date read from a list's name
    """A list from some other file has no '#date' line: read it from the name, '2014.4 TCG' -> '2014-04-01'."""
    m = re.search(r"(\d{4})\.(\d{1,2})(?:\.(\d{1,2}))?", name)  # 'YYYY.M' or 'YYYY.M.D'
    if not m:                                               # no date in the name:
        return None                                         # (unknown)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3) or 1):02d}"  # as 'YYYY-MM-DD' (day 1 if missing)


def load_banlists(folder: Path) -> dict:                    # function: read every banlist file in a folder
    """Every list in every .lflist.conf file in `folder`: {list name: Banlist}. A missing folder gives {}."""
    out: dict = {}                                          # list name -> Banlist
    folder = Path(folder)                                   # (accepts a string too)
    if not folder.is_dir():                                 # no folder:
        return out                                          # (nothing)
    for path in sorted(folder.glob("*.conf")):                    # every banlist file
        current = None                                      # the list being read (None before the first)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():  # each line of the file
            line = line.strip()                             # without spaces around it
            if line.startswith("!"):                              # a new list begins
                name = line[1:].strip()                     # its name
                current = out[name] = Banlist(name, region="OCG" if "OCG" in name.upper() else "TCG")  # start it (OCG lists use OCG dates)
            elif current is None:
                continue                                          # text before the first list
            elif line.lower().startswith("#date "):
                current.date = line[6:].strip()             # the date
            elif line.lower().startswith("#region "):
                current.region = line[8:].strip().upper()   # TCG or OCG
            elif line.lower().startswith("#pool "):
                current.pool = line[6:].strip()             # the game / format it is limited to
            elif line.lower().startswith("#copies "):
                current.copies = int(line[8:].strip())      # the copy cap
            elif line.lower().startswith("#exclude "):
                current.exclude = [t.strip().lower() for t in line[9:].split(",") if t.strip()]  # the excluded traits
            elif line.lower().startswith("#points "):
                current.points = int(line[8:].strip())      # the point cap
            elif line.lower() == "$whitelist":              # a whitelist line:
                current.whitelist = True                    # only listed cards are allowed
            else:
                m = re.match(r"(\d+)\s+(-?\d+)\s*(?:--\s*(.*))?$", line)   # 'card id  copies  --name'
                if m:                                       # a card line:
                    copies = max(0, int(m.group(2)))        # its copies (never negative)
                    current.limits[str(int(m.group(1)))] = copies             # '08131171' and '8131171' are the same Id
                    name = re.split(r"\t|\s{2,}", (m.group(3) or "").strip())[0]   # the name after '--' (tabs / wide gaps: extra notes)
                    name = re.sub(r"\s*\([^)]*\)$", "", name).strip().lower()     # 'Big Shield Gardna (pre-errata)' -> 'big shield gardna'
                    if name:                                # it has a name:
                        current.by_name[name] = copies      # remember it by name too
        for b in out.values():                              # each list read so far
            if b.date is None:                              # no '#date' line:
                b.date = _guess_date(b.name)                # read it from the name
    return out                                              # hand back every list


def card_ids(row: dict) -> list:                            # function: every Id a card goes by
    """Every Id a card goes by: its 'Card Id' plus the 'other ids' column (alternate arts), without leading zeros."""
    first = row.get("Card Id") or next(iter(row.values()), "")   # by column NAME (first column as a fallback for old files)
    ids = [first] + (row.get("other ids") or "").split()    # its main Id plus its alternate-art Ids
    return [str(int(i)) for i in ids if i.strip().isdigit()]  # as numbers without leading zeros


def copies_allowed(b: Banlist, row: dict, default: int) -> int:  # function: copies of a card a list allows
    """How many copies of this card the list allows (0 = not allowed), never more than `default`."""
    if b.copies is not None:                                      # the list caps every card (Highlander)
        default = min(default, b.copies)                    # never more than that
    games = {g.strip().lower() for g in (row.get("formats") or "").split(";")}   # 'TCG; OCG; Master Duel' -> a set
    if b.pool and b.pool.lower() not in games:              # the list is for one game and the card isn't in it
        return 0                                                  # not in that game (e.g. not in Master Duel)
    if b.exclude:                                                 # Genesys: no Link or Pendulum Monsters
        traits = {t.split("=", 1)[-1].strip().lower() for t in (row.get("traits") or "").split("||")}  # the card's traits
        if traits & set(b.exclude):                         # one is excluded:
            return 0                                        # (not allowed)
    for cid in card_ids(row):                               # each Id of the card
        if cid in b.limits:                                 # on the list:
            return min(default, b.limits[cid])              # its limit
    name = (row.get("name") or "").lower()                  # the card's name
    if name in b.by_name:                                         # Id not on the list, but the name is
        return min(default, b.by_name[name])                # its limit
    return 0 if b.whitelist else default                          # not on the list: whitelist -> banned, else unlimited


def released_by(b: Banlist, row: dict) -> bool:             # function: was the card out on the list's date?
    """Was the card out (in the list's region) on the list's date? Cards with no date for that region were not."""
    if not b.date:                                          # the list has no date:
        return True                                               # the list has no date: nothing to check
    date = (row.get("ocg date") if b.region == "OCG" else row.get("tcg date")) or ""  # its release date in the list's region
    return bool(date) and date <= b.date                          # 'YYYY-MM-DD' text compares like dates


def fl_shown_name(name: str, date: str, category: str) -> str:  # function: a Format Library format's name in the dropdown
    """The dropdown name of a Format Library format: 'TeleDAD (2009.01.17)', 'Makyura (OCG 2002.01.01)'.
       (ygo_make_banlists.py and ygo_make_staples.py both use this, so the two files always agree.)"""
    ocg = "OCG " if category == "OCG" and not name.startswith("OCG") else ""  # 'OCG ' in front for OCG formats (unless the name has it)
    return f"{name} ({ocg}{date.replace('-', '.')})"        # 'Name (YYYY.MM.DD)'


def fl_slug(text: str) -> str:                              # function: Format Library's web address form of a name
    """Format Library's address for a name: 'September 2008' -> 'september-2008', 'Gladiator Beast' -> 'gladiator-beast'."""
    return text.strip().lower().replace(" ", "-")           # lower-case, spaces -> dashes


def load_staples(path: Path) -> dict:                       # function: read format_staples.json
    """format_staples.json: {list name: {card name: (score 0-1, usual copies)}}; {} if the file is missing."""
    import json                                                   # (only needed here)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))  # read the file
    except (OSError, ValueError):                           # missing or broken:
        return {}                                           # (nothing)
    return {fmt: {card: (float(v[0]), int(v[1])) for card, v in cards.items()} for fmt, cards in data.items()}  # as numbers


def load_points(path: Path) -> dict:                        # function: read genesys_points.json
    """genesys_points.json: {card name: points} (only cards that cost points); {} if the file is missing."""
    import json                                             # (only needed here)
    try:
        return {k: int(v) for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items()}  # read the file, points as whole numbers
    except (OSError, ValueError):                           # missing or broken:
        return {}                                           # (nothing)


def current_formats(formats: list, today=None) -> set:      # function: the Format Library formats still going on
    """Format Library formats that are still going on (their deck data still grows): the newest TCG format,
       if it is less than a year old. These are refreshed every update and never marked complete."""
    import datetime                                               # (only needed here)
    today = today or datetime.date.today()                  # today (or the date given, for tests)
    dated = [f for f in formats if f.get("date") and f.get("category") == "TCG"]  # TCG formats that have a date
    if not dated:                                           # none:
        return set()                                        # (nothing)
    newest = max(dated, key=lambda f: f["date"])                  # 'YYYY-MM-DD' text sorts like dates
    age = (today - datetime.date.fromisoformat(newest["date"])).days  # its age in days
    return {fl_shown_name(newest["name"], newest["date"], "TCG")} if age < 365 else set()  # its dropdown name if under a year old


def load_complete(path: Path) -> set:                       # function: read complete_formats.txt
    """complete_formats.txt: one format per line, whose staples are finished for good; empty if missing."""
    try:
        return {line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()}  # each non-empty line
    except OSError:                                         # missing:
        return set()                                        # (nothing)
