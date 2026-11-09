"""The settings half of the randomizer: default values, the 'Random' toggles, turning the settings into a
Request, and the card-count text shown above a deck. The settings are one plain dict (key -> value); the
website (web/app.py) lays its own starting values over defaults() and sends the page's settings here.
(Presets are saved by the page itself, in the browser.)"""
import random                                       # rolls the 'Random' toggles
import re                                           # spots a date in a list's name

from ygo_builder import Request                     # what the engine takes
from ygo_labels import strip_display                # removes the display-only ' (opponent)'

# The four Extra Deck type switches, the era choices and the build orders.
EXTRA_TYPES = ["allow_fusion", "allow_synchro", "allow_xyz", "allow_link"]  # the four Extra Deck type switches
ERAS = ["Random", "DM", "GX", "5Ds", "Zexal", "Arc-V", "VRAINS", "SEVENS", "GO RUSH!!"]   # Format Library's eras
BUILD_ORDERS = ["main-first", "extra-first", "random"]      # the build-order choices
UNLIMITED = "Unlimited"                             # the 'no banlist' choice in the Banlist dropdown
RANDOM = "Random"                                   # the 'pick a list for me' choice (like the archetype boxes)


def defaults() -> dict:                                     # function: every setting's starting value
    """Starting value of every setting. The website overrides some of these (WEB_DEFAULTS in web/app.py)."""
    return {                                                # one dict: setting name -> value
        "main_size": "40", "extra_size": "15", "rand_main_size": False, "rand_extra_size": False,  # deck sizes (and their Random boxes)
        "seed": "", "rand_seed": False, "build_order": "main-first",  # seed and build order
        "monster_target": "20", "spell_target": "10", "trap_target": "10", "rand_comp": False,  # Monster / Spell / Trap targets
        # Main Deck monster Levels: off = the built-in fixed weights; on = the three sliders
        # the Level sliders
        "level_sliders": False, "level_low_pct": 60, "level_mid_pct": 30, "level_high_pct": 10, "rand_levels": False,
        "allow_normals": True,                                              # Normal Monsters toggle (off = theme ones + ones a deck card asks for)
        "max_era": "GO RUSH!!", "rand_era": False,          # newest era allowed
        "min_rating": "2", "max_rating": "5", "rand_rating": False,  # rating limits
        # Extra Deck type switches
        "allow_fusion": True, "allow_synchro": True, "allow_xyz": True, "allow_link": True, "rand_extra_types": False,
        # Extra Deck type shares
        "extra_fusion_pct": 25, "extra_synchro_pct": 25, "extra_xyz_pct": 25, "extra_link_pct": 25, "rand_extra_ratios": False,
        "extra_match_main_ratio": True,                     # Extra Deck follows the main deck's theme/generic split
        "banlist": UNLIMITED, "date_pool": True,                           # format: Forbidden List, release-date card pool
        # Theme / Support / Generic shares
        "deck_type_overall_pct": 60, "support_overall_pct": 40, "generic_overall_pct": 0, "rand_theme_support": False,
        # the two themes and their split
        "deck1_pct": 100, "deck2_pct": 0, "deck1_label": "Random", "deck2_label": "Random", "rand_deck12": False,
        # the two support categories and their split
        "support_on": False, "support1_pct": 100, "support2_pct": 0, "support1_label": "Random", "support2_label": "Random", "rand_support12": False,
        "anchor_card": "", "anchor_card_2": "",                             # Card 1 / Card 2: replace Theme 1 / Theme 2 (blank = off)
        "rand_card_1": False, "rand_card_2": False,                         # 'Random card' boxes (the engine picks, seeded)
    }


def _int(text, fallback: int) -> int:                       # function: text -> whole number, with a fallback
    """int(text), or the fallback when it is blank / not a number."""
    try:
        return int(str(text).strip())                       # the number
    except ValueError:                                      # not a number:
        return fallback                                     # use the fallback


