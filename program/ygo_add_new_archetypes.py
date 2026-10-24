# ygo_add_new_archetypes.py
# Adds a row to Stats.csv for every archetype the new cards use that Stats.csv doesn't have yet.
# New rows copy the existing archetype rows: TRUE, FALSE, TRUE, FALSE + the same note.
# Writes the result to a NEW file (Stats_new.csv) so your original stays untouched.

import sys   # file names typed after the script name

from ygo_io import read_api, read_table, write_table   # shared file reading / writing

# --- File names -----------------------------------------------------------
NEW_CARDS = sys.argv[1] if len(sys.argv) > 1 else "new_cards.csv"          # made by ygo_add_new_cards.py
STATS_FILE = sys.argv[2] if len(sys.argv) > 2 else "Stats.csv"             # your current Stats.csv
API_FILE = sys.argv[3] if len(sys.argv) > 3 else "ygo_api_2026-10-20.txt"  # only used for proper spelling
OUT_FILE = sys.argv[4] if len(sys.argv) > 4 else "Stats_new.csv"           # where the result goes

# --- Load Stats.csv -------------------------------------------------------
columns, stats_rows = read_table(STATS_FILE)                  # Label, strictSupports, ..., Notes - every row, in order

have = set()                                                  # archetypes Stats.csv already has (lowercase)
for row in stats_rows:                                        # look at every label
    if row["Label"].startswith("Archetype"):                  # only 'Archetype = X' rows
        have.add(row["Label"].split("=", 1)[1].strip().lower())  # the X part, lowercase for comparing

# --- Proper spelling from the API -----------------------------------------
spelling = {}                                                 # lowercase -> API spelling, e.g. 'gmx' -> 'GMX'
for card in read_api(API_FILE):                               # every card YGOPRODeck has
    if card.get("archetype"):                                 # that has an archetype
        spelling[card["archetype"].lower()] = card["archetype"]

# --- Archetypes the new cards use -----------------------------------------
used = set()                                                  # every archetype on a new card (lowercase)
for row in read_table(NEW_CARDS)[1]:                          # the new cards, one at a time
    for part in row["archetype"].split("||"):                 # 'Archetype = a || Archetype = b' -> pieces
        if "=" in part:                                       # skip empty pieces
            used.add(part.split("=", 1)[1].strip())           # the name after '='

missing = sorted(used - have)                                 # used by new cards but not in Stats yet

# --- Build the new rows ---------------------------------------------------
example = next(r for r in stats_rows if r["Label"].startswith("Archetype"))  # copy an existing archetype row's values
new_rows = []                                                 # rows to add
for a in missing:                                             # one row per missing archetype
    row = dict(example)                                       # same TRUE/FALSE values and note
    row["Label"] = f"Archetype = {spelling.get(a, a.title())}"  # API spelling (or Title Case if the API has none)
    new_rows.append(row)

# --- Write Stats_new.csv --------------------------------------------------
write_table(OUT_FILE, columns, stats_rows + new_rows)        # every old row unchanged, the new archetypes at the end

print(f"Added {len(new_rows)} archetypes:")                   # report what was added
for r in new_rows:
    print("  ", r["Label"])
