# ygo_make_banlists.py
# Builds  "forbidden lists/randomizer formats.lflist.conf"  - every format the randomizer offers - from:
#   1. EDOPro's free banlist files (github.com/ProjectIgnis/LFLists): today's TCG, OCG, WCS and Traditional lists
#   2. Format Library (formatlibrary.com): every TCG and OCG format in its library, each with its own date
#      and banlist  (fl_formats.json = the list of formats, fl_<banlist>_<TCG/OCG>.json = one banlist)
#   3. Master Duel Meta (masterduelmeta.com): today's Master Duel list  (md_cards.json)
#   4. lists made here: Highlander (1 copy of everything) and Genesys (4 point caps: 100, 50, 0, 200)
# The downloads are done by ygo_update.py; this script only reads them from the sources folder.
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
# Any list that can't be built from the downloads (a site was down, a list was renamed) is kept from last time.
#
#   python ygo_make_banlists.py  <sources folder>  <output file>  [cards.csv - to look up card Ids by name]

import re    # reads the EDOPro list headers and card lines
import sys   # folder / file names typed after the script name
from pathlib import Path

from ygo_banlist import fl_shown_name, fl_slug   # Format Library names / addresses (shared with ygo_make_staples.py)
from ygo_io import read_json, read_table          # shared file reading

SOURCES = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("banlist_sources")       # where the downloads are
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("randomizer formats.lflist.conf")
CARDS = Path(sys.argv[3]) if len(sys.argv) > 3 else None                            # optional: for card Ids

# 1. EDOPro lists: (name shown in the randomizer, source file, list name inside that file, date, region, change)
#   region : TCG or OCG - which release date counts for "only cards released by this date"
#   change : 'traditional' turns every Forbidden card into Limited (that's what a Traditional list is)
EDOPRO = [
    ("TCG (2026.10.19)",             "0TCG.lflist.conf",        "2026.10 TCG",         "2026-10-19", "TCG", None),
    ("OCG (2026.10.01)",             "OCG.lflist.conf",         "2026.10 OCG",         "2026-10-01", "OCG", None),
    ("WCS (2026.10.19)",             "World.lflist.conf",       "2026.10 Worlds",      "2026-10-19", "TCG", None),
    ("TCG Traditional (2026.10.01)", "Traditional.lflist.conf", "2026.10 Traditional", "2026-10-01", "TCG", None),
    ("OCG Traditional (2026.10.01)", "OCG.lflist.conf",         "2026.10 OCG",         "2026-10-01", "OCG", "traditional"),
]

# Format Library sections -> copies allowed. ('unlimited' only lists cards that came OFF the list: ignored.)
FL_SECTIONS = {"forbidden": 0, "limited": 1, "semiLimited": 2, "limited1": 1, "limited2": 2, "limited3": 3}
# Master Duel Meta ban statuses -> copies allowed
MD_STATUS = {"forbidden": 0, "limited 1": 1, "limited": 1, "limited 2": 2, "semi-limited": 2}


def read_edopro(path: Path) -> dict:
    """Every list in one .lflist.conf file: {list name: (whitelist?, [(card id, copies, name), ...])}."""
    lists, name = {}, None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("!"):                              # a new list begins
            name = line[1:].strip()
            lists[name] = [False, []]
        elif name is None:
            continue                                          # text before the first list
        elif line.lower() == "$whitelist":                    # only the listed cards are allowed
            lists[name][0] = True
        else:
            m = re.match(r"(\d+)\s+(-?\d+)\s*(?:--\s*(.*))?$", line)   # 'id copies --name'
            if m:
                lists[name][1].append((m.group(1), int(m.group(2)), (m.group(3) or "").split("\t")[0].strip()))
    return {k: (v[0], v[1]) for k, v in lists.items()}


def old_blocks(path: Path) -> dict:
    """The lists already in our output file, as raw text: {shown name: [lines]}.
       EDOPro's files only hold the CURRENT list, so after Konami's next update '2026.10 TCG' is gone from
       them; when a list can't be built from the downloads we keep the copy we made last time."""
    blocks, name = {}, None
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("!"):                          # a new list begins
                name = line[1:].strip()
                blocks[name] = [line]
            elif name is not None and line.strip():           # its lines (blank lines are dropped)
                blocks[name].append(line)
    return blocks


# ---- card name -> Id (Format Library and Master Duel Meta give names, EDOPro wants Ids) ----------
ids_by_name = {}
if CARDS and CARDS.is_file():
    card_columns, card_rows = read_table(CARDS)
    for row in card_rows:
        ids_by_name[row["name"].lower()] = row[card_columns[0]]              # first column = 'Card Id'


