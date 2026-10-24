# ygo_set_eras.py
# Sets every card's ERA from its first release date, using Format Library's eras (formatlibrary.com):
# a card belongs to the era of the FIRST Format Library format it was legal in - the first format dated on or
# after its release. Performage Hat Tricker came out in 2015, so its first format is in ARC-V: it is ARC-V.
#   - a card with a TCG date uses the TCG formats
#   - a card with only an OCG date uses the OCG formats (they stop in 2014; after that the TCG dates are used)
#   - a card newer than the newest format gets the newest era; a card with no date keeps the era it had
# Rewrites the card file in place (only the 'era' column changes).
#
#   python ygo_set_eras.py cards_new.csv api.json

import sys                     # file names typed after the script name
from collections import Counter  # counts the cards in each era, for the report

from ygo_io import read_api, read_table, write_table   # shared file reading / writing

CARDS_FILE = sys.argv[1] if len(sys.argv) > 1 else "cards_new.csv"    # the file to update
API_FILE = sys.argv[2] if len(sys.argv) > 2 else "api.json"           # the YGOPRODeck card list (has the release dates)

# Format Library's eras, oldest first, with the date of the LAST format of each era (formatlibrary.com/api/formats).
# A card released on or before an era's last format date (and after the era before it) is in that era.
# When Format Library adds a new era: add a line at the bottom, and set the last line's date to its last format.
#            era name in cards.csv    last TCG format          last OCG format
ERAS = [
    ("Duel Monsters", "2005-02-13",  "2004-04-10"),    # DM:     Yugi-Kaiba .. Warrior   (OCG: Tokyo Dome .. Catapult)
    ("GX",            "2008-07-26",  "2007-09-01"),    # GX:     Goat .. Gladiator       (OCG: Whale .. Armageddon)
    ("5DS",           "2011-06-18",  "2011-08-31"),    # 5D's:   TeleDAD .. Trishula     (OCG: Dark Cat .. Nexus)
    ("ZEXAL",         "2014-07-12",  "2014-03-20"),    # ZEXAL:  Tengu .. HAT            (OCG: Agent .. Incarnate)
    ("ARC-V",         "2017-07-08",  None),            # ARC-V:  Shaddoll .. Draco Zoo   (no OCG formats from here on)
    ("VRAINS",        "2020-02-22",  None),            # VRAINS: Link Zoo .. Rokket
    ("SEVENS",        "2023-02-12",  None),            # SEVENS: Adamancipator .. Hypernova
    ("GO RUSH!!",     None,          None),            # GO RUSH!!: Kashtira onwards - the newest era takes every newer card
]


def era_of(tcg: str, ocg: str):
    """The era for a card's first release dates ('2015-08-06' style, '' if none), or None if it has no date."""
    if tcg:
        day, column = tcg, 1                                  # a TCG card: compare with the TCG formats
    elif ocg:
        day, column = ocg, 2                                  # an OCG-only card: compare with the OCG formats ...
        if day > max(e[2] for e in ERAS if e[2]):             # ... unless it is newer than every OCG format:
            column = 1                                        #     then the TCG dates decide
    else:
        return None                                           # no date at all
    for era in ERAS:
        last = era[column] or (era[1] if column == 2 else None)   # (OCG column empty: fall back to the TCG date)
        if last is None or day <= last:                       # released on or before this era's last format
            return era[0]
    return ERAS[-1][0]                                        # (not reached: the newest era has no end)


# --- Release dates for every card, by Card Id (and by name, for cards whose Id changed) ---------------
dates, dates_by_name = {}, {}
for c in read_api(API_FILE):
    misc = (c.get("misc_info") or [{}])[0]                    # the extra info block
    pair = (misc.get("tcg_date") or "", misc.get("ocg_date") or "")
    dates[str(c["id"])] = dates_by_name[c["name"].lower()] = pair

# --- Set the era of every card ------------------------------------------------------------------------
columns, rows = read_table(CARDS_FILE)
id_col = columns[0]                                           # 'Card Id'
changed, no_date = Counter(), 0
for r in rows:
    tcg, ocg = dates.get(r[id_col]) or dates_by_name.get(r["name"].lower()) or ("", "")
    era = era_of(tcg, ocg)
    if era is None:
        no_date += 1                                          # no release date: keep the era it has
        continue
    if era != r["era"]:
        changed[f"{r['era'] or '(none)'} -> {era}"] += 1      # count this kind of change, for the report
        r["era"] = era

write_table(CARDS_FILE, columns, rows)

spread = Counter(r["era"] for r in rows)
print(f"Set eras: {sum(changed.values())} cards changed era, {no_date} have no release date (kept as they were).")
print("  cards per era:", ", ".join(f"{e[0]} {spread[e[0]]}" for e in ERAS))
for kind, n in changed.most_common(12):                       # the biggest moves, most common first
    print(f"  {n:5d}  {kind}")
