# ygo_wiki_labels.py
# Rebuilds the archetype labels and adds series labels, using what Yugipedia says each card belongs to
# (card_archseries.csv, made by ygo_wiki_series.py). Whether a name is a series or an archetype comes from
# Yugipedia's two lists (series_list.json, archetype_list.json).
#   - archetype column: the wiki's archetypes for the card, members AND related cards
#   - series column (new, right after archetype): the wiki's series for the card, members AND related
#   - cards with no wiki page keep their old archetypes
#   - Stats: archetype rows are rebuilt to match the cards, and a 'Series = X' row is added per series
# Rewrites cards_new.csv and Stats_new.csv in place.
#
#   python ygo_wiki_labels.py

import re    # regular expressions, for tidying wiki names
from collections import Counter  # counts how many cards use each label

from ygo_io import read_json, read_table, write_table   # shared file reading / writing

CARDS_FILE = "cards_new.csv"          # the big new cards file
STATS_FILE = "Stats_new.csv"          # the big new Stats file
WIKI_FILE = "card_archseries.csv"     # what the wiki says about each card
SERIES_FILE = "series_list.json"      # the series names (Yugipedia's Category:TCG and OCG series, about 225)
ARCHETYPE_FILE = "archetype_list.json"   # the archetype names (Category:TCG and OCG archetypes, about 610)

# Wiki names that should be merged into another name (wiki name, lowercase -> name to use)
# (Number 10X and Number C10X are NOT merged: they are sub-archetypes of Number, like Number C, so they keep their own labels)
MERGE = {"number (spell/trap archetype)": "Number",
         "monarch (spell/trap archetype)": "Monarch", "cyberdark (spell/trap archetype)": "Cyberdark"}
# Wiki names that are not archetypes at all (lowercase)
DROP = {"duel winner", "", "fan-made cards", "type booster"}   # (Fan-Made Cards: designed by fans in contests; Type Booster: old Type Equip Spells - neither is a deck theme)
# Labels from earlier steps that the wiki now replaces (lowercase): our hand-made Train group
OLD_HAND_MADE = {"train"}
# Cards a series page lists that the cards' own pages don't (series -> card names). Add more as you find them.
EXTRA_SERIES = {"Train": ["Number 27: Dreadnought Dreadnoid", "Switch Point", "Goldilocks the Battle Landscaper"]}


def tidy_wiki_name(name):
    """Clean one wiki name: drop leftover comment marks and a trailing '(archetype)' / '(series)'."""
    name = re.sub(r"<!--.*|-->", "", name).strip()           # e.g. 'Slime-->' -> 'Slime'
    return re.sub(r"\s*\((?:archetype|series)\)$", "", name, flags=re.I).strip()   # 'Fusion (archetype)' -> 'Fusion'


# --- The series and archetypes, matched without caring about capitals -----------------
series_titles = [p["title"] for p in read_json(SERIES_FILE)["query"]["categorymembers"]]
SERIES = {t.lower(): tidy_wiki_name(t) for t in series_titles}           # 'umi (series)' -> 'Umi', 'railway' -> 'Railway'
SERIES.update({tidy_wiki_name(t).lower(): tidy_wiki_name(t) for t in series_titles})   # also match the name without '(series)'
archetype_data = read_json(ARCHETYPE_FILE, {}) or {}            # Yugipedia's archetype list (missing in an older data folder: then not used)
ARCHETYPE_TITLES = {p["title"].lower() for p in archetype_data.get("query", {}).get("categorymembers", [])}   # exact titles: 'chaos', 'dark magician (archetype)'
ARCHETYPES = ARCHETYPE_TITLES | {tidy_wiki_name(t).lower() for t in ARCHETYPE_TITLES}   # also match 'Dark Magician' for 'Dark Magician (archetype)'
SERIES_TITLES = {t.lower() for t in series_titles}            # exact series titles: 'chaos (series)', 'train'
unknown = Counter()                                           # names in neither list (they still count as archetypes; reported at the end)

# --- What the wiki says about each card -------------------------------------
wiki = {}                                                     # card name -> (archetypes, series) or None
for row in read_table(WIKI_FILE, encoding="utf-8")[1]:
    if row["status"] != "ok":                                 # no wiki page for this card
        wiki[row["name"]] = None
        continue
    raw = row["archseries"].split(" | ") + row["related_to_archseries"].split(" | ")   # members + related
    archetypes, series = set(), set()
    for value in (v.strip() for v in raw):                    # each name the wiki gives
        if not value:
            continue
        exact_archetype = value.lower() in ARCHETYPE_TITLES and value.lower() not in SERIES_TITLES   # the name IS an archetype title ('Chaos' -
        if exact_archetype or (value.lower() in ARCHETYPES and value.lower() not in SERIES):          #  while the series is 'Chaos (series)'): an archetype
            name = MERGE.get(value.lower(), MERGE.get(tidy_wiki_name(value).lower(), tidy_wiki_name(value)))
            if name.lower() not in DROP:
                archetypes.add(name)
            continue
        if value.lower() in SERIES or tidy_wiki_name(value).lower() in SERIES:   # it's a series
            series.add(SERIES.get(value.lower()) or SERIES[tidy_wiki_name(value).lower()])
            continue
        name = tidy_wiki_name(value)                          # it's an archetype: tidy the name
        name = MERGE.get(value.lower(), MERGE.get(name.lower(), name))   # merge variants
        if name.lower() not in DROP:                          # skip the ones that aren't archetypes
            archetypes.add(name)
            if ARCHETYPES and name.lower() not in ARCHETYPES:   # in neither of Yugipedia's lists: note it
                unknown[name] += 1
    wiki[row["name"]] = (archetypes, series)