def card_line(name: str, copies: int) -> str:
    """One card line. A name we don't have gets Id 0: the randomizer still matches it by name."""
    return f"{ids_by_name.get(name.lower(), '0')} {copies} --{name}"


kept = old_blocks(OUT)                    # last time's lists (read BEFORE we overwrite the file)
lists = []                                # (shown name, [lines]) in dropdown order


def add(shown: str, header: list, cards: list) -> None:
    """Add one finished list: its '!' line, the '#' info lines, then the card lines."""
    lists.append((shown, [f"!{shown}"] + header + cards))


def keep_or_skip(shown: str, why: str) -> None:
    """A list we couldn't build this time: keep last time's copy if there is one."""
    if shown in kept:
        lists.append((shown, kept[shown]))
        print(f"{shown}: kept from last time ({why})")
    else:
        print(f"{shown}: skipped ({why})")


# ---- 4. Highlander: every card allowed, 1 copy each, no date limit ----------------------------------
add("Highlander", ["#copies 1"], [])

# ---- 5. Genesys: TCG cards, no Link / Pendulum Monsters, and a points cap (the points: genesys_points.json) -
for cap in (100, 50, 0, 200):                                 # 100 is the standard cap; events also run these
    add(f"Genesys ({cap} points)", ["#pool TCG", "#exclude Link, Pendulum", f"#points {cap}"], [])

# ---- 1. EDOPro: today's lists ----------------------------------------------------------------------
cache = {}                                                    # file name -> its lists (each file is read once)
for shown, file, source_name, date, region, change in EDOPRO:
    if file not in cache:
        path = SOURCES / file
        cache[file] = read_edopro(path) if path.is_file() else {}   # a failed download: no lists from that file
    found = cache[file].get(source_name)
    if found is None:                                         # not in the downloads any more
        keep_or_skip(shown, f"'{source_name}' not in {file}")
        continue
    whitelist, cards = found
    lines = [f"{cid} {1 if change == 'traditional' and copies == 0 else copies} --{name}"   # Traditional: Forbidden -> Limited
             for cid, copies, name in cards]
    add(shown, [f"#date {date}", f"#region {region}"] + (["$whitelist"] if whitelist else []), lines)

# ---- 3. Master Duel: today's list, only cards that are in Master Duel -------------------------------
md = read_json(SOURCES / "md_cards.json")                     # [{'name': ..., 'banStatus': 'Forbidden'}, ...]
if md:
    copies_of = {}
    for card in md:
        status = (card.get("banStatus") or "").strip().lower()
        if status in MD_STATUS and card.get("name"):
            copies_of[card["name"]] = MD_STATUS[status]       # alternate arts repeat a name: same answer
    add("Master Duel", ["#pool Master Duel"], [card_line(n, c) for n, c in copies_of.items()])
else:
    keep_or_skip("Master Duel", "no md_cards.json")

# ---- 2. Format Library: every TCG and OCG format, oldest first --------------------------------------
formats = read_json(SOURCES / "fl_formats.json") or []
formats = [f for f in formats if f.get("date") and f.get("banlist") and f.get("category") in ("TCG", "OCG")]
formats.sort(key=lambda f: (f["category"] != "TCG", f["date"]))          # TCG formats first, each group by date
for f in formats:
    cat, date = f["category"], f["date"]                      # 'TCG' / 'OCG', '2009-01-17'
    shown = fl_shown_name(f["name"], date, cat)               # 'TeleDAD (2009.01.17)'
    data = read_json(SOURCES / f"fl_{fl_slug(f['banlist'])}_{cat}.json")
    if not isinstance(data, dict):
        keep_or_skip(shown, f"banlist '{f['banlist']}' not downloaded")
        continue
    copies_of = {}
    for section, copies in FL_SECTIONS.items():
        for entry in data.get(section) or []:                 # each entry: {'cardName': ..., 'card': {'name': ...}}
            name = entry.get("cardName") or (entry.get("card") or {}).get("name")
            if name:
                copies_of[name] = copies
    add(shown, [f"#date {date}", f"#region {cat}"], [card_line(n, c) for n, c in copies_of.items()])
if not formats:                                               # Format Library didn't answer: keep all its lists
    for shown in kept:
        if shown not in {s for s, _ in lists}:
            keep_or_skip(shown, "Format Library not downloaded")

# ---- save -------------------------------------------------------------------------------------------
out_lines = ["# Banlists for the Yu-Gi-Oh! randomizer, made by ygo_make_banlists.py",
             "# Sources: EDOPro (github.com/ProjectIgnis/LFLists), Format Library (formatlibrary.com),",
             "#          Master Duel Meta (masterduelmeta.com)", ""]
for _shown, lines in lists:
    out_lines += lines + [""]
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(out_lines), encoding="utf-8")
print(f"saved {len(lists)} lists to {OUT}")
