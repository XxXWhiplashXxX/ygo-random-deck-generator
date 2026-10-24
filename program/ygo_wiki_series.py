# ygo_wiki_series.py
# For every card in cards.csv, reads the card's page on Yugipedia (through its official API, 50 cards per
# request, one request per second) and saves the "archseries" lines from the card's info box - the
# archetypes AND series the wiki says the card belongs to (and is related to), plus what the wiki says it
# supports ("supports_archetypes", "supports").
# Results go to card_archseries.csv, with the date each card was read. A card read less than 30 days ago
# is NOT read again (Yugipedia asks us to keep what we read for about 30 days); older ones are read again,
# so fixes on the wiki reach your cards once a month. The first run reads every card (about 5 minutes).
# Safe to stop and restart: every finished batch is saved straight away.
#
#   python ygo_wiki_series.py cards.csv

import csv              # writes the results one batch at a time
import os               # checks whether the results file already exists
import sys              # the file name typed after the script name
from datetime import date, timedelta   # today's date, and "30 days ago"

from ygo_io import WIKI_BATCH, WIKI_KEEP_DAYS, field_values, read_table, wiki_pages   # shared file reading + Yugipedia downloads

CARDS_FILE = sys.argv[1] if len(sys.argv) > 1 else "cards.csv"   # your card file
OUT_FILE = "card_archseries.csv"                                 # where results are saved
COLUMNS = ["name", "archseries", "related_to_archseries", "supports_archetypes", "supports", "status", "read_on"]   # read_on = the day the page was read
# (supports_archetypes / supports: what Yugipedia's editors say the card supports - "Blackwing", "FIRE" - kept to check the parser against)
today = date.today().isoformat()                                 # e.g. '2026-10-22'
fresh_since = (date.today() - timedelta(days=WIKI_KEEP_DAYS)).isoformat()   # read on or after this day = still fresh

# --- What we already have, and which of it is still fresh ----------------------
kept, stale = [], {}                                             # rows read in the last 30 days / older rows (by name)
if os.path.exists(OUT_FILE):
    for row in read_table(OUT_FILE, encoding="utf-8")[1]:
        if (row.get("read_on") or "") >= fresh_since:            # read recently: keep it as it is
            kept.append(row)
        else:                                                    # no date (an older file) or too old: read it again ...
            stale[row["name"]] = row                             # ... but keep the old result in case the reading stops
done = {row["name"] for row in kept}                             # card names that don't need reading

# --- The card names still to read ---------------------------------------------
names = [row["name"] for row in read_table(CARDS_FILE)[1] if row["name"] not in done]
print(f"{len(names)} cards to read ({len(done)} read in the last {WIKI_KEEP_DAYS} days), "
      f"{-(-len(names) // WIKI_BATCH)} requests.")              # (-(-a // b) rounds the division up)

# --- Write the fresh rows back, then read the rest, 50 at a time ---------------
with open(OUT_FILE, "w", encoding="utf-8", newline="") as f:     # start the file again: fresh rows first
    writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(kept)                                       # what is still fresh
    f.flush()                                                    # (on disk now, in case the run is stopped)
    for start in range(0, len(names), WIKI_BATCH):               # 0, 50, 100, ...
        batch = names[start:start + WIKI_BATCH]                  # the next 50 names
        try:
            pages = wiki_pages(batch)                            # one request for all 50 (then a one-second pause)
        except Exception as e:                                   # network problem: stop, so a restart carries on
            print("Request failed:", e, "- run the script again to continue.")
            writer.writerows(stale[n] for n in names[start:] if n in stale)   # the cards not read yet keep their old result (and old date)
            break
        for name in batch:
            text = pages.get(name)
            if text is None:                                     # no Yugipedia page under this name
                writer.writerow({"name": name, "status": "no page", "read_on": today})
                continue
            writer.writerow({"name": name,
                             "archseries": " | ".join(field_values(text, "archseries")),                # what the card belongs to
                             "related_to_archseries": " | ".join(field_values(text, "related_to_archseries")),   # what it's only related to
                             "supports_archetypes": " | ".join(field_values(text, "supports_archetypes")),       # archetypes it supports (hand-written by the wiki's editors)
                             "supports": " | ".join(field_values(text, "supports")),                             # other things it supports (Types, Attributes ...)
                             "status": "ok", "read_on": today})
        f.flush()                                                # save to disk after every batch
        print(f"{min(start + WIKI_BATCH, len(names))}/{len(names)} cards")

print(f"Done. Results are in {OUT_FILE}")
