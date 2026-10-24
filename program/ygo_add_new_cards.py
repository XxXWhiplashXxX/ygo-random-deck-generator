# ygo_add_new_cards.py
# Reads the YGOPRODeck API file and writes the cards that are NOT yet in cards.csv
# to new_cards.csv, using the same 22 columns. Only the "easy" columns are filled;
# archetype, materials, required cards, card rating and the 4 support columns stay blank for now.

import re    # regular expressions, used to squash whitespace in card text
import sys   # lets us read file names typed after the script name

from ygo_io import make_traits, read_api, read_table, write_table   # shared file reading / writing + traits

# --- File names -----------------------------------------------------------
API_FILE = sys.argv[1] if len(sys.argv) > 1 else "ygo_api_2026-10-20.txt"  # the API text you saved
OLD_FILE = sys.argv[2] if len(sys.argv) > 2 else "cards.csv"               # your current card file
OUT_FILE = sys.argv[3] if len(sys.argv) > 3 else "new_cards.csv"           # where the new rows go
STATS_FILE = sys.argv[4] if len(sys.argv) > 4 else "Stats.csv"             # holds your list of archetypes

# --- Load the API file ----------------------------------------------------
api_cards = read_api(API_FILE)                  # every card YGOPRODeck has

# --- Load the old card file -----------------------------------------------
columns, old_rows = read_table(OLD_FILE)       # the column names (in order) and every card we already have
old_ids = {row[columns[0]] for row in old_rows}               # their Card Ids
old_names = {row["name"].lower() for row in old_rows}         # their names (Konami sometimes gives a card a NEW Id)

# --- Known archetypes -------------------------------------------------------
known = set()                                                 # every archetype name we know, in lowercase
for row in read_table(STATS_FILE)[1]:                         # your Stats.csv, one label per row
    if row["Label"].startswith("Archetype"):                  # only rows like 'Archetype = sky striker'
        known.add(row["Label"].split("=", 1)[1].strip().lower())  # keep the part after '=', lowercase
for card in api_cards:                                        # also add every archetype the API knows
    if card.get("archetype"):                                 # some cards have none
        known.add(card["archetype"].lower())

# API "archetypes" we decided are junk (word pieces, made-up labels, tiny name groups, broken text)
DROP_ARCHETYPES = {"anti", "king", "moth", "fairy", "death", "knight", "butterfly", "alligator", "swamp",
                   "spell card", "dragon card", "fan-made cards", "cosmic synchro monster", "p", "book of",
                   "season", "celebration", "zombie counterpart", "dice", "cyclone", "greed", "solemn",
                   "mischief", "rites", "| supports_archetypes    power patron", "counter",
                   "doll", "train"}                       # doll: not an archetype; train: a series (handled later)
known -= DROP_ARCHETYPES                                      # take them out of the list we search for

# Real archetypes that must be a WHOLE word (none right now; add any that wrongly match inside other words)
WHOLE_WORD_ONLY = set()

# Group archetypes by their first character, so at each spot in the text
# we only test the few archetypes that could start there (much faster).
by_first = {}                                                 # e.g. 'k' -> ['kashtira', 'kuriboh', ...]
for a in known:                                               # go through every archetype
    by_first.setdefault(a[0], []).append(a)                   # file it under its first character


def find_archetypes(text, inside_words):
    """Every known archetype in text. If inside_words is True, archetypes of
    4+ letters may sit inside a longer word (Ankuriboh -> kuriboh)."""
    found = set()                                             # archetypes found so far
    for start in range(len(text)):                            # try every position in the text
        candidates = by_first.get(text[start], [])            # archetypes that begin with this character
        for name in (a for a in candidates if text.startswith(a, start)):  # the ones that really start here
            end = start + len(name)                           # where this match stops
            left_ok = start == 0 or not text[start - 1].isalnum()         # no letter / digit glued on the left
            right_ok = end == len(text) or not text[end].isalnum()         # ... or on the right
            if left_ok and right_ok:                          # a whole word (or words) -> always counts
                found.add(name)
            elif inside_words and len(name) >= 4 and name not in WHOLE_WORD_ONLY:  # part of a word, long enough
                found.add(name)
    return found


def make_archetypes(card, text):
    """Build the archetype text, e.g. 'Archetype = kashtira || Archetype = tearlaments'."""
    found = set()                                             # all archetypes for this card
    if card.get("archetype") and card["archetype"].lower() not in DROP_ARCHETYPES:  # 1) the API's own archetype
        found.add(card["archetype"].lower())                  #    (unless it's one we dropped as junk)
    found |= find_archetypes(card["name"].lower(), inside_words=True)   # 2) archetypes in the card name
    quoted = re.findall(r'"([^"]+)"', text.lower())           # 3) every "quoted" name in the card text
    for q in quoted:                                          # check each quoted name on its own
        found |= find_archetypes(q, inside_words=False)       # whole words only inside quotes
    return " || ".join(f"Archetype = {a}" for a in sorted(found))  # alphabetical, joined like the old file


