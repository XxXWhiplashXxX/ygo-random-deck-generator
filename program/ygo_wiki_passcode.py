# ygo_wiki_passcode.py
# Needs these files next to it:
#   card_archseries.csv  (made by ygo_wiki_series.py)     cards_new.csv  (your card file)
# For every card that had no Yugipedia page under its exact name, it looks the card up by PASSCODE (the
# card's Id): Yugipedia has a page named after every passcode (e.g. "85215458") that leads to the card.
# A page is accepted ONLY if the passcode written on it ("password") is the card's Id, so a new card can
# never be matched to the older card it is named after.            -> wiki_passcode_cards.csv
# 50 passcodes per request, one request per second: usually well under a minute.
#
#   python ygo_wiki_passcode.py

import csv      # writes wiki_passcode_cards.csv
import re       # keeps only the digits of a passcode

from ygo_io import WIKI_BATCH, field_values, read_table, wiki_api   # shared file reading + Yugipedia downloads


def passcode_of(text):                                      # function: the passcode written on a card page
    """The passcode on a card page as a number (leading zeros don't matter), or None.
       Yugipedia calls the field 'password'; the old wiki called it 'passcode' (both are tried)."""
    vals = field_values(text, "password") or field_values(text, "passcode")  # the field's values (two possible names)
    digits = re.sub(r"\D", "", vals[0]) if vals else ""           # keep only the digits
    return int(digits) if digits else None                  # as a number, or None


def pages_by_title(titles):                                 # function: ask for some page titles, following redirects
    """Up to 50 titles -> {asked title: (title of the page it leads to, page text)}, following redirects."""
    data = wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",  # one request for every title...
                     "titles": "|".join(titles), "redirects": "1"})   # '|' separates the titles
    q = data.get("query", {})                               # the query part of the answer
    rename = {s["from"]: s["to"] for s in q.get("normalized", []) + q.get("redirects", [])}   # tidy-ups and redirects
    texts = {p["title"]: p["revisions"][0]["slots"]["main"]["*"]     # final title -> page text
             for p in q.get("pages", {}).values() if "revisions" in p}
    out = {}                                                # asked title -> (final title, text)
    for t in titles:                                        # each asked title
        final = t                                           # start from the title as asked
        while final in rename:                                      # follow every redirect step ("85215458" -> the card)
            final = rename[final]                           # (one step)
        if final in texts:                                  # the page exists:
            out[t] = (final, texts[final])                  # keep it
    return out                                              # hand back what was found


# --- The cards to look up, with their Card Id ---------------------------------
missing = [r["name"] for r in read_table("card_archseries.csv", encoding="utf-8")[1] if r["status"] != "ok"]  # cards with no page under their own name
card_columns, card_rows = read_table("cards_new.csv")       # the card file
ids = {r["name"]: r[card_columns[0]] for r in card_rows}          # card name -> Card Id (the first column)
print(f"Looking up {len(missing)} cards by passcode ...")   # tell the user

# --- The page titles to ask for: the passcode as it is, and with leading zeros to 8 digits -----------
asks = {}                                                         # card name -> the titles to try
for name in missing:                                        # each card to look up
    code = ids.get(name, "")                                # its Id
    if code.isdigit():                                      # a real number:
        asks[name] = list(dict.fromkeys([code, code.zfill(8)]))  # '4031928' and '04031928' (once each)
titles = sorted({t for ts in asks.values() for t in ts})    # every title to ask for, each once
found = {}                                                        # title -> (page title, page text)
for start in range(0, len(titles), WIKI_BATCH):                   # 50 titles per request
    found.update(pages_by_title(titles[start:start + WIKI_BATCH]))  # ask for them

# --- Keep only pages whose passcode is the card's Id ---------------------------
with open("wiki_passcode_cards.csv", "w", encoding="utf-8", newline="") as f:  # write the results
    w = csv.writer(f)                                       # a CSV writer
    # the header
    w.writerow(["name", "card_id", "wiki_title", "archseries", "related_to_archseries", "supports_archetypes", "supports", "status"])
    verified = 0                                            # how many were verified
    for name in missing:                                    # each card
        want = int(ids[name]) if ids.get(name, "").isdigit() else None  # its Id as a number
        match = next((found[t] for t in asks.get(name, []) if t in found and passcode_of(found[t][1]) == want), None)   # first page with the same passcode
        if match is None:                                   # none matched:
            w.writerow([name, ids.get(name, ""), "", "", "", "", "", "no verified match"])  # note it
            continue
        verified += 1                                       # count it
        title, text = match                                 # the page title and text
        w.writerow([name, ids[name], title, " | ".join(field_values(text, "archseries")),  # the card, its Id, the page, and the wiki fields...
                    " | ".join(field_values(text, "related_to_archseries")),  # (related)
                    # (supports, and 'verified')
                    " | ".join(field_values(text, "supports_archetypes")), " | ".join(field_values(text, "supports")), "verified"])
print(f"Done: {verified} of {len(missing)} verified by passcode.")  # the summary
