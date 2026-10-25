"""The GUI-free half of the randomizer window: default values, the 'Random' toggles, turning the settings
into a Request, the card-count text, and saving / loading presets. No Tkinter in here, so it can be tested
without opening a window. The settings are one plain dict; its keys are the SAME names the old GUI saved
in 'randomizer settings.txt', so presets saved by the old window still load."""
import json                                         # presets are stored as JSON
import random                                       # rolls the 'Random' toggles
import re                                           # spots a date in a list's name
from pathlib import Path                            # file paths

from ygo_builder import Request                     # what the engine takes
from ygo_labels import strip_display                # removes the display-only ' (opponent)'

# Keys that hold file locations. Presets do NOT restore these (an old preset would point at the old card file).
PATH_KEYS = ("csv_path", "label_csv_path")

# Which settings are on/off tick boxes, and which are the four Extra Deck type switches.
EXTRA_TYPES = ["allow_fusion", "allow_synchro", "allow_xyz", "allow_link"]
ERAS = ["Random", "DM", "GX", "5Ds", "Zexal", "Arc-V", "VRAINS", "SEVENS", "GO RUSH!!"]   # Format Library's eras
BUILD_ORDERS = ["main-first", "extra-first", "random"]
UNLIMITED = "Unlimited"                             # the 'no banlist' choice in the Forbidden List dropdown
RANDOM = "Random"                                   # the 'pick a list for me' choice (like the archetype boxes)


def defaults() -> dict:
    """Starting value of every setting (same defaults as the old GUI)."""
    return {
        "csv_path": "cards.csv", "label_csv_path": "Stats.csv",
        "main_size": "40", "extra_size": "15", "rand_main_size": False, "rand_extra_size": False,
        "seed": "", "rand_seed": False, "build_order": "main-first",
        "monster_target": "20", "spell_target": "10", "trap_target": "10", "rand_comp": False,
        # Main Deck monster Levels: off = the old fixed weights (old presets load like this); on = the three sliders
        "level_sliders": False, "level_low_pct": 60, "level_mid_pct": 30, "level_high_pct": 10, "rand_levels": False,
        "allow_normals": True,                                              # Normal Monsters toggle (off = theme ones only)
        "max_era": "GO RUSH!!", "rand_era": False,
        "min_rating": "2", "max_rating": "5", "rand_rating": False,
        "allow_fusion": True, "allow_synchro": True, "allow_xyz": True, "allow_link": True, "rand_extra_types": False,
        "banlist": UNLIMITED, "date_pool": True,                           # format: Forbidden List, release-date card pool
        "deck_type_overall_pct": 60, "support_overall_pct": 40, "generic_overall_pct": 0, "rand_theme_support": False,
        "deck1_pct": 100, "deck2_pct": 0, "deck1_label": "Random", "deck2_label": "Random", "rand_deck12": False,
        "support_on": False, "support1_pct": 100, "support2_pct": 0, "support1_label": "Random", "support2_label": "Random", "rand_support12": False,
        "do_export": False, "export_prefix": "random_deck", "verbose_export": False,
    }


def _int(text, fallback: int) -> int:
    """int(text), or the fallback when it is blank / not a number."""
    try:
        return int(str(text).strip())
    except ValueError:
        return fallback


