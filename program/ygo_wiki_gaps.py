# ygo_wiki_gaps.py
# Reads every SERIES page on Yugipedia (the ~225 in series_list.json) and collects the cards linked in its
# "Members" and "Related" sections (NOT "Recommended cards", which are only deck suggestions). Not every
# Yugipedia series page has such a section ("Train" does, "Greed (series)" doesn't); the cards' own pages
# are the main source, this only fills gaps.
# Saves them to series_members.csv, which ygo_series_pages.py adds to the cards' series column.
# (Cards with no wiki page under their own name are handled by ygo_wiki_passcode.py.)
# 5 requests, well under a minute.
#
#   python ygo_wiki_gaps.py

import csv              # writes series_members.csv
import re               # regular expressions, for headings and [[links]]

from ygo_io import WIKI_BATCH, read_json, wiki_pages   # shared file reading + wiki downloads

# Links like [[wikipedia:Train]] or [[File:x.png]] are not card names
# a link to another wiki area, not a card
OTHER_NAMESPACE = re.compile(r"^(?:wikipedia|w|wikt|category|file|image|template|help|special|ja|de|fr|es|it|pt|ko)\s*:", re.I)


def section_links(text):                                    # function: the card links in a series page's Members / Related sections
    """[(card name, 'member' or 'related')] for every [[link]] in a series page's Members / Related sections."""
    heads = [(m.start(), m.end(), len(m.group(1)), m.group(2).strip())             # (start, end, level, heading text)
             for m in re.finditer(r"^(=+)\s*(.*?)\s*\1\s*$", text, re.M)]          # '== Members ==' -> level 2
    out = []                                                # (card, kind) pairs
    for i, (_start, end, level, title) in enumerate(heads):  # each heading
        low = title.lower()                                 # lower-case
        if "recommended" in low or not ("member" in low or "related" in low):      # only Members / Related sections
            continue
        stop = len(text)                                                           # the section runs to the next heading
        for s2, _, lv2, _ in heads[i + 1:]:                                        # of the same or a higher level
            if lv2 <= level:                                # a heading at the same or a higher level:
                stop = s2                                   # the section ends there
                break
        body = text[end:stop]                               # the section's text
        kind = "related" if "related" in low else "member"  # which kind of section
        for link in re.findall(r"\[\[([^\]|#]+)", body):                           # [[Card]] or [[Card|shown text]]
            name = link.strip()                             # the linked page's name
            if name and not OTHER_NAMESPACE.match(name):    # a real card link:
                out.append((name, kind))                    # keep it
    return out                                              # hand back the links


series_titles = [p["title"] for p in read_json("series_list.json")["query"]["categorymembers"]]  # every series page title
print(f"Reading {len(series_titles)} series pages ...")     # tell the user
series_pages = {}                                           # title -> page text
for start in range(0, len(series_titles), WIKI_BATCH):           # 50 pages per request
    series_pages.update(wiki_pages(series_titles[start:start + WIKI_BATCH]))  # download them

with open("series_members.csv", "w", encoding="utf-8", newline="") as f:  # write the results
    w = csv.writer(f)                                       # a CSV writer
    w.writerow(["series", "card", "section"])               # the header
    for title in series_titles:                             # each series
        text = series_pages.get(title)                      # its page
        if text is None:                                          # page missing: note it
            w.writerow([title, "", "no page"])              # note it
            continue
        for card, kind in dict.fromkeys(section_links(text)):     # each link once
            w.writerow([title, card, kind])                 # one row per card
print("Saved series_members.csv")                           # the summary
