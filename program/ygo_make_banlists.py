# ygo_make_banlists.py
# Builds  "forbidden lists/randomizer formats.lflist.conf"  - every format the randomizer offers - from:
#   1. EDOPro's free banlist files (github.com/ProjectIgnis/LFLists): today's TCG, OCG, WCS and Traditional lists
#   2. Format Library (formatlibrary.com): every TCG and OCG format in its library, each with its own date
#      and banlist  (fl_formats.json = the list of formats, fl_<banlist>_<TCG/OCG>.json = one banlist)
#   3. Master Duel Meta (masterduelmeta.com): today's Master Duel list  (md_cards.json)
#   4. lists made here: Highlander (1 copy of everything) and Genesys (4 point caps: 100, 50, 0, 200)
# The downloads are done by ygo_formats_update.py; this script only reads them from the sources folder.
#
# The output is the normal .lflist.conf format EDOPro / Omega use:
#     !TeleDAD (2009.01.17)               <- a list starts with '!' and its name
#     #date 2009-01-17                    <- our extra info (other programs treat '#' lines as comments):
#     #region TCG                         <-   the card-pool date, which release date counts,
#     #pool Master Duel                   <-   (Master Duel) only cards from that game,
#     #copies 1                           <-   (Highlander) the most copies of any card
#     #exclude Link, Pendulum             <-   (Genesys) traits that are not allowed
#     #points 100                         <-   (Genesys) the most points a deck may have
#     5318639 1 --Mystical Space Typhoon  <- card Id, copies allowed (0 Forbidden, 1 Limited, 2 Semi-Limited), name
# Any list that can't be built from the downloads (a site was down, a file changed shape) is kept from last time.
#
#   python ygo_make_banlists.py  <sources folder>  <output file>  [cards.csv - to look up card Ids by name]

import re    # reads the EDOPro list headers and card lines
import sys   # folder / file names typed after the script name
import datetime                                             # today's date, for today's lists
from pathlib import Path                                    # file paths

from ygo_banlist import fl_shown_name, fl_slug   # Format Library names / addresses (shared with ygo_make_staples.py)
from ygo_io import read_json, read_table          # shared file reading

SOURCES = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("banlist_sources")       # where the downloads are
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("randomizer formats.lflist.conf")  # the list file we write
CARDS = Path(sys.argv[3]) if len(sys.argv) > 3 else None                            # optional: for card Ids

# 1. EDOPro lists. Each file holds the CURRENT list, named like '2026.10 TCG'; the name changes every time Konami
#    updates it. So instead of typing the names in, we look for the NEWEST list in each file whose name ends in
#    the right word, and name ours after it: '2026.10 TCG' -> 'TCG (2026.10)'.
#   word   : the end of the list's name inside the file ('TCG', 'OCG', 'Worlds', 'Traditional')
#   region : TCG or OCG - which release date counts for "only cards released by this date"
#   change : 'traditional' turns every Forbidden card into Limited (that's what a Traditional list is)
#   keep   : True  = when a newer list comes out, the old one STAYS in the dropdown (TCG / OCG build up a history)
#            False = the old one is replaced (WCS and the Traditional lists only ever show the newest)
EDOPRO = [                                                  # (shown name start, file, word, region, change, keep)
    ("TCG",             "0TCG.lflist.conf",        "TCG",         "TCG", None,          True),    # today's TCG list
    ("OCG",             "OCG.lflist.conf",         "OCG",         "OCG", None,          True),    # today's OCG list
    ("WCS",             "World.lflist.conf",       "Worlds",      "TCG", None,          False),   # today's WCS list
    ("TCG Traditional", "Traditional.lflist.conf", "Traditional", "TCG", None,          False),   # today's TCG Traditional list
    ("OCG Traditional", "OCG.lflist.conf",         "OCG",         "OCG", "traditional", False),   # today's OCG list made Traditional
]
SHOWN_MONTH = re.compile(r"\((\d{4})\.(\d{2})")              # 'TCG (2026.10.19)' / 'TCG (2026.10)' -> 2026, 10
LIST_NAME = re.compile(r"^(\d{4})\.(\d{1,2})(?:\.(\d{1,2}))?\s+(.+)$")   # '2026.10 TCG' -> 2026, 10, (no day), 'TCG'

