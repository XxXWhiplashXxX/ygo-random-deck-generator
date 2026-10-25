"""
ygo_builder.py  --  knows every card, and builds the MAIN deck.

WHERE THIS FITS: ygo_extra.generate_state() calls Builder.build_main() for the main deck; ygo_final.py then
finishes the whole deck. The Builder is made once (when the cards are loaded) and reused for every deck.

THE FILE, TOP TO BOTTOM
  settings        the numbers that tune the builder (GENERIC_CAP, STAPLE_CAP ...)
  Request         every setting from the window, in one object
  Info            one card: its row from cards.csv plus 'facts' (traits, archetypes ...), needs and helps
  build_infos()   cards.csv -> Infos
  Entry / State   one card copy in the deck / the deck being built
  Builder         set_format() + cap()        the format: copy limits, banlist, release dates, Genesys points
                  allowed()                    the card pool for a deck
                  eval_needs() ... synergy()   how well a card fits the deck as it is RIGHT NOW
                  pick() / fill()              weighted random choosing
                  theme_bucket() / support_bucket() / related_tiers()   which cards a label means
                  wincon_setup()               win-condition themes (Exodia ...)
                  build_main()                 the main deck, in five steps (see below)

HOW THE MAIN DECK IS BUILT (build_main)
  1. an empty deck with the monster / spell / trap targets          _new_deck()
  2. a win-condition theme puts its win card + pieces in first       _place_wincons()
  3. the slots are shared out: theme 1 / 2, support 1 / 2            _slot_plan()
  4. each label's slots are filled with that label's cards           _fill_label()
  5. every slot still empty: staples, helpers, linked cards, filler  _auto_fill()
Every pick is a WEIGHTED random choice - the weight of a card is how good it is for the deck RIGHT NOW:
     weight = level bias  x  needs factor  x  helps factor  (x  staple boost for filler)
  - level bias   : low-Level monsters are likelier (Level <= 4: 4, 5-6: 2, 7+: 1)
  - needs factor : 2 if its needs are covered by cards already in the deck, lower if not, 0 if no card could
  - helps factor : a card that supports certain cards is better when those cards are in the deck
When a card with unmet needs is picked, a PARTNER card that meets them is added too.
Everything random goes through ONE seeded generator, so the same seed gives the same deck.

NAMES USED HERE
  deck      a State: the deck being built (entries, counts, archetypes, what is 'on theme' ...)
  req       the Request (every setting from the window)
  info / c  one card (an Info)
  pool      the cards this deck may use (format, era, rating and Extra Deck switches already applied)
  bucket    the cards one label means ('Archetype = blackwing' -> every Blackwing card in the pool)
  branch    one OR-option of a label: 'A && B || C' has two branches, (A, B) and (C,)
"""
from __future__ import annotations

import random                                           # the (seeded) random generator
from collections import Counter                         # counts most-common Types / Attributes
import re                                               # finds the quoted names in card text
from dataclasses import dataclass, field                # tidy containers for settings / state
from pathlib import Path                                # file paths
from typing import Optional
from dataclasses import replace

import ygo_paths as P                                   # where the 'forbidden lists' folder is
from ygo_banlist import load_banlists, load_staples, load_points, copies_allowed, released_by   # banlists (Forbidden / Limited / Semi-Limited)
from ygo_data import (                                  # everything we built in step 1
    read_csv, load_tree, parse_expr, parse_atom, is_mechanic, mechanic_label,
    card_facts, card_matches, offered_mechanics, need_status, norm, norm_trait,
)

# --------------------------------------------------------------------------
# Tunable numbers (change these to change how picky the builder is)
# --------------------------------------------------------------------------
NEED_BONUS = 2.0              # needs factor for a card whose needs are fully covered
PARTNER_SCORE = 0.4           # trait need not covered yet, but a partner card exists in the pool
HELP_STEP = 0.25              # +25% weight per card it supports (up to HELP_MAX_CARDS)
HELP_MAX_CARDS = 4
NO_TARGET_FACTOR = 0.05       # a support card (Sangan...) with NOTHING in the deck to support: very unlikely
FEW_TARGET_FACTOR = 0.3       # ... with fewer than MIN_TARGETS matching cards in the deck
MIN_TARGETS = 3               # a searcher needs at least this many matching cards (copies) in the deck
SAMPLE_SIZE = 150             # how many random candidates we weigh per pick (speed)
MAX_COPIES = 3                # Yu-Gi-Oh copy limit
MIN_ON_THEME = 8              # if at least this many on-theme/archetype-less candidates exist, ignore the rest
PREFER_THEME = 6.0            # partners that belong to the theme bucket are 6x as likely
MIN_SYNERGY = 8               # support picks must link to the deck if at least this many linked candidates exist
SYNERGY_SAMPLE = 600          # how many candidates we test for a link (bigger = better matches, slower)
OFF_ARCHETYPE = 0.2           # weight multiplier for a card from an archetype the deck's theme does not use

# --------------------------------------------------------------------------
# Era handling (the CSV uses Format Library's eras: Duel Monsters, GX, 5DS, ZEXAL, ARC-V, VRAINS, SEVENS, GO RUSH!! -
# set from each card's release date by ygo_set_eras.py)
# --------------------------------------------------------------------------
ERA_ORDER = ["duel monsters", "gx", "5ds", "zexal", "arc-v", "vrains", "sevens", "go rush!!"]
ERA_ALIAS = {"dm": "duel monsters", "5d's": "5ds", "arc v": "arc-v", "go rush": "go rush!!",
             "modern": "go rush!!"}                      # 'Modern' (the old name, in saved presets) = up to the newest era


def era_index(text: str) -> Optional[int]:
    """Position of an era in time order (0 = oldest), or None if unknown/blank."""
    t = norm(text)                                       # lower-case, tidy spaces
    t = ERA_ALIAS.get(t, t)                              # map nicknames to the CSV spelling
    return ERA_ORDER.index(t) if t in ERA_ORDER else None


# A name after these words is an EXCLUSION ("except 'X'"), not something the card wants.
EXCEPT_RX = re.compile(r'(?:except|other than|excluding)\s+(?:the\s+)?"[^"]+"', re.I)

# --- Generic decks: keep specific / filler cards from taking over ---
SPECIFIC_CAP = 5            # at most this many copies of non-clean cards (archetype-bound or card-naming) in a Generic deck
SPECIFIC_FACTOR = 0.2       # ... and those cards are this much less likely to be picked
NORMAL_CAP = 4              # at most this many vanilla Normal monsters in a Generic deck
NORMAL_FACTOR = 0.3         # ... and they are this much less likely to be picked
BROAD_SHARE = 1 / 3         # a filler card counts as 'broad support' if it helps at least this share of the deck's cards
BROAD_LIST_RX = re.compile(r"\([^()]*\bor\b[^()]*,[^()]*\bor\b[^()]*\)", re.I)   # '(LIGHT or DARK, Pyro or Aqua)' = AND of two ORs, mis-parsed as OR
STAPLE_CAP = 0.20          # format staples fill at most this share of the deck (the first filler step)
STAPLE_MIN = 0.25          # ... and only cards played in at least this share of the format's decks count
STAPLE_BOOST = 9.0         # a filler card played in EVERY deck of the format is (1 + this) times likelier (see format_staples.json)
GENERIC_CAP = 0.15        # plain generic staples are at most this share of the deck (they come after theme and support)
BROAD_BOOST = 4.0           # ... and is this many times likelier than a plain staple
MIN_STRICT_BUCKET = 8       # fewer 'fits this deck' support cards than this -> add the looser matches back
THEME_JUNK_FACTOR = 0.02    # an umbrella-theme card (e.g. 'Warrior') that names a SUB-archetype the deck does not have is this much less likely
JUNK_FACTOR = 0.05          # a 'junk' support / filler pick (see Builder.junk) is this much less likely
PARTNER_FACTOR = 0.1        # ... cards that would need a partner card added are this much less likely
# --- Floodgates (Vanity's Fiend, Mistake, Royal Prison ...): what each one SUITS and what it CLASHES with ---------------
# Each entry is a start of a label ("self = special summon" also covers "self = special summon (gy)"), in lower case.
# "extra deck monster" means any Fusion / Synchro / Xyz / Link monster; "trait = normal" means a Normal Monster.
FLOODGATE_RULES = {
    "floodgate = special summon": {"suits":   ["self = tribute summon", "self = normal summon"],
                                   "clashes": ["self = special summon", "self = fusion summon", "self = synchro summon", "self = xyz summon",
                                               "self = link summon", "self = pendulum summon", "self = ritual summon", "self = contact fusion",
                                               "extra deck monster"]},
    "floodgate = normal summon":  {"suits":   ["self = special summon (hand)"],
                                   "clashes": ["self = normal summon", "self = tribute summon", "self = set monster"]},
    "floodgate = search":         {"suits":   [],
                                   "clashes": ["self = add from deck to hand"]},
    "floodgate = activation":     {"suits":   ["trait = normal"],
                                   "clashes": []},                                 # (too broad to say what it hurts)
    "floodgate = attack":         {"suits":   ["opponent = burn"],
                                   "clashes": ["self = direct attack", "self = multi-attack", "self = piercing battle damage"]},
    "floodgate = graveyard":      {"suits":   ["self = banish"],
                                   "clashes": ["self = send to gy (deck)", "self = self-mill", "self = special summon (gy)", "self = add from gy to hand",
                                               "self = shuffle into deck"]},
    "floodgate = tribute":        {"suits":   [],
                                   "clashes": ["self = tribute"]},                 # (also covers "self = tribute summon")
    "floodgate = burn":           {"suits":   ["opponent = burn"],
                                   "clashes": []},
}
CLASH_SHARE = 1 / 3          # a floodgate clashes with a deck when at least this share of the deck's cards (copies) does / needs what it stops
CLASH_FACTOR = 0.1           # ... then the floodgate is this much less likely - and so is a clashing card once a floodgate is in the deck
BOTH_SIDES = re.compile(r"\b(?:neither player|both players|each player|either player|any player|the turn player|monsters cannot|cards cannot|monster cards cannot|any monster|all face-up monsters|any card)\b|(?:^|[.:;]\s*)monsters\b", re.I)   # a floodgate that also stops YOU (not only "your opponent cannot ...")
ONE_SIDE = re.compile(r"\b(?:your opponent|the other player)\b[^.;]{0,30}\b(?:cannot|can only|must|skips)\b|\byour opponent's hand size limit\b", re.I)   # "Your opponent cannot Special Summon": it names the opponent
SUIT_BOOST = 2.0             # a floodgate is this many times likelier when the deck has at least MIN_TARGETS copies of cards it suits

RANDOM_MECH_MIN = 25        # a 'Random' support in a Generic deck picks a mechanic that at least this many cards perform


