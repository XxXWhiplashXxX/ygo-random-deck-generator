# ygo_series_pages.py
# Adds the cards that Yugipedia's SERIES pages list (Members / Related) to each card's series column,
# for cards whose own wiki page didn't mention the series. Uses series_members.csv (from ygo_wiki_gaps.py).
# Also adds a 'Series = X' row to Stats for any series that had no cards before.
# Rewrites cards_new.csv and Stats_new.csv in place.
#
#   python ygo_series_pages.py

import re    # regular expressions, to drop ' (series)' from page titles

from ygo_io import read_table, write_table   # shared file reading / writing

CARDS_FILE = "cards_new.csv"           # the big new cards file
STATS_FILE = "Stats_new.csv"           # the big new Stats file
SERIES_FILE = "series_members.csv"     # series page -> linked card names


def series_name(title):
    """Wiki page title -> series name: 'Umi (series)' -> 'Umi'."""
    return re.sub(r"\s*\(series\)$", "", title, flags=re.I).strip()


# --- Read the cards ----------------------------------------------------------
columns, rows = read_table(CARDS_FILE)
by_name = {r["name"].lower(): r for r in rows}                 # look a card up by name, ignoring capitals

# --- Add the series labels ---------------------------------------------------
added = 0
used_series = set()                                            # series that now have at least one card
for link in read_table(SERIES_FILE, encoding="utf-8")[1]:    # one row per (series page, card it links to)
    card = by_name.get(link["card"].lower())                   # is the link a real card in our file?
    if card is None:                                           # no: it's 'WIND', 'Main Deck', a character page ...
        continue
    name = series_name(link["series"])
    labels = [p.strip() for p in card["series"].split("||") if p.strip()]   # its current series labels
    label = f"Series = {name.lower()}"                         # lowercase, like the rest of the card file
    if label not in labels:                                    # not there yet: add it
        labels.append(label)
        card["series"] = " || ".join(sorted(labels))           # alphabetical, joined by ' || '
        added += 1
    used_series.add(name)

write_table(CARDS_FILE, columns, rows)

# --- Make sure Stats has a row for every series that is used -----------------
stat_columns, stats = read_table(STATS_FILE)
have = {r["Label"].lower() for r in stats}                    # labels Stats already has
template = next(r for r in stats if r["Label"].startswith("Series = "))   # copy an existing series row's values
new_rows = []
for name in sorted(used_series):
    if f"series = {name}".lower() not in have:                # a series Stats doesn't know yet
        row = dict(template)
        row["Label"] = f"Series = {name}"
        new_rows.append(row)

write_table(STATS_FILE, stat_columns, stats + new_rows)

print(f"Added {added} series labels to cards; {len(new_rows)} new series rows in Stats:",
      ", ".join(r["Label"][9:] for r in new_rows))