# --- Materials ------------------------------------------------------------
# Every monster Type the API uses (Dragon, Winged Beast ...), lowercase, for the Xyz 'type =' rule.
monster_types = {c["race"].lower() for c in api_cards if c["frameType"] not in ("spell", "trap") and c["race"]}


def material_line(card):
    """The first line of the card text, which lists its materials."""
    desc = card["desc"]                                       # full text, with the API's line breaks
    if "[ Monster Effect ]" in desc:                          # Pendulum cards start with the Pendulum Effect...
        desc = desc.split("[ Monster Effect ]", 1)[1]         # ...so take the part after '[ Monster Effect ]'
    lines = [l.strip() for l in desc.splitlines() if l.strip()]  # non-empty lines, spaces trimmed
    lines = [l for l in lines if not l.startswith("(")]       # skip reminder notes like "(This card's original Level...)"
    if lines and re.match(r'[\d"]', lines[0]):                # real material lists start with a number or a "name"
        return lines[0]                                       # e.g. '2 Level 4 monsters' or '"Dark Magician" + ...'
    return ""                                                 # e.g. 'Cannot be Synchro Summoned...' -> no list


def xyz_spec(spec):
    """Turn 'Level 4 Machine monsters' into 'level = 4 && type = machine' when it's that simple."""
    m = re.match(r"Level (\d+) (.*?)\s*monsters?$", spec)     # 'Level <N> <rest> monster(s)'
    if not m:                                                 # anything else (e.g. 'Rank 4 ...'): leave as is
        return spec
    level, rest = m.group(1), m.group(2)                      # the number and the words in between
    if rest == "":                                            # 'Level 4 monsters'
        return f"level = {level}"
    if rest.lower() in monster_types:                         # 'Level 4 Machine monsters'
        return f"level = {level} && type = {rest.lower()}"
    q = re.fullmatch(r'"([^"]+)"', rest)                      # 'Level 4 "Springans" monsters'
    if q:
        return f"level = {level} && archetype = {q.group(1).lower()}"
    return spec                                               # harder ones stay plain text (the reader copes)


def material_slot(part, frame):
    """One piece between the ' + ' signs -> a 'count = N: (...)' line."""
    m = re.match(r"(\d+)(\+| or more)?\s+(.*)$", part)        # leading number, maybe '+' or ' or more'
    if m:                                                     # e.g. '2+ Level 4 monsters'
        count = m.group(1) + ("+" if m.group(2) else "")      # '2' or '2+'
        spec = m.group(3)                                     # the rest of the text
    else:                                                     # no number -> it means 1
        count, spec = "1", part
    if re.fullmatch(r'"[^"]+"', spec):                        # just a quoted card name, e.g. '"Dark Magician"'
        spec = f"name = {spec}"                               # -> name = "Dark Magician"
    elif "xyz" in frame:                                      # Xyz: structure 'Level N ...' like the old file
        spec = xyz_spec(spec)
    return f"count = {count}: ({spec})"                       # the finished line


def make_materials(card):
    """Build the whole materials cell for an Extra Deck monster ('' for everyone else)."""
    frame = card["frameType"]                                 # e.g. 'synchro_pendulum'
    kind = next((k for k in ("fusion", "synchro", "xyz", "link") if k in frame), None)  # which summon type
    if kind is None:                                          # not an Extra Deck monster
        return ""
    lines = [kind.capitalize() + ":"]                         # header, e.g. 'Synchro:'
    if kind == "link":                                        # Link: rating goes right after the header
        lines.append(f"link = {card.get('linkval')}")
    mat_text = material_line(card)                            # the material list (may be '')
    if mat_text:                                              # only add slots when there is a list
        for part in mat_text.split(" + "):                    # one slot per ' + ' piece
            lines.append(material_slot(part.strip(), frame))
    if kind == "synchro" and card.get("level"):               # Synchro: Levels must add up to its own Level (skip Level 0)
        lines.append(f"sumlevel = {card.get('level')}")
    return "\n".join(lines)                                   # one cell, lines separated by line breaks


# --- Required cards -------------------------------------------------------
# Every real card name the API knows, lowercase -> proper spelling (Skill cards aren't real deck cards).
real_names = {c["name"].lower(): c["name"] for c in api_cards if c["frameType"] != "skill"}