# --------------------------------------------------------------------------
# Settings for one deck (same field names as the old DeckBuildRequest so the GUI can plug in later)
# --------------------------------------------------------------------------
@dataclass
class Request:
    main_deck_size: int = 40
    deck_type_overall_pct: int = 60                      # % of slots for theme cards
    deck_type_1_label: Optional[str] = None              # None / "Random" = pick an archetype or series for me
    deck_type_1_pct: int = 100
    deck_type_2_label: Optional[str] = None
    deck_type_2_pct: int = 0
    generic_overall_pct: int = 0                        # dedicated generic slots (0 preserves old presets)
    support_overall_pct: int = 40                        # % of slots for support cards
    support_on: bool = True                              # False = ignore the Support labels; fill those slots with staples + cards that help >= 1/3 of the deck
    support_1_label: Optional[str] = None
    support_1_pct: int = 100
    support_2_label: Optional[str] = None
    support_2_pct: int = 0
    main_monster_target: Optional[int] = 20              # soft type targets
    main_spell_target: Optional[int] = 10
    main_trap_target: Optional[int] = 10
    max_era: Optional[str] = None                        # e.g. "GX": that era and every earlier one
    min_card_rating: Optional[float] = None
    max_card_rating: Optional[float] = None
    seed: Optional[int] = None
    # Extra deck settings (used by ygo_extra.py)
    extra_deck_size: int = 15
    allow_fusion: bool = True
    allow_synchro: bool = True
    allow_xyz: bool = True
    allow_link: bool = True
    extra_type_pcts: tuple[int, int, int, int] = (25, 25, 25, 25)  # Fusion, Synchro, Xyz, Link shares
    extra_match_main_ratio: bool = True                     # match main Theme:Generic ratio in Extra Deck
    build_order: str = "main-first"                      # main-first / extra-first / random
    # Format settings
    banlist: Optional[str] = None                        # name of a list in 'forbidden lists' (None = Unlimited)
    date_pool: bool = True                               # with a banlist: only cards released by the list's date
    # Main Deck monster Levels (Builder.level_bias) and the Normal Monsters toggle (Builder.normal_ok)
    level_sliders: bool = False                          # False = the fixed 4 / 2 / 1 weights, as before
    level_low_pct: int = 60                              # share of Main Deck monsters that are Level 1-4 ...
    level_mid_pct: int = 30                              # ... Level 5-6 ...
    level_high_pct: int = 10                             # ... Level 7 or higher (soft targets; they add up to 100)
    allow_normals: bool = True                           # False = no Normal Monsters, except the theme's own


# --------------------------------------------------------------------------
# One card, pre-digested so we do not re-parse text on every pick
# --------------------------------------------------------------------------
@dataclass
class Info:
    row: dict                                            # the raw CSV row
    name: str
    facts: dict                                          # what the card IS (type, attribute, ...)
    needs: list                                          # OR-branches from strictSupportedBy + generalSupportedBy
    helps: list                                          # OR-branches from strictSupports (who it helps)
    offered: set                                         # mechanics it performs (for enablers)
    generic: bool                                        # no needs at all and no strictSupports
    kind: str                                            # Monster / Spell / Trap
    level: int                                           # 0 for non-monsters
    main_deck: bool                                      # False for Fusion/Synchro/Xyz/Link
    era: Optional[int]
    rating: float
    clean: bool = False                                  # truly generic: no archetype, no named cards, nothing specific needed


def is_clean(r: dict, facts: dict, extra: bool, no_needs: bool, names_friends: bool = False, ignore_text: bool = False) -> bool:
    """A 'clean' card works on its own in ANY deck, so a Generic deck may use it freely.
       Belonging to an archetype does NOT stop that (Summoned Skull, the Monarchs are staples that happen to have an archetype).
       What does stop it:
         - required cards, or Ritual / Pendulum (they need a partner card),
         - hard needs (`no_needs` False),
         - its Supports column pointing at any card trait (`names_friends`): a name, archetype, Type, Attribute ... Only a pure
           mechanic ('Self = Draw') is fine, because that describes the card's own effect,
         - text (Extra Deck: materials) that quotes something other than its own name / own archetype."""
    if (r.get("required cards") or "").strip():
        return False                                     # needs particular cards
    if {"ritual", "pendulum"} & facts["trait"]:
        return False                                     # needs a partner card
    own = facts["archetype"] | {norm(r["name"])}         # names the card itself answers to
    text = "" if ignore_text else ((r.get("materials") if extra else r.get("card text")) or "")   # flavor text names nothing
    if any(norm(q) not in own for q in re.findall(r'"([^"]+)"', text)):
        return False                                     # names some OTHER card / archetype
    if names_friends:
        return False                                     # its support points at an archetype / named card
    return extra or no_needs


# Words that only appear in real game text. A monster whose text (minus the "(treated as ...)" bracket) has none of them is
# flavor text only: Summoned Skull's "Among the Fiend-Type monsters..." must not count as support for Fiends.
GAME_WORDS = re.compile(r"\b(atk|def|level|rank|summon\w*|graveyard|gy|deck|hand|field|turn|phase|destroy\w*|banish\w*|draw|damage|opponent|"
                        r"tribute\w*|counter\w*|spell|trap|position|equip\w*|target\w*|negate\w*|life points|lp|discard\w*|control|attacks?|"
                        r"increase\w*|decrease\w*|gains?|loses?|cannot|can't|you|your|once|per|if|when|while|activate\w*|effect\w*|sent|send|"
                        r"add|select|change\w*|return\w*|remove\w*|treated|material\w*|excavate|shuffle|reveal|flip\w*|face-up|face-down|zone|battle)\b|[:;+]", re.I)


WEAK_WORDS = {"when", "if", "while", "once", "per", "turn", "field", "control", "level", "position", "effect", "sent"}   # common in flavor text too
EFFECT_SHAPE = re.compile(r"[:;]|\bthis card\b|\byou\b|\byour\b|\bopponent", re.I)             # how real effect text is built


def text_is_flavor(kind: str, facts: dict) -> bool:
    """Normal and Non-effect monsters have NO effect: everything in their card text is flavor, so it is ignored entirely.
       (A Pendulum monster is the exception: its Pendulum Effect is real text.)"""
    return kind == "Monster" and bool({"normal", "non-effect"} & facts["trait"]) and "pendulum" not in facts["trait"]


def flavor_only(r: dict, extra: bool) -> bool:
    """True for a Main Deck monster whose card text is only flavor text (no game words at all)."""
    if extra or (r.get("primary card type") or "").strip() != "Monster":
        return False
    text = re.sub(r"\([^)]*\)", "", r.get("card text") or "").strip()      # drop '(This card is always treated as ...)'
    if not text:
        return False
    hits = [m.group(0).lower() for m in GAME_WORDS.finditer(text)]
    if not hits:
        return True
    # Ojama Green: "...when the three are together, something happens." One weak word ('when', 'if') and no effect structure
    # (no colon, no "you / this card / opponent") is still just flavor.
    return all(h in WEAK_WORDS for h in hits) and not EFFECT_SHAPE.search(text)


DISCARD_TRIGGER = re.compile(r"(?:if|when|while)\b[^.:;]{0,60}\b(?:is|are|was|were) discarded|discarded to the (?:gy|graveyard)", re.I)
DISCARD_BY_EFFECT = re.compile(r"discarded[^.:;]{0,60}by (?:a|an|your opponent's|the)? ?(?:card )?effect|by a card effect", re.I)
LOW_ATK_VANILLA = 500        # a vanilla (Normal) archetype monster with at most this ATK (Ojama Green...) only exists for its archetype


def discard_need(r: dict, facts: dict) -> str:
    """The mechanic a card needs when its effect only triggers from being DISCARDED, else ''.
         'Self = Discard (effect)' : discarded by a card EFFECT (Dark World: a cost does not trigger them)
         'Self = Discard'          : discarded for any reason, cost included (Fabled, Danger!)"""
    text = r.get("card text") or ""
    if not DISCARD_TRIGGER.search(text):
        return ""
    if "dark world" in facts["archetype"] or DISCARD_BY_EFFECT.search(text):
        return "Self = Discard (effect)"
    return "Self = Discard"


def card_kind_of(atom) -> Optional[str]:
    """'Monster' / 'Spell' / 'Trap' if this atom pins down what kind of card it is (Card Type = Trap, Trait = Effect Monster,
       Trait = Quick-Play Spell ...), else None."""
    v = norm(atom.value)
    if atom.field == "card type":
        return v.capitalize() if v in ("monster", "spell", "trap") else None
    if atom.field == "trait" and atom.op == "=" and "trap monster" not in v:      # (a Trap Monster really is both)
        if v.endswith("monster"): return "Monster"
        if "spell" in v: return "Spell"
        if "trap" in v: return "Trap"
    return None


def possible(branch) -> bool:
    """False for an 'A && B' branch no card can satisfy, e.g. 'Trait = Effect Monster && Card Type = Trap'.
       These come from text like 'Spell, Trap, or Effect Monster' being read as ONE AND-list instead of an OR-list."""
    kinds = {k for a in branch if (k := card_kind_of(a))}
    return len(kinds) <= 1


def misleading_negative_type_support(card_text: str) -> bool:
    """Non-Type monsters being weakened/restricted is not a support relationship.

    Only remove negative Type branches when the text identifies an actual penalty;
    cards genuinely strengthening non-Type monsters still retain their support.
    """
    txt = card_text.lower()
    return (bool(re.search(r"\bnon[- ](?:[a-z-]+)(?:-type)?\b", txt))
            and bool(re.search(r"\b(?:lose|loses|lost|takes? no damage|no damage|change all non-)\b", txt)))


def build_infos(rows: list[dict]) -> list[Info]:
    """Turn every CSV row into an Info (done once, takes a few seconds)."""
    out = []
    for r in rows:
        facts = card_facts(r)                            # what it is
        kind = r.get("primary card type", "").strip()    # Monster / Spell / Trap
        extra = bool({"fusion", "synchro", "xyz", "link"} & facts["trait"])   # Extra Deck monster?
        # needs = strictSupportedBy and generalSupportedBy joined: either column can hold a requirement
        needs = parse_expr(r.get("strictSupportedBy", "")) + parse_expr(r.get("generalSupportedBy", ""))
        helps = parse_expr(r.get("strictSupports", ""))
        needs = [branch for branch in needs if possible(branch)]        # drop AND-branches no card can meet (Effect Monster && Trap)
        helps = [branch for branch in helps if possible(branch)]
        # A debuff or damage restriction on non-X monsters is not positive support
        # for non-X monsters (e.g. Night Dragolich, Splendid VENUS, Gatling Ogre).
        # Protect generation even if cards.csv was built before the updater fix.
        if misleading_negative_type_support(r.get("card text", "")):
            helps = [branch for branch in helps
                     if not any(a.field == "type" and a.op == "!=" for a in branch)]
        if kind == "Monster" and not needs and not extra:
            dn = discard_need(r, facts)                  # 'If this card is discarded: ...' needs a way to discard it
            if dn:
                needs = parse_expr(dn)
        if kind == "Monster" and (text_is_flavor(kind, facts) or flavor_only(r, extra)):
            needs, helps = [], []                        # no real effect: any 'supports' were read from flavor text (Summoned Skull)
        try:    rating = float(r.get("card rating", "") or 0)
        except ValueError: rating = 0.0
        # a 0-ATK vanilla archetype member (Ojama Green) is a joke on its own and only makes sense with its archetype
        archetype_vanilla = (kind == "Monster" and (text_is_flavor(kind, facts) or flavor_only(r, extra)) and not extra
                             and bool(facts["archetype"] - {norm(r["name"])})
                             and facts.get("atk") is not None and 0 <= facts["atk"] <= LOW_ATK_VANILLA)
        out.append(Info(
            row=r, name=r["name"], facts=facts, needs=needs, helps=helps,
            offered=offered_mechanics(r),
            generic=(not needs and not helps and not archetype_vanilla),
            kind=kind, level=facts["level"] or 0,
            main_deck=(kind in ("Spell", "Trap")) or (kind == "Monster" and not extra),
            era=era_index(r.get("era", "")), rating=rating,
            clean=(not archetype_vanilla) and is_clean(r, facts, extra, not needs,
                           any(not is_mechanic(a) for br in helps for a in br), ignore_text=text_is_flavor(kind, facts)),     # supports ANY card trait (name, archetype, Type, ...) = not generic
        ))
    return out