# Format Library sections -> copies allowed. ('unlimited' only lists cards that came OFF the list: ignored.)
FL_SECTIONS = {"forbidden": 0, "limited": 1, "semiLimited": 2, "limited1": 1, "limited2": 2, "limited3": 3}  # Format Library section -> copies allowed
# Master Duel Meta ban statuses -> copies allowed
MD_STATUS = {"forbidden": 0, "limited 1": 1, "limited": 1, "limited 2": 2, "semi-limited": 2}  # Master Duel status -> copies allowed


def read_edopro(path: Path) -> dict:                        # function: read every list in an EDOPro file
    """Every list in one .lflist.conf file: {list name: (whitelist?, [(card id, copies, name), ...])}."""
    lists, name = {}, None                                  # list name -> [whitelist?, cards]; the list being read
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():  # each line
        line = line.strip()                                 # without spaces around it
        if line.startswith("!"):                              # a new list begins
            name = line[1:].strip()                         # its name
            lists[name] = [False, []]                       # start it
        elif name is None:
            continue                                          # text before the first list
        elif line.lower() == "$whitelist":                    # only the listed cards are allowed
            lists[name][0] = True                           # (note it)
        else:
            m = re.match(r"(\d+)\s+(-?\d+)\s*(?:--\s*(.*))?$", line)   # 'id copies --name'
            if m:                                           # a card line:
                # (Id, copies, name without any notes after a tab)
                lists[name][1].append((m.group(1), int(m.group(2)), (m.group(3) or "").split("\t")[0].strip()))
    return {k: (v[0], v[1]) for k, v in lists.items()}      # hand back name -> (whitelist?, cards)


def old_blocks(path: Path) -> dict:                         # function: last time's lists, as raw text
    """The lists already in our output file, as raw text: {shown name: [lines]}.
       EDOPro's files only hold the CURRENT list, so after Konami's next update '2026.10 TCG' is gone from
       them; when a list can't be built from the downloads we keep the copy we made last time."""
    blocks, name = {}, None                                 # list name -> its lines; the list being read
    if path.is_file():                                      # the file exists:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():  # each line
            if line.startswith("!"):                          # a new list begins
                name = line[1:].strip()                     # its name
                blocks[name] = [line]                       # start it with the '!' line
            elif name is not None and line.strip():           # its lines (blank lines are dropped)
                blocks[name].append(line)                   # keep it
    return blocks                                           # hand back the lists


# ---- card name -> Id (Format Library and Master Duel Meta give names, EDOPro wants Ids) ----------
ids_by_name = {}                                            # lower-case card name -> Id
if CARDS and CARDS.is_file():                               # a cards.csv was given:
    card_columns, card_rows = read_table(CARDS)             # read it
    for row in card_rows:                                   # each card
        ids_by_name[row["name"].lower()] = row[card_columns[0]]              # first column = 'Card Id'


def newest_list(lists: dict, word: str):                    # function: the newest list in a file with this word
    """The newest list in one EDOPro file whose name is 'YYYY.M[.D] <word>' (any capitals), as
       (its name in the file, 'YYYY.MM' or 'YYYY.MM.DD' for our name), or None if there is none."""
    best = None                                             # (year, month, day, name in the file) of the newest so far
    for name in lists:                                      # every list in the file
        m = LIST_NAME.match(name.strip())                   # split it into date and word
        if not m or m.group(4).strip().lower() != word.lower():  # not a dated name, or the wrong word:
            continue                                        # not a dated list, or a different kind ('2026.10 Worlds' in a TCG file)
        key = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), name)   # numbers, so 2026.10 is newer than 2026.9
        if best is None or key > best:                      # newer than the best so far:
            best = key                                      # keep it
    if best is None:                                        # nothing matched
        return None                                         # (none)
    year, month, day, name = best                           # the newest one
    when = f"{year}.{month:02d}" + (f".{day:02d}" if day else "")   # '2026.10' (or '2026.10.19' if the name has a day)
    return name, when                                       # hand back its name and our date text