def roll(v: dict, rng: random.Random) -> None:              # function: apply every ticked 'Random' box
    """Apply every ticked 'Random' box by overwriting its settings in v (the page then shows the rolled values)."""
    if v["rand_main_size"]:                                 # random Main Deck size:
        v["main_size"] = str(rng.randint(40, 60))           # 40-60
    if v["rand_extra_size"]:                                # random Extra Deck size:
        v["extra_size"] = str(rng.randint(0, 15))           # 0-15
    if v["rand_seed"]:                                      # random seed:
        v["seed"] = str(rng.randint(0, 2_147_483_647))      # any number
    if v["rand_extra_types"]:                               # random Extra Deck switches:
        for k in EXTRA_TYPES:                                   # each type on/off; it may even pick none
            v[k] = rng.choice([True, False])                # on or off
    if v["rand_comp"]:                                          # monster/spell/trap targets that add up to the main size
        ms = max(40, min(60, _int(v["main_size"], 40)))     # the main size (40-60)
        m = rng.randint(0, ms)                              # monsters: 0 to all of it
        s = rng.randint(0, ms - m)                          # spells: 0 to what's left
        v["monster_target"], v["spell_target"], v["trap_target"] = str(m), str(s), str(ms - m - s)  # traps get the rest
    if v["rand_era"]:                                       # random era:
        v["max_era"] = rng.choice(ERAS[1:])                     # any real era (not "Random")
    if v.get("rand_levels"):                                    # three Level shares that add up to 100
        low = rng.randint(0, 100)                           # Level 1-4 share
        mid = rng.randint(0, 100 - low)                     # Level 5-6 share
        v["level_low_pct"], v["level_mid_pct"], v["level_high_pct"] = low, mid, 100 - low - mid  # Level 7+ gets the rest
    if v["rand_rating"]:                                        # 0-5 in half steps, low one first
        a, b = sorted(rng.choice([i / 2 for i in range(11)]) for _ in range(2))  # two random ratings, lowest first
        v["min_rating"], v["max_rating"] = f"{a:g}", f"{b:g}"  # written without a trailing '.0'
    if v["rand_theme_support"]:                             # random Theme / Support / Generic split:
        theme = rng.randint(0, 100)                         # theme share
        support = rng.randint(0, 100 - theme)               # support share (from what's left)
        # generic gets the rest
        v["deck_type_overall_pct"], v["support_overall_pct"], v["generic_overall_pct"] = theme, support, 100 - theme - support
    for flag, a, b in (                                     # the two slider pairs
                       ("rand_deck12", "deck1_pct", "deck2_pct"),  # (themes)
                       ("rand_support12", "support1_pct", "support2_pct")):  # (support)
        if v[flag]:                                             # a slider pair: one random value, the other is the rest
            v[a] = rng.randint(0, 100)                      # the first gets a random share
            v[b] = 100 - v[a]                               # the second gets the rest


