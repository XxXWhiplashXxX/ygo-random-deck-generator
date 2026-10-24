# ygo_wiki_supports.py
# Adds the supports Yugipedia's editors list on a card's page that our parser missed.
# Yugipedia's card pages say what each card supports: archetypes/series ("supports_archetypes", e.g. Trap Hole)
# and Types/Attributes ("supports", e.g. Reptile, DARK). ygo_wiki_series.py saved them in card_archseries.csv.
# A support is added to the card ONLY if all of these are true:
#   (a) the parser doesn't already have it (as a support OR a need) - the parser's reading always wins
#   (b) it is one of our labels (a row in Stats_new.csv)
#   (c) the card's own text names it (a "quoted" archetype name, or the Type / Attribute word)
#       - this drops Yugipedia's mistakes, e.g. Tour Bus To Forbidden Realms "supporting" every Attribute
#   (d) the card is not itself a member of that archetype/series (members already go with their own deck)
# Each label goes in the column its Stats row allows (strictSupports, or generalSupports if only that is TRUE).
# Runs after the support-column parser (ygo_fill_supports.py) on every update. Rewrites the file in place.
#
#   python ygo_wiki_supports.py cards_filled.csv

import re    # regular expressions: tidying wiki names, looking for words in the card text
import sys   # the file name typed after the script name
from collections import Counter   # counts what was added and what was skipped

from ygo_io import read_table, write_table   # shared file reading / writing

CARDS_FILE = sys.argv[1] if len(sys.argv) > 1 else "cards_filled.csv"   # the cards, after the parser
STATS_FILE = "Stats_new.csv"                  # our labels
WIKI_FILE = "card_archseries.csv"             # what Yugipedia says about each card

# Sub-archetypes the card text names WITHOUT quoting the archetype's name (lowercase name -> patterns)
#   Number 10X  = "Number 101" .. "Number 107":  'a number between "101" and "107" in its name'
#   Number C10X = "Number C101" .. "Number C107": '"Number C101" through "Number C107" in its name'
ALSO_NAMED = {"number 10x": [r'between "101" and "107"', r'"Number 10[1-7]'],
              "number c10x": [r'"Number C10[1-7]']}
COLUMNS = ("strictSupports", "generalSupports", "strictSupportedBy", "generalSupportedBy")   # the 4 support columns


def tidy(name):
    """A Yugipedia name -> our name: drop comment marks and a trailing '(archetype)', '(Spell/Trap archetype)', '(series)'."""
    name = re.sub(r"<!--.*|-->", "", name).strip()                     # e.g. 'Slime-->' -> 'Slime'
    return re.sub(r"\s*\((?:[^)]*archetype|series)\)$", "", name, flags=re.I).strip()   # 'Kuriboh (archetype)' -> 'Kuriboh'


def atoms(text):
    """Every single label in a column, lowercase: '(Archetype = HERO && Trait = Fusion Monster) || Level = 4'
       -> {'archetype = hero', 'trait = fusion monster', 'level = 4'}."""
    return {p.strip().lower() for p in re.split(r"\|\||&&|[()]", text or "") if p.strip()}


def names_it(text, label):
    """(c) Does the card text name this label? Archetypes/series: their name right after a quote mark ('"Hole"',
       '"Trap Hole"', '"Fluffal" monster'). Types/Attributes: the whole word, same capitals ('Reptile', 'DARK'),
       but not 'non-DARK' (a card that hits or hates non-DARK monsters doesn't support DARK)."""
    kind, name = label.split(" = ", 1)
    if kind in ("Archetype", "Series"):
        if '"' + name.lower() in text.lower():                         # a quote mark, then the name
            return True
        return any(re.search(p, text) for p in ALSO_NAMED.get(name.lower(), []))   # the Number 10X wordings
    return re.search(rf"(?<!non-)\b{re.escape(name)}\b", text) is not None   # \b = a word edge ('Fiend' isn't 'Archfiend'); (?<!non-) = not right after 'non-'


# --- Our labels, and which column each may go in -------------------------------------------
place = {}                                    # lowercase label -> (the label spelled as in Stats, the column to put it in)
for row in read_table(STATS_FILE)[1]:
    label = row["Label"]                      # e.g. 'Archetype = Trap Hole', 'Type = Reptile'
    if label.startswith(("Archetype = ", "Series = ", "Type = ", "Attribute = ")):
        if row["strictSupports"] == "TRUE":   # most labels: a strict support
            place[label.lower()] = (label, "strictSupports")
        elif row["generalSupports"] == "TRUE":   # a few only allow a general one
            place[label.lower()] = (label, "generalSupports")

# --- What Yugipedia says each card supports -------------------------------------------------
wiki = {}                                     # card name -> the labels Yugipedia gives it
for row in read_table(WIKI_FILE, encoding="utf-8")[1]:
    if row["status"] not in ("ok", "verified"):   # no wiki page for this card
        continue
    wanted = []
    for name in (tidy(v) for v in row.get("supports_archetypes", "").split(" | ") if v.strip()):
        wanted.append(f"Archetype = {name}" if f"archetype = {name.lower()}" in place else f"Series = {name}")   # archetype first, then series
    for name in (v.strip() for v in row.get("supports", "").split(" | ") if v.strip()):
        wanted.append(f"Type = {name}" if f"type = {name.lower()}" in place else f"Attribute = {name}")   # 'Xyz Monster' etc. fail (b): skipped
    wiki[row["name"]] = wanted

# --- Add them to the cards -------------------------------------------------------------------
columns, rows = read_table(CARDS_FILE)
count, examples = Counter(), []               # what happened, and a few added ones to show
for row in rows:
    have = set().union(*(atoms(row[c]) for c in COLUMNS))             # (a) what the parser already gave this card
    members = atoms(row.get("archetype")) | atoms(row.get("series"))   # (d) what the card belongs to
    for label in wiki.get(row["name"], []):
        key = label.lower()
        if key in have:
            count["parser already has it"] += 1
        elif key not in place:
            count["not one of our labels"] += 1                        # e.g. 'Xyz Monster', or an archetype we don't have
        elif key in members:
            count["card is a member"] += 1
        elif not names_it(row["card text"], place[key][0]):
            count["card text doesn't name it"] += 1
        else:                                                          # passed every check: add it
            label, column = place[key]                                 # Stats' spelling, and the column it goes in
            row[column] = f"{row[column]} || {label}" if row[column] else label   # ' || ' = "or" another support
            have.add(key)                                              # (so a label Yugipedia lists twice is added once)
            count["added"] += 1
            if len(examples) < 12:
                examples.append(f"{row['name']} -> {label}")

write_table(CARDS_FILE, columns, rows)
print(", ".join(f"{k}: {v}" for k, v in count.most_common()))
print("  e.g.", " | ".join(examples))