def keep_latest(start: str, why: str) -> None:             # function: keep last time's list of one kind, whatever its date
    """A today's-list we couldn't build: keep last time's copy. We don't know its exact name (the date in it
       changes), so look for the kept list whose name is '<start> (...)' - e.g. 'TCG (2026.10)'."""
    old = [n for n in kept if n.startswith(start + " (")]   # 'TCG (' never matches 'TCG Traditional ('
    if old:                                                 # we have one:
        keep_or_skip(old[-1], why)                          # keep it (the last one, if the file somehow had two)
    else:
        print(f"{start}: skipped ({why})")                  # say it was skipped


def keep_history(start: str, current: str) -> None:        # function: keep the older lists of one kind (TCG / OCG)
    """After a new '<start> (...)' list was made, keep every older one from last time, newest first.
       A kept list from the SAME month as the new one is the same list under an older name
       ('TCG (2026.10.19)' next to the new 'TCG (2026.10)'), so it is dropped instead of shown twice."""
    month = SHOWN_MONTH.search(current).groups()            # the new list's (year, month)
    old = [n for n in kept if n.startswith(start + " (") and n != current]   # last time's lists of this kind
    for name in sorted(old, reverse=True):                  # newest first ('2026.07' sorts after '2026.04')
        m = SHOWN_MONTH.search(name)                        # its (year, month)
        if m and m.groups() == month:                       # the same list under its old name:
            print(f"{name}: dropped (now called {current})")   # say so
            continue
        keep_or_skip(name, "an older list, kept for history")   # keep it as it was


def card_line(name: str, copies: int) -> str:               # function: one card line, looked up by name
    """One card line. A name we don't have gets Id 0: the randomizer still matches it by name."""
    return f"{ids_by_name.get(name.lower(), '0')} {copies} --{name}"  # 'Id copies --name' (Id 0 if we don't know it)


kept = old_blocks(OUT)                    # last time's lists (read BEFORE we overwrite the file)
lists = []                                # (shown name, [lines]) in dropdown order


def add(shown: str, header: list, cards: list) -> None:     # function: add one finished list
    """Add one finished list: its '!' line, the '#' info lines, then the card lines."""
    lists.append((shown, [f"!{shown}"] + header + cards))   # the '!' line, info lines, card lines


def keep_or_skip(shown: str, why: str) -> None:             # function: keep last time's copy of a list, or skip it
    """A list we couldn't build this time: keep last time's copy if there is one."""
    if shown in kept:                                       # we have one:
        lists.append((shown, kept[shown]))                  # keep it
        print(f"{shown}: kept from last time ({why})")      # say so
    else:
        print(f"{shown}: skipped ({why})")                  # say it was skipped


# ---- 4. Highlander: every card allowed, 1 copy each, no date limit ----------------------------------
add("Highlander", ["#copies 1"], [])

# ---- 5. Genesys: TCG cards, no Link / Pendulum Monsters, and a points cap (the points: genesys_points.json) -
for cap in (100, 50, 0, 200):                                 # 100 is the standard cap; events also run these
    add(f"Genesys ({cap} points)", ["#pool TCG", "#exclude Link, Pendulum", f"#points {cap}"], [])

# ---- 1. EDOPro: today's lists ----------------------------------------------------------------------
cache = {}                                                    # file name -> its lists (each file is read once)
today = datetime.date.today().isoformat()                              # e.g. '2026-11-09'
for start, file, word, region, change, keep in EDOPRO:        # each EDOPro list
    if file not in cache:                                   # not read yet:
        path = SOURCES / file                               # the downloaded file
        cache[file] = read_edopro(path) if path.is_file() else {}   # a failed download: no lists from that file
    newest = newest_list(cache[file], word)                 # the newest '<date> <word>' list in it (or None)
    if newest is None:                                      # nothing usable in the downloads
        if keep:                                            # a kind with a history: keep all of last time's
            for name in sorted((n for n in kept if n.startswith(start + " (")), reverse=True):   # newest first
                keep_or_skip(name, f"no '<date> {word}' list in {file}")   # keep each one
        else:
            keep_latest(start, f"no '<date> {word}' list in {file}")   # keep last time's (or skip)
        continue
    source_name, when = newest                              # its name in the file, and our date text
    shown = f"{start} ({when})"                             # e.g. 'TCG (2026.10)'
    whitelist, cards = cache[file][source_name]             # its whitelist flag and cards
    lines = [f"{cid} {1 if change == 'traditional' and copies == 0 else copies} --{name}"   # Traditional: Forbidden -> Limited
             for cid, copies, name in cards]                # (for every card)
    # '#date' = today: this list is in force right now, so every card released by today counts. (The name only
    # says the month, not the day it started, so it can't be used for the release-date pool.)
    # (When a newer list replaces it, the kept copy keeps the last date it was refreshed: about when it ended.)
    add(shown, [f"#date {today}", f"#region {region}"] + (["$whitelist"] if whitelist else []), lines)
    if keep:                                                # TCG / OCG: the older lists stay too
        keep_history(start, shown)                          # keep the older ones too

