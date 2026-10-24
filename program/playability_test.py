"""
playability_test.py  --  builds many random decks and counts the cards in them that can't work.

What counts as USELESS is decided by ygo_final.useless_cards() (the same check the builder's last step uses):
  strict need     its strictSupportedBy is not met by the deck (main + Extra)
  required card   a card it requires is missing
  unsummonable    an Extra Deck monster whose materials the main deck can't supply
  ritual          a Ritual Monster with no Ritual Spell, or a Ritual Spell with no Ritual Monster
  floodgate       a floodgate that stops what at least 1/3 of its own deck does
generalSupportedBy needs do NOT count.

Run:  python playability_test.py [number of decks] [results file name] [levels=40/40/20] [nonormals] [rating=4-5]
        levels=A/B/C   turn the Level sliders on with these shares (Level 1-4 / 5-6 / 7+)
        nonormals      turn the Normal Monsters toggle off
        rating=A-B     set the rating limits (min-max)
"""
import json                                             # saves every deck's result
import random                                           # random settings per deck
import sys                                              # the command-line settings
import time                                             # how long it took
from collections import Counter                         # tallies of failures
from multiprocessing import Pool                        # build two decks at once
from pathlib import Path

from ygo_data import read_csv, load_tree, parse_atom, card_matches
from ygo_builder import Builder, Request
from ygo_extra import ExtraBuilder
from ygo_final import build_deck, Rules, useless_cards

DATA = Path(__file__).resolve().parent / "data"         # program/data
B = EB = RULES = None                                   # made once per worker process (see start())


def start():
    """Load the cards once in each worker (takes a few seconds)."""
    global B, EB, RULES
    B = Builder(read_csv(DATA / "cards.csv"), load_tree(DATA / "game_mechanics_tree.csv"))
    EB = ExtraBuilder(B)
    RULES = Rules(B)


def settings(seed: int) -> Request:
    """The same random settings for a seed every run, so before / after tests build the same decks."""
    rng = random.Random(10_000 + seed)
    extra = {}                                          # the optional settings from the command line (see the bottom)
    if LEVELS:
        extra.update(level_sliders=True, level_low_pct=LEVELS[0], level_mid_pct=LEVELS[1], level_high_pct=LEVELS[2])
    if NO_NORMALS:
        extra["allow_normals"] = False
    if RATING:
        extra.update(min_card_rating=RATING[0], max_card_rating=RATING[1])
    return Request(seed=seed, deck_type_1_label="Random", support_1_label="Random",
                   build_order=rng.choice(["main-first", "extra-first"]), **extra)


LEVELS = None                                           # e.g. (40, 40, 20) from 'levels=40/40/20'; None = sliders off
NO_NORMALS = False                                      # True from 'nonormals': the Normal Monsters toggle off
RATING = None                                           # e.g. (4, 5) from 'rating=4-5': the rating limits


def check(seed: int) -> dict:
    """Build one deck and return what is useless in it."""
    try:
        d = build_deck(B, EB, settings(seed))
    except Exception as e:                              # a crash counts as a failed deck
        return {"seed": seed, "error": repr(e)}
    bad_main, bad_extra = useless_cards(B, EB, RULES, d.main, d.extra)
    theme = parse_atom(d.debug["themes"][0])                    # e.g. 'Archetype = blackwing'
    on_theme = sum(1 for e in d.main if card_matches([(theme,)], e.info.facts))   # main-deck copies that ARE the theme
    bad = [(why, n, sum(1 for e in d.main if e.info.name == n)) for n, why in bad_main.items()]
    bad += [(why, n, sum(1 for e in d.extra if e.info.name == n)) for n, why in bad_extra.items()]
    mons = [e.info for e in d.main if e.info.kind == "Monster"]                  # one Info per main-deck monster copy
    bands = [sum(1 for m in mons if B.level_band(m) == b) for b in range(3)]     # copies at Level 1-4 / 5-6 / 7+
    stray = sum(1 for m in mons if "normal" in m.facts["trait"] and m.name not in B.theme_names)   # Normal Monsters NOT of the theme
    lo, hi = RATING or (None, None)
    in_range = lambda e: (lo is None or e.info.rating >= lo) and (hi is None or e.info.rating <= hi)
    themed = [e for e in d.main if e.origin.startswith(("theme", "support"))]   # theme + support picks
    return {"in_range": sum(map(in_range, d.main)), "themed": len(themed), "themed_in": sum(map(in_range, themed)), "bands": bands, "stray_normals": stray,"seed": seed, "theme": d.debug.get("themes"), "order": d.debug.get("order"),
            "main": len(d.main), "extra": len(d.extra), "swaps": len(d.debug.get("last_check", [])), "repairs": len(d.debug.get("last_check_repairs", [])), "theme_share": on_theme / max(1, len(d.main)), "bad": bad}