def roll(v: dict, rng: random.Random) -> None:
    """Apply every ticked 'Random' box by overwriting its settings in v (the window then shows the rolled values)."""
    if v["rand_main_size"]:
        v["main_size"] = str(rng.randint(40, 60))
    if v["rand_extra_size"]:
        v["extra_size"] = str(rng.randint(0, 15))
    if v["rand_seed"]:
        v["seed"] = str(rng.randint(0, 2_147_483_647))
    if v["rand_extra_types"]:
        for k in EXTRA_TYPES:                                   # each type on/off; it may even pick none (like the old GUI)
            v[k] = rng.choice([True, False])
    if v["rand_comp"]:                                          # monster/spell/trap targets that add up to the main size
        ms = max(40, min(60, _int(v["main_size"], 40)))
        m = rng.randint(0, ms)
        s = rng.randint(0, ms - m)
        v["monster_target"], v["spell_target"], v["trap_target"] = str(m), str(s), str(ms - m - s)
    if v["rand_era"]:
        v["max_era"] = rng.choice(ERAS[1:])                     # any real era (not "Random")
    if v.get("rand_levels"):                                    # three Level shares that add up to 100
        low = rng.randint(0, 100)
        mid = rng.randint(0, 100 - low)
        v["level_low_pct"], v["level_mid_pct"], v["level_high_pct"] = low, mid, 100 - low - mid
    if v["rand_rating"]:                                        # 0-5 in half steps, low one first
        a, b = sorted(rng.choice([i / 2 for i in range(11)]) for _ in range(2))
        v["min_rating"], v["max_rating"] = f"{a:g}", f"{b:g}"
    if v["rand_theme_support"]:
        theme = rng.randint(0, 100)
        support = rng.randint(0, 100 - theme)
        v["deck_type_overall_pct"], v["support_overall_pct"], v["generic_overall_pct"] = theme, support, 100 - theme - support
    for flag, a, b in (
                       ("rand_deck12", "deck1_pct", "deck2_pct"),
                       ("rand_support12", "support1_pct", "support2_pct")):
        if v[flag]:                                             # a slider pair: one random value, the other is the rest
            v[a] = rng.randint(0, 100)
            v[b] = 100 - v[a]


def make_request(v: dict, banlists: list = ()) -> Request:
    """Settings dict -> Request. Blank / bad numbers fall back to the defaults, like the old GUI.
       `banlists` = the Forbidden List names; a 'Random' Forbidden List picks one of them (or Unlimited)."""
    def num(key):                                               # optional whole number: blank -> None (no target)
        t = str(v[key]).strip()
        return _int(t, None) if t else None

    def flt(key):                                               # optional rating bound: blank / bad -> None
        try:
            return float(str(v[key]).strip())
        except ValueError:
            return None

    lo, hi = flt("min_rating"), flt("max_rating")
    if lo is not None and hi is not None and lo > hi:           # swapped bounds are fixed, not rejected
        lo, hi = hi, lo
    seed_text = str(v["seed"]).strip()
    if not seed_text:                                           # blank seed -> a fresh one (shown in the preview)
        seed = random.randint(0, 2_147_483_647)
    elif seed_text.lstrip("-").isdigit():
        seed = int(seed_text)
    else:                                                       # text seed -> a number made from the text
        seed = int.from_bytes(seed_text.encode("utf-8"), "little") & 0x7FFFFFFF
    era = str(v["max_era"]).strip()
    banlist = str(v.get("banlist", UNLIMITED)).strip()          # old presets have no banlist -> Unlimited
    if banlist == RANDOM:                                       # same seed -> same list, so a deck can be remade
        banlist = random.Random(seed).choice([UNLIMITED] + list(banlists))
    return Request(
        main_deck_size=max(40, min(60, _int(v["main_size"], 40))),
        extra_deck_size=max(0, min(15, _int(v["extra_size"], 15))),
        deck_type_overall_pct=int(v["deck_type_overall_pct"]),
        deck_type_1_label=strip_display(v["deck1_label"]), deck_type_1_pct=int(v["deck1_pct"]),
        deck_type_2_label=strip_display(v["deck2_label"]), deck_type_2_pct=int(v["deck2_pct"]),
        generic_overall_pct=int(v.get("generic_overall_pct", 0)),
        support_overall_pct=int(v["support_overall_pct"]), support_on=bool(v.get("support_on", False)),   # old presets lack the key -> OFF
        support_1_label=strip_display(v["support1_label"]), support_1_pct=int(v["support1_pct"]),
        support_2_label=strip_display(v["support2_label"]), support_2_pct=int(v["support2_pct"]),
        main_monster_target=num("monster_target"), main_spell_target=num("spell_target"), main_trap_target=num("trap_target"),
        max_era=None if era in ("", "Random") else era,
        min_card_rating=lo, max_card_rating=hi, seed=seed,
        allow_fusion=bool(v["allow_fusion"]), allow_synchro=bool(v["allow_synchro"]),
        allow_xyz=bool(v["allow_xyz"]), allow_link=bool(v["allow_link"]),
        build_order=v["build_order"],
        banlist=None if banlist in ("", UNLIMITED) else banlist,
        date_pool=bool(v.get("date_pool", True)),
        level_sliders=bool(v.get("level_sliders", False)),                         # (.get: old presets lack these keys)
        level_low_pct=_int(v.get("level_low_pct", 60), 60), level_mid_pct=_int(v.get("level_mid_pct", 30), 30),
        level_high_pct=_int(v.get("level_high_pct", 10), 10),
        allow_normals=bool(v.get("allow_normals", True)),
    )


