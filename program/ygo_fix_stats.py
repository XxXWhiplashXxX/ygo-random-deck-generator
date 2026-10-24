# ygo_fix_stats.py
# Tidies Stats_new.csv and keeps cards_new.csv in step with it:
#   1. removes duplicate labels (keeps the first one)
#   2. merges archetypes that are the same thing under two spellings (e.g. 'Assault Mode' -> '/Assault Mode'),
#      in Stats AND in the cards' archetype column
#   3. tidies spacing and capitals: 'Trait  = X', 'Trait= X', 'trait = X', 'Link Rating =1', 'Def = 100'
#      all become 'Field = Value' with the field written one standard way
# Both files are rewritten in place (they are the new files we made, not your originals).

import re   # regular expressions, for splitting a label into field / value
import sys  # file names typed after the script name

from ygo_io import read_table, write_table   # shared file reading / writing

STATS_FILE = sys.argv[1] if len(sys.argv) > 1 else "Stats_new.csv"   # the big new Stats file
CARDS_FILE = sys.argv[2] if len(sys.argv) > 2 else "cards_new.csv"   # the big new cards file

# Same archetype, two spellings: wrong spelling (lowercase) -> the spelling to keep (as in Stats)
MERGE_ARCHETYPES = {"assault mode": "/Assault Mode"}

# How each label field should be written (lowercase field -> standard spelling)
FIELD_NAMES = {"trait": "Trait", "def": "DEF", "atk": "ATK", "link rating": "Link Rating", "level": "Level",
               "rank": "Rank", "scale": "Scale", "type": "Type", "attribute": "Attribute",
               "archetype": "Archetype", "card type": "Card Type",
               "alternative win condition": "Alternative Win Condition"}


def tidy_label(label):
    """'Trait  = Effect Monster' -> 'Trait = Effect Monster'; 'Link Rating =1' -> 'Link Rating = 1'."""
    m = re.match(r"\s*(.*?)\s*(!=|<=|>=|=)\s*(.*?)\s*$", label)  # field, operator, value (spaces ignored)
    if not m:                                                 # no operator at all: leave it alone
        return label
    field, op, value = m.groups()                             # the three parts
    field = FIELD_NAMES.get(field.lower(), field)             # standard spelling of the field
    return f"{field} {op} {value}"                            # put back together with single spaces


# --- Fix Stats ------------------------------------------------------------
columns, stats = read_table(STATS_FILE)                       # Label, strictSupports, ..., Notes - every row

merge_lower = {k.lower() for k in MERGE_ARCHETYPES}           # wrong spellings, lowercase
kept, seen = [], set()                                        # rows we keep; labels already kept (lowercase)
tidied = removed = 0                                          # counters for the report
for row in stats:                                             # every Stats row, in order
    new_label = tidy_label(row["Label"])                            # tidy spacing and capitals
    if new_label != row["Label"]:                             # count the ones that changed
        tidied += 1
        row["Label"] = new_label
    if new_label.startswith("Archetype = ") and new_label[12:].lower() in merge_lower:  # a wrong spelling
        removed += 1                                          # drop it; the right spelling's row stays
        continue
    key = new_label.lower()                                   # compare without caring about capitals
    if key in seen:                                           # a duplicate of an earlier row
        removed += 1
        continue
    seen.add(key)                                             # remember it
    kept.append(row)                                          # keep the row

write_table(STATS_FILE, columns, kept)                        # write Stats back
print(f"Stats: tidied {tidied} labels, removed {removed} rows, {len(kept)} rows left")

# --- Fix the cards' archetype column --------------------------------------
card_columns, cards = read_table(CARDS_FILE)                  # the cards file

fixed = 0                                                     # cards we changed
for card in cards:                                            # every card
    parts = [p.strip() for p in card["archetype"].split("||") if p.strip()]  # its archetype labels
    new_parts = []                                            # the labels after merging
    for p in parts:                                           # each label, e.g. 'Archetype = assault mode'
        name = p.split("=", 1)[1].strip() if "=" in p else p  # the archetype name
        if name.lower() in MERGE_ARCHETYPES:                  # a wrong spelling...
            p = f"Archetype = {MERGE_ARCHETYPES[name.lower()].lower()}"  # ...becomes the kept one (lowercase, like the file)
        if p not in new_parts:                                # don't list the same archetype twice
            new_parts.append(p)
    new_value = " || ".join(sorted(new_parts))                # alphabetical, like the rest of the file
    if new_value != " || ".join(sorted(parts)):               # only count real changes
        fixed += 1
    if new_parts != parts:                                    # write back only if something changed
        card["archetype"] = new_value

write_table(CARDS_FILE, card_columns, cards)                  # write the cards back
print(f"Cards: merged archetype spellings on {fixed} cards")
