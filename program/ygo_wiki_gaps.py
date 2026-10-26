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
OTHER_NAMESPACE = re.compile(r"^(?:wikipedia|w|wikt|category|file|image|template|help|special|ja|de|fr|es|it|pt|ko)\s*:", re.I)


def section_links(text):
    """[(card name, 'member' or 'related')] for every [[link]] in a series page's Members / Related sections."""
    heads = [(m.start(), m.end(), len(m.group(1)), m.group(2).strip())             # (start, end, level, heading text)
             for m in re.finditer(r"^(=+)\s*(.*?)\s*\1\s*$", text, re.M)]          # '== Members ==' -> level 2
    out = []
    for i, (_start, end, level, title) in enumerate(heads):
        low = title.lower()
        if "recommended" in low or not ("member" in low or "related" in low):      # only Members / Related sections
            continue
        stop = len(text)                                                           # the section runs to the next heading
        for s2, _, lv2, _ in heads[i + 1:]:                                        # of the same or a higher level
            if lv2 <= level:
                stop = s2
                break
        body = text[end:stop]
        kind = "related" if "related" in low else "member"
        for link in re.findall(r"\[\[([^\]|#]+)", body):                           # [[Card]] or [[Card|shown text]]
            name = link.strip()
            if name and not OTHER_NAMESPACE.match(name):
                out.append((name, kind))
    return out


series_titles = [p["title"] for p in read_json("series_list.json")["query"]["categorymembers"]]
print(f"Reading {len(series_titles)} series pages ...")
series_pages = {}
for start in range(0, len(series_titles), WIKI_BATCH):           # 50 pages per request
    series_pages.update(wiki_pages(series_titles[start:start + WIKI_BATCH]))

with open("series_members.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["series", "card", "section"])
    for title in series_titles:
        text = series_pages.get(title)
        if text is None:                                          # page missing: note it
            w.writerow([title, "", "no page"])
            continue
        for card, kind in dict.fromkeys(section_links(text)):     # each link once
            w.writerow([title, card, kind])
print("Saved series_members.csv")
