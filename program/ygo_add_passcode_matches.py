# ygo_add_passcode_matches.py
# Copies the passcode-verified wiki pages (wiki_passcode_cards.csv, made by ygo_wiki_passcode.py)
# into card_archseries.csv, so those cards count as "has a wiki page" from now on.
# Afterwards re-run:  python ygo_wiki_labels.py   then   python ygo_series_pages.py
#
#   python ygo_add_passcode_matches.py

from ygo_io import read_table, write_table   # shared file reading / writing

_, passcode_rows = read_table("wiki_passcode_cards.csv", encoding="utf-8")  # the confirmed passcode matches
verified = {r["name"]: r for r in passcode_rows if r["status"] == "verified"}   # only confirmed matches
columns, rows = read_table("card_archseries.csv", encoding="utf-8")   # name, archseries, related_to_archseries, status

changed = 0                                                 # cards fixed
for r in rows:                                              # each card's wiki row
    v = verified.get(r["name"])                             # its confirmed match (if any)
    if v and r["status"] != "ok":                             # had no page before, now has a confirmed one
        r["archseries"] = v["archseries"]                     # what the card belongs to
        r["related_to_archseries"] = v["related_to_archseries"]   # what it's related to
        for col in ("supports_archetypes", "supports"):         # what Yugipedia says it supports (if both files have the column)
            if col in r and col in v:                       # both files have it:
                r[col] = v[col]                             # copy it
        r["status"] = "ok"                                  # it has a page now
        changed += 1                                        # count it

write_table("card_archseries.csv", columns, rows, encoding="utf-8")  # save the wiki notes
print(f"Added {changed} passcode-verified cards to card_archseries.csv")  # the summary
