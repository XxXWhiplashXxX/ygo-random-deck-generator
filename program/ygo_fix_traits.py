# ygo_fix_traits.py
# Rebuilds the traits column of EVERY card from the YGOPRODeck API, using the same rule as
# ygo_add_new_cards.py (make_traits in ygo_io.py), so old and new cards follow one rule. Fixes the Normal/Effect mix-up,
# missing Flip/Tuner/Xyz/Pendulum traits, lowercase 'ritual', etc.
# Also repairs card texts whose bullet "●" was damaged long ago (it shows as "?Ã‚Â"): they get YGOPRODeck's text.
# Writes a NEW file (cards_new.csv); the input file is not changed.

import re    # squashes the API text's line breaks into spaces
import sys   # file names typed after the script name
from collections import Counter  # counts how many cards got each kind of fix

from ygo_io import make_traits, read_api, read_table, write_table   # shared file reading / writing + traits

# --- File names -----------------------------------------------------------
IN_FILE = sys.argv[1] if len(sys.argv) > 1 else "cards_merged.csv"        # the merged card file
API_FILE = sys.argv[2] if len(sys.argv) > 2 else "ygo_api_2026-10-20.txt"  # the API text you saved
OUT_FILE = sys.argv[3] if len(sys.argv) > 3 else "cards_new.csv"           # where the fixed file goes

# --- Load the API, keyed by Card Id ---------------------------------------
api_cards = read_api(API_FILE)                                # every card YGOPRODeck has
api = {str(c["id"]): c for c in api_cards}                    # '96146814' -> that card's API data
api_by_name = {c["name"].lower(): c for c in api_cards}       # fallback: Konami sometimes gives a card a new Id


# --- Read, fix, and count -------------------------------------------------
columns, rows = read_table(IN_FILE)                           # the merged card file

id_col = columns[0]                                           # 'Card Id' (with the hidden mark in its name)
changed = 0                                                   # how many cards we changed
repaired = 0                                                  # how many damaged card texts we repaired
DAMAGED = ("?Ã‚Â\x8f", "?Â\x8f")                             # the two broken forms of the bullet "●" (an encoding mix-up)
kinds = Counter()                                             # what kind of change, for the report
skipped = []                                                  # cards the API can't help with

for row in rows:                                              # every card
    card = api.get(row[id_col]) or api_by_name.get(row["name"].lower())   # its API data (by Id, else by name)
    text = row.get("card text") or ""
    if any(d in text for d in DAMAGED):                       # a damaged bullet in the card text:
        if card is not None and card.get("desc"):
            row["card text"] = re.sub(r"\s+", " ", card["desc"]).strip()   # take YGOPRODeck's text, one line like every other card (as ygo_add_new_cards.py does)
        else:
            for d in DAMAGED:
                text = text.replace(d, "●")                    # not in the API: just put the bullet back
            row["card text"] = text
        repaired += 1
    if card is None:                                          # not in the API (shouldn't happen)
        skipped.append(row["name"])
        continue
    if card["frameType"] not in ("spell", "trap") and not card.get("typeline"):  # monster with no type info
        skipped.append(row["name"])
        continue
    new = make_traits(card)                                   # the traits the API says it should have
    if new != row["traits"]:                                  # different from what the file says
        old_set = {t.strip() for t in row["traits"].split("||")}   # old labels
        new_set = {t.strip() for t in new.split("||")}             # new labels
        added = ",".join(sorted(t.replace("Trait = ", "") for t in new_set - old_set))    # what we added
        removed = ",".join(sorted(t.replace("Trait = ", "") for t in old_set - new_set))  # what we removed
        kinds[f"+[{added}] -[{removed}]"] += 1                # count this kind of change
        row["traits"] = new                                   # make the fix
        changed += 1

# --- Write the new file ---------------------------------------------------
write_table(OUT_FILE, columns, rows)                          # every card, with fixed traits

print(f"Fixed traits on {changed} cards, repaired {repaired} damaged card texts, wrote {len(rows)} cards to {OUT_FILE}")  # summary
for kind, n in kinds.most_common():                           # each kind of fix, most common first
    print(f"  {n:4d}  {kind}")
if skipped:                                                   # anything we couldn't check
    print("Not checked (no API type info):", skipped)