# Fusion monsters that say one of these can't use Polymerization (Contact Fusions, Gladiator Beasts ...)
NO_POLY = re.compile(r"Cannot be Fusion Summoned|Must (first )?be Special Summoned", re.I)


def make_required(card, text):
    """Build the required-cards text, e.g. 'Name = Dark Magician && Name = Polymerization'."""
    text = re.sub(r"\([^)]*always treated as[^)]*\)", "", text)  # drop '(This card is always treated as "X")' notes
    found = set()                                             # proper card names this card needs
    for q in re.findall(r'"([^"]+)"', text):                  # every "quoted" name in the text
        real = real_names.get(q.lower())                      # is it the name of a real card?
        if real and real.lower() != card["name"].lower():     # yes, and it isn't this card itself
            found.add(real)                                   # keep the API's spelling
    if "fusion" in card["frameType"] and not NO_POLY.search(text):  # a Fusion monster that can use Polymerization
        found.add("Polymerization")
    return " && ".join(f"Name = {n}" for n in sorted(found))  # alphabetical, joined like the old file


# --- Card kinds we skip ---------------------------------------------------
SKIP_FRAMES = {"skill", "token"}                # Speed Duel Skill cards and Tokens are not deck cards
SKIP_NAMES = {"???"}                            # the tournament placeholder card


def primary_type(card):
    """Monster, Spell or Trap, from the API 'type' text."""
    if card["frameType"] == "spell":                          # Spell Card
        return "Spell"
    if card["frameType"] == "trap":                           # Trap Card
        return "Trap"
    return "Monster"                                          # everything else left is a monster


def number(value):
    """Turn a number into text, or '' if the API has none."""
    return "" if value is None else str(value)                # missing values become blank cells


# --- Build the new rows ---------------------------------------------------
new_rows = []                                                 # the rows we will write
for card in api_cards:                                        # go through every card in the API
    if str(card["id"]) in old_ids:                            # already in cards.csv -> skip it
        continue
    if card["name"].lower() in old_names:                     # same card under a new Id (e.g. Dark Magician) -> skip it too,
        continue                                              # so it isn't added twice and the existing row stays as it is
    if card["frameType"] in SKIP_FRAMES or card["name"] in SKIP_NAMES:  # Skill, Token or '???' -> skip it
        continue

    is_monster = primary_type(card) == "Monster"              # many columns only apply to monsters
    if is_monster and not card["race"]:                       # monster with no Type = the API hasn't filled it in yet
        print("Skipped (API data incomplete):", card["name"])   # say which card, so we can add it later
        continue
    frame = card["frameType"]                                 # e.g. 'xyz', 'link', 'synchro_pendulum'
    row = {col: "" for col in columns}                        # start with all 22 columns blank

    row[columns[0]] = str(card["id"])                         # Card Id (first column; its name has a hidden mark)
    row["name"] = card["name"]                                # card name, as written by the API
    row["primary card type"] = primary_type(card)             # Monster / Spell / Trap
    row["traits"] = make_traits(card)                         # e.g. 'Trait = Effect'
    row["card text"] = re.sub(r"\s+", " ", card["desc"]).strip()  # squash line breaks/extra spaces into one space
    row["era"] = "GO RUSH!!"                                  # a new card is from the newest era (ygo_set_eras.py checks its date)
    row["archetype"] = make_archetypes(card, card["desc"])    # all archetypes, old style
    row["materials"] = make_materials(card)                   # '' unless it's an Extra Deck monster
    row["required cards"] = make_required(card, row["card text"])  # specific cards the text names

    if is_monster:                                            # monster-only columns
        row["type"] = card["race"]                            # monster Type, e.g. 'Machine'
        row["attribute"] = card.get("attribute", "")          # e.g. 'LIGHT'
        row["atk"] = number(card.get("atk"))                  # ATK
        if "link" not in frame:                               # Link monsters have no DEF -> leave blank
            row["def"] = number(card.get("def"))              # DEF
        if "xyz" in frame:                                    # the API stores Rank in 'level' for Xyz
            row["rank"] = number(card.get("level"))
        elif "link" in frame:                                 # Link Rating is 'linkval'
            row["link rating"] = number(card.get("linkval"))
        else:                                                 # everyone else has a normal Level
            row["level"] = number(card.get("level"))

    new_rows.append(row)                                      # keep the finished row

# --- Write the file -------------------------------------------------------
write_table(OUT_FILE, columns, new_rows)                      # same columns, same order as your cards.csv

print(f"Wrote {len(new_rows)} new cards to {OUT_FILE}")       # tell us how many were written
