# ygo_make_staples.py
# Builds  program/data/format_staples.json  - how often each card was really played in each Format Library format -
# from the deck data ygo_update.py downloads (in the sources folder):
#     fl_formats.json                          every format (name, date, category)
#   and in program/data/staples_cache (kept between runs, so a stopped download carries on):
#     <format>_popular.json                    the format's popular deck types   e.g. TeleDAD, Lightsworn, Gladiator Beast
#     <format>__<deck type>.json               one deck type's card list: for every card, the share of those decks
#                                              that ran 0, 1, 2 or 3 copies
# For each format:  score = the average, over its deck types, of the share of decks that played the card
#                   copies = the copy count those decks ran most often
#     "TeleDAD (2009.01.17)": {"Mystical Space Typhoon": [0.92, 1], "Treeborn Frog": [0.55, 3], ...}
# The randomizer's FILLER picks lean on these (ygo_builder.py: STAPLE_BOOST). A format whose deck data
# couldn't be (fully) downloaded keeps last time's scores. A format with no decks at all is saved as {} (0 staples).
# When a format is finished (every deck type read) it goes on the COMPLETE list (program/data/complete_formats.txt)
# and its cache files are deleted: it is never downloaded again. A format with 0 staples is NEVER complete:
# its empty deck list is deleted, so the next update asks Format Library again (its page may be fixed by then).
# The current format (ygo_banlist.current_formats: the newest TCG format, under a year old) is never complete.
#
#   python ygo_make_staples.py  <sources folder>  <output file>  [deck files folder]  [complete list]

import sys    # folder / file names typed after the script name
from pathlib import Path

from ygo_banlist import fl_shown_name, fl_slug, current_formats, load_complete   # shared with ygo_update.py
from ygo_io import read_json, write_json_lines   # shared file reading / writing

SOURCES = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("banlist_sources")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("format_staples.json")
DECKS = Path(sys.argv[3]) if len(sys.argv) > 3 else SOURCES / "staples"      # the deck files (program/data/staples_cache)
COMPLETE = Path(sys.argv[4]) if len(sys.argv) > 4 else OUT.parent / "complete_formats.txt"   # finished formats
SECTIONS = ("mainMonsters", "mainSpells", "mainTraps", "extraMonsters")   # side decks don't count
MIN_SCORE = 0.05                                     # cards played in under 5% of decks are left out (keeps the file small)


def finish(shown: str, fmt: str) -> None:
    """A format is done: put it on the complete list and delete its cache files (unless it is current)."""
    if shown in current:
        return                                       # still going on: refreshed every update
    complete.add(shown)
    for path in [DECKS / f"{fmt}_popular.json"] + list(DECKS.glob(f"{fmt}__*.json")):
        path.unlink(missing_ok=True)                 # the scores are saved: the raw files aren't needed any more


old = read_json(OUT) or {}                           # last time's scores (kept for formats we couldn't download now)
formats = read_json(SOURCES / "fl_formats.json") or []
current = current_formats(formats)                   # e.g. {'Protocol (2026.02.15)'}
complete = load_complete(COMPLETE)                   # formats finished on earlier runs
complete -= {shown for shown, cards in old.items() if not cards}   # ... but a format with 0 staples is never finished
out = {}
for f in formats:
    if not (f.get("date") and f.get("category") in ("TCG", "OCG")):
        continue                                     # same formats as the Forbidden Lists
    shown = fl_shown_name(f["name"], f["date"], f["category"])
    fmt = fl_slug(f["name"])
    popular = read_json(DECKS / f"{fmt}_popular.json")
    if not isinstance(popular, list):                # no deck data downloaded for this format
        if shown in old:
            out[shown] = old[shown]                  # keep last time's
        continue
    played = {}                                      # card -> total share of decks that played it (over all deck types)
    copies = {}                                      # card -> {copies: total share of decks that ran that many}
    types = 0                                        # deck types we have a card list for
    for deck in popular:
        summary = read_json(DECKS / f"{fmt}__{fl_slug(deck.get('cleanName') or deck.get('name') or '')}.json")
        if not isinstance(summary, dict):
            continue
        types += 1
        for section in SECTIONS:
            for entry in summary.get(section) or []:
                name = entry.get("cardName") or (entry.get("card") or {}).get("name")
                if not name:
                    continue
                share = {n: (entry.get(key) or 0) / 100 for n, key in
                         ((1, "oneCopyPercent"), (2, "twoCopyPercent"), (3, "threeCopyPercent"))}
                played[name] = played.get(name, 0) + sum(share.values())       # share of these decks running 1+ copies
                tally = copies.setdefault(name, {})
                for n, v in share.items():
                    tally[n] = tally.get(n, 0) + v
    if not popular:                                  # Format Library sent no decks for this format (yet):
        out[shown] = old.get(shown) or {}            # 0 staples for now -
        (DECKS / f"{fmt}_popular.json").unlink(missing_ok=True)   # and the empty list is deleted, so the next update asks again
        print(f"{shown}: no decks yet - will check again next update")
        continue
    if types < len(popular):                         # some deck types failed to download (a slow site):
        if shown in old:                             # don't save half the data, or it would never be fixed -
            out[shown] = old[shown]                  # keep last time's (if any) and try again next update
        print(f"{shown}: only {types} of {len(popular)} deck types downloaded - will try again next time")
        continue
    out[shown] = {name: [round(total / types, 3), max(copies[name], key=copies[name].get)]   # [score, usual copies]
                  for name, total in sorted(played.items(), key=lambda kv: -kv[1]) if total / types >= MIN_SCORE}
    print(f"{shown}: {types} deck types, {len(out[shown])} cards" + ("  (current)" if shown in current else ""))
    finish(shown, fmt)                               # every deck type read: done

for shown, cards in old.items():                     # a format Format Library no longer lists: keep it
    out.setdefault(shown, cards)
OUT.parent.mkdir(parents=True, exist_ok=True)
write_json_lines(OUT, out)                           # one format per line: easy to read
COMPLETE.write_text("\n".join(sorted(complete)) + "\n", encoding="utf-8")   # one finished format per line
print(f"saved staples for {len(out)} formats to {OUT}; {len(complete)} formats complete")
