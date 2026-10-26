# ygo_clean_labels.py
# Removes or merges duplicate ARCHETYPE labels, using Yugipedia first and YGOPRODeck only as a last resort.
# It is the last step of every card update, and can also be run by hand on the finished files.
#
# Every 'Archetype = X' label gets one decision, checked in this order:
#   1. KEEP     Yugipedia's archetype list has X                         ('Blackwing')
#   2. SERIES   Yugipedia calls X a series (its list, or 'X (series)' on a card page)    ('Umi', 'Flint')
#   3. TYPE     X is a monster Type or Attribute                         ('psychic' -> Type = Psychic)
#   4. MERGE    X is a misspelling of an archetype on Yugipedia's list  ('Ritual Beast Ulti-' -> 'Ritual Beast Ulti')
#   5. KEEP     a Yugipedia card page names X as an archetype           ('Empower', 'King')
#   6. Yugipedia has nothing about X, so X came from YGOPRODeck:
#        MERGE  the cards' Yugipedia pages say what X really supports    ('fossil fusion' -> 'Fossil')
#        REMOVE X is part of a longer name already used                  ('gaia' -> already 'Gaia The Fierce Knight')
#        KEEP   X is unique: kept, with capitals                          ('ganbara' -> 'Ganbara')
# Then the decisions are applied everywhere: Stats.csv's rows, the cards' archetype and series columns,
# and the four support columns.
#
#   python ygo_clean_labels.py cards.csv Stats.csv        (run where archetype_list.json, series_list.json
#                                                          and card_archseries.csv are: the work folder, or data/)
# Add --dry-run to only print the decisions without changing any file.

import re    # matching labels inside the support columns
import sys   # the file names typed after the script name

from ygo_io import read_json, read_table, write_table   # shared file reading / writing

args = [a for a in sys.argv[1:] if a != "--dry-run"]
DRY_RUN = "--dry-run" in sys.argv                                     # True = report only, write nothing
CARDS_FILE = args[0] if len(args) > 0 else "cards_filled.csv"         # the card file to clean (in place)
STATS_FILE = args[1] if len(args) > 1 else "Stats_new.csv"            # the label file to clean (in place)
SUPPORT_COLS = ("strictSupports", "generalSupports", "strictSupportedBy", "generalSupportedBy")
SERIES_NOTE = "Series are not generic by definition"                  # the Notes text series rows use


def tidy(title: str) -> str:
    """'Dark Magician (archetype)' / 'Umi (series)' / 'Monarch (Spell/Trap archetype)' -> the bare name."""
    return re.sub(r"\s*\((?:[^()]*archetype|series)\)$", "", title.strip(), flags=re.I).strip()


def letters(text: str) -> str:
    """Only the letters and digits, lowercase: 'Ritual Beast Ulti-' and 'Ritual Beast Ulti' both give 'ritualbeastulti'."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def wiki_titles(file: str) -> dict:
    """One of Yugipedia's category lists -> {lowercase bare name: spelling}."""
    members = (read_json(file, {}) or {}).get("query", {}).get("categorymembers", [])
    return {tidy(p["title"]).lower(): tidy(p["title"]) for p in members}


# ---------------- what Yugipedia says ----------------
WIKI_ARCHETYPES = wiki_titles("archetype_list.json")        # Category:TCG and OCG archetypes
WIKI_SERIES = wiki_titles("series_list.json")               # Category:TCG and OCG series
if not WIKI_ARCHETYPES or not WIKI_SERIES:                  # never decide anything from a missing list
    sys.exit("archetype_list.json / series_list.json are missing or empty: nothing was changed.")
WIKI_BY_LETTERS = {letters(n): n for n in WIKI_ARCHETYPES.values()}   # for spotting misspellings (step 4)

page_names, page_series = {}, {}                            # names card pages give: as archetypes / as '(series)'
wiki_rows = {}                                              # card name (lowercase) -> its row in card_archseries.csv
for row in read_table("card_archseries.csv", encoding="utf-8")[1]:
    if row["status"] != "ok":                               # no wiki page for this card
        continue
    wiki_rows[row["name"].lower()] = row
    for value in (row["archseries"] + " | " + row["related_to_archseries"]).split(" | "):
        value = value.strip()
        if not value:
            continue
        target = page_series if value.lower().endswith("(series)") else page_names
        target[tidy(value).lower()] = tidy(value)

