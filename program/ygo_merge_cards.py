# ygo_merge_cards.py
# Joins your cards.csv and new_cards.csv (made by ygo_add_new_cards.py) into one file.
# Writes the result to a NEW file (cards_merged.csv) so your original cards.csv stays untouched.
#
#   python ygo_merge_cards.py cards.csv new_cards.csv cards_merged.csv

import sys  # file names typed after the script name

from ygo_io import read_table, write_table   # shared file reading / writing

OLD_FILE = sys.argv[1] if len(sys.argv) > 1 else "cards.csv"          # your current card file
NEW_FILE = sys.argv[2] if len(sys.argv) > 2 else "new_cards.csv"      # made by ygo_add_new_cards.py
OUT_FILE = sys.argv[3] if len(sys.argv) > 3 else "cards_merged.csv"   # where the joined file goes

columns, old_rows = read_table(OLD_FILE)                      # your existing cards
new_columns, new_rows = read_table(NEW_FILE)                  # the new cards
if new_columns != columns:                                    # both files must have the same columns
    sys.exit("The two files have different columns - stopping.")

old_ids = {r[columns[0]] for r in old_rows}                   # Card Ids already in the old file
added = [r for r in new_rows if r[columns[0]] not in old_ids] # only cards that aren't there yet (safe to re-run)
rows = old_rows + added                                       # old cards first, then the new ones

write_table(OUT_FILE, columns, rows)
print(f"{len(old_rows)} old + {len(added)} new = {len(rows)} cards written to {OUT_FILE}")