if __name__ == "__main__":
    args = sys.argv[1:]
    for a in list(args):                                # optional settings: levels=40/40/20  and  nonormals
        if a.startswith("levels="):
            LEVELS = tuple(int(x) for x in a[7:].split("/")); args.remove(a)
        elif a == "nonormals":
            NO_NORMALS = True; args.remove(a)
        elif a.startswith("rating="):
            RATING = tuple(float(x) for x in a[7:].split("-")); args.remove(a)
    n = int(args[0]) if args else 300
    out = args[1] if len(args) > 1 else "playability_results.json"
    t0 = time.time()
    with Pool(processes=2, initializer=start) as pool:  # (this machine has 2 cores)
        results = []
        for i, r in enumerate(pool.imap_unordered(check, range(n), chunksize=2), 1):
            results.append(r)
            if i % 25 == 0:
                print(f"{i}/{n} decks  ({time.time() - t0:.0f}s)", flush=True)
    results.sort(key=lambda r: r["seed"])
    errors = [r for r in results if "error" in r]
    ok = [r for r in results if "error" not in r]
    clean = [r for r in ok if not r["bad"]]
    cards = sum(r["main"] + r["extra"] for r in ok)
    dead = sum(k for r in ok for _, _, k in r["bad"])
    print(f"\n{len(results)} decks built in {time.time() - t0:.0f}s, {len(errors)} crashed")
    print(f"decks with NO useless card : {len(clean)}/{len(ok)} = {len(clean) / max(1, len(ok)):.1%}")
    print(f"card copies that work      : {cards - dead}/{cards} = {(cards - dead) / max(1, cards):.1%}")
    print("useless cards by rule:", dict(Counter(why for r in ok for why, _, _ in r["bad"])))
    swaps = [r["swaps"] for r in ok]
    print(f"last-check swaps per deck   : average {sum(swaps) / max(1, len(ok)):.1f}, decks with 10+: {sum(1 for s in swaps if s >= 10)}")
    print(f"last-check repairs per deck : average {sum(r.get('repairs', 0) for r in ok) / max(1, len(ok)):.1f}")
    print(f"theme share (main deck)     : average {sum(r['theme_share'] for r in ok) / max(1, len(ok)):.1%}")
    bt = [sum(r["bands"][b] for r in ok) for b in range(3)]                       # all decks' monsters together
    print(f"monster Levels 1-4 / 5-6 / 7+: {' / '.join(f'{x / max(1, sum(bt)):.0%}' for x in bt)}"
          + (f"   (sliders: {'/'.join(map(str, LEVELS))})" if LEVELS else "   (sliders off)"))
    print(f"Normal Monsters outside the theme: {sum(r['stray_normals'] for r in ok)} copies in {sum(1 for r in ok if r['stray_normals'])} decks"
          + ("   (Normal toggle OFF)" if NO_NORMALS else "   (Normal toggle on)"))
    if RATING:
        print(f"cards inside rating {RATING[0]:g}-{RATING[1]:g}: whole main deck {sum(r['in_range'] for r in ok) / max(1, sum(r['main'] for r in ok)):.1%}, "
              f"theme + support picks {sum(r['themed_in'] for r in ok) / max(1, sum(r['themed'] for r in ok)):.1%}")
    print("short decks (main < 40):", sum(1 for r in ok if r["main"] < 40), "| avg Extra Deck size:",
          round(sum(r["extra"] for r in ok) / max(1, len(ok)), 1))
    (DATA.parent / out).write_text(json.dumps(results, indent=1))
    for e in errors[:5]:
        print("CRASH", e)