# --------------------------------------------------------------------------
# The deck as it is being built
# --------------------------------------------------------------------------
@dataclass
class Entry:
    info: Info
    origin: str                                          # theme1 / theme2 / support1 / support2 / partner / generic
    reason: str = ""                                     # why it was picked (shown in the report)


@dataclass
class State:
    entries: list = field(default_factory=list)          # every card copy, in pick order
    counts: dict = field(default_factory=dict)           # name -> copies in deck
    unique: dict = field(default_factory=dict)           # name -> Info (one per distinct card)
    offered: set = field(default_factory=set)            # all mechanics the deck performs
    traits: set = field(default_factory=set)             # all traits the deck's cards have
    quota: Optional[dict] = None                         # remaining Monster/Spell/Trap targets
    arche: set = field(default_factory=set)              # archetypes used by the THEME cards
    prefer: set = field(default_factory=set)             # names of every card in the theme bucket (partners favour these)
    theme_arche: set = field(default_factory=set)        # archetypes the THEME LABELS name (unlike `arche`, never grows from the cards picked)
    generic_deck: bool = False                           # True when the theme is Generic (no archetype / type / attribute)

    def add(self, info: Info, copies: int, origin: str, reason: str) -> None:
        """Put `copies` copies of a card in the deck and update every running total."""
        for _ in range(copies):
            self.entries.append(Entry(info, origin, reason))
        self.counts[info.name] = self.counts.get(info.name, 0) + copies
        self.unique[info.name] = info
        self.offered |= info.offered                     # new mechanics become available
        self.traits |= info.facts["trait"]               # new traits become available
        if origin.startswith("theme"):                   # theme cards define which archetypes "belong" in this deck
            self.arche |= info.facts["archetype"]
        if self.quota is not None and info.kind in self.quota:   # use up some of the type target
            self.quota[info.kind] = max(0, self.quota[info.kind] - copies)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def t2_unused(req) -> bool:
    """True when the second theme gets no slots."""
    return req.deck_type_2_pct <= 0 or req.deck_type_1_pct >= 100 or req.deck_type_overall_pct <= 0


def junk_name(text: str) -> bool:
    """True if a 'Name = ...' value in the required-cards column is obviously not a card name:
       a joining word ('or', 'and'), a sentence fragment cut out of card text, or garbled characters."""
    t = text.strip()
    return (t.lower() in ("or", "and")                    # 'Name = or' came from 'X or Y'
            or len(t) > 60 or ";" in t                    # a whole clause, not a name (real names reach ~50)
            or t[:1].islower() or t[:2] == "s,"           # starts mid-sentence ('s, Plant monsters ...', 'monster that ...')
            or any(ord(ch) > 127 and ch in "ÃÂÎ±" for ch in t))   # mojibake


def clean_required(row: dict) -> dict:
    """Return the row with its 'required cards' column blanked when EVERY name in it is junk (see junk_name).
       Mixed values are left alone: the later stage already ignores the junk atoms."""
    col = row.get("required cards") or ""
    names = re.findall(r"Name\s*=\s*([^&|]+)", col)
    if names and all(junk_name(n) for n in names):
        row = dict(row)                                    # never edit the caller's row in place
        row["required cards"] = ""
    return row