# ---------------- our files ----------------
stat_columns, stats = read_table(STATS_FILE)
card_columns, cards = read_table(CARDS_FILE)
TYPES = {r["Label"][7:].lower(): r["Label"] for r in stats if r["Label"].startswith("Type = ")}        # 'zombie' -> 'Type = Zombie'
ATTRIBUTES = {r["Label"][12:].lower(): r["Label"] for r in stats if r["Label"].startswith("Attribute = ")}
STAT_SPELLING = {r["Label"][12:].lower(): r["Label"][12:] for r in stats if r["Label"].startswith("Archetype = ")}


def split_labels(text: str) -> list:
    """'Archetype = a || Archetype = b' -> ['a', 'b'] (the names after '=')."""
    return [p.split("=", 1)[1].strip() for p in (text or "").split("||") if "=" in p]


def members(name: str) -> list:
    """The cards whose archetype column has this archetype."""
    key = name.lower()
    return [c for c in cards if key in (a.lower() for a in split_labels(c.get("archetype")))]


def nice(name: str) -> str:
    """Capitals for a name that only exists in lowercase: 'ganbara' -> 'Ganbara'."""
    return name.title() if name == name.lower() else name


# ---------------- 1. one decision per archetype ----------------
def decide(name: str) -> tuple:
    """(action, new label or None, reason). action is 'keep', 'rename' or 'remove'."""
    key = name.lower()
    if key in WIKI_ARCHETYPES:                                                          # step 1
        better = WIKI_ARCHETYPES[key] if name == key else name                         # fix an all-lowercase spelling
        return ("keep" if better == name else "rename", "Archetype = " + better, "on Yugipedia's archetype list")
    if key in WIKI_SERIES or key in page_series:                                        # step 2
        series = WIKI_SERIES.get(key) or page_series[key]
        return ("rename", "Series = " + series, "Yugipedia calls it a series")
    if key in TYPES:                                                                    # step 3
        return ("rename", TYPES[key], "it is a monster Type")
    if key in ATTRIBUTES:
        return ("rename", ATTRIBUTES[key], "it is an Attribute")
    if letters(name) in WIKI_BY_LETTERS:                                                # step 4
        return ("rename", "Archetype = " + WIKI_BY_LETTERS[letters(name)], "misspelling of a Yugipedia archetype")
    if key in page_names:                                                               # step 5
        better = nice(page_names[key]) if name == key else name
        return ("keep" if better == name else "rename", "Archetype = " + better, "named on Yugipedia card pages")
    # step 6: Yugipedia has nothing about this name - it came from YGOPRODeck
    first_word = key.split()[0]
    pages = [wiki_rows[c["name"].lower()] for c in members(name) if c["name"].lower() in wiki_rows]
    if key in wiki_rows:                                                                # a card with this exact name
        pages.append(wiki_rows[key])
    said = set()                                                                        # archetypes those pages name
    for page in pages:
        for col in ("supports_archetypes", "archseries"):
            said |= {tidy(v) for v in page.get(col, "").split(" | ") if tidy(v).lower() in WIKI_ARCHETYPES}
    same_word = sorted(a for a in said if a.lower().split()[0] == first_word)          # 'Fossil' for 'fossil fusion'
    if len(same_word) == 1:
        return ("rename", "Archetype = " + same_word[0], "Yugipedia: its cards support " + same_word[0])
    longer = sorted({n for n in list(WIKI_ARCHETYPES.values()) + list(page_names.values()) + list(WIKI_SERIES.values())
                     if n.lower() != key and n.lower().startswith(key + " ")})   # a set: each name once
    if longer:                                                                          # 'gaia' -> 'Gaia The Fierce Knight' ...
        return ("remove", None, "duplicate of " + ", ".join(longer[:3]))
    better = nice(name)
    return ("keep" if better == name else "rename", "Archetype = " + better, "YGOPRODeck only, no duplicate")


names = sorted(set(STAT_SPELLING.values()) | {a for c in cards for a in split_labels(c.get("archetype"))}, key=str.lower)
plan = {}                                                                               # lowercase name -> decision
for n in names:
    if n.lower() not in plan:
        plan[n.lower()] = decide(STAT_SPELLING.get(n.lower(), n))

