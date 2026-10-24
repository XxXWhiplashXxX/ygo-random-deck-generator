# ygo_add_dates.py
# Adds four columns to the card file, from the YGOPRODeck API (the misc=yes download):
#   tcg date   - first TCG release, e.g. 2014-02-07  (blank if the card isn't out in the TCG yet)
#   ocg date   - first OCG release
#   other ids  - alternate-art Ids, joined by ' ' (banlists sometimes list a card under one of these)
#   formats    - where the card can be played, joined by '; ' e.g. 'TCG; OCG; Master Duel'
#                (a banlist with '#pool Master Duel' only allows cards whose formats include Master Duel)
# The randomizer uses the dates for the "only cards released by the list's date" card pool.
# Existing columns are not touched. Rewrites the card file in place.
#
#   python ygo_add_dates.py cards_new.csv api.json

import sys   # file names typed after the script name

from ygo_io import read_api, read_table, write_table   # shared file reading / writing

CARDS_FILE = sys.argv[1] if len(sys.argv) > 1 else "cards_new.csv"     # the card file
API_FILE = sys.argv[2] if len(sys.argv) > 2 else "api.json"             # the misc=yes download
NEW_COLUMNS = ["tcg date", "ocg date", "other ids", "formats"]       # the columns we fill

api_cards = read_api(API_FILE)                                          # every card YGOPRODeck has
by_id = {str(c["id"]): c for c in api_cards}                            # Card Id -> API card
by_name = {c["name"].lower(): c for c in api_cards}                     # fallback when Konami changed the Id

columns, rows = read_table(CARDS_FILE)                                  # our card file
id_col = columns[0]                                                     # 'Card Id'
for col in NEW_COLUMNS:                                                 # add the columns at the end if missing
    if col not in columns:
        columns.append(col)

found = 0
for r in rows:
    card = by_id.get(r[id_col]) or by_name.get(r["name"].lower())       # this card in the API (by Id, else by name)
    if card is None:                                                    # not in the API: leave the columns blank
        for col in NEW_COLUMNS:
            r.setdefault(col, "")
        continue
    found += 1
    misc = (card.get("misc_info") or [{}])[0]                           # dates live in the extra info block
    r["tcg date"] = misc.get("tcg_date", "")                           # e.g. '2014-02-07'
    r["ocg date"] = misc.get("ocg_date", "")
    ids = {str(i["id"]) for i in card.get("card_images", [])} | {str(card["id"])}   # every Id this card has
    ids.discard(r[id_col])                                              # minus the one already in 'Card Id'
    r["other ids"] = " ".join(sorted(ids))
    r["formats"] = "; ".join(misc.get("formats", []))                  # e.g. 'TCG; OCG; Master Duel; Edison'

write_table(CARDS_FILE, columns, rows)
print(f"Dates, Ids and formats added for {found} of {len(rows)} cards")