def breakdown_text(req: Request) -> str:
    """The 'how many cards go where' lines shown at the top of the preview."""
    total = req.main_deck_size
    theme = int(round(total * req.deck_type_overall_pct / 100.0))
    generic = int(round(total * req.generic_overall_pct / 100.0))
    support = total - theme - generic
    d1 = int(round(theme * req.deck_type_1_pct / 100.0))
    s1 = int(round(support * req.support_1_pct / 100.0))
    fmt = req.banlist or UNLIMITED                                                  # the format line
    if req.banlist and req.date_pool and re.search(r"\d{4}\.\d{2}\.\d{2}", req.banlist):   # only lists with a date
        fmt += " (only cards released by its date)"
    return (f"Main deck size: {total}\n"
            f"  Deck Theme total: {theme}  (Deck1: {d1}, Deck2: {theme - d1})\n"
            f"  Generic total: {generic}\n"
            + (f"  Support total: {support}  (Support1: {s1}, Support2: {support - s1})\n" if req.support_on else
               f"  Support total: {support}  (Auto: staples + cards that help 1/3 of the deck)\n") +
            (f"Monster Levels: 1-4 {req.level_low_pct}% / 5-6 {req.level_mid_pct}% / 7+ {req.level_high_pct}%\n"
               if req.level_sliders else "") +
            ("Normal Monsters: off (only the theme's own)\n" if not req.allow_normals else "") +
            f"Format: {fmt}\n"
            f"Seed: {req.seed}   Build order: {req.build_order}\n")


# ---------------- presets (same file format as the old GUI) ----------------
def load_presets(folder: Path) -> tuple[dict, str | None]:
    """(presets, last_preset_name) from 'randomizer settings.txt'; empty if missing or broken."""
    try:
        data = json.loads((folder / "randomizer settings.txt").read_text(encoding="utf-8"))
        return data.get("presets", {}) or {}, data.get("last_preset")
    except (OSError, ValueError):
        return {}, None


def save_presets(folder: Path, presets: dict, last_name: str, current: dict) -> None:
    """Write all presets plus a hidden '_last' snapshot of the current settings."""
    data = {"last_preset": last_name, "presets": dict(presets)}
    data["presets"]["_last"] = dict(current)
    (folder / "randomizer settings.txt").write_text(json.dumps(data, indent=2), encoding="utf-8")


def merge_preset(base: dict, preset: dict) -> dict:
    """defaults + a loaded preset (unknown keys and file paths ignored, so old presets are safe to load)."""
    out = dict(base)
    for k, val in preset.items():
        if k in out and k not in PATH_KEYS:
            out[k] = val
    return out


if __name__ == "__main__":                                      # headless self-test
    v = defaults()
    v["deck1_label"] = "Archetype = Laval"; v["support1_label"] = "Opponent = Negation (opponent)"; v["seed"] = "abc"
    r = make_request(v); print(r.deck_type_1_label, r.support_1_label, r.seed, r.max_era, r.min_card_rating, r.max_card_rating)
    for k in ("rand_main_size", "rand_comp", "rand_rating", "rand_era", "rand_deck12", "rand_extra_types"):
        v[k] = True
    roll(v, random.Random(3))
    print(v["main_size"], v["monster_target"], v["spell_target"], v["trap_target"], v["min_rating"], v["max_rating"], v["max_era"], v["deck1_pct"], v["deck2_pct"])
    v["banlist"] = "Random"
    print(breakdown_text(make_request(v, ["Highlander", "HAT (2014.07.12)", "Master Duel"])))
    v["min_rating"], v["max_rating"] = "4", "1"; print("swap:", make_request(v).min_card_rating, make_request(v).max_card_rating)
    old = {"main_size": "55", "csv_path": "old v29.csv", "nonsense": 1}                 # an old-style preset
    m = merge_preset(defaults(), old); print(m["main_size"], m["csv_path"], m["banlist"])