def make_request(v: dict, banlists: list = ()) -> Request:  # function: the settings dict -> a Request for the engine
    """Settings dict -> Request. Blank / bad numbers fall back to the defaults.
       `banlists` = the Forbidden List names; a 'Random' Forbidden List picks one of them (or Unlimited)."""
    def num(key):                                               # optional whole number: blank -> None (no target)
        t = str(v[key]).strip()                             # the text, without spaces
        return _int(t, None) if t else None                 # a number, or None if blank

    def flt(key):                                               # optional rating bound: blank / bad -> None
        try:
            return float(str(v[key]).strip())               # the number
        except ValueError:                                  # not a number:
            return None                                     # (no limit)

    lo, hi = flt("min_rating"), flt("max_rating")           # the two rating limits
    if lo is not None and hi is not None and lo > hi:           # swapped bounds are fixed, not rejected
        lo, hi = hi, lo                                     # swap them
    seed_text = str(v["seed"]).strip()                      # the seed as typed
    if not seed_text:                                           # blank seed -> a fresh one (shown on the page)
        seed = random.randint(0, 2_147_483_647)             # any number
    elif seed_text.lstrip("-").isdigit():                   # a whole number:
        seed = int(seed_text)                               # use it
    else:                                                       # text seed -> a number made from the text
        seed = int.from_bytes(seed_text.encode("utf-8"), "little") & 0x7FFFFFFF  # the text's bytes as a positive number
    era = str(v["max_era"]).strip()                         # the era as typed
    banlist = str(v.get("banlist", UNLIMITED)).strip()          # older saved settings have no banlist -> Unlimited
    if banlist == RANDOM:                                       # same seed -> same list, so a deck can be remade
        banlist = random.Random(seed).choice([UNLIMITED] + list(banlists))  # (Unlimited counts as one of the choices)
    return Request(                                         # build the Request
        main_deck_size=max(40, min(60, _int(v["main_size"], 40))),  # Main Deck size, kept between 40 and 60
        extra_deck_size=max(0, min(15, _int(v["extra_size"], 15))),  # Extra Deck size, kept between 0 and 15
        deck_type_overall_pct=int(v["deck_type_overall_pct"]),  # theme share
        deck_type_1_label=strip_display(v["deck1_label"]), deck_type_1_pct=int(v["deck1_pct"]),  # Theme 1 label (display text removed) and share
        deck_type_2_label=strip_display(v["deck2_label"]), deck_type_2_pct=int(v["deck2_pct"]),  # Theme 2 label and share
        generic_overall_pct=int(v.get("generic_overall_pct", 0)),  # generic share
        support_overall_pct=int(v["support_overall_pct"]), support_on=bool(v.get("support_on", False)),   # older saved settings lack the key -> OFF
        support_1_label=strip_display(v["support1_label"]), support_1_pct=int(v["support1_pct"]),  # Support 1 label and share
        support_2_label=strip_display(v["support2_label"]), support_2_pct=int(v["support2_pct"]),  # Support 2 label and share
        # Monster / Spell / Trap targets
        main_monster_target=num("monster_target"), main_spell_target=num("spell_target"), main_trap_target=num("trap_target"),
        max_era=None if era in ("", "Random") else era,     # 'Random' or blank era = no limit
        min_card_rating=lo, max_card_rating=hi, seed=seed,  # rating limits and seed
        allow_fusion=bool(v["allow_fusion"]), allow_synchro=bool(v["allow_synchro"]),  # Extra Deck switches
        allow_xyz=bool(v["allow_xyz"]), allow_link=bool(v["allow_link"]),  # (Xyz and Link)
        extra_type_pcts=(max(0, _int(v.get("extra_fusion_pct", 25), 25)),  # Extra Deck shares (no negatives)
                         max(0, _int(v.get("extra_synchro_pct", 25), 25)),  # (Synchro)
                         max(0, _int(v.get("extra_xyz_pct", 25), 25)),  # (Xyz)
                         max(0, _int(v.get("extra_link_pct", 25), 25))),  # (Link)
        extra_match_main_ratio=bool(v.get("extra_match_main_ratio", True)),  # Extra Deck follows the main deck's split?
        build_order=v["build_order"],                       # main-first / extra-first / random
        banlist=None if banlist in ("", UNLIMITED) else banlist,  # Unlimited -> None
        date_pool=bool(v.get("date_pool", True)),           # only cards released by the list's date?
        level_sliders=bool(v.get("level_sliders", False)),                         # (.get: settings saved before these existed lack the keys)
        # the three Level shares
        level_low_pct=_int(v.get("level_low_pct", 60), 60), level_mid_pct=_int(v.get("level_mid_pct", 30), 30),
        level_high_pct=_int(v.get("level_high_pct", 10), 10),  # (Level 7+)
        allow_normals=bool(v.get("allow_normals", True)),   # the Normal Monsters toggle
        anchor_card=str(v.get("anchor_card", "") or "").strip() or None,   # blank -> None (off; older saved settings lack these keys)
        anchor_card_2=str(v.get("anchor_card_2", "") or "").strip() or None,  # Card 2 (blank -> None)
        rand_card_1=bool(v.get("rand_card_1", False)), rand_card_2=bool(v.get("rand_card_2", False)),  # the 'Random card' boxes
    )


