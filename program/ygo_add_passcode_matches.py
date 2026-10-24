# ygo_add_passcode_matches.py
# Copies the passcode-verified wiki pages (wiki_passcode_cards.csv, made by ygo_wiki_passcode.py)
# into card_archseries.csv, so those cards count as "has a wiki page" from now on.
# Afterwards re-run:  python ygo_wiki_labels.py   then   python ygo_series_pages.py
#
#   python ygo_add_passcode_matches.py

from ygo_io import read_table, write_table   # shared file reading / writing

_, passcode_rows = read_table("wiki_passcode_cards.csv", encoding="utf-8")
verified = {r["name"]: r for r in passcode_rows if r["status"] == "verified"}   # only confirmed matches
columns, rows = read_table("card_archseries.csv", encoding="utf-8")   # name, archseries, related_to_archseries, status

changed = 0
for r in rows:
    v = verified.get(r["name"])
    if v and r["status"] != "ok":                             # had no page before, now has a confirmed one
        r["archseries"] = v["archseries"]                     # what the card belongs to
        r["related_to_archseries"] = v["related_to_archseries"]   # what it's related to
        for col in ("supports_archetypes", "supports"):         # what Yugipedia says it supports (if both files have the column)
            if col in r and col in v:
                r[col] = v[col]
        r["status"] = "ok"
        changed += 1

write_table("card_archseries.csv", columns, rows, encoding="utf-8")
print(f"Added {changed} passcode-verified cards to card_archseries.csv")
