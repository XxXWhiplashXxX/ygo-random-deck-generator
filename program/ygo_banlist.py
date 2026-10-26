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
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Banlist:
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


def _guess_date(name: str) -> Optional[str]:
    """A list from some other file has no '#date' line: read it from the name, '2014.4 TCG' -> '2014-04-01'."""
    m = re.search(r"(\d{4})\.(\d{1,2})(?:\.(\d{1,2}))?", name)
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3) or 1):02d}"


def load_banlists(folder: Path) -> dict:
    """Every list in every .lflist.conf file in `folder`: {list name: Banlist}. A missing folder gives {}."""
    out: dict = {}
    folder = Path(folder)
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.conf")):                    # every banlist file
        current = None
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("!"):                              # a new list begins
                name = line[1:].strip()
                current = out[name] = Banlist(name, region="OCG" if "OCG" in name.upper() else "TCG")
            elif current is None:
                continue                                          # text before the first list
            elif line.lower().startswith("#date "):
                current.date = line[6:].strip()
            elif line.lower().startswith("#region "):
                current.region = line[8:].strip().upper()
            elif line.lower().startswith("#pool "):
                current.pool = line[6:].strip()
            elif line.lower().startswith("#copies "):
                current.copies = int(line[8:].strip())
            elif line.lower().startswith("#exclude "):
                current.exclude = [t.strip().lower() for t in line[9:].split(",") if t.strip()]
            elif line.lower().startswith("#points "):
                current.points = int(line[8:].strip())
            elif line.lower() == "$whitelist":
                current.whitelist = True
            else:
                m = re.match(r"(\d+)\s+(-?\d+)\s*(?:--\s*(.*))?$", line)   # 'card id  copies  --name'
                if m:
                    copies = max(0, int(m.group(2)))
                    current.limits[str(int(m.group(1)))] = copies             # '08131171' and '8131171' are the same Id
                    name = re.split(r"\t|\s{2,}", (m.group(3) or "").strip())[0]   # the name after '--' (tabs / wide gaps: extra notes)
                    name = re.sub(r"\s*\([^)]*\)$", "", name).strip().lower()     # 'Big Shield Gardna (pre-errata)' -> 'big shield gardna'
                    if name:
                        current.by_name[name] = copies
        for b in out.values():
            if b.date is None:
                b.date = _guess_date(b.name)
    return out


def card_ids(row: dict) -> list:
    """Every Id a card goes by: its 'Card Id' plus the 'other ids' column (alternate arts), without leading zeros."""
    first = row.get("Card Id") or next(iter(row.values()), "")   # by column NAME (first column as a fallback for old files)
    ids = [first] + (row.get("other ids") or "").split()
    return [str(int(i)) for i in ids if i.strip().isdigit()]


def copies_allowed(b: Banlist, row: dict, default: int) -> int:
    """How many copies of this card the list allows (0 = not allowed), never more than `default`."""
    if b.copies is not None:                                      # the list caps every card (Highlander)
        default = min(default, b.copies)
    games = {g.strip().lower() for g in (row.get("formats") or "").split(";")}   # 'TCG; OCG; Master Duel' -> a set
    if b.pool and b.pool.lower() not in games:
        return 0                                                  # not in that game (e.g. not in Master Duel)
    if b.exclude:                                                 # Genesys: no Link or Pendulum Monsters
        traits = {t.split("=", 1)[-1].strip().lower() for t in (row.get("traits") or "").split("||")}
        if traits & set(b.exclude):
            return 0
    for cid in card_ids(row):
        if cid in b.limits:
            return min(default, b.limits[cid])
    name = (row.get("name") or "").lower()
    if name in b.by_name:                                         # Id not on the list, but the name is
        return min(default, b.by_name[name])
    return 0 if b.whitelist else default                          # not on the list: whitelist -> banned, else unlimited


def released_by(b: Banlist, row: dict) -> bool:
    """Was the card out (in the list's region) on the list's date? Cards with no date for that region were not."""
    if not b.date:
        return True                                               # the list has no date: nothing to check
    date = (row.get("ocg date") if b.region == "OCG" else row.get("tcg date")) or ""
    return bool(date) and date <= b.date                          # 'YYYY-MM-DD' text compares like dates


def fl_shown_name(name: str, date: str, category: str) -> str:
    """The dropdown name of a Format Library format: 'TeleDAD (2009.01.17)', 'Makyura (OCG 2002.01.01)'.
       (ygo_make_banlists.py and ygo_make_staples.py both use this, so the two files always agree.)"""
    ocg = "OCG " if category == "OCG" and not name.startswith("OCG") else ""
    return f"{name} ({ocg}{date.replace('-', '.')})"


def fl_slug(text: str) -> str:
    """Format Library's address for a name: 'September 2008' -> 'september-2008', 'Gladiator Beast' -> 'gladiator-beast'."""
    return text.strip().lower().replace(" ", "-")


def load_staples(path: Path) -> dict:
    """format_staples.json: {list name: {card name: (score 0-1, usual copies)}}; {} if the file is missing."""
    import json                                                   # (only needed here)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {fmt: {card: (float(v[0]), int(v[1])) for card, v in cards.items()} for fmt, cards in data.items()}


def load_points(path: Path) -> dict:
    """genesys_points.json: {card name: points} (only cards that cost points); {} if the file is missing."""
    import json
    try:
        return {k: int(v) for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return {}


def current_formats(formats: list, today=None) -> set:
    """Format Library formats that are still going on (their deck data still grows): the newest TCG format,
       if it is less than a year old. These are refreshed every update and never marked complete."""
    import datetime                                               # (only needed here)
    today = today or datetime.date.today()
    dated = [f for f in formats if f.get("date") and f.get("category") == "TCG"]
    if not dated:
        return set()
    newest = max(dated, key=lambda f: f["date"])                  # 'YYYY-MM-DD' text sorts like dates
    age = (today - datetime.date.fromisoformat(newest["date"])).days
    return {fl_shown_name(newest["name"], newest["date"], "TCG")} if age < 365 else set()


def load_complete(path: Path) -> set:
    """complete_formats.txt: one format per line, whose staples are finished for good; empty if missing."""
    try:
        return {line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()}
    except OSError:
        return set()