def breakdown_text(req: Request) -> str:                    # function: the 'how many cards go where' text
    """The 'how many cards go where' lines shown above the deck on the page."""
    total = req.main_deck_size                              # Main Deck size
    fmt = req.banlist or UNLIMITED                                                  # the format line
    theme = int(round(total * req.deck_type_overall_pct / 100.0))  # theme cards
    generic = int(round(total * req.generic_overall_pct / 100.0))  # generic cards
    support = total - theme - generic                       # support gets the rest
    d1 = int(round(theme * req.deck_type_1_pct / 100.0))    # Theme 1's part (Theme 2 gets the rest)
    s1 = int(round(support * req.support_1_pct / 100.0))    # Support 1's part (Support 2 gets the rest)
    if req.banlist and req.date_pool and re.search(r"\d{4}\.\d{2}\.\d{2}", req.banlist):   # only lists with a date
        fmt += " (only cards released by its date)"         # add a note
    card_lines = ""                                                                 # one line per theme slot that holds a card
    # each card box
    for who, name, rolled in (("Theme 1", req.anchor_card, req.rand_card_1), ("Theme 2", req.anchor_card_2, req.rand_card_2)):
        if name or rolled:                                  # in use:
            # add a line for it
            card_lines += f"    {who} replaced by: {name or 'a random card'} (3 copies, or the format's limit)\n"
    return (f"Main deck size: {total}\n"                    # the text, line by line
            f"  Deck Theme total: {theme}  (Deck1: {d1}, Deck2: {theme - d1})\n"  # theme line
            + card_lines +                                                          # Card 1 / Card 2 (blank when not used)
            f"  Generic total: {generic}\n"                 # generic line
            # support line: Support 1 / 2 split...
            + (f"  Support total: {support}  (Support1: {s1}, Support2: {support - s1})\n" if req.support_on else
               f"  Support total: {support}  (Auto: staples + cards that help 1/3 of the deck)\n") +  # ...or automatic support
            # Level sliders line...
            (f"Monster Levels: 1-4 {req.level_low_pct}% / 5-6 {req.level_mid_pct}% / 7+ {req.level_high_pct}%\n"
               if req.level_sliders else "") +              # ...only when they're on
            # Normal Monsters line, only when off
            ("Normal Monsters: off (only the theme's own and ones a deck card asks for)\n" if not req.allow_normals else "") +
            f"Format: {fmt}\n"                              # format line
            f"Seed: {req.seed}   Build order: {req.build_order}\n")  # seed and build order


if __name__ == "__main__":                                      # headless self-test
    v = defaults()                                          # every default
    # a theme, a support with a display suffix, a text seed
    v["deck1_label"] = "Archetype = Laval"; v["support1_label"] = "Opponent = Negation (opponent)"; v["seed"] = "abc"
    # turn it into a Request and print what came out
    r = make_request(v); print(r.deck_type_1_label, r.support_1_label, r.seed, r.max_era, r.min_card_rating, r.max_card_rating)
    for k in ("rand_main_size", "rand_comp", "rand_rating", "rand_era", "rand_deck12", "rand_extra_types"):  # tick some Random boxes
        v[k] = True                                         # (on)
    roll(v, random.Random(3))                               # roll them with a fixed seed
    # print what was rolled
    print(v["main_size"], v["monster_target"], v["spell_target"], v["trap_target"], v["min_rating"], v["max_rating"], v["max_era"], v["deck1_pct"], v["deck2_pct"])
    v["banlist"] = "Random"                                 # a Random banlist
    print(breakdown_text(make_request(v, ["Highlander", "HAT (2014.07.12)", "Master Duel"])))  # print the card-count text
    # swapped rating limits get fixed
    v["min_rating"], v["max_rating"] = "4", "1"; print("swap:", make_request(v).min_card_rating, make_request(v).max_card_rating)