# --- Rewrite the cards -------------------------------------------------------
columns, rows = read_table(CARDS_FILE)
if "series" not in columns:                                   # add the new column right after 'archetype'
    columns.insert(columns.index("archetype") + 1, "series")

spelling = {}                                                 # lowercase -> spelling to use in Stats
arch_count, series_count = Counter(), Counter()               # how many cards use each label
series_spelling = {}                                          # lowercase series -> spelling to use in Stats
for t in series_titles:                                       # the series list has the official spelling
    series_spelling[tidy_wiki_name(t).lower()] = tidy_wiki_name(t)
kept_old = 0                                                  # cards with no wiki page
for row in rows:
    info = wiki.get(row["name"])
    if info is None:                                          # no wiki page: keep the old archetypes
        kept_old += 1
        old = [p.split("=", 1)[1].strip() for p in row["archetype"].split("||") if "=" in p]
        archetypes = {a for a in old if a.lower() not in OLD_HAND_MADE}
        series = set(row.get("series", "").replace("Series = ", "").split(" || ")) - {""}
    else:
        archetypes, series = set(info[0]), set(info[1])       # copies, so the extras below don't change `wiki`
        if not archetypes and not series:                     # the wiki gives this card nothing at all...
            old = [p.split("=", 1)[1].strip() for p in row["archetype"].split("||") if "=" in p]
            quoted = [q.lower() for q in re.findall(r'"([^"]+)"', row["card text"])]   # every "quoted" name in its text
            # ...so keep an old archetype only if the text really names it (a support card, e.g. Kaibaman -> Blue-Eyes)
            archetypes = {a for a in old if a.lower() not in OLD_HAND_MADE
                          and any(q == a.lower() or q.startswith(a.lower()) for q in quoted)}
    for s, names in EXTRA_SERIES.items():                     # hand-added series members
        if row["name"] in names:
            series.add(s)
    for a in archetypes:                                      # remember a nice spelling for Stats
        spelling.setdefault(a.lower(), a)
        arch_count[a.lower()] += 1
    for s in series:
        key = s.lower()                                       # count series without caring about capitals
        series_count[key] += 1
        if s != key or key not in series_spelling:            # remember the nicest spelling ('Railway', not 'railway')
            series_spelling[key] = s if s != key else series_spelling.get(key, s)
    # the card file writes labels in lowercase, alphabetical, joined by ' || ' (same as before)
    row["archetype"] = " || ".join(f"Archetype = {a}" for a in sorted({a.lower() for a in archetypes}))
    row["series"] = " || ".join(f"Series = {s.lower()}" for s in sorted(series))

write_table(CARDS_FILE, columns, rows)

# --- Rebuild the archetype and series rows in Stats --------------------------
stat_columns, stats = read_table(STATS_FILE)

old_spelling = {}                                             # keep the spelling Stats already used
for r in stats:
    if r["Label"].startswith("Archetype = "):
        old_spelling[r["Label"][12:].lower()] = r["Label"][12:]
template = next(r for r in stats if r["Label"].startswith("Archetype = "))   # TRUE/FALSE values to copy
# Archetype names the support columns still use (e.g. 'Skull Servant'): keep their Stats rows even if no card is a member,
# otherwise re-running the parser would lose those support links.
SUPPORT_COLS = ("strictSupports", "generalSupports", "strictSupportedBy", "generalSupportedBy")
for r in rows:
    for col in SUPPORT_COLS:
        for name in re.findall(r"Archetype = ([^|&()]+)", r.get(col, "")):
            name = name.strip()
            if name.lower() not in arch_count:                # not already an archetype some card belongs to
                arch_count[name.lower()] = 0                  # keep it (0 member cards)
                spelling.setdefault(name.lower(), name)
other_rows = [r for r in stats if not r["Label"].startswith(("Archetype = ", "Series = "))]   # everything else stays

new_rows = []
for a in sorted(arch_count):                                  # one row per archetype used by a card
    row = dict(template)
    row["Label"] = "Archetype = " + old_spelling.get(a, spelling[a])
    new_rows.append(row)
for s in sorted(series_count):                                # one row per series used by a card
    row = dict(template)
    row["Label"] = "Series = " + series_spelling.get(s, s)    # official spelling, e.g. 'Series = Railway'
    row["Notes"] = "Series are not generic by definition"
    new_rows.append(row)

write_table(STATS_FILE, stat_columns, other_rows + new_rows)

print(f"Cards: {len(rows)} ({kept_old} with no wiki page kept their old archetypes)")
print(f"  {sum(1 for r in rows if r['archetype'])} cards have an archetype, {sum(1 for r in rows if r['series'])} have a series")
print(f"Stats: {len(arch_count)} archetypes, {len(series_count)} series (was {len(old_spelling)} archetypes)")
if unknown:                                                   # names Yugipedia lists as neither (often a typo or an old name on a card page)
    print(f"  {len(unknown)} names are in neither Yugipedia list (kept as archetypes):",
          ", ".join(f"{n} ({c})" for n, c in unknown.most_common(15)))