class Builder:
    """Holds the filtered card pool plus caches, and builds decks from a Request."""

    def __init__(self, rows: list[dict], tree: dict, labels: list[str] | None = None):
        self.tree = tree                                 # mechanic tree (for need_status / covers)
        self.labels = labels or []
        rows = [clean_required(r) for r in rows]         # drop junk 'required cards' values ('or', cut-off sentences)
        self.infos = build_infos(rows)                   # all cards, pre-digested
        self._partner_cache: dict = {}                   # trait-branch -> list of Infos that satisfy it
        self.by_name = {norm(i.name): i for i in self.infos}                     # normalised name -> card
        self.arche_names = {a for i in self.infos for a in i.facts["archetype"]}   # every archetype that exists
        self._frac: dict = {}                            # branch -> share of all main-deck cards it matches (for `rarity`)
        self._refs: dict = {}                            # card name -> [("card", name) / ("arche", archetype)] it mentions
        self.banlists = load_banlists(P.FORBIDDEN)       # every list in the 'forbidden lists' folder
        self.all_staples = load_staples(P.DATA / "format_staples.json")   # list name -> {card: (score, copies)}
        self.staples: dict = {}                          # the chosen format's staples ({} = no deck data)
        self.all_points = load_points(P.DATA / "genesys_points.json")    # card name -> Genesys points (most cards: 0)
        self.point_cap: Optional[int] = None             # the chosen Genesys cap (None = not Genesys)
        self._format = None                              # (banlist, date_pool) the caps below were made for
        self._caps: dict = {}                            # card name -> copies allowed in this format (0 = not allowed)
        self._default_cap = MAX_COPIES

    # ---- format: banlist, release-date pool, Highlander ------------------
    def set_format(self, req: Request) -> None:
        """Work out how many copies of each card this deck may have (done once per format, then remembered)."""
        key = (req.banlist, req.date_pool)
        if key == self._format:
            return                                       # same format as last time: nothing to redo
        self._format = key
        self.staples = self.all_staples.get(req.banlist, {}) if req.banlist else {}   # how often each card was played
        chosen = self.banlists.get(req.banlist) if req.banlist else None
        self.point_cap = chosen.points if chosen is not None else None                # Genesys: the deck's point budget
        self._default_cap = MAX_COPIES                                   # (a list can lower it: Highlander -> 1)
        banlist = self.banlists.get(req.banlist) if req.banlist else None      # the chosen list (None = Unlimited)
        if banlist is not None and not getattr(banlist, "names_fixed", False):       # first use of this list: old names -> current names
            from ygo_names import NameResolver                           # ('Red-Eyes B. Dragon' -> 'Red-Eyes Black Dragon')
            resolver = NameResolver([i.name for i in self.infos])
            banlist.by_name = {(resolver.find(n) or n).lower(): c for n, c in banlist.by_name.items()}
            banlist.names_fixed = True
        self._caps = {}
        for i in self.infos:
            n = copies_allowed(banlist, i.row, self._default_cap) if banlist else self._default_cap   # Forbidden 0 / Limited 1 / ...
            if banlist and req.date_pool and not released_by(banlist, i.row):       # not out yet on the list's date
                n = 0
            self._caps[i.name] = n

    def card_points(self, name: str) -> int:
        """Genesys points of one copy (0 for most cards, and always 0 outside Genesys)."""
        return self.all_points.get(name, 0) if self.point_cap is not None else 0

    def deck_points(self, entries: list) -> int:
        """Genesys points of a list of Entry (one per copy)."""
        return sum(self.card_points(e.info.name) for e in entries) if self.point_cap is not None else 0

    def cap(self, name: str) -> int:
        """Copies of this card a deck may have in the current format (3 normally; 0 = not allowed)."""
        return self._caps.get(name, self._default_cap)

    # ---- filters ---------------------------------------------------------
    def rating_ok(self, c: Info, req: Optional[Request] = None) -> bool:
        """Is this card inside the rating limits? (Generic filler must be; theme / support picks PREFER it - see rating_tier.)"""
        lo, hi = (req.min_card_rating, req.max_card_rating) if req is not None else self.rating_range
        return (lo is None or c.rating >= lo) and (hi is None or c.rating <= hi)

    rating_range = (None, None)                          # set by allowed() / build_main so helpers without `req` can use rating_ok

    def rating_tier(self, cards: list) -> list:
        """Theme / support picks: the cards to choose from, nearest the rating limits first (ratings are whole numbers 0-5).
             1. every card INSIDE the min-max range, while any is left
             2. else the cards BELOW the range, closest first (a 4-5 deck takes 3s, then 2s ...)
             3. else - only when nothing inside or below is left - the cards ABOVE the range, closest first
           So a small theme (Sphinx) is still built, but runs on its out-of-range cards only when it must."""
        lo, hi = self.rating_range
        if lo is None and hi is None:
            return cards                                                      # no limits: every card is fine
        inside = [c for c in cards if self.rating_ok(c)]
        if inside:
            return inside                                                     # 1. inside the range
        below = [c for c in cards if lo is not None and c.rating < lo]
        if below:
            best = max(c.rating for c in below)                               # 2. the closest rating under the range
            return [c for c in below if c.rating == best]
        above = [c for c in cards if hi is not None and c.rating > hi]
        if above:
            best = min(c.rating for c in above)                               # 3. the closest rating over the range
            return [c for c in above if c.rating == best]
        return cards

    def theme_archetypes(self, req: Request) -> set:
        """Archetypes named by the deck's theme labels ('Archetype = Sphinx' -> {'sphinx'})."""
        out = set()
        for label in (req.deck_type_1_label, req.deck_type_2_label):
            if label and "=" in label:
                at = parse_atom(label)
                if at.field == "archetype" and not is_mechanic(at):
                    out.add(norm(at.value))
        return out

    def allowed(self, req: Request, extra: bool = False, rated: bool = True) -> list[Info]:
        """Cards that pass the era and rating filters: main-deck cards, or (extra=True) Extra Deck monsters.
           rated=False skips the rating limits (theme and synergy cards matter more than rating)."""
        self.set_format(req)                                       # banlist / release dates / Highlander
        self.set_levels(req)                                       # Level sliders + Normal Monsters toggle
        self.rating_range = (req.min_card_rating, req.max_card_rating)   # for rating_tier (the Extra Deck asks before build_main runs)
        max_idx = era_index(req.max_era) if req.max_era else None
        keep = []
        for i in self.infos:
            if i.main_deck == extra: continue                      # keep only the half we are asked for
            if self.cap(i.name) <= 0: continue                     # Forbidden, or not released by the list's date
            if max_idx is not None and i.era is not None and i.era > max_idx: continue
            if rated and not self.rating_ok(i, req): continue
            keep.append(i)
        return keep

    # ---- partners --------------------------------------------------------
    def partners_for(self, traits: tuple, pool: list[Info]) -> list[Info]:
        """All pool cards that satisfy a branch's TRAIT atoms (cached per pool+branch)."""
        key = (traits, id(pool))
        if key not in self._partner_cache:
            self._partner_cache[key] = [p for p in pool if card_matches([traits], p.facts)]
        return self._partner_cache[key]

    # ---- needs -----------------------------------------------------------
    def eval_needs(self, info: Info, deck: State, pool: list[Info], scan_partners: bool = True):
        """Return (factor, partner_traits).
           factor        : multiplier for the pick weight (1.0 for cards with no needs).
           partner_traits: trait atoms of the best branch if a partner must be added, else None."""
        if not info.needs:
            return 1.0, None
        best_score, best_partner = 0.0, None
        for branch in info.needs:
            traits = tuple(a for a in branch if not is_mechanic(a))   # "is it a WATER monster?" part  (HARD)
            mechs = [a for a in branch if is_mechanic(a)]             # "Self = Banish (GY)" part      (SOFT)
            partner = None
            if not traits:
                t = 1.0                                               # nothing about other cards required
            elif any(o.name != info.name and card_matches([traits], o.facts) for o in deck.unique.values()):
                t = 1.0                                               # a deck card already satisfies it
            elif (not scan_partners) or self.partners_for(traits, pool):   # (skip the slow pool scan when asked)
                t, partner = PARTNER_SCORE, traits                    # we can add a partner later
            else:
                t = 0.0                                               # impossible with this pool
            unmet = sum(1 for m in mechs if need_status(m, deck.offered, deck.traits, self.tree) == "unmet")
            score = t / (1 + 0.5 * unmet)                             # each unmet soft need lowers the score
            if score > best_score:
                best_score, best_partner = score, partner
        return NEED_BONUS * best_score, best_partner

    def help_factor(self, info: Info, deck: State) -> float:
        """Bigger when the cards this card supports are already in the deck."""
        branches = [tuple(a for a in branch if not is_mechanic(a)) for branch in info.helps]
        branches = [branch for branch in branches if branch]                         # ignore branches with no trait atoms
        if not branches:
            return 1.0
        hit = [o for o in deck.unique.values() if o.name != info.name and any(card_matches([branch], o.facts) for branch in branches)]
        n = len(hit)                                                  # different cards it supports
        copies = sum(deck.counts[o.name] for o in hit)                  # ...and how many copies of them
        if copies == 0:
            return NO_TARGET_FACTOR                                   # nothing to search / support
        if copies < MIN_TARGETS:
            return FEW_TARGET_FACTOR                                  # too few targets to be worth playing
        return 1 + HELP_STEP * min(n, HELP_MAX_CARDS)

    def coverage(self, info: Info, deck: State) -> float:
        """Share of the deck's cards (by copies) that this card's strictSupports point at. 0.5 = helps half the deck."""
        total = len(deck.entries)
        branches = [tuple(a for a in branch if not is_mechanic(a)) for branch in info.helps]
        # keep only branches that name an identity (archetype / Type / Attribute / trait). A branch of bare ATK / DEF / Level / Card Type
        # is usually half of a conjunction the filler split (The Melody of Awakening Dragon: 'Dragon ATK>=3000' AND 'DEF<=2500').
        branches = [branch for branch in branches if any(a.field in ("archetype", "type", "attribute", "trait") for a in branch)]
        if not branches or total == 0:
            return 0.0
        helped = sum(deck.counts[o.name] for o in deck.unique.values() if o.name != info.name
                     and any(card_matches([branch], o.facts) for branch in branches))
        return helped / total

    def rarity(self, info: Info, deck: State) -> float:
        """0..1: how specific the trait is that this card supports. 'Effect Monster' (half of all cards) -> ~0; 'Rock' -> ~0.9.
           Uses the rarest branch that on its own covers 1/3+ of the deck."""
        total = len(deck.entries)
        if total == 0:
            return 0.0
        best = 0.5
        for branch in info.helps:
            t = tuple(a for a in branch if not is_mechanic(a))
            if not any(a.field in ("archetype", "type", "attribute", "trait") for a in t):
                continue
            helped = sum(deck.counts[o.name] for o in deck.unique.values() if o.name != info.name and card_matches([t], o.facts))
            if helped / total < BROAD_SHARE:
                continue
            key = repr(t)
            if key not in self._frac:                                                  # share of ALL main-deck cards this branch matches
                main = [i for i in self.infos if i.main_deck]
                self._frac[key] = sum(1 for i in main if card_matches([t], i.facts)) / max(1, len(main))
            best = min(best, self._frac[key])
        return 1.0 - min(best, 0.5) * 2.0

    def broad_support(self, deck: State, pool: list[Info]) -> dict:
        """{card name: boost} for cards that help at least BROAD_SHARE of the cards already in the deck."""
        out = {}
        for c in pool:
            if not c.helps or not c.main_deck or deck.counts.get(c.name, 0) >= self.cap(c.name) or self.junk(c, deck):
                continue
            if BROAD_LIST_RX.search(c.row.get("card text", "") or ""):             # 'Chaotic Elements' style lists are mis-parsed -> do not trust them
                continue
            priority, share = self._support_matches(c, deck)
            if priority:
                out[c.name] = BROAD_BOOST * (1.0 + share) * (0.25 + self.rarity(c, deck)) * (1.0 + priority)   # the more it helps, and the RARER the trait, the better
        return out

    def synergy(self, info: Info, deck: State) -> int:
        """How strongly a card is LINKED to the cards already in the deck (0 = unrelated).
           +1 for every deck card its strictSupports point at (up to 4),
           +1 if one of its needs is a real trait (not just 'is a Spell') that a deck card satisfies."""
        n = 0
        for o in deck.unique.values():                                  # who does it support?
            if o.name == info.name: continue
            for branch in info.helps:
                t = tuple(a for a in branch if not is_mechanic(a))
                if t and card_matches([t], o.facts):
                    n += 1; break
        n = min(n, HELP_MAX_CARDS)
        for branch in info.needs:                                          # who satisfies its needs?
            t = tuple(a for a in branch if not is_mechanic(a))
            plain = all(a.field == "card type" and a.value in ("monster", "spell", "trap") for a in t)
            if t and not plain and any(o.name != info.name and card_matches([t], o.facts) for o in deck.unique.values()):
                n += 1; break
        return n

    @staticmethod
    def tags(info: Info) -> set:
        """Everything a card DOES or NEEDS, as lower-case labels ('self = special summon (gy)'), plus 'extra deck monster' and 'trait = normal'.
           Worked out once per card and kept on the card."""
        if not hasattr(info, "_tags"):
            t = set(info.offered)                                                     # what it does (both Supports columns)
            t |= {mechanic_label(a) for branch in info.needs for a in branch if is_mechanic(a)}   # what it needs
            if info.kind == "Monster" and not info.main_deck: t.add("extra deck monster")
            if "normal" in info.facts["trait"]: t.add("trait = normal")
            info._tags = t
        return info._tags

    @staticmethod
    def hits(tags: set, starts: list) -> bool:
        """True if any tag begins with any of the label starts ('self = special summon' covers 'self = special summon (gy)')."""
        return any(t.startswith(s) for t in tags for s in starts)

    @staticmethod
    def one_sided(info: Info) -> bool:
        """True for a floodgate that only stops the OPPONENT ("Your opponent cannot Special Summon", "the other player cannot ..."):
           it never hurts your own deck, so it never clashes."""
        text = info.row.get("card text", "") or ""
        return bool(ONE_SIDE.search(text)) and not BOTH_SIDES.search(text)   # it names the opponent, and nothing says it stops both players

    def floodgate_factor(self, info: Info, deck: State) -> float:
        """Weight multiplier from FLOODGATE_RULES (1.0 = no effect):
             - a floodgate candidate whose clashes cover CLASH_SHARE of the deck -> CLASH_FACTOR; one the deck suits -> SUIT_BOOST,
             - a candidate that clashes with a floodgate ALREADY in the deck -> CLASH_FACTOR.
           A floodgate whose own exceptions fit the deck is never punished (Barrier Statue of the Inferno in a FIRE deck:
           its strictSupports - FIRE monsters - cover at least BROAD_SHARE of the deck)."""
        total = len(deck.entries)                                                     # copies in the deck so far
        if total == 0:
            return 1.0                                                                # nothing to clash with yet
        f = 1.0
        for gate in (g for g in self.tags(info) if g in FLOODGATE_RULES and not self.one_sided(info)):   # the candidate is a floodgate that stops you too
            rule = FLOODGATE_RULES[gate]
            clashing = sum(deck.counts[o.name] for o in deck.unique.values() if o.name != info.name and self.hits(self.tags(o), rule["clashes"]))
            suited = sum(deck.counts[o.name] for o in deck.unique.values() if o.name != info.name and self.hits(self.tags(o), rule["suits"]))
            if clashing / total >= CLASH_SHARE and self.coverage(info, deck) < BROAD_SHARE:
                f *= CLASH_FACTOR                                                     # it would stop what this deck does
            elif suited >= MIN_TARGETS:
                f *= SUIT_BOOST                                                       # the deck plays around it
        mine = self.tags(info)
        for o in deck.unique.values():                                                # floodgates already in the deck
            if o.name == info.name: continue
            for gate in (g for g in self.tags(o) if g in FLOODGATE_RULES and not self.one_sided(o)):   # (one that only stops the opponent is fine)
                spared = [t for t in (tuple(a for a in br if not is_mechanic(a)) for br in o.helps) if t]   # the floodgate's exceptions ("except FIRE monsters")
                if self.hits(mine, FLOODGATE_RULES[gate]["clashes"]) and not (spared and card_matches(spared, info.facts)):   # (a card its exception spares is fine)
                    f *= CLASH_FACTOR                                                 # this card does what the deck's floodgate stops
                    return f                                                          # (once is enough)
        return f

    @staticmethod
    def archetype_fit(info: Info, deck: State) -> float:
        """Penalise cards of a DIFFERENT archetype than the theme (e.g. a Kozmo card in a Snake-Eye deck).
        Cards with no archetype (generic staples) are never penalised."""
        a = info.facts["archetype"]
        if not a or not deck.arche or (a & deck.arche):
            return 1.0
        return OFF_ARCHETYPE

    def set_levels(self, req: Request) -> None:
        """Read the Level sliders and the Normal Monsters toggle from the Request (done on every allowed() call; cheap).
             self.level_shares : None = sliders off (the fixed 4 / 2 / 1 weights), else the three shares (0..1, adding up to 1)
             self.level_target : how many Main Deck monsters the shares are worked out from (the Monster target, or 20)
             self.allow_normals: False = no Normal Monsters, except cards of the deck's theme (self.theme_names)"""
        if req.level_sliders:
            raw = [max(0, req.level_low_pct), max(0, req.level_mid_pct), max(0, req.level_high_pct)]
            total = sum(raw) or 1                                                   # (all three at 0: treat as equal shares)
            self.level_shares = tuple(x / total for x in raw) if sum(raw) else (1 / 3, 1 / 3, 1 / 3)
        else:
            self.level_shares = None
        self.level_target = req.main_monster_target or 20
        self.allow_normals = req.allow_normals
        if self.band_freq is None:                                                  # worked out once: how common each band is
            mons = [i for i in self.infos if i.kind == "Monster" and i.main_deck]
            self.band_freq = tuple(max(0.01, sum(1 for i in mons if self.level_band(i) == b) / max(1, len(mons))) for b in range(3))

    level_shares = None                                  # (class defaults, so a Builder works before set_levels is called)
    level_target = 20
    allow_normals = True
    band_freq = None                                     # share of ALL Main Deck monsters in each band (about 69% / 14% / 17%)
    theme_names: set = set()                             # names of the theme's cards (set per deck by ygo_extra.generate_state)

    @staticmethod
    def level_band(info: Info) -> int:
        """0 = Level 1-4 (and Level 0 / no Level), 1 = Level 5-6, 2 = Level 7 or higher."""
        return 0 if info.level <= 4 else (1 if info.level <= 6 else 2)

    def level_bias(self, info: Info, deck: Optional[State] = None) -> float:
        """How much likelier a monster is because of its Level (Spells / Traps: always 1.0).
           Sliders OFF: low-Level monsters are more playable, so Level <= 4: 4, 5-6: 2, 7+: 1 (as before).
           Sliders ON : a band's weight follows its slider share, and - when we know the deck - a band that already has its
                        share of monsters becomes much rarer while a band that is still short becomes likelier."""
        if info.kind != "Monster":
            return 1.0
        band = self.level_band(info)
        if self.level_shares is None:                                               # sliders off: the old fixed weights
            return (4.0, 2.0, 1.0)[band]
        share = self.level_shares[band]
        # divide by how common the band is: most monsters are Level 1-4, so without this they would turn up far more
        # often than their slider says (a 40 / 40 / 20 setting came out as 59 / 22 / 18)
        w = max(0.05, share * 10) / (3 * self.band_freq[band])                     # 0% -> tiny (soft: rare, never banned)
        if deck is not None:
            have = sum(deck.counts[o.name] for o in deck.unique.values()
                       if o.kind == "Monster" and o.main_deck and self.level_band(o) == band)   # copies already in this band
            w *= 2.0 if have < share * self.level_target else 0.1                   # still short -> likelier; full -> rarer
        return w

    @staticmethod
    def strong_normal(info: Info) -> bool:
        """Independent high-stat Normal Monsters remain eligible even with outside-theme Normals disabled.

        Levels 1-4: 1900+ ATK or 2100+ DEF. Levels 5-6: 2500+ ATK or 3000+ DEF.
        Levels 7+ do not get an exemption, and Normal Pendulum monsters keep their original handling.
        """
        if info.kind != "Monster" or "normal" not in info.facts["trait"]:
            return False
        if "pendulum" in info.facts["trait"]:
            return False
        level = info.level
        atk = info.facts.get("atk")
        defense = info.facts.get("def")
        if 1 <= level <= 4:
            return (atk is not None and atk >= 1900) or (defense is not None and defense >= 2100)
        if 5 <= level <= 6:
            return (atk is not None and atk >= 2500) or (defense is not None and defense >= 3000)
        return False

    def normal_ok(self, info: Info) -> bool:
        """Off excludes outside-theme Normals, except independently useful high-stat Normals.
        Other format, rating and deck-building checks are still applied separately.
        """
        return (self.allow_normals or "normal" not in info.facts["trait"]
                or info.name in self.theme_names or self.strong_normal(info))

    def starters_needed(self, monsters: int) -> int:
        """How many Level 4-or-lower monsters the 'starter' rule asks for in a deck with this many monsters:
           8, or 40% of them if fewer - and with the sliders ON, never more than the Level 1-4 slider allows."""
        need = min(8, -(-monsters * 2 // 5))                                       # (8 = ygo_final.STARTERS)
        if self.level_shares is not None:
            need = min(need, round(self.level_shares[0] * monsters))
        return need

    # ---- picking ---------------------------------------------------------
    def pick(self, bucket: list[Info], deck: State, pool: list[Info], rng: random.Random, use_fit: bool = True, need_synergy: bool = False,
             boost: Optional[dict] = None, staples: bool = False):
        """Choose one card from `bucket` by weight. Returns (info, partner_traits) or (None, None).
           staples=True (filler only): cards the format's decks really played are likelier (format_staples.json)."""
        legal = [c for c in bucket if deck.counts.get(c.name, 0) < self.cap(c.name)     # not already at its copy limit
                 and self.normal_ok(c)]                                               # Normal Monsters toggle (theme cards always ok)
        if self.point_cap is not None:                                                # Genesys: only cards that still fit
            left = max(0, self.point_cap - self.deck_points(deck.entries))             # (never below 0: 0-point cards always fit;
            legal = [c for c in legal if self.card_points(c.name) <= left]           #  enforce_points fixes any overshoot)
        if deck.quota is not None:                                                      # respect type targets if possible
            q = [c for c in legal if c.kind not in deck.quota or deck.quota[c.kind] > 0]
            legal = q or legal
        if not legal:
            return None, None
        if deck.generic_deck:                                                           # Generic deck: limit specific + vanilla cards
            specific = sum(1 for e in deck.entries if not e.info.clean)
            normals = sum(1 for e in deck.entries if "normal" in e.info.facts["trait"])
            if specific >= SPECIFIC_CAP:
                legal = [c for c in legal if c.clean or (boost and c.name in boost)] or legal   # cap reached: clean (or broad-support) cards only
            if normals >= NORMAL_CAP:
                legal = [c for c in legal if "normal" not in c.facts["trait"]] or legal
        if use_fit and deck.arche:                                                      # prefer on-theme cards outright
            on_theme = [c for c in legal if self.archetype_fit(c, deck) == 1.0]
            if len(on_theme) >= MIN_ON_THEME:                                         # only if enough of them exist
                legal = on_theme
        if not use_fit and not deck.generic_deck:                                       # THEME picks: drop stray sub-archetype cards outright
            tidy = [c for c in legal if not self.theme_junk(c, deck)]                   # (Destiny HERO's D-Fusion in a 'Fusion' deck with no Destiny HERO)
            if len(tidy) >= MIN_ON_THEME:                                             # ...but only if enough clean cards remain
                legal = tidy
        if need_synergy:                                                              # support picks must link to the deck
            probe = rng.sample(legal, min(SYNERGY_SAMPLE, len(legal)))
            linked = [c for c in probe if self.synergy(c, deck) > 0]
            if len(linked) >= MIN_SYNERGY:                                            # enough linked cards: use only those
                legal = linked
        legal = self.rating_tier(legal)                                               # nearest the rating limits first (see rating_tier)
        sample = rng.sample(legal, min(SAMPLE_SIZE, len(legal)))                      # weigh a random subset (speed)
        if staples and self.staples:                                                  # ...but always weigh the format's staples
            seen = {c.name for c in sample}
            sample += [c for c in legal if c.name in self.staples and c.name not in seen]
        scored = []
        for c in sample:
            f, partner = self.eval_needs(c, deck, pool)
            fit = self.archetype_fit(c, deck) if use_fit else 1.0                       # off-archetype cards are less likely
            lb = self.level_bias(c, deck)                                               # Level weight (sliders or 4 / 2 / 1)
            if not use_fit and self.level_shares is not None:
                lb = lb ** 0.5                                                        # THEME picks follow the sliders softly
            w = lb * f * self.help_factor(c, deck) * fit
            if use_fit and self.junk(c, deck):
                w *= JUNK_FACTOR                                                      # support / filler picks must work in THIS deck
            elif not use_fit and not deck.generic_deck and self.theme_junk(c, deck):
                w *= THEME_JUNK_FACTOR                                                # theme picks: no stray sub-archetype cards
            if deck.generic_deck:                                                       # Generic deck: staples beat specific / vanilla cards
                w *= (1.0 if (c.clean or (boost and c.name in boost)) else SPECIFIC_FACTOR) * (NORMAL_FACTOR if "normal" in c.facts["trait"] else 1.0)
                if partner is not None:
                    w *= PARTNER_FACTOR                                               # needs a specific partner card: avoid in Generic decks
            w *= self.floodgate_factor(c, deck)                                         # floodgates: not in decks they stop, nor cards they stop in their decks
            if boost:
                w *= boost.get(c.name, 1.0)                                           # broad-support filler is favoured
            if staples and c.name in self.staples:
                w *= 1.0 + STAPLE_BOOST * self.staples[c.name][0]                     # played in most decks of this format
            scored.append((w, c, partner))
        total = sum(w for w, _, _ in scored)
        if total <= 0:                                                                # everything was impossible: uniform
            _, c, partner = rng.choice(scored)
            return c, partner
        w, c, partner = rng.choices(scored, weights=[s[0] for s in scored])[0]        # weighted draw
        return c, partner

    def fill(self, deck: State, bucket: list[Info], pool: list[Info], desired: int,
             origin: str, label: str, rng: random.Random, need_synergy: bool = False) -> None:
        """Add cards from `bucket` until `desired` slots are used (partners use slots too)."""
        attempts = 0
        while desired > 0 and attempts < desired * 40:
            attempts += 1
            card, partner = self.pick(bucket, deck, pool, rng, use_fit=not origin.startswith("theme"), need_synergy=need_synergy)
            if card is None:
                break
            copies = rng.randint(1, min(self.cap(card.name) - deck.counts.get(card.name, 0), desired))
            why = f"{origin}: {label}"
            if need_synergy:
                why += f" (linked to {self.synergy(card, deck)} deck features)"
            deck.add(card, copies, origin, why)
            desired -= copies
            if partner and desired > 0:                                               # add a card that satisfies its needs
                options = [p for p in self.partners_for(partner, pool)
                           if deck.counts.get(p.name, 0) < self.cap(p.name) and p.name != card.name and self.normal_ok(p)]
                options = self.rating_tier(options)                                   # nearest the rating limits first
                if options:
                    weights = [self.level_bias(p, deck) * self.archetype_fit(p, deck) * self.help_factor(p, deck)
                               * (PREFER_THEME if p.name in deck.prefer else 1.0) for p in options]   # stay on-theme
                    p = rng.choices(options, weights=weights)[0]
                    n = rng.randint(1, min(2, self.cap(p.name) - deck.counts.get(p.name, 0), desired))
                    deck.add(p, n, "partner", f"partner for {card.name}")
                    desired -= n

    def related_tiers(self, arche: str, pool: list[Info], kind: str = "archetype") -> list[list[Info]]:
        """For a SMALL archetype (or series: kind='series') that has run out of its own cards, two tiers of related cards (best first):
             1. cards that work WITH it: their text names it in quotes, or their Supports column names it
             2. clean (stand-alone) monsters of its most common Type and Attribute
           Its own cards are never included (they were already used)."""
        members = [c for c in pool if arche in c.facts[kind]]
        mine = {c.name for c in members}
        if kind == "archetype":                                                        # archetype names are already found by refs()
            named = [c for c in pool if c.name not in mine and any(k == "arche" and v == arche for k, v in self.refs(c))]
        else:                                                                         # a series: look for its name in quotes directly
            named = [c for c in pool if c.name not in mine and arche in
                     {norm(q) for q in re.findall(r'"([^"]+)"', EXCEPT_RX.sub("", c.row.get("card text", "")))}]
        helpers = [c for c in pool if c.name not in mine and c.helps and any(
            a.field == kind and norm(a.value) == arche for br in c.helps for a in br if not is_mechanic(a))]
        tier1 = list({c.name: c for c in named + helpers}.values())
        monsters = [c for c in members if c.kind == "Monster"]
        types = Counter(t for c in monsters for t in c.facts["type"]).most_common(1)           # the archetype's usual Type...
        attrs = Counter(t for c in monsters for t in c.facts["attribute"]).most_common(1)      # ...and Attribute
        want_t = {types[0][0]} if types else set()
        want_a = {attrs[0][0]} if attrs else set()
        tier2 = [c for c in pool if c.name not in mine and c.kind == "Monster" and c.clean and self.rating_ok(c)
                 and ((c.facts["type"] & want_t) or (c.facts["attribute"] & want_a))]
        return [tier1, tier2]

    # ---- buckets ---------------------------------------------------------
    def theme_bucket(self, label: str, pool: list[Info]) -> list[Info]:
        """Cards that ARE the label (trait label) or PERFORM it (mechanic label)."""
        atom = parse_atom(label)
        if is_mechanic(atom):
            want = self.tree.get(mechanic_label(atom), {mechanic_label(atom)})
            return [c for c in pool if c.offered & want]
        return [c for c in pool if card_matches([(atom,)], c.facts)]

    def support_bucket(self, label: str, pool: list[Info], arche: frozenset = frozenset()) -> list[Info]:
        """Cards whose Supports columns mention the label.
           A branch only counts if its OTHER archetype / name conditions fit the deck: D/D Gryphon supports
           '(Archetype = Dark Contract && Card Type = Trap)', which is NOT general Trap support, so it is only
           offered when the deck is actually built around Dark Contract (`arche` = the theme archetypes).
           Plain restating qualifiers ('Card Type = Monster') and stat limits (Level <= 4) are ignored."""
        atom = parse_atom(label)
        if is_mechanic(atom):
            want = self.tree.get(mechanic_label(atom), {mechanic_label(atom)})
            return [c for c in pool if c.offered & want]
        target = (atom.field, norm_trait(atom.value))

        def branch_fits(branch) -> bool:
            names_label = False
            for a in branch:
                if is_mechanic(a):
                    continue
                if (a.field, norm_trait(a.value)) == target:
                    names_label = True
                elif a.field in ("archetype", "name") and not (a.op == "=" and norm(a.value) in arche):
                    return False                                                      # supports only some OTHER archetype
            return names_label
        strict = [c for c in pool if any(branch_fits(branch) for branch in c.helps)]
        if len(strict) >= MIN_STRICT_BUCKET:
            return strict
        # Too few cards support this label for THIS deck (most Spell / Trap support is written for ONE other archetype,
        # e.g. 'Ancient Gear Spells', which is useless in a Blackwing deck). Do NOT add those off-theme loose matches.
        # Instead read the label as "play more cards of this kind": add cards that ARE of that kind (a Spell for
        # 'Card Type = Spell') and that work here: generic ones, or ones from the deck's own archetypes.
        own = [c for c in pool if card_matches([[atom]], c.facts)
               and not is_mechanic(atom) and (c.clean or (c.facts["archetype"] & arche))]
        seen = {c.name for c in strict}
        return strict + [c for c in own if c.name not in seen]

    def refs(self, c: Info) -> list:
        """Cards and archetypes this card's text names in quotes (not itself, not 'except "X"' exclusions)."""
        if c.name not in self._refs:
            text = EXCEPT_RX.sub("", c.row.get("card text", ""))
            out = []
            for q in re.findall(r'"([^"]+)"', text):
                n = norm(q)
                if n == norm(c.name):
                    continue
                if n in self.by_name:
                    out.append(("card", self.by_name[n].name))
                elif n in self.arche_names:
                    out.append(("arche", n))
            self._refs[c.name] = out
        return self._refs[c.name]

    def junk(self, c: Info, deck: State) -> bool:
        """True if the card cannot really work in this deck, even if its 'needs' look covered:
             (a) it belongs to an archetype that is not the deck's theme and has fewer than 2 other cards in the deck
                 (a lone D/D Gryphon, or Witch + Rucia of an off-theme archetype), or
             (b) its text names cards / archetypes and NONE of them is in the deck (EMERGENCY! with no Rescue-ACE)."""
        if c.clean:
            return False                                                              # a standalone card works in any deck
        own = c.facts["archetype"] - {norm(c.name)}
        if own and not (own & deck.arche):
            mates = {o.name for o in deck.unique.values() if o.name != c.name and (o.facts["archetype"] & own)}
            if len(mates) < 2:
                return True
        refs = self.refs(c)
        if refs:
            for kind, v in refs:
                if kind == "card" and v in deck.counts:
                    return False
                if kind == "arche" and (v in deck.arche or sum(1 for o in deck.unique.values() if v in o.facts["archetype"]) >= 3):
                    return False
            return True
        return False

    def theme_junk(self, c: Info, deck: State) -> bool:
        """For THEME picks under a broad umbrella theme ('Warrior' = 124 cards across Symphonic Warrior, Magnet Warrior, ...):
           True if the card's text names an archetype that is NOT the deck's theme and has fewer than 3 cards in the deck."""
        refs = [v for kind, v in self.refs(c) if kind == "arche"]
        if not refs:
            return False
        home = deck.theme_arche                                                          # the label's own archetype(s); empty for a Type / Attribute theme
                                                                                       # (picked cards' archetypes do NOT count: they start out as strays)
        return not any(v in home or sum(1 for o in deck.unique.values() if v in o.facts["archetype"]) >= 3 for v in refs)

    def wincon_extras(self, label: str) -> list:
        """Extra Deck cards a wincon label needs: the win card, plus any Extra Deck card it requires by name (C88 needs Number 88)."""
        if not norm(label or "").startswith(self.WIN_PREFIX):
            return []
        target = self.by_name.get(norm(self.WIN_ALIASES.get(norm(label.split("=", 1)[1]), label.split("=", 1)[1].strip())))
        if target is None or target.main_deck:
            return []
        out = [target]
        for nm in re.findall(r"Name = ([^&|]+?)\s*(?:&&|\|\||$)", target.row.get("required cards") or ""):
            c = self.by_name.get(norm(nm))
            if c is not None and not c.main_deck and c.name != target.name:
                out.insert(0, c)
        return out

    WIN_PREFIX = "alternative win condition ="
    WIN_ALIASES = {"exodia": "Exodia the Forbidden One"}                              # label word -> the card that actually wins

    def wincon_setup(self, label: str, pool: list[Info], rng: random.Random) -> Optional[tuple]:
        """'Alternative Win Condition = Destiny Board' -> (pieces, bucket, note).
             pieces = [(Info, copies, reason)]  the win card + everything it needs, placed in the deck FIRST
             bucket = cards that help the win card (for the rest of the theme slots)
           Returns None if `label` is not a wincon label."""
        if not norm(label or "").startswith(self.WIN_PREFIX):
            return None
        value = (label.split("=", 1)[1]).strip()
        target = self.by_name.get(norm(self.WIN_ALIASES.get(norm(value), value)))     # the win card, found by name
        if target is None:
            return ([], [], f"wincon card '{value}' not found")
        by_pool = {c.name: c for c in pool}
        note = ""
        pieces = []
        if not target.main_deck:                                                      # Xyz wincons (Number 88...): the Extra Deck gets the card
            chain = self.wincon_extras(label)                                         # ...and what it needs; the MAIN deck gets the materials
            note = f"Extra Deck wincon: {[c.name for c in chain]} go to the Extra Deck"
            for card in chain:
                m = re.search(r"count\s*=\s*(\d+)\s*:\s*\(\s*level\s*=\s*(\d+)", card.row.get("materials") or "", re.I)
                if not m:
                    continue
                need, lvl = int(m.group(1)), int(m.group(2))                           # '3 Level 8 monsters' -> 2 summons' worth, capped
                want = min(need * 2, 8)
                cands = [c for c in pool if c.kind == "Monster" and c.main_deck and c.level == lvl and not (c.row.get("required cards") or "").strip()
                         and self.normal_ok(c)]                                       # (Normal Monsters toggle)
                cands = [c for c in cands if c.clean] or [c for c in cands if self.loose_generic(c)] or cands
                while want > 0 and cands:
                    c = rng.choice(cands); cands = [x for x in cands if x.name != c.name]
                    n = min(self.cap(c.name), want)
                    pieces.append((c, n, f"material for wincon {card.name}")); want -= n
            if "rank-up-magic" in norm(target.row.get("card text") or ""):          # Number C88 must be summoned with a Rank-Up-Magic spell
                ruM = [c for c in pool if "rank-up-magic" in c.facts["archetype"] and c.kind == "Spell" and c.main_deck]
                def points_at(c):                                                     # does this spell's Supports column point at the win card?
                    return any(card_matches([tuple(a for a in br if not is_mechanic(a))], target.facts) for br in c.helps if any(not is_mechanic(a) for a in br))
                ruM = [c for c in ruM if points_at(c)] or ruM
                for c in rng.sample(ruM, min(2, len(ruM))):
                    pieces.append((c, 1, "Rank-Up-Magic for the wincon"))
        else:
            # 1 copy for Exodia / Destiny Board, otherwise a normal 1-3
            one = "exodia" in norm(target.name) or norm(target.name) == "destiny board"
            if self.cap(target.name) <= 0:                                            # the win card is Forbidden in this format
                return ([], [], f"wincon card '{target.name}' is not allowed in this format")
            pieces.append((target, 1 if one else rng.randint(1, self.cap(target.name)), f"wincon: {target.name}"))
            req = (target.row.get("required cards") or "")                            # 'Name = A && Name = B' -> the pieces it names
            for nm in re.findall(r"Name = ([^&|]+?)\s*(?:&&|\|\||$)", req):
                piece = by_pool.get(nm.strip())                                      # a named piece that is in the allowed pool
                if piece and piece.main_deck and piece.name != target.name:
                    pieces.append((piece, 1, f"wincon piece of {target.name}"))
            for arche in re.findall(r"Archetype = ([^&|]+?)\s*(?:&&|\|\||$)", target.row.get("strictSupportedBy") or ""):
                mates = [c for c in pool if norm(arche) in c.facts["archetype"] and c.main_deck and c.name != target.name]
                for c in mates[:4]:                                                   # Destiny Board: the four Spirit Message cards
                    pieces.append((c, 1, f"wincon piece of {target.name} ({arche})"))
        # cards that help the win card: their Supports columns name it or its archetype (broad traits like 'Normal' do not count)
        helpers = []
        for c in pool:
            if c.name == target.name or not c.helps or not c.main_deck:
                continue
            for br in c.helps:
                t = tuple(a for a in br if not is_mechanic(a))
                if t and card_matches([t], target.facts) and any(a.field in ("archetype", "name") for a in t):
                    helpers.append(c); break
        # plain staples that make a win-by-combo / win-by-turns deck work: draw cards + ways to survive (negate attacks / effects)
        staples = []
        for staple_label in ("Self = Draw", "Opponent = Negate attack", "Opponent = Negation"):
            staples += [c for c in self.theme_bucket(staple_label, pool) if c.clean and c.main_deck]
        bucket = list({c.name: c for c in helpers + staples}.values())
        if len(bucket) < 15:                                                          # still thin: any clean staple
            bucket += [c for c in pool if c.clean and c.main_deck and c not in bucket][:80]
        return (pieces, bucket, note)

    @staticmethod
    def normal_support_specific(c: Info) -> bool:
        """Don't use conditional Normal/non-Effect support as standalone Generic filler.

        Inspect the text as well as the CSV: older database builds can lack
        strict requirements, and even `clean` cards must obey this rule.
        """
        if c.kind not in ("Spell", "Trap"):
            return False
        txt = (c.row.get("card text") or "").lower()
        if re.search(r"\b(?:normal monsters?|non[- ]effect monsters?|except for (?:all )?effect monsters?)\b", txt):
            return True
        return any("trait = normal monster" in (c.row.get(k) or "").lower()
                   or "trait != effect monster" in (c.row.get(k) or "").lower()
                   for k in ("strictSupports", "strictSupportedBy"))

    @staticmethod
    def independent_generic(c: Info) -> bool:
        """Independent Main Deck candidates for Generic slots.

        Only *strict* support/needs impose synergy requirements here. The general
        columns describe soft preferences, not a mandatory theme or partner.
        Keep the separate named-card, Ritual/Pendulum, and card-text safeguards.
        """
        if not c.main_deck or Builder.normal_support_specific(c):
            return False
        if (c.row.get("strictSupports") or "").strip() or (c.row.get("strictSupportedBy") or "").strip():
            return False
        return is_clean(c.row, c.facts, False, True, False,
                        ignore_text=text_is_flavor(c.kind, c.facts))

    @staticmethod
    def loose_generic(c: Info) -> bool:
        """Generic enough for a Generic deck: no parsed needs, no required cards, not Ritual / Pendulum."""
        return (not Builder.normal_support_specific(c) and c.generic
                and not (c.row.get("required cards") or "").strip()) and not ({"ritual", "pendulum"} & c.facts["trait"])

    def random_mechanic(self, pool: list[Info], rng: random.Random) -> str:
        """For a 'Random' support in a Generic deck: choose a mechanic label that plenty of cards perform
           (so the deck has a real support theme instead of drawing from the whole card pool)."""
        counts: dict = {}
        for c in pool:
            for m in c.offered:
                counts[m] = counts.get(m, 0) + 1
        good = sorted(m for m, n in counts.items() if n >= RANDOM_MECH_MIN)           # sorted -> reproducible
        return rng.choice(good) if good else ""

   
    def random_theme(self, pool: list[Info], rng: random.Random) -> str:
        """Choose uniformly from the combined pool of supported random theme labels."""
        prefixes = (
            "Archetype = ",
            "Series = ",
            "Alternative Win Condition = ",
            "Attribute = ",
            "Type = ",
            "Trait = ",
        )

        candidates = {
            label for label in self.labels
            if label == "Generic"
            or any(label.startswith(prefix) for prefix in prefixes)
        }

        # Random means every distinct eligible label gets one entry.
        candidates.discard("Random")
        candidates = sorted(candidates, key=str.lower)

        return rng.choice(candidates) if candidates else "Generic"


    # ---- the whole deck --------------------------------------------------
    def build_main(self, req: Request, pre: Optional[list] = None) -> tuple[State, dict]:
        """The main deck, in five steps. pre = [(Info, copies, reason)]: cards to place first (extra-first build
           order: the Extra Deck's materials). Returns (the deck, debug info for the preview)."""
        rng = random.Random(req.seed)                                                 # the ONE random generator
        pool = self.allowed(req, rated=False)                                         # theme + synergy cards ignore the rating limits...
        self.rating_range = (req.min_card_rating, req.max_card_rating)               # ...only generic filler obeys them
        if not pool:
            raise ValueError("No cards left after the era/rating filters.")
        size = max(40, min(60, req.main_deck_size))
        # Resolve Random themes once, before setting up win conditions.
        theme1_label = req.deck_type_1_label
        theme2_label = req.deck_type_2_label

        if norm(theme1_label or "") == "random":
            theme1_label = self.random_theme(pool, rng)

        if norm(theme2_label or "") == "random":
            theme2_label = self.random_theme(pool, rng)
        resolved_req = replace(req,deck_type_1_label=theme1_label,deck_type_2_label=theme2_label,)
        deck = self._new_deck(resolved_req)                                               # 1. an empty deck with its type targets
        pre, wincon_bucket, wdebug = self._place_wincons(resolved_req, pool, rng, pre)
        for info, n, why in (pre or []):                                              #    pre-placed cards use up slots first
            deck.add(info, n, "material", why)
        t1, t2, s1, s2 = self._slot_plan(req, size, deck)                             # 3. slots for theme 1 / 2, support 1 / 2
        debug = {"size": size, "slots": {"theme1": t1, "theme2": t2, "support1": s1, "support2": s2},
                 "selected_categories": {"theme1": theme1_label, "theme2": theme2_label,
                                         "support1": req.support_1_label, "support2": req.support_2_label}}
        if wdebug:
            debug["wincon"] = wdebug
        for origin, slots, label, is_support in (("theme1", t1, theme1_label, False),
                                                ("theme2", t2, theme2_label, False),
                                                ("support1", s1, req.support_1_label, True),
                                                ("support2", s2, req.support_2_label, True),):
            if slots > 0:
                self._fill_label(deck, origin, slots, label, is_support, pool, rng, wincon_bucket, debug)
        # Dedicated generic allocation: selected independently of support, with legal standalone cards.
        generic_slots = min(max(0, round(max(0, size - len(pre or [])) * req.generic_overall_pct / 100)), size - len(deck.entries))
        if generic_slots:
            self._fill_generic_slots(deck, pool, generic_slots, rng)
        self._auto_fill(deck, pool, size, rng)                                        # 5. staples, helpers, filler
        while len(deck.entries) > size:                                               # partners can overshoot slightly
            deck.entries.pop()
        return deck, debug

    def _new_deck(self, req: Request) -> State:
        """Step 1: an empty deck, with the monster / spell / trap targets (if any were set) and whether it is a
           Generic deck (no real theme at all)."""
        quotas = None
        if any(v is not None for v in (req.main_monster_target, req.main_spell_target, req.main_trap_target)):
            quotas = {"Monster": req.main_monster_target or 0, "Spell": req.main_spell_target or 0, "Trap": req.main_trap_target or 0}
        deck = State(quota=quotas)
        deck.generic_deck = (norm(req.deck_type_1_label or "") in ("generic", "")             # no real theme at all
                             and (t2_unused(req) or norm(req.deck_type_2_label or "") in ("generic", "")))
        return deck

    def _place_wincons(self, req: Request, pool: list[Info], rng: random.Random, pre: Optional[list]) -> tuple:
        """Step 2: a win-condition theme (Exodia, Final Countdown ...) puts the win card and its pieces in first, and
           its helpers become that theme's cards. Returns (cards to place first, {theme label: helper cards}, notes)."""
        wincon_bucket = {}                                                            # theme label -> its helper bucket
        wdebug = []
        for theme_label in (req.deck_type_1_label, req.deck_type_2_label):
            w = self.wincon_setup(theme_label or "", pool, rng)
            if w is None:
                continue                                                              # not a win-condition label
            pieces, wb, note = w
            wincon_bucket[theme_label] = wb
            wdebug.append(f"{theme_label}: {[p[0].name for p in pieces]} {note}".strip())
            pre = list(pre or []) + pieces                                            # the win card + its pieces go in first
        return pre, wincon_bucket, wdebug

    @staticmethod
    def _slot_plan(req: Request, size: int, deck: State) -> tuple:
        """Step 3: how the slots left over are shared: theme vs support (the big slider), then theme 1 vs 2 and
           support 1 vs 2. With Support off, the support slots are left to the auto-fill (step 5)."""
        room = max(0, size - len(deck.entries))                                       # slots left for theme / support
        theme_total = round(room * req.deck_type_overall_pct / 100)
        generic_total = round(room * max(0, min(100, req.generic_overall_pct)) / 100)
        theme_total = min(room - generic_total, theme_total)
        support_total = room - theme_total - generic_total
        t1 = round(theme_total * req.deck_type_1_pct / 100); t2 = theme_total - t1
        s1 = round(support_total * req.support_1_pct / 100); s2 = support_total - s1
        if not req.support_on:                                                        # Support OFF: labels ignored, the top-up fills every support slot
            s1 = s2 = 0
        return t1, t2, s1, s2

    def _fill_label(self, deck: State, origin: str, slots: int, label: Optional[str], is_support: bool,
                    pool: list[Info], rng: random.Random, wincon_bucket: dict, debug: dict) -> None:
        """Step 4, for one label: find the label's cards (its 'bucket') and fill `slots` with them. A theme that runs
           out (a small archetype / series) is topped up with its RELATED cards rather than random ones."""
        def real(lbl):                                                                # blank / Random / Generic are not real labels
            return lbl and norm(lbl) not in ("random", "generic")

        if not is_support and not real(label):                                        # Random theme -> pick an archetype or series
            label = self.random_theme(pool, rng) if norm(label or "random") == "random" else ""
        if is_support and norm(label or "") == "random":
            self._fill_auto_support(deck, pool, slots, rng, origin, debug)
            return
        if norm(label or "") == "generic" or not label:
            bucket = [c for c in pool if (self.independent_generic(c) or self.loose_generic(c)) and self.rating_ok(c)]   # Strictly independent or previously accepted generic
        elif is_support:
            bucket = self.support_bucket(label, pool, frozenset(deck.arche))
        elif label in wincon_bucket:
            bucket = wincon_bucket[label]                                             # wincon theme = cards that help the win card
        else:
            bucket = self.theme_bucket(label, pool)
        if deck.generic_deck and is_support and norm(label or "") != "generic":       # Generic deck + a support mechanic:
            tidy = [c for c in bucket if not (c.row.get("required cards") or "").strip()      # skip cards that need named cards
                    and not ({"ritual", "pendulum"} & c.facts["trait"])]                        # and ones that need partners
            if len(tidy) >= 10:
                bucket = tidy
        if not bucket:                                                                # no support: auto-detect instead of unrelated cards
            if is_support:
                self._fill_auto_support(deck, pool, slots, rng, origin, debug)
                return
            bucket = [c for c in pool if self.rating_ok(c)] or pool
        if not is_support and label and real(label):                                  # remember which archetype the label itself names
            at = parse_atom(label)
            if at.field == "archetype" and not is_mechanic(at):
                deck.theme_arche.add(norm(at.value))
        debug[origin] = f"{label or 'generic'} ({len(bucket)} cards)"
        if not is_support and bucket is not pool:                                     # remember what "on theme" means
            deck.prefer |= {c.name for c in bucket}
        wants_generic = norm(label or "") in ("generic", "")                          # a Generic label stays unlinked on purpose
        before = len(deck.entries)
        self.fill(deck, bucket, pool, slots, origin, label or "generic", rng,
                  need_synergy=(is_support and not wants_generic))
        short = slots - (len(deck.entries) - before)                                  # theme slots the archetype could not fill
        if not is_support and short > 0 and label and real(label):
            at2 = parse_atom(label)
            if at2.field in ("archetype", "series") and not is_mechanic(at2):         # a small archetype / series ran out: use related cards
                got = 0
                for tier in self.related_tiers(norm(at2.value), pool, at2.field):
                    if short <= 0:
                        break
                    b2 = len(deck.entries)
                    self.fill(deck, tier, pool, short, origin, f"related to {label}", rng)
                    short -= len(deck.entries) - b2
                    got += len(deck.entries) - b2
                if got:
                    debug[origin] += f" + {got} related cards"

    def _fill_generic_slots(self, deck: State, pool: list[Info], slots: int, rng: random.Random) -> None:
        """Fill the explicit Generic share with standalone cards, not random archetype engines."""
        bucket = [c for c in pool if c.main_deck and self.rating_ok(c)
                  and not self.normal_support_specific(c)
                  and (c.clean or self.independent_generic(c) or self.loose_generic(c))]
        self.fill(deck, bucket, pool, slots, "generic", "dedicated Generic share", rng)

    def _support_detail(self, info: Info, deck: State) -> tuple[int, float, str]:
        """Best qualifying support category, coverage, and the actual supported label."""
        total = len(deck.entries)
        if total < 6:
            return 0, 0.0, ""
        best = (0, 0.0, "")
        for branch in info.helps:
            categories = []
            for atom in branch:
                if is_mechanic(atom): categories.append(5)
                elif atom.field == "type": categories.append(4)
                elif atom.field == "attribute": categories.append(3)
                elif atom.field == "archetype": categories.append(2)
            if not categories:
                continue
            hits = 0
            for other in deck.unique.values():
                if other.name == info.name:
                    continue
                matches = True
                for atom in branch:
                    if is_mechanic(atom):
                        want = self.tree.get(mechanic_label(atom), {mechanic_label(atom)})
                        if not (other.offered & want): matches = False; break
                    elif not card_matches([(atom,)], other.facts):
                        matches = False; break
                if matches:
                    hits += deck.counts[other.name]
            share = hits / total
            if share >= BROAD_SHARE:
                label = " && ".join(f"{a.field.title()} {a.op} {str(a.value).title()}" for a in branch)
                candidate = (max(categories), share, label)
                if candidate[:2] > best[:2]:
                    best = candidate
        return best

    def _support_matches(self, info: Info, deck: State) -> tuple[int, float]:
        """Priority and coverage, shared with broad-support weighting."""
        priority, share, _ = self._support_detail(info, deck)
        return priority, share

    def _fill_auto_support(self, deck: State, pool: list[Info], slots: int,
                           rng: random.Random, origin: str, debug: dict) -> None:
        """Automatic support: Mechanic > Type > Attribute > Archetype > generic.
        Only select cards whose strictSupports really cover a third of the current deck.
        """
        before = len(deck.entries)
        for _ in range(slots * 5):
            if len(deck.entries) - before >= slots: break
            ranked = [(c, self._support_matches(c, deck)) for c in pool if c.main_deck and c.helps]
            ranked = [(c, rank) for c, rank in ranked if rank[0] and not self.junk(c, deck)]
            card = None
            for priority in (5, 4, 3, 2):
                bucket = [c for c, r in ranked if r[0] == priority]
                if bucket:
                    card, _ = self.pick(bucket, deck, pool, rng, need_synergy=False,
                                        boost={c.name: 1 + r[1] * 3 for c, r in ranked if r[0] == priority})
                    if card: break
            if card is None: break
            room = min(slots - (len(deck.entries) - before), self.cap(card.name) - deck.counts.get(card.name, 0))
            if room <= 0: break
            _, coverage, selected_label = self._support_detail(card, deck)
            deck.add(card, rng.randint(1, room), origin,
                     f"automatic support: {selected_label} ({coverage:.0%} of deck)")
        left = slots - (len(deck.entries) - before)
        debug[origin] = f"automatic support ({slots-left}/{slots} matched; remaining slots use normal filler)"

    def _auto_fill(self, deck: State, pool: list[Info], size: int, rng: random.Random) -> None:
        """Step 5: fill every slot still empty, trying these in order each time (the first that gives a card wins):
             0. format staples   what that format's decks really played (Format Library / today's rankings),
                                 at most STAPLE_CAP of the deck
             A. broad support    cards that help 1/3+ of the deck (rare traits like Rock first)
             B. linked cards     cards that support, or are needed by, at least one deck card
             C. generic cards    plain filler, at most GENERIC_CAP of the deck (unless nothing else is left)
           A Generic deck (no theme) is all staples: it uses generic + broad cards together instead."""
        rated_pool = [c for c in pool if self.rating_ok(c)] or pool                   # generic filler obeys the rating limits
        generic_pool = [c for c in rated_pool if c.main_deck
                        and not self.normal_support_specific(c)
                        and not self.normal_support_specific(c)
                        and (c.clean or self.independent_generic(c) or self.loose_generic(c))] or rated_pool   # independent generic filler
        by_name = {c.name: c for c in pool}
        guard = 0
        generic_cap = round(size * GENERIC_CAP)                                       # how many generic copies we allow
        generic_used = 0
        staple_cap = round(size * STAPLE_CAP)                                         # how many staple copies we allow
        staple_used = 0
        staple_pool = [c for c in rated_pool if c.main_deck and c.name in self.staples
                       and self.staples[c.name][0] >= STAPLE_MIN]                     # step 0's cards (empty: no deck data)
        linked: list = []
        linked_at = -99                                                               # deck size when `linked` was last built
        while len(deck.entries) < size and guard < 500:
            guard += 1
            boost = self.broad_support(deck, [c for c in pool if c.helps])            # A: cards that help >= 1/3 of the deck
            broad_cards = [by_name[n] for n in boost if n in by_name]
            if len(deck.entries) - linked_at >= 6:                                    # B: rebuild now and then (the deck keeps changing)
                linked = [c for c in rated_pool if c.main_deck and not c.clean and c.name not in boost
                          and not self.junk(c, deck) and self.synergy(c, deck) > 0]
                linked_at = len(deck.entries)
            linked_ok = [c for c in linked if deck.counts.get(c.name, 0) < self.cap(c.name)]
            tiers = [(broad_cards, "broad support (helps 1/3+ of the deck)"),
                     (linked_ok, "linked to the deck (supports or is needed by a deck card)")]
            if staple_used < staple_cap:                                              # 0: format staples go first
                ok = [c for c in staple_pool if deck.counts.get(c.name, 0) < self.cap(c.name) and not self.junk(c, deck)]
                tiers.insert(0, (ok, "format staple"))
            if deck.generic_deck:                                                     # a Generic deck IS staples: keep the old mix (staples + broad support)
                tiers = [(generic_pool + broad_cards, "generic top-up")]
            elif generic_used < generic_cap:                                          # generics only up to the cap...
                tiers.append((generic_pool, "generic top-up"))
            else:                                                                     # ...unless nothing else is left
                tiers.append((generic_pool, "generic top-up (over cap, nothing else left)"))
            card = None
            for bucket, why in tiers:                                                 # first tier that can give a card wins
                if bucket:
                    card, _ = self.pick(bucket, deck, pool, rng, boost=boost if (why.startswith("broad") or deck.generic_deck) else None,
                                        staples=True)                                # filler leans on the format's staples
                    if card is not None:
                        break
            if card is None:
                break                                                                 # nothing can be added any more
            room = min(self.cap(card.name) - deck.counts.get(card.name, 0), size - len(deck.entries))
            n = rng.randint(1, room)
            if card.name in self.staples:                                             # a staple: as many copies as decks ran
                if self.staples[card.name][1] > 0:                                    # (0 = unknown, today's formats: keep 1-3)
                    n = max(1, min(self.staples[card.name][1], room))
                share = f"in {round(self.staples[card.name][0] * 100)}% of this format's decks"
                why = f"format staple ({share})" if why == "format staple" else f"{why} (also a format staple: {share})"
            if why.startswith("format staple"):                                       # its own origin: trimmed after plain filler
                deck.add(card, n, "staple", why)
                staple_used += n
            elif why.startswith(("broad support", "linked to the deck")):
                # Automatic support belongs to Support 1, not generic filler.
                _, coverage, selected_label = self._support_detail(card, deck)
                detail = (f"{selected_label} ({coverage:.0%} of deck)" if selected_label
                          else "supports existing deck cards")
                deck.add(card, n, "support1", f"{why}: {detail}")
            else:
                deck.add(card, n, "generic", why)
            if why.startswith("generic"):
                generic_used += n

    # ---- quality check ---------------------------------------------------
    def quality(self, deck: State, pool: list[Info]) -> tuple[int, int]:
        """(cards with needs, how many of those have every HARD need and SOFT need covered)."""
        needy = ok = 0
        for c in deck.unique.values():
            if not c.needs: continue
            needy += 1
            for branch in c.needs:
                traits = tuple(a for a in branch if not is_mechanic(a))
                mechs = [a for a in branch if is_mechanic(a)]
                trait_ok = (not traits) or any(o.name != c.name and card_matches([traits], o.facts) for o in deck.unique.values())
                mech_ok = all(need_status(m, deck.offered, deck.traits, self.tree) != "unmet" for m in mechs)
                if trait_ok and mech_ok:
                    ok += 1
                    break
        return needy, ok


def baseline_deck(builder: Builder, req: Request, theme_label: str) -> State:
    """The OLD way, for comparison: random cards from the theme pool, ignoring needs entirely."""
    rng = random.Random(req.seed)
    pool = builder.allowed(req)
    bucket = builder.theme_bucket(theme_label, pool) or pool
    deck = State()
    while len(deck.entries) < req.main_deck_size:
        c = rng.choice(bucket if rng.random() < 0.6 else pool)
        n = rng.randint(1, min(builder.cap(c.name) - deck.counts.get(c.name, 0), req.main_deck_size - len(deck.entries)) or 1)
        if deck.counts.get(c.name, 0) < builder.cap(c.name): deck.add(c, n, "baseline", "")
    return deck


# --------------------------------------------------------------------------
# Self-test:  python ygo_builder.py
# --------------------------------------------------------------------------
if __name__ == "__main__":
    here = Path(__file__).resolve().parent / "data"         # the data folder (program/data)
    builder = Builder(read_csv(here / "cards.csv"), load_tree(here / "game_mechanics_tree.csv"))
    pool_all = builder.allowed(Request())
    print(f"{len(pool_all)} main-deckable cards\n")

    # One deck printed in full, so you can read it.
    req = Request(seed=7, deck_type_1_label="Archetype = snake-eye", support_1_label="Self = Special Summon")
    deck, dbg = builder.build_main(req)
    print("DEBUG:", dbg)
    for origin in ("theme1", "theme2", "support1", "support2", "partner", "staple", "generic"):
        names = [e.info.name for e in deck.entries if e.origin == origin]
        if names:
            print(f"\n[{origin}] {len(names)} cards")
            for n in dict.fromkeys(names): print(f"   {names.count(n)}x {n}")
    needy, ok = builder.quality(deck, pool_all)
    print(f"\nneeds covered: {ok}/{needy}")

    # Compare against the old random method on many seeds and themes.
    themes = ["Archetype = snake-eye", "Archetype = blackwing", "Type = Dragon", "Attribute = WATER", "Archetype = laval"]
    new_r, old_r = [], []
    for th in themes:
        for seed in range(5):
            r = Request(seed=seed, deck_type_1_label=th)
            n_need, n_ok = builder.quality(builder.build_main(r)[0], pool_all)
            o_need, o_ok = builder.quality(baseline_deck(builder, r, th), pool_all)
            if n_need: new_r.append(n_ok / n_need)
            if o_need: old_r.append(o_ok / o_need)
    print(f"\navg share of cards with needs covered over {len(new_r)} new decks: {sum(new_r)/len(new_r):.0%}")
    print(f"avg share over {len(old_r)} old-style random decks:               {sum(old_r)/len(old_r):.0%}")