# ---- 3. Master Duel: today's list, only cards that are in Master Duel -------------------------------
md = read_json(SOURCES / "md_cards.json")                     # [{'name': ..., 'banStatus': 'Forbidden'}, ...]
if md:                                                      # downloaded:
    copies_of = {}                                          # card name -> copies
    for card in md:                                         # each card on the list
        status = (card.get("banStatus") or "").strip().lower()  # its status, lower-case
        if status in MD_STATUS and card.get("name"):        # one we understand, with a name:
            copies_of[card["name"]] = MD_STATUS[status]       # alternate arts repeat a name: same answer
    add("Master Duel", ["#pool Master Duel"], [card_line(n, c) for n, c in copies_of.items()])
else:
    keep_or_skip("Master Duel", "no md_cards.json")         # keep last time's (or skip)

# ---- 2. Format Library: every TCG and OCG format, oldest first --------------------------------------
formats = read_json(SOURCES / "fl_formats.json") or []      # Format Library's format list
formats = [f for f in formats if f.get("date") and f.get("banlist") and f.get("category") in ("TCG", "OCG")]  # dated TCG / OCG formats with a banlist
formats.sort(key=lambda f: (f["category"] != "TCG", f["date"]))          # TCG formats first, each group by date
for f in formats:                                           # each format
    cat, date = f["category"], f["date"]                      # 'TCG' / 'OCG', '2009-01-17'
    shown = fl_shown_name(f["name"], date, cat)               # 'TeleDAD (2009.01.17)'
    data = read_json(SOURCES / f"fl_{fl_slug(f['banlist'])}_{cat}.json")  # its downloaded banlist
    if not isinstance(data, dict):                          # not downloaded:
        keep_or_skip(shown, f"banlist '{f['banlist']}' not downloaded")  # keep last time's (or skip)
        continue
    copies_of = {}                                          # card name -> copies
    for section, copies in FL_SECTIONS.items():             # each section
        for entry in data.get(section) or []:                 # each entry: {'cardName': ..., 'card': {'name': ...}}
            name = entry.get("cardName") or (entry.get("card") or {}).get("name")  # the card's name (two possible places)
            if name:                                        # found:
                copies_of[name] = copies                    # note its copies
    add(shown, [f"#date {date}", f"#region {cat}"], [card_line(n, c) for n, c in copies_of.items()])
if not formats:                                               # Format Library didn't answer: keep all its lists
    for shown in kept:                                      # each of last time's lists
        todays = any(shown.startswith(start + " (") for start, *_ in EDOPRO)   # an old copy of a today's list (made above)
        if shown not in {s for s, _ in lists} and not todays:              # not built this time:
            keep_or_skip(shown, "Format Library not downloaded")  # keep it

# ---- save -------------------------------------------------------------------------------------------
out_lines = ["# Banlists for the Yu-Gi-Oh! randomizer, made by ygo_make_banlists.py",
             "# Sources: EDOPro (github.com/ProjectIgnis/LFLists), Format Library (formatlibrary.com),",
             "#          Master Duel Meta (masterduelmeta.com)", ""]
for _shown, lines in lists:                                 # each list
    out_lines += lines + [""]                               # its lines and a blank line
OUT.parent.mkdir(parents=True, exist_ok=True)               # make the folder if missing
OUT.write_text("\n".join(out_lines), encoding="utf-8")      # save the file
print(f"saved {len(lists)} lists to {OUT}")                 # the summary
