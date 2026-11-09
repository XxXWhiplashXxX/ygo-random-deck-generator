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
shutil.copy(CARDS, P.BACKUPS / f"cards_before_related_trim_{stamp}.csv")  # copy the cards
shutil.copy(STATS, P.BACKUPS / f"Stats_before_related_trim_{stamp}.csv")  # copy Stats

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
    labels = [p.strip() for p in row.get("series", "").split("||") if p.strip()]  # the card's series labels
    keep = []                                               # the ones it keeps
    for lab in labels:                                      # each one
        if label_key(lab[len("Series = "):]) in LORE_SERIES:   # e.g. 'Series = recolored counterpart'
            dropped.append((row["name"], lab))              # note it for the report
            dropped_count[lab] += 1                         # count it
        else:
            keep.append(lab)                                # keep it
    row["series"] = " || ".join(keep)                       # write the kept ones back

for w in wiki_rows:                                            # 3b. related links that fail the rule
    if w.get("status") != "ok" or w["name"] not in cards:      # no wiki page, or not a card in our file
        continue
    member_keys = {label_key(v) for v in w["archseries"].split(" | ") if v.strip()}   # labels it REALLY belongs to
    related = [v.strip() for v in w["related_to_archseries"].split(" | ") if v.strip()]  # its related labels
    kept_keys = {label_key(v) for v in related if (w["name"], v) in keep_links}        # related links that pass the rule
    lose = {label_key(v) for v in related} - kept_keys - member_keys                   # lore-only and not a member
    if not lose:                                            # nothing to remove:
        continue
    row = cards[w["name"]]                                  # the card's row
    for col, prefix in (("archetype", "Archetype = "), ("series", "Series = ")):        # both label columns
        labels = [p.strip() for p in row.get(col, "").split("||") if p.strip()]        # e.g. ['Archetype = kuriboh', ...]
        keep = []                                           # the ones it keeps
        for lab in labels:                                  # each label
            key = label_key(lab[len(prefix):]) if lab.startswith(prefix) else ""       # 'Archetype = kuriboh' -> 'kuriboh'
            if key in lose:                                 # one to remove:
                dropped.append((w["name"], lab))                                       # removed: note it
                dropped_count[lab] += 1                     # count it
            else:
                keep.append(lab)                            # keep it
        row[col] = " || ".join(keep)                           # same layout as before: joined by ' || '

write_table(CARDS, columns, rows)                           # save the cards

# --- 4. Remove Stats rows for archetypes / series no card belongs to any more ----
# (A label the Supports columns still use is kept, so the support links don't break - same rule as ygo_wiki_labels.py.)
used = set()                                                   # every label some card still has, lowercase
for r in rows:                                              # each card
    for col in ("archetype", "series"):                     # its two label columns
        used |= {p.strip().lower() for p in r.get(col, "").split("||") if p.strip()}  # every label in them
    for col in ("strictSupports", "generalSupports", "strictSupportedBy", "generalSupportedBy"):  # its four support columns
        for kind, name in re.findall(r"(Archetype|Series) = ([^|&()]+)", r.get(col, "")):  # every archetype / series label in them
            used.add(f"{kind} = {name.strip()}".lower())    # counts as used
stat_columns, stats = read_table(STATS)                     # the label file
kept_stats, removed_stats = [], []                          # rows kept / labels removed
for s in stats:                                             # each row
    label = s["Label"]                                      # its label
    lore = label.startswith("Series = ") and label_key(label[len("Series = "):]) in LORE_SERIES  # a lore-only series?
    if lore or (label.startswith(("Archetype = ", "Series = ")) and label.lower() not in used):  # lore, or an archetype / series no card has any more
        removed_stats.append(label)                            # a lore series, or nothing has this label now
    else:
        kept_stats.append(s)                                # keep it
write_table(STATS, stat_columns, kept_stats)                # save Stats

# --- 5. Report -------------------------------------------------------------------
report = P.BACKUPS / f"related_dropped_{stamp}.txt"         # the report file
with open(report, "w", encoding="utf-8") as f:              # write it
    f.write(f"{len(dropped)} related labels removed from {len({c for c, _ in dropped})} cards\n\n")  # the totals
    f.write("Labels removed from Stats (no cards left):\n")  # heading
    f.writelines(f"  {s}\n" for s in sorted(removed_stats))  # each removed Stats label
    f.write("\nEvery removed link (card -> label):\n")      # heading
    f.writelines(f"  {c} -> {lab}\n" for c, lab in sorted(dropped))  # each removed link

print(f"Removed {len(dropped)} related labels from {len({c for c, _ in dropped})} cards.")  # the summary
print(f"Removed {len(removed_stats)} Stats rows with no cards left:", ", ".join(removed_stats[:20]))  # (Stats rows)
print("Most-dropped labels:", ", ".join(f"{lab} ({n})" for lab, n in dropped_count.most_common(12)))  # (the labels removed most)
print(f"Backups and the full list are in {P.BACKUPS}")      # where the backups are