# A removed label that support columns still use would leave broken text behind: keep it instead.
def support_pattern(name: str):
    """Finds 'Archetype = <name>' as a whole label (not the start of a longer name) inside a support column."""
    return re.compile(r"Archetype = " + re.escape(name) + r"(?=\s*(?:\)|\|\||&&|$))", re.I)

for key, (action, _new, why) in list(plan.items()):
    if action == "remove" and any(support_pattern(key).search(c.get(col, "")) for c in cards for col in SUPPORT_COLS):
        plan[key] = ("keep", "Archetype = " + nice(STAT_SPELLING.get(key, key)), why + " (kept: support columns use it)")

changes = {k: v for k, v in plan.items() if v[0] != "keep"}
print(f"{len(plan)} archetypes checked, {len(changes)} changed:")
for key, (_action, new, why) in sorted(changes.items()):
    print(f"   {STAT_SPELLING.get(key, key)!r:36} -> {new or 'removed'}   ({why})")
if DRY_RUN:
    sys.exit(0)

# ---------------- 2. Stats.csv ----------------
template = next(r for r in stats if r["Label"].startswith("Archetype = "))   # TRUE/FALSE values for a new row
existing = {r["Label"].lower() for r in stats}
new_stats, seen = [], set()
for r in stats:
    label = r["Label"]
    if label.startswith("Archetype = ") and label[12:].lower() in plan:
        action, new, _ = plan[label[12:].lower()]
        if action == "remove":
            continue                                                       # drop the row
        if new.lower() != label.lower() and new.lower() in existing:
            continue                                                       # merged into a row that already exists
        r = dict(r, Label=new)                                             # renamed (or respelled)
        if new.startswith("Series = "):
            r["Notes"] = SERIES_NOTE
    if r["Label"].lower() in seen:
        continue                                                           # never two rows for one label
    seen.add(r["Label"].lower())
    new_stats.append(r)
for _action, new, _ in plan.values():                                       # a merge target no row has yet
    if new and new.lower() not in seen:
        row = dict(template, Label=new)
        if new.startswith("Series = "):
            row["Notes"] = SERIES_NOTE
        new_stats.append(row)
        seen.add(new.lower())

# ---------------- 3. the cards ----------------
def join(prefix: str, values: set) -> str:
    """{'b', 'a'} -> 'Prefix = a || Prefix = b' (lowercase, A-Z: the card file's usual format)."""
    return " || ".join(f"{prefix} = {v}" for v in sorted({v.lower() for v in values}))


def top_level_split(text: str) -> list:
    """Split at '||' only OUTSIDE brackets: 'A || (B || C)' -> ['A ', ' (B || C)'] (a bracket group stays whole)."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
        elif text.startswith("||", i) and depth == 0:
            parts.append(text[start:i])
            start = i + 2
            i += 1                                                         # skip the second '|'
        i += 1
    parts.append(text[start:])
    return parts


replacers = [(support_pattern(k), new) for k, (action, new, _) in changes.items() if action == "rename"]
for c in cards:
    archetypes = set()
    series = set(split_labels(c.get("series")))
    for a in split_labels(c.get("archetype")):
        action, new, _ = plan.get(a.lower(), ("keep", "Archetype = " + a, ""))
        if action == "remove" or not new:
            continue
        if new.startswith("Archetype = "):
            archetypes.add(new[12:])
        elif new.startswith("Series = "):
            series.add(new[9:])                                            # now a series member instead
        # (a Type / Attribute: the card's own type column already says what it is, so nothing to add)
    c["archetype"] = join("Archetype", archetypes)
    if "series" in c:
        c["series"] = join("Series", series)
    for col in SUPPORT_COLS:
        text = c.get(col, "")
        for pattern, new in replacers:
            text = pattern.sub(new, text)                                  # 'Archetype = Umi' -> 'Series = Umi'
        pieces = [p.strip() for p in top_level_split(text)]                # a merge can create the same label twice
        c[col] = " || ".join(dict.fromkeys(p for p in pieces if p))        # (dict.fromkeys keeps order, drops repeats)

write_table(STATS_FILE, stat_columns, new_stats)
write_table(CARDS_FILE, card_columns, cards)
print(f"Stats: {len(stats)} -> {len(new_stats)} rows. Cards rewritten.")
