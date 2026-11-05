"""
ygo_trim_related.py  --  one-off clean-up: removes the lore-only "related" archetype / series labels from cards.csv.

Yugipedia's "related" links were added to the archetype and series columns as if they were members.
This script keeps a related link only when the cards really interact (the rule is in ygo_related.py)
and removes the rest. A card's REAL memberships are never removed - except lore-only series (Recolored counterpart, Signature move ...,
listed in ygo_related.LORE_SERIES), which are removed from every card.

Before changing anything it copies cards.csv and Stats.csv into program/backups.
It also writes 'related_dropped.txt' (in program/backups) listing every link it removed, so you can skim it.

Run from the program folder:
    python ygo_trim_related.py
"""
import re                                      # tidying label names in Stats
import shutil                                  # copying files for the backup
import time                                    # the date and time in the backup name
from collections import Counter                # counting how often each label was dropped

import ygo_paths as P                          # where the data and backup folders are
from ygo_io import read_table, write_table     # reading and writing CSV files
from ygo_related import LORE_SERIES, RelatedChecker, kept_related, label_key, members_from_wiki   # the keep / drop rule + lore-only series

CARDS = P.DATA / "cards.csv"                   # the card file the randomizer uses
STATS = P.DATA / "Stats.csv"                   # the label list (archetypes, series, Types ...)
WIKI = P.DATA / "card_archseries.csv"          # what Yugipedia said about each card (members and related)

# --- 1. Back up the two files we change ------------------------------------------
stamp = time.strftime("%Y-%m-%d_%H%M%S")       # e.g. 2026-11-04_223000
P.BACKUPS.mkdir(exist_ok=True)                 # make the backups folder if it is missing
shutil.copy(CARDS, P.BACKUPS / f"cards_before_related_trim_{stamp}.csv")
shutil.copy(STATS, P.BACKUPS / f"Stats_before_related_trim_{stamp}.csv")

# --- 2. Read everything ----------------------------------------------------------
columns, rows = read_table(CARDS)                              # every card
cards = {r["name"]: r for r in rows}                           # look a card up by its name
wiki_rows = read_table(WIKI, encoding="utf-8")[1]              # the wiki notes, one row per card
checker = RelatedChecker(cards, members_from_wiki(wiki_rows))  # knows each label's real members
keep_links = kept_related(wiki_rows, checker)                  # every related link that passes the rule (two passes)

# --- 3. Decide which related labels each card loses ------------------------------
dropped = []                                                   # (card, label) pairs we removed, for the report
dropped_count = Counter()                                      # how many cards lost each label
for row in rows:                                               # 3a. lore-only series go from EVERY card, members too
    labels = [p.strip() for p in row.get("series", "").split("||") if p.strip()]
    keep = []
    for lab in labels:
        if label_key(lab[len("Series = "):]) in LORE_SERIES:   # e.g. 'Series = recolored counterpart'
            dropped.append((row["name"], lab))
            dropped_count[lab] += 1
        else:
            keep.append(lab)
    row["series"] = " || ".join(keep)

for w in wiki_rows:                                            # 3b. related links that fail the rule
    if w.get("status") != "ok" or w["name"] not in cards:      # no wiki page, or not a card in our file
        continue
    member_keys = {label_key(v) for v in w["archseries"].split(" | ") if v.strip()}   # labels it REALLY belongs to
    related = [v.strip() for v in w["related_to_archseries"].split(" | ") if v.strip()]
    kept_keys = {label_key(v) for v in related if (w["name"], v) in keep_links}        # related links that pass the rule
    lose = {label_key(v) for v in related} - kept_keys - member_keys                   # lore-only and not a member
    if not lose:
        continue
    row = cards[w["name"]]
    for col, prefix in (("archetype", "Archetype = "), ("series", "Series = ")):        # both label columns
        labels = [p.strip() for p in row.get(col, "").split("||") if p.strip()]        # e.g. ['Archetype = kuriboh', ...]
        keep = []
        for lab in labels:
            key = label_key(lab[len(prefix):]) if lab.startswith(prefix) else ""       # 'Archetype = kuriboh' -> 'kuriboh'
            if key in lose:
                dropped.append((w["name"], lab))                                       # removed: note it
                dropped_count[lab] += 1
            else:
                keep.append(lab)
        row[col] = " || ".join(keep)                           # same layout as before: joined by ' || '

write_table(CARDS, columns, rows)

# --- 4. Remove Stats rows for archetypes / series no card belongs to any more ----
# (A label the Supports columns still use is kept, so the support links don't break - same rule as ygo_wiki_labels.py.)
used = set()                                                   # every label some card still has, lowercase
for r in rows:
    for col in ("archetype", "series"):
        used |= {p.strip().lower() for p in r.get(col, "").split("||") if p.strip()}
    for col in ("strictSupports", "generalSupports", "strictSupportedBy", "generalSupportedBy"):
        for kind, name in re.findall(r"(Archetype|Series) = ([^|&()]+)", r.get(col, "")):
            used.add(f"{kind} = {name.strip()}".lower())
stat_columns, stats = read_table(STATS)
kept_stats, removed_stats = [], []
for s in stats:
    label = s["Label"]
    lore = label.startswith("Series = ") and label_key(label[len("Series = "):]) in LORE_SERIES
    if lore or (label.startswith(("Archetype = ", "Series = ")) and label.lower() not in used):
        removed_stats.append(label)                            # a lore series, or nothing has this label now
    else:
        kept_stats.append(s)
write_table(STATS, stat_columns, kept_stats)

# --- 5. Report -------------------------------------------------------------------
report = P.BACKUPS / f"related_dropped_{stamp}.txt"
with open(report, "w", encoding="utf-8") as f:
    f.write(f"{len(dropped)} related labels removed from {len({c for c, _ in dropped})} cards\n\n")
    f.write("Labels removed from Stats (no cards left):\n")
    f.writelines(f"  {s}\n" for s in sorted(removed_stats))
    f.write("\nEvery removed link (card -> label):\n")
    f.writelines(f"  {c} -> {lab}\n" for c, lab in sorted(dropped))

print(f"Removed {len(dropped)} related labels from {len({c for c, _ in dropped})} cards.")
print(f"Removed {len(removed_stats)} Stats rows with no cards left:", ", ".join(removed_stats[:20]))
print("Most-dropped labels:", ", ".join(f"{lab} ({n})" for lab, n in dropped_count.most_common(12)))
print(f"Backups and the full list are in {P.BACKUPS}")
