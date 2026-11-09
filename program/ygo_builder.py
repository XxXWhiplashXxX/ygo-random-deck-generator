"""
ygo_builder.py  --  knows every card, and builds the MAIN deck.

WHERE THIS FITS: ygo_extra.generate_state() calls Builder.build_main() for the main deck; ygo_final.py then
finishes the whole deck. The Builder is made once (when the cards are loaded) and reused for every deck.

THE FILE, TOP TO BOTTOM
  settings        the numbers that tune the builder (GENERIC_CAP, STAPLE_CAP ...)
  Request         every setting from the page, in one object
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
  req       the Request (every setting from the page)
  info / c  one card (an Info)
  pool      the cards this deck may use (format, era, rating and Extra Deck switches already applied)
  bucket    the cards one label means ('Archetype = blackwing' -> every Blackwing card in the pool)
  branch    one OR-option of a label: 'A && B || C' has two branches, (A, B) and (C,)
"""
from __future__ import annotations                          # lets type hints name classes that are defined further down the file

import random                                           # the (seeded) random generator
from collections import Counter                         # counts most-common Types / Attributes
import re                                               # finds the quoted names in card text
from dataclasses import dataclass, field, replace       # tidy containers for settings / state; replace() copies one with changes
from pathlib import Path                                # file paths
from typing import Optional                                 # Optional[X] = "an X or None"

import ygo_paths as P                                   # where the 'forbidden lists' folder is
from ygo_labels import theme_labels                      # the labels a deck theme can be (shared with the website)
from ygo_banlist import load_banlists, load_staples, load_points, copies_allowed, released_by   # banlists (Forbidden / Limited / Semi-Limited)
from ygo_data import (                                  # everything we built in step 1
    read_csv, load_tree, parse_expr, parse_atom, is_mechanic, mechanic_label,  # (CSV readers, label parsing, mechanic helpers)
    card_facts, card_matches, offered_mechanics, need_status, norm, norm_trait,  # (card facts and need matching)
)

# --------------------------------------------------------------------------
# Tunable numbers (change these to change how picky the builder is)
# --------------------------------------------------------------------------
NEED_BONUS = 2.0              # needs factor for a card whose needs are fully covered
PARTNER_SCORE = 0.4           # trait need not covered yet, but a partner card exists in the pool
HELP_STEP = 0.25              # +25% weight per card it supports (up to HELP_MAX_CARDS)
HELP_MAX_CARDS = 4                                          # the 'helps' bonus stops growing after this many cards
NO_TARGET_FACTOR = 0.05       # a support card (Sangan...) with NOTHING in the deck to support: very unlikely
FEW_TARGET_FACTOR = 0.3       # ... with fewer than MIN_TARGETS matching cards in the deck
MIN_TARGETS = 3               # a searcher needs at least this many matching cards (copies) in the deck
SAMPLE_SIZE = 150             # how many random candidates we weigh per pick (speed)
MAX_COPIES = 3                # Yu-Gi-Oh copy limit
MIN_ON_THEME = 8              # if at least this many on-theme/archetype-less candidates exist, ignore the rest
PREFER_THEME = 6.0            # partners that belong to the theme bucket are 6x as likely
AUTO_THEME_SHARE = 2 / 3      # automatic support: share of its slots that go to cards supporting the theme (the rest: other support)
AUTO_MECHANIC_EDGE = 2.0      # automatic support, 'the rest' bucket: mechanic support is 2x as likely as Type / Attribute / archetype support
THEME_SUPPORT_BOOST = 3.0     # a THEME pick that also supports the theme (Pyramid Turtle / Goblin Zombie in a Zombie deck) is 3x as likely
MIN_SYNERGY = 8               # support picks must link to the deck if at least this many linked candidates exist
SYNERGY_SAMPLE = 600          # how many candidates we test for a link (bigger = better matches, slower)
OFF_ARCHETYPE = 0.2           # weight multiplier for a card from an archetype the deck's theme does not use

# --------------------------------------------------------------------------
# Era handling (the CSV uses Format Library's eras: Duel Monsters, GX, 5DS, ZEXAL, ARC-V, VRAINS, SEVENS, GO RUSH!! -
# set from each card's release date by ygo_set_eras.py)
# --------------------------------------------------------------------------
ERA_ORDER = ["duel monsters", "gx", "5ds", "zexal", "arc-v", "vrains", "sevens", "go rush!!"]  # the eras, oldest first (the spelling used in cards.csv)
ERA_ALIAS = {"dm": "duel monsters", "5d's": "5ds", "arc v": "arc-v", "go rush": "go rush!!",  # other spellings of an era -> the cards.csv spelling
             "modern": "go rush!!"}                      # 'Modern' (the old name, in saved presets) = up to the newest era


def era_index(text: str) -> Optional[int]:                  # function: an era's position in time (0 = oldest)
    """Position of an era in time order (0 = oldest), or None if unknown/blank."""
    t = norm(text)                                       # lower-case, tidy spaces
    t = ERA_ALIAS.get(t, t)                              # map nicknames to the CSV spelling
    return ERA_ORDER.index(t) if t in ERA_ORDER else None   # its position, or None if it isn't an era we know


# A name after these words is an EXCLUSION ("except 'X'"), not something the card wants.
EXCEPT_RX = re.compile(r'(?:except|other than|excluding)\s+(?:the\s+)?"[^"]+"', re.I)  # 'except "X"' / 'other than "X"' / 'excluding "X"' (with an optional 'the')

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
FLOODGATE_RULES = {                                         # floodgate kind -> the labels it suits and the labels it clashes with
    "floodgate = special summon": {"suits":   ["self = tribute summon", "self = normal summon"],  # stops Special Summons: good for Normal / Tribute Summon decks...
                                   # ...bad for decks that Special Summon in any way
                                   "clashes": ["self = special summon", "self = fusion summon", "self = synchro summon", "self = xyz summon",
                                               # (more Special Summon kinds)
                                               "self = link summon", "self = pendulum summon", "self = ritual summon", "self = contact fusion",
                                               "extra deck monster"]},  # (and any Extra Deck monster)
    "floodgate = normal summon":  {"suits":   ["self = special summon (hand)"],  # stops Normal Summons: good for decks that Special Summon from the hand
                                   # bad for Normal / Tribute Summon / set decks
                                   "clashes": ["self = normal summon", "self = tribute summon", "self = set monster"]},
    "floodgate = search":         {"suits":   [],           # stops searching: suits nothing in particular
                                   "clashes": ["self = add from deck to hand"]},  # bad for decks that add cards from the Deck
    "floodgate = activation":     {"suits":   ["trait = normal"],  # stops activations: fine with vanilla Normal Monsters
                                   "clashes": []},                                 # (too broad to say what it hurts)
    "floodgate = attack":         {"suits":   ["opponent = burn"],  # stops attacks: fine for burn decks
                                   # bad for decks that attack directly / many times / with piercing
                                   "clashes": ["self = direct attack", "self = multi-attack", "self = piercing battle damage"]},
    "floodgate = graveyard":      {"suits":   ["self = banish"],  # stops the Graveyard: good for banish decks
                                   # bad for decks that use the Graveyard
                                   "clashes": ["self = send to gy (deck)", "self = self-mill", "self = special summon (gy)", "self = add from gy to hand",
                                               "self = shuffle into deck"]},  # (or shuffle cards back)
    "floodgate = tribute":        {"suits":   [],           # stops Tributes: suits nothing in particular
                                   "clashes": ["self = tribute"]},                 # (also covers "self = tribute summon")
    "floodgate = burn":           {"suits":   ["opponent = burn"],  # stops burn: fine for (one-sided) burn decks
                                   "clashes": []},          # (clashes with nothing)
}
CLASH_SHARE = 1 / 3          # a floodgate clashes with a deck when at least this share of the deck's cards (copies) does / needs what it stops
CLASH_FACTOR = 0.1           # ... then the floodgate is this much less likely - and so is a clashing card once a floodgate is in the deck
BOTH_SIDES = re.compile(r"\b(?:neither player|both players|each player|either player|any player|the turn player|monsters cannot|cards cannot|monster cards cannot|any monster|all face-up monsters|any card)\b|(?:^|[.:;]\s*)monsters\b", re.I)   # a floodgate that also stops YOU (not only "your opponent cannot ...")
ONE_SIDE = re.compile(r"\b(?:your opponent|the other player)\b[^.;]{0,30}\b(?:cannot|can only|must|skips)\b|\byour opponent's hand size limit\b", re.I)   # "Your opponent cannot Special Summon": it names the opponent
SUIT_BOOST = 2.0             # a floodgate is this many times likelier when the deck has at least MIN_TARGETS copies of cards it suits

RANDOM_MECH_MIN = 25        # a 'Random' support in a Generic deck picks a mechanic that at least this many cards perform


# --------------------------------------------------------------------------
# Settings for one deck (ygo_settings.make_request() fills these from the page's settings)
# --------------------------------------------------------------------------
@dataclass                                                  # makes a simple settings container from the fields below
class Request:                                              # class: every setting for one deck
    main_deck_size: int = 40                                # Main Deck size (40-60)
    deck_type_overall_pct: int = 60                      # % of slots for theme cards
    deck_type_1_label: Optional[str] = None              # None / "Random" = pick an archetype or series for me
    deck_type_1_pct: int = 100                              # Theme 1's share of the theme slots
    deck_type_2_label: Optional[str] = None                 # the second theme (None = none)
    deck_type_2_pct: int = 0                                # Theme 2's share of the theme slots
    generic_overall_pct: int = 0                        # % of slots for plain generic cards (0 = none)
    support_overall_pct: int = 40                        # % of slots for support cards
    support_on: bool = True                              # False = ignore the Support labels; fill those slots with staples + cards that help >= 1/3 of the deck
    support_1_label: Optional[str] = None                   # the first Support category
    support_1_pct: int = 100                                # its share of the support slots
    support_2_label: Optional[str] = None                   # the second Support category
    support_2_pct: int = 0                                  # its share of the support slots
    main_monster_target: Optional[int] = 20              # soft type targets
    main_spell_target: Optional[int] = 10                   # (spells)
    main_trap_target: Optional[int] = 10                    # (traps)
    max_era: Optional[str] = None                        # e.g. "GX": that era and every earlier one
    min_card_rating: Optional[float] = None                 # lowest card rating allowed (None = no limit)
    max_card_rating: Optional[float] = None                 # highest card rating allowed (None = no limit)
    seed: Optional[int] = None                              # the random seed (same seed + same settings = same deck)
    # Extra deck settings (used by ygo_extra.py)
    extra_deck_size: int = 15                               # Extra Deck size (0-15)
    allow_fusion: bool = True                               # Fusion Monsters allowed?
    allow_synchro: bool = True                              # Synchro Monsters allowed?
    allow_xyz: bool = True                                  # Xyz Monsters allowed?
    allow_link: bool = True                                 # Link Monsters allowed?
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
    allow_normals: bool = True                           # False = no Normal Monsters, except the theme's own and ones a deck card asks for
    # "Base deck around a single card" (Advanced). Card 1 replaces Theme 1 and Card 2 replaces Theme 2: that theme's
    # slots are filled from the card's needs, supporters, archetype, Type / Attribute and the cards it supports
    # (Builder._fill_card). Support, Generic, sliders, deck sizes, banlist and Highlander all still apply.
    anchor_card: Optional[str] = None                    # Card 1: replaces Theme 1 (None / blank = off)
    anchor_card_2: Optional[str] = None                  # Card 2: replaces Theme 2 (None / blank = off)
    rand_card_1: bool = False                            # 'Random card' ticked for Card 1: the engine picks it (seeded)
    rand_card_2: bool = False                            # ... for Card 2


# --------------------------------------------------------------------------
# "Base deck around a single card": the numbers that shape it
# --------------------------------------------------------------------------
ANCHOR_COPIES = 3             # the chosen card is always played at 3 (a banlist or Highlander can lower it)
# The fill order around a card, and the share of its theme slots each step may use. A step that runs out of cards
# passes its unused slots on to the NEXT step; slots left after the last step cross over to the other theme
# (build_main), and only then go to the usual staples / filler.
ANCHOR_TIERS = [              # (step key, share of the card's theme slots, what the report calls it)
    ("needs",      0.20, "what it needs"),                  # step 1: the cards it needs
    ("supporters", 0.20, "cards that support it"),          # step 2: cards that support it
    ("archetype",  0.30, "its archetype"),                  # step 3: its archetype
    ("type_attr",  0.15, "its Type / Attribute"),           # step 4: its Type / Attribute
    ("supports",   0.15, "cards it supports"),              # step 5: cards it supports
]
ANCHOR_SPECIFIC_BOOST = 4.0   # a supporter that names the card or its archetype is this many times likelier ...
ANCHOR_BROAD_BOOST = 1.5      # ... one that supports its Type / Attribute this many times


class AnchorError(ValueError):                              # class: the error raised when Card 1 / Card 2 can't be used
    """The chosen card can't be built around (not found, Forbidden in this format, Extra Deck size 0 ...).
       The message is shown to the player as it is, so it is written for them."""


# --------------------------------------------------------------------------
# One card, pre-digested so we do not re-parse text on every pick
# --------------------------------------------------------------------------
@dataclass                                                  # makes a simple data container from the fields below
class Info:                                                 # class: one card, read once and kept
    row: dict                                            # the raw CSV row
    name: str                                               # the card's name
    facts: dict                                          # what the card IS (type, attribute, ...)
    needs: list                                          # OR-branches from strictSupportedBy + generalSupportedBy
    helps: list                                          # OR-branches from strictSupports (who it helps)
    offered: set                                         # mechanics it performs (for enablers)
    generic: bool                                        # no needs at all and no strictSupports
    kind: str                                            # Monster / Spell / Trap
    level: int                                           # 0 for non-monsters
    main_deck: bool                                      # False for Fusion/Synchro/Xyz/Link
    era: Optional[int]                                      # its era as a number (see era_index), or None
    rating: float                                           # its rating (0-5)
    clean: bool = False                                  # truly generic: no archetype, no named cards, nothing specific needed
    aliases: tuple = ()                                  # other names this card counts as, SAME deck section only (Builder fills it):
                                                         # Fusion Substitute -> ('Polymerization',). NOT Marine Dolphin -> Aqua Dolphin:
                                                         # NEX monsters share a name with a Main Deck card, but they are Extra Deck cards


# function: is this card 'clean' (works in any deck)?
def is_clean(r: dict, facts: dict, extra: bool, no_needs: bool, names_friends: bool = False, ignore_text: bool = False) -> bool:
    """A 'clean' card works on its own in ANY deck, so a Generic deck may use it freely.
       Belonging to an archetype does NOT stop that (Summoned Skull, the Monarchs are staples that happen to have an archetype).
       What does stop it:
         - required cards, or Ritual / Pendulum (they need a partner card),
         - hard needs (`no_needs` False),
         - its Supports column pointing at any card trait (`names_friends`): a name, archetype, Type, Attribute ... Only a pure
           mechanic ('Self = Draw') is fine, because that describes the card's own effect,
         - text (Extra Deck: materials) that quotes something other than its own name / own archetype."""
    if (r.get("required cards") or "").strip():             # it lists required cards
        return False                                     # needs particular cards
    if {"ritual", "pendulum"} & facts["trait"]:             # a Ritual or Pendulum card
        return False                                     # needs a partner card
    own = facts["archetype"] | {norm(r["name"])}         # names the card itself answers to
    text = "" if ignore_text else ((r.get("materials") if extra else r.get("card text")) or "")   # flavor text names nothing
    if any(norm(q) not in own for q in re.findall(r'"([^"]+)"', text)):  # every quoted name in the text that isn't itself
        return False                                     # names some OTHER card / archetype
    if names_friends:                                       # its support column names something specific
        return False                                     # its support points at an archetype / named card
    return extra or no_needs                                # Extra Deck cards are clean here; Main Deck ones only with no hard needs


# Words that only appear in real game text. A monster whose text (minus the "(treated as ...)" bracket) has none of them is
# flavor text only: Summoned Skull's "Among the Fiend-Type monsters..." must not count as support for Fiends.
# every word that only real effect text uses (or ':', ';', '+')
GAME_WORDS = re.compile(r"\b(atk|def|level|rank|summon\w*|graveyard|gy|deck|hand|field|turn|phase|destroy\w*|banish\w*|draw|damage|opponent|"
                        # (more game words)
                        r"tribute\w*|counter\w*|spell|trap|position|equip\w*|target\w*|negate\w*|life points|lp|discard\w*|control|attacks?|"
                        # (more game words)
                        r"increase\w*|decrease\w*|gains?|loses?|cannot|can't|you|your|once|per|if|when|while|activate\w*|effect\w*|sent|send|"
                        # (more game words)
                        r"add|select|change\w*|return\w*|remove\w*|treated|material\w*|excavate|shuffle|reveal|flip\w*|face-up|face-down|zone|battle)\b|[:;+]", re.I)


WEAK_WORDS = {"when", "if", "while", "once", "per", "turn", "field", "control", "level", "position", "effect", "sent"}   # common in flavor text too
EFFECT_SHAPE = re.compile(r"[:;]|\bthis card\b|\byou\b|\byour\b|\bopponent", re.I)             # how real effect text is built


def text_is_flavor(kind: str, facts: dict) -> bool:         # function: is all of this card's text flavor text?
    """Normal and Non-effect monsters have NO effect: everything in their card text is flavor, so it is ignored entirely.
       (A Pendulum monster is the exception: its Pendulum Effect is real text.)"""
    # a Monster that is Normal / Non-effect and not a Pendulum
    return kind == "Monster" and bool({"normal", "non-effect"} & facts["trait"]) and "pendulum" not in facts["trait"]


def flavor_only(r: dict, extra: bool) -> bool:              # function: is this monster's text only flavor (no game words)?
    """True for a Main Deck monster whose card text is only flavor text (no game words at all)."""
    if extra or (r.get("primary card type") or "").strip() != "Monster":  # Extra Deck cards and non-monsters:
        return False                                        # (not flavor)
    text = re.sub(r"\([^)]*\)", "", r.get("card text") or "").strip()      # drop '(This card is always treated as ...)'
    if not text:                                            # nothing left:
        return False                                        # (not flavor)
    hits = [m.group(0).lower() for m in GAME_WORDS.finditer(text)]  # every game word found
    if not hits:                                            # none at all:
        return True                                         # it's flavor
    # Ojama Green: "...when the three are together, something happens." One weak word ('when', 'if') and no effect structure
    # (no colon, no "you / this card / opponent") is still just flavor.
    return all(h in WEAK_WORDS for h in hits) and not EFFECT_SHAPE.search(text)  # only weak words, and nothing shaped like an effect


# "if/when ... is discarded" or "discarded to the GY"
DISCARD_TRIGGER = re.compile(r"(?:if|when|while)\b[^.:;]{0,60}\b(?:is|are|was|were) discarded|discarded to the (?:gy|graveyard)", re.I)
# "discarded ... by a card effect"
DISCARD_BY_EFFECT = re.compile(r"discarded[^.:;]{0,60}by (?:a|an|your opponent's|the)? ?(?:card )?effect|by a card effect", re.I)
LOW_ATK_VANILLA = 500        # a vanilla (Normal) archetype monster with at most this ATK (Ojama Green...) only exists for its archetype


def discard_need(r: dict, facts: dict) -> str:              # function: the Discard mechanic a card needs, if it only works when discarded
    """The mechanic a card needs when its effect only triggers from being DISCARDED, else ''.
         'Self = Discard (effect)' : discarded by a card EFFECT (Dark World: a cost does not trigger them)
         'Self = Discard'          : discarded for any reason, cost included (Fabled, Danger!)"""
    text = r.get("card text") or ""                         # the card's text
    if not DISCARD_TRIGGER.search(text):                    # no discard trigger:
        return ""                                           # (it needs nothing)
    if "dark world" in facts["archetype"] or DISCARD_BY_EFFECT.search(text):  # Dark World, or the text says 'by a card effect'
        return "Self = Discard (effect)"                    # (only an effect discard triggers it)
    return "Self = Discard"                                 # any discard works


def card_kind_of(atom) -> Optional[str]:                    # function: which kind of card a label piece means (or None)
    """'Monster' / 'Spell' / 'Trap' if this atom pins down what kind of card it is (Card Type = Trap, Trait = Effect Monster,
       Trait = Quick-Play Spell ...), else None."""
    v = norm(atom.value)                                    # the label value, normalised
    if atom.field == "card type":                           # 'Card Type = ...':
        return v.capitalize() if v in ("monster", "spell", "trap") else None  # Monster / Spell / Trap (or None)
    if atom.field == "trait" and atom.op == "=" and "trap monster" not in v:      # (a Trap Monster really is both)
        if v.endswith("monster"): return "Monster"          # 'Xyz Monster', 'Effect Monster' ...
        if "spell" in v: return "Spell"                     # 'Quick-Play Spell', 'Ritual Spell' ...
        if "trap" in v: return "Trap"                       # 'Continuous Trap' ...
    return None                                             # it doesn't say


def possible(branch) -> bool:                               # function: can any card meet this AND-branch?
    """False for an 'A && B' branch no card can satisfy, e.g. 'Trait = Effect Monster && Card Type = Trap'.
       These come from text like 'Spell, Trap, or Effect Monster' being read as ONE AND-list instead of an OR-list."""
    kinds = {k for a in branch if (k := card_kind_of(a))}   # the card kinds each piece pins down
    return len(kinds) <= 1                                  # more than one kind (Monster AND Trap) is impossible


def misleading_negative_type_support(card_text: str) -> bool:  # function: does the text only weaken non-X monsters?
    """Non-Type monsters being weakened/restricted is not a support relationship.

    Only remove negative Type branches when the text identifies an actual penalty;
    cards genuinely strengthening non-Type monsters still retain their support.
    """
    txt = card_text.lower()                                 # lower-case text
    return (bool(re.search(r"\bnon[- ](?:[a-z-]+)(?:-type)?\b", txt))  # mentions 'non-X' monsters...
            and bool(re.search(r"\b(?:lose|loses|lost|takes? no damage|no damage|change all non-)\b", txt)))  # ...together with a penalty word


def build_infos(rows: list[dict]) -> list[Info]:            # function: every CSV row -> an Info
    """Turn every CSV row into an Info (done once, takes a few seconds)."""
    out = []                                                # the list we build
    for r in rows:                                          # each row
        facts = card_facts(r)                            # what it is
        kind = r.get("primary card type", "").strip()    # Monster / Spell / Trap
        extra = bool({"fusion", "synchro", "xyz", "link"} & facts["trait"])   # Extra Deck monster?
        # needs = strictSupportedBy and generalSupportedBy joined: either column can hold a requirement
        needs = parse_expr(r.get("strictSupportedBy", "")) + parse_expr(r.get("generalSupportedBy", ""))  # what it needs, from both SupportedBy columns
        helps = parse_expr(r.get("strictSupports", ""))     # who it helps
        needs = [branch for branch in needs if possible(branch)]        # drop AND-branches no card can meet (Effect Monster && Trap)
        helps = [branch for branch in helps if possible(branch)]  # (same for helps)
        # A debuff or damage restriction on non-X monsters is not positive support
        # for non-X monsters (e.g. Night Dragolich, Splendid VENUS, Gatling Ogre).
        # Protect generation even if cards.csv was built before the updater fix.
        if misleading_negative_type_support(r.get("card text", "")):  # it only weakens non-X monsters:
            helps = [branch for branch in helps             # drop the 'Type != X' help branches
                     if not any(a.field == "type" and a.op == "!=" for a in branch)]  # (keep every other branch)
        if kind == "Monster" and not needs and not extra:   # a Main Deck monster with no needs so far
            dn = discard_need(r, facts)                  # 'If this card is discarded: ...' needs a way to discard it
            if dn:                                          # it has one:
                needs = parse_expr(dn)                      # that is its need
        if kind == "Monster" and (text_is_flavor(kind, facts) or flavor_only(r, extra)):  # a monster with no real effect
            needs, helps = [], []                        # no real effect: any 'supports' were read from flavor text (Summoned Skull)
        try:    rating = float(r.get("card rating", "") or 0)  # the rating as a number...
        except ValueError: rating = 0.0                     # ...or 0 if the cell isn't a number
        # a 0-ATK vanilla archetype member (Ojama Green) is a joke on its own and only makes sense with its archetype
        # a vanilla monster with no effect...
        archetype_vanilla = (kind == "Monster" and (text_is_flavor(kind, facts) or flavor_only(r, extra)) and not extra
                             and bool(facts["archetype"] - {norm(r["name"])})  # ...that belongs to an archetype other than its own name...
                             and facts.get("atk") is not None and 0 <= facts["atk"] <= LOW_ATK_VANILLA)  # ...and has very low ATK
        out.append(Info(                                    # build the Info
            row=r, name=r["name"], facts=facts, needs=needs, helps=helps,  # (row, name, facts, needs, helps)
            offered=offered_mechanics(r),                   # the mechanics it performs
            generic=(not needs and not helps and not archetype_vanilla),  # generic = no needs and no helps (and not a joke vanilla)
            kind=kind, level=facts["level"] or 0,           # Monster / Spell / Trap, and its Level (0 if none)
            main_deck=(kind in ("Spell", "Trap")) or (kind == "Monster" and not extra),  # Spells, Traps and non-Extra monsters go in the Main Deck
            era=era_index(r.get("era", "")), rating=rating,  # era as a number, and rating
            clean=(not archetype_vanilla) and is_clean(r, facts, extra, not needs,  # 'clean' (works in any deck)?
                           any(not is_mechanic(a) for br in helps for a in br), ignore_text=text_is_flavor(kind, facts)),     # supports ANY card trait (name, archetype, Type, ...) = not generic
        ))
    return out                                              # every card's Info


# --------------------------------------------------------------------------
# The deck as it is being built
# --------------------------------------------------------------------------
@dataclass                                                  # makes a simple data container
class Entry:                                                # class: one card copy in the deck
    info: Info                                              # the card
    origin: str                                          # theme1 / theme2 / support1 / support2 / partner / generic
    reason: str = ""                                     # why it was picked (shown in the report)


@dataclass                                                  # makes a simple data container
class State:                                                # class: the deck while it is being built
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
    theme_atoms: set = field(default_factory=set)        # (field, value) of each non-mechanic theme label, e.g. ('type', 'zombie')
    theme_cards: dict = field(default_factory=dict)      # name -> Info for every card in the theme buckets (the theme's future targets)
    theme_support: dict = field(default_factory=dict)    # name -> True/False: does this card support the theme? (worked out once per deck)

    latest = None                                        # (class-wide, not a field) the deck being built right now, so the
                                                         # Normal Monsters toggle can ask "does this deck call for Normals?"

    def __post_init__(self):                                # runs right after a State is made
        State.latest = self                              # every new or rebuilt deck becomes 'the deck being built'

    def add(self, info: Info, copies: int, origin: str, reason: str) -> None:  # method: put copies of a card in the deck
        """Put `copies` copies of a card in the deck and update every running total."""
        for _ in range(copies):                             # one Entry per copy
            self.entries.append(Entry(info, origin, reason))  # (with why it was picked)
        self.counts[info.name] = self.counts.get(info.name, 0) + copies  # count the copies
        self.unique[info.name] = info                       # the card itself, once
        self.offered |= info.offered                     # new mechanics become available
        self.traits |= info.facts["trait"]               # new traits become available
        if origin.startswith("theme"):                   # theme cards define which archetypes "belong" in this deck
            self.arche |= info.facts["archetype"]           # add its archetypes
        if self.quota is not None and info.kind in self.quota:   # use up some of the type target
            self.quota[info.kind] = max(0, self.quota[info.kind] - copies)  # one fewer of that kind still wanted


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def t2_unused(req) -> bool:                                 # function: does Theme 2 get no slots?
    """True when the second theme gets no slots."""
    return req.deck_type_2_pct <= 0 or req.deck_type_1_pct >= 100 or req.deck_type_overall_pct <= 0  # its share is 0, Theme 1 takes everything, or there are no theme slots


def junk_name(text: str) -> bool:                           # function: is a required-card name obviously not a card name?
    """True if a 'Name = ...' value in the required-cards column is obviously not a card name:
       a joining word ('or', 'and'), a sentence fragment cut out of card text, or garbled characters."""
    t = text.strip()                                        # without spaces around it
    return (t.lower() in ("or", "and")                    # 'Name = or' came from 'X or Y'
            or len(t) > 60 or ";" in t                    # a whole clause, not a name (real names reach ~50)
            or t[:1].islower() or t[:2] == "s,"           # starts mid-sentence ('s, Plant monsters ...', 'monster that ...')
            or any(ord(ch) > 127 and ch in "ÃÂÎ±" for ch in t))   # mojibake


def clean_required(row: dict) -> dict:                      # function: blank a required-cards column that is all junk
    """Return the row with its 'required cards' column blanked when EVERY name in it is junk (see junk_name).
       Mixed values are left alone: the later stage already ignores the junk atoms."""
    col = row.get("required cards") or ""                   # the column's text
    names = re.findall(r"Name\s*=\s*([^&|]+)", col)         # every 'Name = ...' in it
    if names and all(junk_name(n) for n in names):          # there are some, and all of them are junk
        row = dict(row)                                    # never edit the caller's row in place
        row["required cards"] = ""                          # blank the column
    return row                                              # hand back the row


class Builder:                                              # class: the deck engine
    """Holds the filtered card pool plus caches, and builds decks from a Request."""

    def __init__(self, rows: list[dict], tree: dict, labels: list[str] | None = None):  # runs once when the cards are loaded
        self.tree = tree                                 # mechanic tree (for need_status / covers)
        self.labels = labels or []                          # every label (for the Random choices)
        rows = [clean_required(r) for r in rows]         # drop junk 'required cards' values ('or', cut-off sentences)
        self.infos = build_infos(rows)                   # all cards, pre-digested
        self._partner_cache: dict = {}                   # trait-branch -> list of Infos that satisfy it
        self.by_name = {norm(i.name): i for i in self.infos}                     # normalised name -> card
        for i in self.infos:                                                     # each card's alias names, kept only when that
            same = set()                                                         # card lives in the same deck (Main / Extra) -
            for n in (i.row.get("alias") or "").split("||"):                     # so an alias can never move an Extra Deck card
                other = self.by_name.get(norm(n)) if n.strip() else None         # into the Main Deck, or the other way round
                if other is not None and other.name != i.name and other.main_deck == i.main_deck:  # another card in the same deck section
                    same.add(other.name)                    # keep it as an alias
            i.aliases = tuple(sorted(same))                 # sorted, so the order is always the same
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
        self._default_cap = MAX_COPIES                      # 3 copies unless a list lowers it

    # ---- format: banlist, release-date pool, Highlander ------------------
    def set_format(self, req: Request) -> None:             # method: set the copy limits for a format
        """Work out how many copies of each card this deck may have (done once per format, then remembered)."""
        key = (req.banlist, req.date_pool)                  # the format: (banlist, date pool)
        if key == self._format:                             # same as last time?
            return                                       # same format as last time: nothing to redo
        self._format = key                                  # remember it
        self.staples = self.all_staples.get(req.banlist, {}) if req.banlist else {}   # how often each card was played
        chosen = self.banlists.get(req.banlist) if req.banlist else None  # the chosen list (or None)
        self.point_cap = chosen.points if chosen is not None else None                # Genesys: the deck's point budget
        self._default_cap = MAX_COPIES                                   # (a list can lower it: Highlander -> 1)
        banlist = self.banlists.get(req.banlist) if req.banlist else None      # the chosen list (None = Unlimited)
        if banlist is not None and not getattr(banlist, "names_fixed", False):       # first use of this list: old names -> current names
            from ygo_names import NameResolver                           # ('Red-Eyes B. Dragon' -> 'Red-Eyes Black Dragon')
            resolver = NameResolver([i.name for i in self.infos])  # a name finder for every card
            banlist.by_name = {(resolver.find(n) or n).lower(): c for n, c in banlist.by_name.items()}  # rewrite the list's names to the current card names
            banlist.names_fixed = True                      # don't do it again
        self._caps = {}                                     # card name -> copies allowed
        for i in self.infos:                                # each card
            n = copies_allowed(banlist, i.row, self._default_cap) if banlist else self._default_cap   # Forbidden 0 / Limited 1 / ...
            if banlist and req.date_pool and not released_by(banlist, i.row):       # not out yet on the list's date
                n = 0                                       # (none allowed)
            self._caps[i.name] = n                          # remember it

    def card_points(self, name: str) -> int:                # method: one card's Genesys points
        """Genesys points of one copy (0 for most cards, and always 0 outside Genesys)."""
        return self.all_points.get(name, 0) if self.point_cap is not None else 0  # its points (Genesys only)

    def deck_points(self, entries: list) -> int:            # method: a list of copies' Genesys points
        """Genesys points of a list of Entry (one per copy)."""
        return sum(self.card_points(e.info.name) for e in entries) if self.point_cap is not None else 0  # add up every copy (Genesys only)

    def cap(self, name: str) -> int:                        # method: copies of a card allowed in this format
        """Copies of this card a deck may have in the current format (3 normally; 0 = not allowed)."""
        return self._caps.get(name, self._default_cap)      # its limit (3 if the card isn't listed)

    # ---- filters ---------------------------------------------------------
    def rating_ok(self, c: Info, req: Optional[Request] = None) -> bool:  # method: is a card inside the rating limits?
        """Is this card inside the rating limits? (Generic filler must be; theme / support picks PREFER it - see rating_tier.)"""
        lo, hi = (req.min_card_rating, req.max_card_rating) if req is not None else self.rating_range  # the limits from the request, or the ones saved by allowed()
        return (lo is None or c.rating >= lo) and (hi is None or c.rating <= hi)  # above the minimum and below the maximum (a missing limit always passes)

    rating_range = (None, None)                          # set by allowed() / build_main so helpers without `req` can use rating_ok

    def rating_tier(self, cards: list) -> list:             # method: narrow a list to the cards nearest the rating limits
        """Theme / support picks: the cards to choose from, nearest the rating limits first (ratings are whole numbers 0-5).
             1. every card INSIDE the min-max range, while any is left
             2. else the cards BELOW the range, closest first (a 4-5 deck takes 3s, then 2s ...)
             3. else - only when nothing inside or below is left - the cards ABOVE the range, closest first
           So a small theme (Sphinx) is still built, but runs on its out-of-range cards only when it must."""
        lo, hi = self.rating_range                          # the current limits
        if lo is None and hi is None:                       # no limits at all:
            return cards                                                      # no limits: every card is fine
        inside = [c for c in cards if self.rating_ok(c)]    # cards inside the limits
        if inside:                                          # any?
            return inside                                                     # 1. inside the range
        below = [c for c in cards if lo is not None and c.rating < lo]  # cards below the minimum
        if below:                                           # any?
            best = max(c.rating for c in below)                               # 2. the closest rating under the range
            return [c for c in below if c.rating == best]   # just the ones with that rating
        above = [c for c in cards if hi is not None and c.rating > hi]  # cards above the maximum
        if above:                                           # any?
            best = min(c.rating for c in above)                               # 3. the closest rating over the range
            return [c for c in above if c.rating == best]   # just the ones with that rating
        return cards                                        # (should not happen)

    def theme_archetypes(self, req: Request) -> set:        # method: archetypes the theme labels name
        """Archetypes named by the deck's theme labels ('Archetype = Sphinx' -> {'sphinx'})."""
        out = set()                                         # the set we build
        for label in (req.deck_type_1_label, req.deck_type_2_label):  # each theme label
            if label and "=" in label:                      # a real label ('X = Y')
                at = parse_atom(label)                      # split it into field / operator / value
                if at.field == "archetype" and not is_mechanic(at):  # an archetype label (not a mechanic)
                    out.add(norm(at.value))                 # add the archetype
        return out                                          # hand back the set

    def allowed(self, req: Request, extra: bool = False, rated: bool = True) -> list[Info]:  # method: the card pool for a deck
        """Cards that pass the era and rating filters: main-deck cards, or (extra=True) Extra Deck monsters.
           rated=False skips the rating limits (theme and synergy cards matter more than rating)."""
        self.set_format(req)                                       # banlist / release dates / Highlander
        self.set_levels(req)                                       # Level sliders + Normal Monsters toggle
        self.rating_range = (req.min_card_rating, req.max_card_rating)   # for rating_tier (the Extra Deck asks before build_main runs)
        max_idx = era_index(req.max_era) if req.max_era else None  # the newest era allowed, as a number
        keep = []                                           # the cards that pass
        for i in self.infos:                                # every card
            if i.main_deck == extra: continue                      # keep only the half we are asked for
            if self.cap(i.name) <= 0: continue                     # Forbidden, or not released by the list's date
            if max_idx is not None and i.era is not None and i.era > max_idx: continue  # too new for the era limit
            if rated and not self.rating_ok(i, req): continue  # outside the rating limits (only when rated)
            keep.append(i)                                  # it passes
        return keep                                         # hand back the pool

    # ---- partners --------------------------------------------------------
    def partners_for(self, traits: tuple, pool: list[Info]) -> list[Info]:  # method: pool cards that meet a need's trait part
        """All pool cards that satisfy a branch's TRAIT atoms (cached per pool+branch)."""
        key = (traits, id(pool))                            # the cache key: the branch and this pool
        if key not in self._partner_cache:                  # not worked out yet:
            self._partner_cache[key] = [p for p in pool if card_matches([traits], p.facts)]  # every pool card that matches the branch
        return self._partner_cache[key]                     # hand back the cached list

    # ---- needs -----------------------------------------------------------
    def eval_needs(self, info: Info, deck: State, pool: list[Info], scan_partners: bool = True):  # method: how well a card's needs are met (and whether it needs a partner)
        """Return (factor, partner_traits).
           factor        : multiplier for the pick weight (1.0 for cards with no needs).
           partner_traits: trait atoms of the best branch if a partner must be added, else None."""
        if not info.needs:                                  # no needs:
            return 1.0, None                                # full weight, no partner
        best_score, best_partner = 0.0, None                # the best branch found so far
        for branch in info.needs:                           # each OR-option
            traits = tuple(a for a in branch if not is_mechanic(a))   # "is it a WATER monster?" part  (HARD)
            mechs = [a for a in branch if is_mechanic(a)]             # "Self = Banish (GY)" part      (SOFT)
            partner = None                                  # no partner needed (yet)
            if not traits:                                  # no trait part:
                t = 1.0                                               # nothing about other cards required
            elif any(o.name != info.name and card_matches([traits], o.facts) for o in deck.unique.values()):  # another deck card matches the trait part
                t = 1.0                                               # a deck card already satisfies it
            elif (not scan_partners) or self.partners_for(traits, pool):   # (skip the slow pool scan when asked)
                t, partner = PARTNER_SCORE, traits                    # we can add a partner later
            else:
                t = 0.0                                               # impossible with this pool
            unmet = sum(1 for m in mechs if need_status(m, deck.offered, deck.traits, self.tree) == "unmet")  # how many mechanic needs nothing in the deck offers
            score = t / (1 + 0.5 * unmet)                             # each unmet soft need lowers the score
            if score > best_score:                          # the best branch so far?
                best_score, best_partner = score, partner   # remember it
        return NEED_BONUS * best_score, best_partner        # the best branch's score (doubled), and its partner if one is needed

    def supports_theme(self, info: Info, deck: State) -> bool:  # method: does this card support the deck's theme?
        """True if this card supports one of the deck's THEME labels: a branch of its Supports names the theme
           (Pyramid Turtle: 'Type = Zombie && DEF <= 2000' in a 'Type = Zombie' deck) AND enough of the theme's
           own cards fit the whole branch, stat limits included (MIN_TARGETS of them). Worked out once per card per deck."""
        if info.name in deck.theme_support:                                           # already worked out for this deck
            return deck.theme_support[info.name]            # (the saved answer)
        result = False                                      # no until a branch says yes
        for branch in info.helps:                           # each thing it helps
            atoms = [a for a in branch if not is_mechanic(a)]                         # the trait part only
            if not any((a.field, norm_trait(a.value)) in deck.theme_atoms for a in atoms):  # none of the trait pieces is a theme label
                continue                                                              # this branch doesn't name the theme
            targets = sum(1 for o in deck.theme_cards.values()                        # theme cards it could search / summon
                          if o.name != info.name and card_matches([tuple(atoms)], o.facts))
            if targets >= MIN_TARGETS:                      # enough of them:
                result = True                               # it supports the theme
                break
        deck.theme_support[info.name] = result              # save the answer for this deck
        return result                                       # hand it back

    @staticmethod                                           # doesn't need the Builder itself
    def wants_normals(deck: State) -> bool:                 # method: does any deck card call for Normal Monsters?
        """True if a card already in the deck calls for Normal Monsters (its Supports columns mention them:
           Rescue Rabbit, Unexpected Dai, Heavy Polymerization ...)."""
        cols = ("strictSupports", "generalSupports", "strictSupportedBy", "generalSupportedBy")  # the four support columns
        return any("trait = normal monster" in (c.row.get(k) or "").lower()  # any deck card with 'Trait = Normal Monster' in any of them
                   for c in deck.unique.values() for k in cols)  # (looking at every deck card)

    def named_in_deck(self, info: Info, deck: State) -> bool:  # method: does a deck card name this card in quotes?
        """True if a card in the deck names this card in "quotes" in its text (King of the Skull Servants -> "Skull Servant")."""
        quoted = f'"{info.name}"'.lower()                                             # how a card text names another card
        return any(quoted in (c.row.get("card text") or "").lower()  # any other deck card whose text contains it
                   for c in deck.unique.values() if c.name != info.name)  # (looking at every other deck card)

    def help_factor(self, info: Info, deck: State) -> float:  # method: weight bonus for supporting cards already in the deck
        """Bigger when the cards this card supports are already in the deck."""
        branches = [tuple(a for a in branch if not is_mechanic(a)) for branch in info.helps]  # the trait part of each help branch
        branches = [branch for branch in branches if branch]                         # ignore branches with no trait atoms
        if not branches:                                    # nothing specific:
            return 1.0                                      # no bonus, no penalty
        # different deck cards it helps
        hit = [o for o in deck.unique.values() if o.name != info.name and any(card_matches([branch], o.facts) for branch in branches)]
        n = len(hit)                                                  # different cards it supports
        copies = sum(deck.counts[o.name] for o in hit)                  # ...and how many copies of them
        if copies == 0:                                     # none:
            return NO_TARGET_FACTOR                                   # nothing to search / support
        if copies < MIN_TARGETS:                            # fewer than MIN_TARGETS:
            return FEW_TARGET_FACTOR                                  # too few targets to be worth playing
        return 1 + HELP_STEP * min(n, HELP_MAX_CARDS)       # +25% per card it helps (up to 4)

    def coverage(self, info: Info, deck: State) -> float:   # method: what share of the deck this card helps
        """Share of the deck's cards (by copies) that this card's strictSupports point at. 0.5 = helps half the deck."""
        total = len(deck.entries)                           # cards in the deck
        branches = [tuple(a for a in branch if not is_mechanic(a)) for branch in info.helps]  # the trait part of each help branch
        # keep only branches that name an identity (archetype / Type / Attribute / trait). A branch of bare ATK / DEF / Level / Card Type
        # is usually half of a conjunction the filler split (The Melody of Awakening Dragon: 'Dragon ATK>=3000' AND 'DEF<=2500').
        # keep only branches that name an archetype / Type / Attribute / trait
        branches = [branch for branch in branches if any(a.field in ("archetype", "type", "attribute", "trait") for a in branch)]
        if not branches or total == 0:                      # nothing to measure:
            return 0.0                                      # (helps nobody)
        helped = sum(deck.counts[o.name] for o in deck.unique.values() if o.name != info.name  # copies of other deck cards it helps...
                     and any(card_matches([branch], o.facts) for branch in branches))  # ...through any of its branches
        return helped / total                               # as a share of the deck

    def rarity(self, info: Info, deck: State) -> float:     # method: how specific the trait a card helps is (0 = very common, 1 = rare)
        """0..1: how specific the trait is that this card supports. 'Effect Monster' (half of all cards) -> ~0; 'Rock' -> ~0.9.
           Uses the rarest branch that on its own covers 1/3+ of the deck."""
        total = len(deck.entries)                           # cards in the deck
        if total == 0:                                      # empty deck:
            return 0.0                                      # (no answer)
        best = 0.5                                          # 0.5 = 'half of all cards' (the most common it counts)
        for branch in info.helps:                           # each help branch
            t = tuple(a for a in branch if not is_mechanic(a))  # its trait part
            if not any(a.field in ("archetype", "type", "attribute", "trait") for a in t):  # not an identity trait
                continue
            # deck copies it helps through this branch
            helped = sum(deck.counts[o.name] for o in deck.unique.values() if o.name != info.name and card_matches([t], o.facts))
            if helped / total < BROAD_SHARE:                # helps less than a third of the deck
                continue
            key = repr(t)                                   # a key for the cache
            if key not in self._frac:                                                  # share of ALL main-deck cards this branch matches
                main = [i for i in self.infos if i.main_deck]  # every Main Deck card
                self._frac[key] = sum(1 for i in main if card_matches([t], i.facts)) / max(1, len(main))  # the share of them it matches
            best = min(best, self._frac[key])               # keep the rarest
        return 1.0 - min(best, 0.5) * 2.0                   # 0.5 share -> 0, 0 share -> 1

    def broad_support(self, deck: State, pool: list[Info]) -> dict:  # method: boosts for cards that help a third of the deck
        """{card name: boost} for cards that help at least BROAD_SHARE of the cards already in the deck."""
        out = {}                                            # card name -> boost
        for c in pool:                                      # each pool card
            # helps nothing, Extra Deck, at its limit, or junk here
            if not c.helps or not c.main_deck or deck.counts.get(c.name, 0) >= self.cap(c.name) or self.junk(c, deck):
                continue
            if BROAD_LIST_RX.search(c.row.get("card text", "") or ""):             # 'Chaotic Elements' style lists are mis-parsed -> do not trust them
                continue
            priority, share = self._support_matches(c, deck)  # how strongly it supports the deck and how much of it
            if priority:                                    # it does:
                out[c.name] = BROAD_BOOST * (1.0 + share) * (0.25 + self.rarity(c, deck)) * (1.0 + priority)   # the more it helps, and the RARER the trait, the better
        return out                                          # hand back the boosts

    def synergy(self, info: Info, deck: State) -> int:      # method: how linked a card is to the deck (0 = unrelated)
        """How strongly a card is LINKED to the cards already in the deck (0 = unrelated).
           +1 for every deck card its strictSupports point at (up to 4),
           +1 if one of its needs is a real trait (not just 'is a Spell') that a deck card satisfies."""
        n = 0                                               # the link count
        for o in deck.unique.values():                                  # who does it support?
            if o.name == info.name: continue                # not itself
            for branch in info.helps:                       # each help branch
                t = tuple(a for a in branch if not is_mechanic(a))  # its trait part
                if t and card_matches([t], o.facts):        # it helps this deck card:
                    n += 1; break                           # one link (one per deck card)
        n = min(n, HELP_MAX_CARDS)                          # at most 4
        for branch in info.needs:                                          # who satisfies its needs?
            t = tuple(a for a in branch if not is_mechanic(a))  # its trait part
            plain = all(a.field == "card type" and a.value in ("monster", "spell", "trap") for a in t)  # just 'needs a Monster / Spell / Trap' (too plain to count)
            # a real trait another deck card meets
            if t and not plain and any(o.name != info.name and card_matches([t], o.facts) for o in deck.unique.values()):
                n += 1; break                               # one more link
        return n                                            # hand back the count

    @staticmethod                                           # doesn't need the Builder itself
    def tags(info: Info) -> set:                            # method: everything a card does or needs, as lower-case labels
        """Everything a card DOES or NEEDS, as lower-case labels ('self = special summon (gy)'), plus 'extra deck monster' and 'trait = normal'.
           Worked out once per card and kept on the card."""
        if not hasattr(info, "_tags"):                      # not worked out yet:
            t = set(info.offered)                                                     # what it does (both Supports columns)
            t |= {mechanic_label(a) for branch in info.needs for a in branch if is_mechanic(a)}   # what it needs
            if info.kind == "Monster" and not info.main_deck: t.add("extra deck monster")  # an Extra Deck monster
            if "normal" in info.facts["trait"]: t.add("trait = normal")  # a Normal Monster
            info._tags = t                                  # keep it on the card
        return info._tags                                   # hand back the labels

    @staticmethod                                           # doesn't need the Builder itself
    def hits(tags: set, starts: list) -> bool:              # method: does any label start with any of these?
        """True if any tag begins with any of the label starts ('self = special summon' covers 'self = special summon (gy)')."""
        return any(t.startswith(s) for t in tags for s in starts)  # (prefix match)

    @staticmethod                                           # doesn't need the Builder itself
    def one_sided(info: Info) -> bool:                      # method: does this floodgate only stop the opponent?
        """True for a floodgate that only stops the OPPONENT ("Your opponent cannot Special Summon", "the other player cannot ..."):
           it never hurts your own deck, so it never clashes."""
        text = info.row.get("card text", "") or ""          # the card's text
        return bool(ONE_SIDE.search(text)) and not BOTH_SIDES.search(text)   # it names the opponent, and nothing says it stops both players

    def floodgate_factor(self, info: Info, deck: State) -> float:  # method: weight change for floodgates
        """Weight multiplier from FLOODGATE_RULES (1.0 = no effect):
             - a floodgate candidate whose clashes cover CLASH_SHARE of the deck -> CLASH_FACTOR; one the deck suits -> SUIT_BOOST,
             - a candidate that clashes with a floodgate ALREADY in the deck -> CLASH_FACTOR.
           A floodgate whose own exceptions fit the deck is never punished (Barrier Statue of the Inferno in a FIRE deck:
           its strictSupports - FIRE monsters - cover at least BROAD_SHARE of the deck)."""
        total = len(deck.entries)                                                     # copies in the deck so far
        if total == 0:
            return 1.0                                                                # nothing to clash with yet
        f = 1.0                                             # start with no change
        for gate in (g for g in self.tags(info) if g in FLOODGATE_RULES and not self.one_sided(info)):   # the candidate is a floodgate that stops you too
            rule = FLOODGATE_RULES[gate]                    # what it suits / clashes with
            # copies of deck cards it would stop
            clashing = sum(deck.counts[o.name] for o in deck.unique.values() if o.name != info.name and self.hits(self.tags(o), rule["clashes"]))
            # copies of deck cards it suits
            suited = sum(deck.counts[o.name] for o in deck.unique.values() if o.name != info.name and self.hits(self.tags(o), rule["suits"]))
            if clashing / total >= CLASH_SHARE and self.coverage(info, deck) < BROAD_SHARE:  # stops a third of the deck, and its exceptions don't save it
                f *= CLASH_FACTOR                                                     # it would stop what this deck does
            elif suited >= MIN_TARGETS:                     # the deck has enough cards it suits
                f *= SUIT_BOOST                                                       # the deck plays around it
        mine = self.tags(info)                              # what this candidate does / needs
        for o in deck.unique.values():                                                # floodgates already in the deck
            if o.name == info.name: continue                # not itself
            for gate in (g for g in self.tags(o) if g in FLOODGATE_RULES and not self.one_sided(o)):   # (one that only stops the opponent is fine)
                spared = [t for t in (tuple(a for a in br if not is_mechanic(a)) for br in o.helps) if t]   # the floodgate's exceptions ("except FIRE monsters")
                if self.hits(mine, FLOODGATE_RULES[gate]["clashes"]) and not (spared and card_matches(spared, info.facts)):   # (a card its exception spares is fine)
                    f *= CLASH_FACTOR                                                 # this card does what the deck's floodgate stops
                    return f                                                          # (once is enough)
        return f                                            # hand back the multiplier

    @staticmethod                                           # doesn't need the Builder itself
    def archetype_fit(info: Info, deck: State) -> float:    # method: penalty for a card of another archetype
        """Penalise cards of a DIFFERENT archetype than the theme (e.g. a Kozmo card in a Snake-Eye deck).
        Cards with no archetype (generic staples) are never penalised."""
        a = info.facts["archetype"]                         # the card's archetypes
        if not a or not deck.arche or (a & deck.arche):     # generic card, the deck has no archetype yet, or they share one
            return 1.0                                      # no penalty
        return OFF_ARCHETYPE                                # penalty

    def set_levels(self, req: Request) -> None:             # method: read the Level sliders and the Normal Monsters toggle
        """Read the Level sliders and the Normal Monsters toggle from the Request (done on every allowed() call; cheap).
             self.level_shares : None = sliders off (the fixed 4 / 2 / 1 weights), else the three shares (0..1, adding up to 1)
             self.level_target : how many Main Deck monsters the shares are worked out from (the Monster target, or 20)
             self.allow_normals: False = no Normal Monsters, except cards of the deck's theme (self.theme_names)"""
        if req.level_sliders:                               # sliders on:
            raw = [max(0, req.level_low_pct), max(0, req.level_mid_pct), max(0, req.level_high_pct)]  # the three shares (no negatives)
            total = sum(raw) or 1                                                   # (all three at 0: treat as equal shares)
            self.level_shares = tuple(x / total for x in raw) if sum(raw) else (1 / 3, 1 / 3, 1 / 3)  # as shares that add up to 1
        else:
            self.level_shares = None                        # sliders off
        self.level_target = req.main_monster_target or 20   # the Monster target, or 20 if blank
        self.allow_normals = req.allow_normals              # the toggle
        if self.band_freq is None:                                                  # worked out once: how common each band is
            mons = [i for i in self.infos if i.kind == "Monster" and i.main_deck]  # every Main Deck monster
            # the share of them in each Level band
            self.band_freq = tuple(max(0.01, sum(1 for i in mons if self.level_band(i) == b) / max(1, len(mons))) for b in range(3))

    level_shares = None                                  # (class defaults, so a Builder works before set_levels is called)
    level_target = 20                                       # (class default)
    allow_normals = True                                    # (class default)
    band_freq = None                                     # share of ALL Main Deck monsters in each band (about 69% / 14% / 17%)
    theme_names: set = set()                             # names of the theme's cards (set per deck by ygo_extra.generate_state)
    anchor_names: set = set()                            # "build around a single card": that card's name (+ its alias names);
                                                         # ygo_final never swaps these out. Empty = mode off (set per deck)

    @staticmethod                                           # doesn't need the Builder itself
    def level_band(info: Info) -> int:                      # method: which Level band a monster is in
        """0 = Level 1-4 (and Level 0 / no Level), 1 = Level 5-6, 2 = Level 7 or higher."""
        return 0 if info.level <= 4 else (1 if info.level <= 6 else 2)  # 1-4 -> 0, 5-6 -> 1, 7+ -> 2

    def level_bias(self, info: Info, deck: Optional[State] = None) -> float:  # method: weight from a monster's Level
        """How much likelier a monster is because of its Level (Spells / Traps: always 1.0).
           Sliders OFF: low-Level monsters are more playable, so Level <= 4: 4, 5-6: 2, 7+: 1 (as before).
           Sliders ON : a band's weight follows its slider share, and - when we know the deck - a band that already has its
                        share of monsters becomes much rarer while a band that is still short becomes likelier."""
        if info.kind != "Monster":                          # Spells and Traps:
            return 1.0                                      # no effect
        band = self.level_band(info)                        # its band
        if self.level_shares is None:                                               # sliders off: the old fixed weights
            return (4.0, 2.0, 1.0)[band]                    # Level 1-4: 4, 5-6: 2, 7+: 1
        share = self.level_shares[band]                     # that band's slider share
        # divide by how common the band is: most monsters are Level 1-4, so without this they would turn up far more
        # often than their slider says (a 40 / 40 / 20 setting came out as 59 / 22 / 18)
        w = max(0.05, share * 10) / (3 * self.band_freq[band])                     # 0% -> tiny (soft: rare, never banned)
        if deck is not None:                                # we know the deck:
            have = sum(deck.counts[o.name] for o in deck.unique.values()  # monster copies already in this band
                       if o.kind == "Monster" and o.main_deck and self.level_band(o) == band)   # copies already in this band
            w *= 2.0 if have < share * self.level_target else 0.1                   # still short -> likelier; full -> rarer
        return w                                            # hand back the weight

    def normal_ok(self, info: Info, deck: Optional[State] = None) -> bool:  # method: the Normal Monsters toggle check
        """The Normal Monsters toggle.
             On  : every Normal Monster is allowed (unchanged).
             Off : a Normal Monster is only allowed if it is one of the theme's own cards (Blue-Eyes White Dragon in Blue-Eyes),
                   or the deck asks for it: a deck card supports Normal Monsters (Rescue Rabbit, Transcendosaurus Meteorus)
                   or names it (King of the Skull Servants -> "Skull Servant").
           `deck` = the deck to ask; when it is left out, the deck being built right now (State.latest) is used.
           Other format, rating and deck-building checks are still applied separately."""
        if self.allow_normals or "normal" not in info.facts["trait"] or info.name in self.theme_names:
            return True                                                               # toggle on / not a Normal / the theme's own
        deck = deck or State.latest                         # the deck to ask
        if deck is None:                                                              # no deck yet: nothing can ask for it
            return False                                    # (not allowed)
        return self.wants_normals(deck) or self.named_in_deck(info, deck)  # a deck card asks for Normals, or names this card

    def starters_needed(self, monsters: int) -> int:        # method: how many low-Level 'starter' monsters a deck needs
        """How many Level 4-or-lower monsters the 'starter' rule asks for in a deck with this many monsters:
           8, or 40% of them if fewer - and with the sliders ON, never more than the Level 1-4 slider allows."""
        need = min(8, -(-monsters * 2 // 5))                                       # (8 = ygo_final.STARTERS)
        if self.level_shares is not None:                   # sliders on:
            need = min(need, round(self.level_shares[0] * monsters))  # never more than the Level 1-4 slider allows
        return need                                         # hand back the number

    # ---- picking ---------------------------------------------------------
    # method: weighted random choice of one card
    def pick(self, bucket: list[Info], deck: State, pool: list[Info], rng: random.Random, use_fit: bool = True, need_synergy: bool = False,
             boost: Optional[dict] = None, staples: bool = False, skip_normals: bool = False):  # (its options, continued)
        """Choose one card from `bucket` by weight. Returns (info, partner_traits) or (None, None).
           staples=True (filler only): cards the format's decks really played are likelier (format_staples.json).
           skip_normals=True (Type / Attribute / mechanic themes): no Normal Monsters unless the deck has a card that calls for them."""
        legal = [c for c in bucket if deck.counts.get(c.name, 0) < self.cap(c.name)     # not already at its copy limit
                 and self.normal_ok(c)]                                               # Normal Monsters toggle (theme cards always ok)
        if self.point_cap is not None:                                                # Genesys: only cards that still fit
            left = max(0, self.point_cap - self.deck_points(deck.entries))             # (never below 0: 0-point cards always fit;
            legal = [c for c in legal if self.card_points(c.name) <= left]           #  enforce_points fixes any overshoot)
        if deck.quota is not None:                                                      # respect type targets if possible
            q = [c for c in legal if c.kind not in deck.quota or deck.quota[c.kind] > 0]  # cards of a kind that is still wanted
            legal = q or legal                              # those if any, else all of them
        if skip_normals and not self.wants_normals(deck):                              # a Type / Attribute theme: no vanilla Normals...
            no_vanilla = [c for c in legal if not (c.kind == "Monster" and "normal" in c.facts["trait"])  # drop vanilla Normal Monsters...
                          or self.named_in_deck(c, deck)]                              # (one a deck card names is still fine)
            legal = no_vanilla or legal                                               # ...unless they are all that is left
        if not legal:                                       # nothing can go in:
            return None, None                               # (no pick)
        if deck.generic_deck:                                                           # Generic deck: limit specific + vanilla cards
            specific = sum(1 for e in deck.entries if not e.info.clean)  # non-clean copies already in
            normals = sum(1 for e in deck.entries if "normal" in e.info.facts["trait"])  # Normal Monster copies already in
            if specific >= SPECIFIC_CAP:                    # too many specific cards:
                legal = [c for c in legal if c.clean or (boost and c.name in boost)] or legal   # cap reached: clean (or broad-support) cards only
            if normals >= NORMAL_CAP:                       # too many Normals:
                legal = [c for c in legal if "normal" not in c.facts["trait"]] or legal  # no more Normals (if anything else is left)
        if use_fit and deck.arche:                                                      # prefer on-theme cards outright
            on_theme = [c for c in legal if self.archetype_fit(c, deck) == 1.0]  # cards that fit the deck's archetypes
            if len(on_theme) >= MIN_ON_THEME:                                         # only if enough of them exist
                legal = on_theme                            # use only those
        if not use_fit and not deck.generic_deck:                                       # THEME picks: drop stray sub-archetype cards outright
            tidy = [c for c in legal if not self.theme_junk(c, deck)]                   # (Destiny HERO's D-Fusion in a 'Fusion' deck with no Destiny HERO)
            if len(tidy) >= MIN_ON_THEME:                                             # ...but only if enough clean cards remain
                legal = tidy                                # use only those
        if need_synergy:                                                              # support picks must link to the deck
            probe = rng.sample(legal, min(SYNERGY_SAMPLE, len(legal)))  # a random sample to test
            linked = [c for c in probe if self.synergy(c, deck) > 0]  # the ones linked to the deck
            if len(linked) >= MIN_SYNERGY:                                            # enough linked cards: use only those
                legal = linked                              # use only those
        legal = self.rating_tier(legal)                                               # nearest the rating limits first (see rating_tier)
        sample = rng.sample(legal, min(SAMPLE_SIZE, len(legal)))                      # weigh a random subset (speed)
        if staples and self.staples:                                                  # ...but always weigh the format's staples
            seen = {c.name for c in sample}                 # names already in the sample
            sample += [c for c in legal if c.name in self.staples and c.name not in seen]  # add the staples the sample missed
        scored = []                                         # (weight, card, partner) for each candidate
        for c in sample:                                    # each candidate
            f, partner = self.eval_needs(c, deck, pool)     # how well its needs are met
            fit = self.archetype_fit(c, deck) if use_fit else 1.0                       # off-archetype cards are less likely
            lb = self.level_bias(c, deck)                                               # Level weight (sliders or 4 / 2 / 1)
            if not use_fit and self.level_shares is not None:
                lb = lb ** 0.5                                                        # THEME picks follow the sliders softly
            hf = self.help_factor(c, deck)                                              # how much it supports what is ALREADY in the deck
            if not use_fit and self.supports_theme(c, deck):                            # THEME pick that supports the theme itself:
                hf = max(hf, 1.0) * THEME_SUPPORT_BOOST                               #  its targets are coming, so boost it (not 'nothing to search yet')
            w = lb * f * hf * fit                           # the weight: Level x needs x helps x archetype fit
            if use_fit and self.junk(c, deck):              # a support / filler pick that can't really work here
                w *= JUNK_FACTOR                                                      # support / filler picks must work in THIS deck
            elif not use_fit and not deck.generic_deck and self.theme_junk(c, deck):  # a theme pick that names a stray sub-archetype
                w *= THEME_JUNK_FACTOR                                                # theme picks: no stray sub-archetype cards
            if deck.generic_deck:                                                       # Generic deck: staples beat specific / vanilla cards
                # clean (or boosted) cards count fully; specific and Normal cards less
                w *= (1.0 if (c.clean or (boost and c.name in boost)) else SPECIFIC_FACTOR) * (NORMAL_FACTOR if "normal" in c.facts["trait"] else 1.0)
                if partner is not None:                     # it needs a partner:
                    w *= PARTNER_FACTOR                                               # needs a specific partner card: avoid in Generic decks
            w *= self.floodgate_factor(c, deck)                                         # floodgates: not in decks they stop, nor cards they stop in their decks
            if boost:                                       # boosts given:
                w *= boost.get(c.name, 1.0)                                           # broad-support filler is favoured
            if staples and c.name in self.staples:          # a format staple:
                w *= 1.0 + STAPLE_BOOST * self.staples[c.name][0]                     # played in most decks of this format
            scored.append((w, c, partner))                  # keep its weight
        total = sum(w for w, _, _ in scored)                # all weights added up
        if total <= 0:                                                                # everything was impossible: uniform
            _, c, partner = rng.choice(scored)              # pick any one
            return c, partner                               # hand it back
        w, c, partner = rng.choices(scored, weights=[s[0] for s in scored])[0]        # weighted draw
        return c, partner                                   # hand back the pick

    def fill(self, deck: State, bucket: list[Info], pool: list[Info], desired: int,  # method: add cards from a bucket until the slots are used
             # (its options, continued)
             origin: str, label: str, rng: random.Random, need_synergy: bool = False, boost: Optional[dict] = None,
             fit: Optional[bool] = None, skip_normals: bool = False) -> None:  # (its options, continued)
        """Add cards from `bucket` until `desired` slots are used (partners use slots too).
           boost = {card name: multiplier} passed on to pick() (used by the single-card build to favour close supporters).
           fit = use the archetype-fit / junk checks; None = decided by origin (support picks yes, theme picks no).
           skip_normals = no Normal Monsters unless the deck calls for them (passed on to pick())."""
        use_fit = (not origin.startswith("theme")) if fit is None else fit  # theme picks skip the fit checks unless told otherwise
        attempts = 0                                        # tries so far
        while desired > 0 and attempts < desired * 40:      # until the slots are used (or too many tries)
            attempts += 1                                   # count this try
            card, partner = self.pick(bucket, deck, pool, rng, use_fit=use_fit, need_synergy=need_synergy,  # pick a card
                                      boost=boost, skip_normals=skip_normals)  # (with the options)
            if card is None:                                # nothing left to pick
                break
            copies = rng.randint(1, min(self.cap(card.name) - deck.counts.get(card.name, 0), desired))  # 1 to the copies that still fit (never more than the slots left)
            why = f"{origin}: {label}"                      # why it was picked
            if need_synergy:                                # a support pick:
                why += f" (linked to {self.synergy(card, deck)} deck features)"  # say how linked it is
            deck.add(card, copies, origin, why)             # add the copies
            desired -= copies                               # fewer slots left
            if partner and desired > 0:                                               # add a card that satisfies its needs
                options = [p for p in self.partners_for(partner, pool)  # pool cards that meet the need...
                           # ...with copies left, not the card itself, Normal toggle ok
                           if deck.counts.get(p.name, 0) < self.cap(p.name) and p.name != card.name and self.normal_ok(p)]
                options = self.rating_tier(options)                                   # nearest the rating limits first
                if options:                                 # any?
                    # weight: Level x archetype fit x help
                    weights = [self.level_bias(p, deck) * self.archetype_fit(p, deck) * self.help_factor(p, deck)
                               * (PREFER_THEME if p.name in deck.prefer else 1.0) for p in options]   # stay on-theme
                    p = rng.choices(options, weights=weights)[0]  # pick one
                    n = rng.randint(1, min(2, self.cap(p.name) - deck.counts.get(p.name, 0), desired))  # 1 or 2 copies (never past its limit or the slots left)
                    deck.add(p, n, "partner", f"partner for {card.name}")  # add them, marked 'partner'
                    desired -= n                            # fewer slots left

    def related_tiers(self, arche: str, pool: list[Info], kind: str = "archetype") -> list[list[Info]]:  # method: related cards for a small archetype that ran out of cards
        """For a SMALL archetype (or series: kind='series') that has run out of its own cards, two tiers of related cards (best first):
             1. cards that work WITH it: their text names it in quotes, or their Supports column names it
             2. clean (stand-alone) monsters of its most common Type (Dinosaur for Transcendosaurus)
             3. clean monsters that only share its most common Attribute - used only once tier 2 runs out,
                so a FIRE Dinosaur archetype doesn't fill up with FIRE Warriors
           Its own cards are never included (they were already used)."""
        members = [c for c in pool if arche in c.facts[kind]]  # its own cards
        mine = {c.name for c in members}                    # their names
        if kind == "archetype":                                                        # archetype names are already found by refs()
            # cards whose text names this archetype
            named = [c for c in pool if c.name not in mine and any(k == "arche" and v == arche for k, v in self.refs(c))]
        else:                                                                         # a series: look for its name in quotes directly
            named = [c for c in pool if c.name not in mine and arche in  # cards whose text quotes the series name...
                     # ...(leaving out 'except "X"' exclusions)
                     {norm(q) for q in re.findall(r'"([^"]+)"', EXCEPT_RX.sub("", c.row.get("card text", "")))}]
        helpers = [c for c in pool if c.name not in mine and c.helps and any(  # cards whose Supports column names it...
            a.field == kind and norm(a.value) == arche for br in c.helps for a in br if not is_mechanic(a))]  # ...in a trait piece of any help branch
        tier1 = list({c.name: c for c in named + helpers}.values())  # tier 1, each card once
        monsters = [c for c in members if c.kind == "Monster"]  # its monsters
        types = Counter(t for c in monsters for t in c.facts["type"]).most_common(1)           # the archetype's usual Type...
        attrs = Counter(t for c in monsters for t in c.facts["attribute"]).most_common(1)      # ...and Attribute
        want_t = {types[0][0]} if types else set()          # the usual Type (if any)
        want_a = {attrs[0][0]} if attrs else set()          # the usual Attribute (if any)
        # clean monsters (not its own) inside the rating limits
        clean = [c for c in pool if c.name not in mine and c.kind == "Monster" and c.clean and self.rating_ok(c)]
        tier2 = [c for c in clean if c.facts["type"] & want_t]                          # same Type
        tier3 = [c for c in clean if not (c.facts["type"] & want_t) and c.facts["attribute"] & want_a]   # same Attribute only
        return [tier1, tier2, tier3]                        # the three tiers, best first

    # ---- buckets ---------------------------------------------------------
    def theme_bucket(self, label: str, pool: list[Info]) -> list[Info]:  # method: the cards a theme label means
        """Cards that ARE the label (trait label) or PERFORM it (mechanic label)."""
        atom = parse_atom(label)                            # the label split into field / operator / value
        if is_mechanic(atom):                               # a mechanic label ('Self = Special Summon'):
            want = self.tree.get(mechanic_label(atom), {mechanic_label(atom)})  # it plus every mechanic counted as it (the tree)
            return [c for c in pool if c.offered & want]    # cards that perform any of them
        return [c for c in pool if card_matches([(atom,)], c.facts)]  # a trait label: cards that have that trait

    def support_bucket(self, label: str, pool: list[Info], arche: frozenset = frozenset()) -> list[Info]:  # method: the cards a support label means
        """Cards whose Supports columns mention the label.
           A branch only counts if its OTHER archetype / name conditions fit the deck: D/D Gryphon supports
           '(Archetype = Dark Contract && Card Type = Trap)', which is NOT general Trap support, so it is only
           offered when the deck is actually built around Dark Contract (`arche` = the theme archetypes).
           Plain restating qualifiers ('Card Type = Monster') and stat limits (Level <= 4) are ignored."""
        atom = parse_atom(label)                            # the label split into field / operator / value
        if is_mechanic(atom):                               # a mechanic label:
            want = self.tree.get(mechanic_label(atom), {mechanic_label(atom)})  # it plus every mechanic counted as it
            return [c for c in pool if c.offered & want]    # cards that perform any of them
        target = (atom.field, norm_trait(atom.value))       # the trait we are looking for, normalised

        def branch_fits(branch) -> bool:                    # inner function: does this help branch count as supporting the label?
            names_label = False                             # not yet
            for a in branch:                                # each piece of the branch
                if is_mechanic(a):                          # mechanic pieces don't matter here
                    continue
                if (a.field, norm_trait(a.value)) == target:  # it names the label:
                    names_label = True                      # (yes)
                elif a.field in ("archetype", "name") and not (a.op == "=" and norm(a.value) in arche):  # an archetype / name piece the deck doesn't have
                    return False                                                      # supports only some OTHER archetype
            return names_label                              # counts only if it named the label
        strict = [c for c in pool if any(branch_fits(branch) for branch in c.helps)]  # cards with a fitting branch
        if len(strict) >= MIN_STRICT_BUCKET:                # enough of them:
            return strict                                   # use them
        # Too few cards support this label for THIS deck (most Spell / Trap support is written for ONE other archetype,
        # e.g. 'Ancient Gear Spells', which is useless in a Blackwing deck). Do NOT add those off-theme loose matches.
        # Instead read the label as "play more cards of this kind": add cards that ARE of that kind (a Spell for
        # 'Card Type = Spell') and that work here: generic ones, or ones from the deck's own archetypes.
        own = [c for c in pool if card_matches([[atom]], c.facts)  # cards that ARE of that kind...
               and not is_mechanic(atom) and (c.clean or (c.facts["archetype"] & arche))]  # ...and are clean or of the deck's archetypes
        seen = {c.name for c in strict}                     # names already in the strict list
        return strict + [c for c in own if c.name not in seen]  # strict ones first, then the rest

    def refs(self, c: Info) -> list:                        # method: what a card's text names in quotes
        """Cards and archetypes this card's text names in quotes (not itself, not 'except "X"' exclusions)."""
        if c.name not in self._refs:                        # not worked out yet:
            text = EXCEPT_RX.sub("", c.row.get("card text", ""))  # the text without exclusions
            out = []                                        # the list we build
            for q in re.findall(r'"([^"]+)"', text):        # every quoted piece
                n = norm(q)                                 # normalised
                if n == norm(c.name):                       # its own name:
                    continue
                if n in self.by_name:                       # a real card:
                    out.append(("card", self.by_name[n].name))  # ('card', name)
                elif n in self.arche_names:                 # an archetype:
                    out.append(("arche", n))                # ('arche', archetype)
            self._refs[c.name] = out                        # save it
        return self._refs[c.name]                           # hand back the saved list

    def junk(self, c: Info, deck: State) -> bool:           # method: can this card really NOT work in this deck?
        """True if the card cannot really work in this deck, even if its 'needs' look covered:
             (a) it belongs to an archetype that is not the deck's theme and has fewer than 2 other cards in the deck
                 (a lone D/D Gryphon, or Witch + Rucia of an off-theme archetype), or
             (b) its text names cards / archetypes and NONE of them is in the deck (EMERGENCY! with no Rescue-ACE)."""
        if c.clean:                                         # a clean card:
            return False                                                              # a standalone card works in any deck
        own = c.facts["archetype"] - {norm(c.name)}         # its archetypes (not counting its own name)
        if own and not (own & deck.arche):                  # it has some, and the deck's theme has none of them
            # other deck cards of its archetype
            mates = {o.name for o in deck.unique.values() if o.name != c.name and (o.facts["archetype"] & own)}
            if len(mates) < 2:                              # fewer than 2:
                return True                                 # (junk)
        refs = self.refs(c)                                 # what its text names
        if refs:                                            # it names something:
            for kind, v in refs:                            # each named card / archetype
                if kind == "card" and v in deck.counts:     # that card is in the deck:
                    return False                            # (fine)
                # that archetype is the theme, or has 3+ cards in the deck:
                if kind == "arche" and (v in deck.arche or sum(1 for o in deck.unique.values() if v in o.facts["archetype"]) >= 3):
                    return False                            # (fine)
            return True                                     # none of them is here: junk
        return False                                        # nothing wrong

    def theme_junk(self, c: Info, deck: State) -> bool:     # method: does a theme card name a stray sub-archetype?
        """For THEME picks under a broad umbrella theme ('Warrior' = 124 cards across Symphonic Warrior, Magnet Warrior, ...):
           True if the card's text names an archetype that is NOT the deck's theme and has fewer than 3 cards in the deck."""
        refs = [v for kind, v in self.refs(c) if kind == "arche"]  # archetypes its text names
        if not refs:                                        # none:
            return False                                    # (fine)
        home = deck.theme_arche                                                          # the label's own archetype(s); empty for a Type / Attribute theme
                                                                                       # (picked cards' archetypes do NOT count: they start out as strays)
        # junk unless one of them is the theme or has 3+ cards in the deck
        return not any(v in home or sum(1 for o in deck.unique.values() if v in o.facts["archetype"]) >= 3 for v in refs)

    def wincon_extras(self, label: str) -> list:            # method: the Extra Deck cards a win-condition label needs
        """Extra Deck cards a wincon label needs: the win card, plus any Extra Deck card it requires by name (C88 needs Number 88)."""
        if not norm(label or "").startswith(self.WIN_PREFIX):  # not a win-condition label:
            return []                                       # (none)
        # the win card (by its label word or full name)
        target = self.by_name.get(norm(self.WIN_ALIASES.get(norm(label.split("=", 1)[1]), label.split("=", 1)[1].strip())))
        if target is None or target.main_deck:              # not found, or a Main Deck card:
            return []                                       # (none)
        out = [target]                                      # the win card itself
        for nm in re.findall(r"Name = ([^&|]+?)\s*(?:&&|\|\||$)", target.row.get("required cards") or ""):  # every 'Name = ...' it requires
            c = self.by_name.get(norm(nm))                  # that card
            if c is not None and not c.main_deck and c.name != target.name:  # an Extra Deck card that isn't the win card
                out.insert(0, c)                            # put it first (it is summoned first)
        return out                                          # hand back the chain

    WIN_PREFIX = "alternative win condition ="              # how every win-condition label starts
    WIN_ALIASES = {"exodia": "Exodia the Forbidden One"}                              # label word -> the card that actually wins

    def wincon_setup(self, label: str, pool: list[Info], rng: random.Random) -> Optional[tuple]:  # method: set up a win-condition theme
        """'Alternative Win Condition = Destiny Board' -> (pieces, bucket, note).
             pieces = [(Info, copies, reason)]  the win card + everything it needs, placed in the deck FIRST
             bucket = cards that help the win card (for the rest of the theme slots)
           Returns None if `label` is not a wincon label."""
        if not norm(label or "").startswith(self.WIN_PREFIX):  # not a win-condition label:
            return None                                     # (nothing to do)
        value = (label.split("=", 1)[1]).strip()            # the part after '='
        target = self.by_name.get(norm(self.WIN_ALIASES.get(norm(value), value)))     # the win card, found by name
        if target is None:                                  # not found:
            return ([], [], f"wincon card '{value}' not found")  # (nothing placed; say why)
        by_pool = {c.name: c for c in pool}                 # card name -> card, for this pool
        note = ""                                           # a note for the report
        pieces = []                                         # (card, copies, reason) to place first
        if not target.main_deck:                                                      # Xyz wincons (Number 88...): the Extra Deck gets the card
            chain = self.wincon_extras(label)                                         # ...and what it needs; the MAIN deck gets the materials
            note = f"Extra Deck wincon: {[c.name for c in chain]} go to the Extra Deck"  # the note
            for card in chain:                              # each Extra Deck card in the chain
                # its materials: 'count = N : (level = L'
                m = re.search(r"count\s*=\s*(\d+)\s*:\s*\(\s*level\s*=\s*(\d+)", card.row.get("materials") or "", re.I)
                if not m:                                   # not that pattern
                    continue
                need, lvl = int(m.group(1)), int(m.group(2))                           # '3 Level 8 monsters' -> 2 summons' worth, capped
                want = min(need * 2, 8)                     # twice the materials needed, 8 at most
                # Main Deck monsters of that Level with no required cards
                cands = [c for c in pool if c.kind == "Monster" and c.main_deck and c.level == lvl and not (c.row.get("required cards") or "").strip()
                         and self.normal_ok(c)]                                       # (Normal Monsters toggle)
                cands = [c for c in cands if c.clean] or [c for c in cands if self.loose_generic(c)] or cands  # clean ones first, then loosely generic ones, then any
                while want > 0 and cands:                   # until enough copies are planned
                    c = rng.choice(cands); cands = [x for x in cands if x.name != c.name]  # pick one and don't pick it again
                    n = min(self.cap(c.name), want)         # as many copies as allowed / still wanted
                    pieces.append((c, n, f"material for wincon {card.name}")); want -= n  # plan them; fewer still wanted
            if "rank-up-magic" in norm(target.row.get("card text") or ""):          # Number C88 must be summoned with a Rank-Up-Magic spell
                # Rank-Up-Magic Spells
                ruM = [c for c in pool if "rank-up-magic" in c.facts["archetype"] and c.kind == "Spell" and c.main_deck]
                def points_at(c):                                                     # does this spell's Supports column point at the win card?
                    # any help branch whose trait part matches the win card
                    return any(card_matches([tuple(a for a in br if not is_mechanic(a))], target.facts) for br in c.helps if any(not is_mechanic(a) for a in br))
                ruM = [c for c in ruM if points_at(c)] or ruM  # the ones that point at it, if any
                for c in rng.sample(ruM, min(2, len(ruM))):  # up to 2 of them
                    pieces.append((c, 1, "Rank-Up-Magic for the wincon"))  # 1 copy each
        else:
            # 1 copy for Exodia / Destiny Board, otherwise a normal 1-3
            one = "exodia" in norm(target.name) or norm(target.name) == "destiny board"  # Exodia / Destiny Board: 1 copy
            if self.cap(target.name) <= 0:                                            # the win card is Forbidden in this format
                return ([], [], f"wincon card '{target.name}' is not allowed in this format")  # (nothing placed; say why)
            # the win card itself (1 copy, or 1-3)
            pieces.append((target, 1 if one else rng.randint(1, self.cap(target.name)), f"wincon: {target.name}"))
            req = (target.row.get("required cards") or "")                            # 'Name = A && Name = B' -> the pieces it names
            for nm in re.findall(r"Name = ([^&|]+?)\s*(?:&&|\|\||$)", req):  # each 'Name = ...' it requires
                piece = by_pool.get(nm.strip())                                      # a named piece that is in the allowed pool
                if piece and piece.main_deck and piece.name != target.name:  # a Main Deck piece that isn't the win card
                    pieces.append((piece, 1, f"wincon piece of {target.name}"))  # 1 copy each
            # each archetype its strict need names
            for arche in re.findall(r"Archetype = ([^&|]+?)\s*(?:&&|\|\||$)", target.row.get("strictSupportedBy") or ""):
                # that archetype's other Main Deck cards
                mates = [c for c in pool if norm(arche) in c.facts["archetype"] and c.main_deck and c.name != target.name]
                for c in mates[:4]:                                                   # Destiny Board: the four Spirit Message cards
                    pieces.append((c, 1, f"wincon piece of {target.name} ({arche})"))  # 1 copy each
        # cards that help the win card: their Supports columns name it or its archetype (broad traits like 'Normal' do not count)
        helpers = []                                        # cards that help the win card
        for c in pool:                                      # each pool card
            if c.name == target.name or not c.helps or not c.main_deck:  # the win card itself, helps nothing, or Extra Deck
                continue
            for br in c.helps:                              # each help branch
                t = tuple(a for a in br if not is_mechanic(a))  # its trait part
                if t and card_matches([t], target.facts) and any(a.field in ("archetype", "name") for a in t):  # matches the win card through a name / archetype piece
                    helpers.append(c); break                # keep it (once)
        # plain staples that make a win-by-combo / win-by-turns deck work: draw cards + ways to survive (negate attacks / effects)
        staples = []                                        # plain staples
        for staple_label in ("Self = Draw", "Opponent = Negate attack", "Opponent = Negation"):  # draw / stop attacks / negate
            staples += [c for c in self.theme_bucket(staple_label, pool) if c.clean and c.main_deck]  # clean Main Deck cards that do it
        bucket = list({c.name: c for c in helpers + staples}.values())  # helpers + staples, each card once
        if len(bucket) < 15:                                                          # still thin: any clean staple
            bucket += [c for c in pool if c.clean and c.main_deck and c not in bucket][:80]  # add up to 80 clean Main Deck cards
        return (pieces, bucket, note)                       # hand back (pieces, bucket, note)

    @staticmethod                                           # doesn't need the Builder itself
    def normal_support_specific(c: Info) -> bool:           # method: is this Spell/Trap support for Normal Monsters only?
        """Don't use conditional Normal/non-Effect support as standalone Generic filler.

        Inspect the text as well as the CSV: older database builds can lack
        strict requirements, and even `clean` cards must obey this rule.
        """
        if c.kind not in ("Spell", "Trap"):                 # only Spells and Traps count
            return False                                    # (no)
        txt = (c.row.get("card text") or "").lower()        # its text, lower-case
        # it mentions Normal / non-Effect monsters
        if re.search(r"\b(?:normal monsters?|non[- ]effect monsters?|except for (?:all )?effect monsters?)\b", txt):
            return True                                     # (yes)
        return any("trait = normal monster" in (c.row.get(k) or "").lower()  # or its strict columns name Normal Monsters...
                   or "trait != effect monster" in (c.row.get(k) or "").lower()  # ...or 'not an Effect Monster'
                   for k in ("strictSupports", "strictSupportedBy"))  # (the two strict columns)

    @staticmethod                                           # doesn't need the Builder itself
    def independent_generic(c: Info) -> bool:               # method: can this card fill a Generic slot on its own?
        """Independent Main Deck candidates for Generic slots.

        Only *strict* support/needs impose synergy requirements here. The general
        columns describe soft preferences, not a mandatory theme or partner.
        Keep the separate named-card, Ritual/Pendulum, and card-text safeguards.
        """
        if not c.main_deck or Builder.normal_support_specific(c):  # Extra Deck, or Normal-only support
            return False                                    # (no)
        if (c.row.get("strictSupports") or "").strip() or (c.row.get("strictSupportedBy") or "").strip():  # anything in the strict columns
            return False                                    # (no)
        return is_clean(c.row, c.facts, False, True, False,  # otherwise: the usual clean-card check
                        ignore_text=text_is_flavor(c.kind, c.facts))  # (flavor text ignored)

    @staticmethod                                           # doesn't need the Builder itself
    def loose_generic(c: Info) -> bool:                     # method: generic enough for a Generic deck?
        """Generic enough for a Generic deck: no parsed needs, no required cards, not Ritual / Pendulum."""
        return (not Builder.normal_support_specific(c) and c.generic  # not Normal-only support, no needs / helps...
                # ...no required cards, not Ritual / Pendulum
                and not (c.row.get("required cards") or "").strip()) and not ({"ritual", "pendulum"} & c.facts["trait"])

    def random_mechanic(self, pool: list[Info], rng: random.Random) -> str:  # method: pick a mechanic for 'Random' support in a Generic deck
        """For a 'Random' support in a Generic deck: choose a mechanic label that plenty of cards perform
           (so the deck has a real support theme instead of drawing from the whole card pool)."""
        counts: dict = {}                                   # mechanic -> cards that perform it
        for c in pool:                                      # each pool card
            for m in c.offered:                             # each mechanic it performs
                counts[m] = counts.get(m, 0) + 1            # count it
        good = sorted(m for m, n in counts.items() if n >= RANDOM_MECH_MIN)           # sorted -> reproducible
        return rng.choice(good) if good else ""             # a random one (or '' if none qualifies)

   
    def _theme_index(self) -> dict:                         # method: (field, value) -> card names, for fast theme lookups
        """{(field, value): {card names}} for every card, built once (under a second) and then kept.
           A theme label like 'Archetype = Blackwing' matches a card exactly when the card's facts hold that
           value (trait_atom_true), so looking it up here gives the same answer as theme_bucket(), instantly."""
        if getattr(self, "_theme_idx", None) is None:       # not built yet:
            idx: dict = {}                                  # the index we build
            for c in self.infos:                                           # main AND Extra Deck cards
                for field_name, values in c.facts.items():  # each fact (type, attribute, archetype ...)
                    if isinstance(values, (set, frozenset)):               # text facts (numbers like ATK are not themes)
                        for v in values:                    # each value it holds
                            idx.setdefault((field_name, v), set()).add(c.name)  # file the card under it
            self._theme_idx = idx                           # keep it
        return self._theme_idx                              # hand back the index

    def theme_has_cards(self, label: str, legal: set) -> bool:  # method: does a theme label have any card this format allows?
        """Does this theme label have at least one card in `legal` (the names this format allows)?"""
        if label == "Generic":                              # 'Generic':
            return True                                                    # Generic is never empty
        if norm(label).startswith(self.WIN_PREFIX):                        # a win condition: its win card must be legal
            value = label.split("=", 1)[1].strip()          # the part after '='
            target = self.by_name.get(norm(self.WIN_ALIASES.get(norm(value), value)))  # the win card
            return target is not None and target.name in legal  # found and allowed
        atom = parse_atom(label)                            # the label split into field / operator / value
        members = self._theme_index().get((atom.field, norm_trait(atom.value)), ())  # every card with that fact
        return any(name in legal for name in members)       # any of them allowed?

    def random_theme(self, pool: list[Info], rng: random.Random, req: Optional["Request"] = None) -> str:  # method: pick a random theme that has cards in this format
        """Choose uniformly from the theme labels (the same list the Theme 1 / Theme 2 boxes offer) that have at
           least one card this format allows. With `req`, Extra Deck cards count too, so an all-Extra-Deck theme
           (CXyz ...) can still be picked on purpose - it makes a funny deck. A theme with NO legal cards at all
           (a 2024 archetype in Goat Format) is skipped: it would just be a Generic deck with a wrong name."""
        legal = {c.name for c in pool}                                     # main-deck cards this format allows
        if req is not None:                                 # the request was given:
            legal |= {c.name for c in self.allowed(req, extra=True, rated=False)}   # + Extra Deck (ratings don't matter for themes)
        candidates = sorted(set(theme_labels(self.labels)) - {"Random"}, key=str.lower)   # each label once, A-Z
        while candidates:                                   # until a label works (or none are left)
            pick = rng.choice(candidates)                                  # same first roll as before ...
            if self.theme_has_cards(pick, legal):           # does it have legal cards?
                return pick                                                # ... and kept when it has legal cards
            candidates.remove(pick)                                        # otherwise drop it and roll again
        return "Generic"                                    # nothing worked: a Generic deck


    # ---- "base deck around a single card" ----------------------------------
    def find_card(self, text: str) -> Optional[Info]:       # method: find the card a player typed
        """The card a player typed: exact name first (any capitals), then old / renamed / garbled names. None = not found."""
        if not (text or "").strip():                        # blank:
            return None                                     # (none)
        hit = self.by_name.get(norm(text))                                            # 1. the name as typed
        if hit is None:                                     # not found:
            from ygo_names import NameResolver                                        # 2. 'Red-Eyes B. Dragon' -> its current name
            found = NameResolver([i.name for i in self.infos]).find(text)  # try the old / garbled-name finder
            hit = self.by_name.get(norm(found)) if found else None  # the card, if it found one
        return hit                                          # hand back the card (or None)

    def anchor_card(self, req: Request, text: Optional[str]) -> Optional[Info]:  # method: read and check one card box
        """One card box (Card 1 or Card 2), checked: blank -> None; otherwise the card, which must exist and be
           allowed in the chosen format (banlist / release date). Raises AnchorError with a message for the player."""
        if not (text or "").strip():
            return None                                                               # this box is off
        anchor = self.find_card(text)                       # find it
        if anchor is None:                                  # not found:
            raise AnchorError(f'No card named "{text}" was found. Pick a name from the suggestions.')  # (error shown to the player)
        self.set_format(req)                                                          # the copy limits of this format
        if self.cap(anchor.name) <= 0:                                                # Forbidden, or not out by the list's date
            raise AnchorError(f"{anchor.name} is not allowed in {req.banlist or 'this format'}.")  # (error shown to the player)
        if not anchor.main_deck and req.extra_deck_size <= 0:                          # an Extra Deck card needs an Extra Deck
            raise AnchorError(f"{anchor.name} is an Extra Deck card, so the Extra Deck size can't be 0.")  # (error shown to the player)
        return anchor                                       # the card, checked

    def anchor_cards(self, req: Request) -> dict:           # method: both card boxes, checked
        """{'theme1': Card 1 or None, 'theme2': Card 2 or None}. Card 1 replaces Theme 1, Card 2 replaces Theme 2."""
        # Card 1 for Theme 1, Card 2 for Theme 2
        return {"theme1": self.anchor_card(req, req.anchor_card), "theme2": self.anchor_card(req, req.anchor_card_2)}

    def random_card(self, req: Request, rng: random.Random, taken: set = frozenset()) -> str:  # method: pick a random card for a 'Random card' box
        """A 'Random card' pick: any card this format allows, inside the era and rating limits (so it isn't junk),
           from the Main Deck - or the Extra Deck too when the Extra Deck size isn't 0. `taken` = names not to pick
           (the other card box). Uses the deck's seeded generator, so the same seed picks the same card."""
        cards = self.allowed(req)                                                     # Main Deck cards (era + rating + format)
        if req.extra_deck_size > 0:                         # the Extra Deck is used:
            cards = cards + self.allowed(req, extra=True)                             # + Extra Deck monsters
        cards = [c for c in cards if c.name not in taken] or self.allowed(req, rated=False)   # (nothing rated: ignore ratings)
        cards = sorted(cards, key=lambda c: c.name)                                   # a fixed order, so the seed decides
        return rng.choice(cards).name                       # pick one

    def anchor_copies(self, anchor: Info) -> int:           # method: how many copies of the card-box card
        """Always 3 copies, unless the format allows fewer (Limited -> 1, Semi-Limited -> 2, Highlander -> 1)."""
        return min(ANCHOR_COPIES, self.cap(anchor.name))    # 3, unless the format's limit is lower

    @staticmethod                                           # doesn't need the Builder itself
    def _identity(branch: tuple) -> bool:                   # method: does this branch name WHAT a card is?
        """True if a support branch names WHAT a card is (name, archetype, series, Type, Attribute, trait), not just
           'is a Monster' or 'Level 4 or lower'. Those plain branches would match nearly every card."""
        return any(a.field in ("name", "archetype", "series", "type", "attribute", "trait") for a in branch)  # any name / archetype / series / Type / Attribute / trait piece

    def anchor_buckets(self, anchor: Info, pool: list[Info], materials: list[Info]) -> dict:  # method: the five card groups for a single-card deck
        """The five groups of cards for a single-card deck, as {step key: (bucket, boost or None)}. In fill order:
             needs       cards that meet its needs: the trait part of each need (a 'WATER monster'), cards that
                         ENABLE its mechanic needs (send-to-GY cards for 'Banish (GY)'), and - for an Extra Deck
                         card - the Main Deck monsters that can be its materials (`materials`)
             supporters  cards whose Supports column points at it (its name, archetype, Type or Attribute), or
                         whose text names it / its archetype in quotes
             archetype   the other cards of its archetype(s)
             type_attr   monsters of its Type or Attribute (Main Deck monsters only)
             supports    cards that ARE what it supports (its strictSupports column)
           The anchor itself is never in a bucket (it is placed first, separately)."""
        others = [c for c in pool if c.name != anchor.name]                           # the anchor goes in on its own
        out = {}                                            # step key -> (bucket, boost)

        # 1. needs: trait part via partners_for (the same cards a partner pick would use), mechanic part via enablers
        needs: dict = {c.name: c for c in materials if c.name != anchor.name}         # Extra Deck card: its materials first
        for branch in anchor.needs:                         # each OR-option of its needs
            traits = tuple(a for a in branch if not is_mechanic(a))                   # "is a WATER monster" (hard)
            mechs = [a for a in branch if is_mechanic(a)]                             # "Self = Banish (GY)" (soft)
            if traits:                                      # a trait part:
                for c in self.partners_for(traits, others):  # every card that meets it
                    needs[c.name] = c                       # add it
            for m in mechs:                                                           # a card that makes the mechanic possible:
                for c in others:                                                      # ask need_status with that ONE card as the 'deck'
                    if need_status(m, c.offered, c.facts["trait"], self.tree) == "met":  # this card alone makes the mechanic possible
                        needs[c.name] = c                   # add it
        out["needs"] = (list(needs.values()), None)         # step 1's bucket (no boosts)

        # 2. supporters: their Supports column points at the anchor (specific = name / archetype / series: likelier)
        sup, boost = {}, {}                                 # supporter cards, and their boosts
        for c in others:                                    # each other card
            best = 0.0                                      # its best boost so far (0 = not a supporter)
            for branch in c.helps:                          # each help branch
                t = tuple(a for a in branch if not is_mechanic(a))  # its trait part
                if t and self._identity(t) and card_matches([t], anchor.facts):      # this branch describes the anchor
                    specific = any(a.field in ("name", "archetype", "series") for a in t)  # a name / archetype / series piece
                    best = max(best, ANCHOR_SPECIFIC_BOOST if specific else ANCHOR_BROAD_BOOST)  # specific supporters get the bigger boost
            for kind, v in self.refs(c):                                              # its text names the anchor / its archetype
                # the card itself, or one of its archetypes
                if (kind == "card" and v == anchor.name) or (kind == "arche" and v in anchor.facts["archetype"]):
                    best = max(best, ANCHOR_SPECIFIC_BOOST)  # (specific)
            if best:                                        # it supports the card:
                sup[c.name], boost[c.name] = c, best        # keep it and its boost
        out["supporters"] = (list(sup.values()), boost)     # step 2's bucket, with boosts

        # 3. archetype: every card of its archetype(s) (its own name as an 'archetype' doesn't count)
        own = anchor.facts["archetype"] - {norm(anchor.name)}  # its archetypes (not counting its own name)
        out["archetype"] = ([c for c in others if c.facts["archetype"] & own], None)  # step 3's bucket

        # 4. Type / Attribute: Main Deck monsters that share either one (only a monster has these)
        if anchor.kind == "Monster":                        # a monster:
            out["type_attr"] = ([c for c in others if c.kind == "Monster" and c.main_deck  # Main Deck monsters...
                                 # ...of its Type or its Attribute
                                 and ((c.facts["type"] & anchor.facts["type"]) or (c.facts["attribute"] & anchor.facts["attribute"]))], None)
        else:
            out["type_attr"] = ([], None)                   # a Spell / Trap: nothing for this step

        # 5. cards it supports: the cards its strictSupports describe (identity branches only, see _identity)
        # the trait part of each identity help branch
        branches = [t for t in (tuple(a for a in br if not is_mechanic(a)) for br in anchor.helps) if t and self._identity(t)]
        out["supports"] = ([c for c in others if branches and card_matches(branches, c.facts)], None)  # cards that are what it supports
        return out                                          # hand back all five

    def _place_card(self, deck: State, card: Info, origin: str) -> None:  # method: put a card-box card in the deck
        """Put a Main Deck card-box card in at 3 copies (or the format's limit), and make its archetype 'on theme'.
           (An Extra Deck card goes in the Extra Deck instead - ygo_extra.generate_state does that.)"""
        own = card.facts["archetype"] - {norm(card.name)}   # its archetypes (not counting its own name)
        deck.arche |= own                                                             # its archetype is 'on theme' (archetype_fit, junk)
        deck.theme_arche |= own                             # (and counts as the theme's archetype)
        if card.main_deck:                                  # a Main Deck card:
            n = self.anchor_copies(card)                    # 3, or the format's limit
            # add it, marked 'required' so it is never removed
            deck.add(card, n, "required", f"{'Card 1' if origin == 'theme1' else 'Card 2'}: the card this deck is built around ({n} copies)")

    # method: fill a theme slot around one card
    def _fill_card(self, deck: State, origin: str, slots: int, card: Info, pool: list[Info], rng: random.Random,
                   materials: list[Info], debug: dict) -> int:  # (its inputs, continued)
        """Fill a theme slot (`slots` cards) around ONE card, step by step (ANCHOR_TIERS): what it needs, cards that support
           it, its archetype, its Type / Attribute, cards it supports. Each step may use its share of the slots; a step that
           runs short passes its slots to the next one. Returns how many slots it could NOT fill (they cross over to the
           other theme). materials = Main Deck monsters that can summon it, when it is an Extra Deck card."""
        buckets = self.anchor_buckets(card, pool, materials)  # the five card groups
        deck.prefer |= {c.name for key in ("needs", "supporters", "archetype") for c in buckets[key][0]}   # partner picks favour these
        carry, total, notes = 0, 0, []                                                # carry = slots an earlier step didn't use
        for key, share, what in ANCHOR_TIERS:               # each step, in order
            bucket, boost = buckets[key]                    # its cards and boosts
            want = min(round(slots * share) + carry, slots - total)                   # its share + anything passed on to it
            before = len(deck.entries)                      # cards in the deck before this step
            if want > 0 and bucket:                         # slots to fill, and cards to fill them with
                self.fill(deck, bucket, pool, want, origin, f"{what} ({card.name})", rng,  # fill the slots
                          need_synergy=(key in ("type_attr", "supports")),             # big buckets: prefer cards linked to the deck
                          boost=boost, fit=(key != "archetype"))                       # its own archetype: theme-style picks
            got = len(deck.entries) - before                # cards this step added
            carry = max(0, want - got)                                                # unused slots go to the next step
            total += got                                    # slots used so far
            notes.append(f"{what}: {got}")                  # note it for the report
        own = ", ".join(sorted(card.facts["archetype"] - {norm(card.name)})) or "none"  # its archetypes, for the report
        line = f"{card.name} (archetype: {own}) - " + ", ".join(notes)  # the report line
        debug[origin] = (debug[origin] + "; then " + line) if debug.get(origin) else line  # save it (added to any earlier line)
        return max(0, slots - total)                        # slots it couldn't fill

    # ---- the whole deck --------------------------------------------------
    def build_main(self, req: Request, pre: Optional[list] = None, materials: Optional[dict] = None) -> tuple[State, dict]:
        """The main deck, in five steps. pre = [(Info, copies, reason)]: cards to place first (extra-first build
           order: the Extra Deck's materials). Returns (the deck, debug info for the preview).
           Card 1 / Card 2 ('Base deck around a single card'): a card REPLACES its theme slot. It goes in at 3 copies
           and its theme's slots are filled around it (_fill_card). Theme slots one side can't fill cross over to the
           other theme first, and only then go to staples / filler. Support, Generic and the sliders work as usual.
           materials = {'theme1' / 'theme2': Main Deck monsters that can summon that card} for Extra Deck cards."""
        rng = random.Random(req.seed)                                                 # the ONE random generator
        pool = self.allowed(req, rated=False)                                         # theme + synergy cards ignore the rating limits...
        self.rating_range = (req.min_card_rating, req.max_card_rating)               # ...only generic filler obeys them
        if not pool:                                        # no cards at all:
            raise ValueError("No cards left after the era/rating filters.")  # (build_deck widens the filters and tries again)
        size = max(40, min(60, req.main_deck_size))         # the Main Deck size, kept between 40 and 60
        cards = self.anchor_cards(req)                                                # {'theme1': Card 1, 'theme2': Card 2} (None = off)
        # Resolve Random themes once, before setting up win conditions. A slot with a card has no theme label at all.
        theme1_label = None if cards["theme1"] else req.deck_type_1_label  # a card in slot 1 means no Theme 1 label
        theme2_label = None if cards["theme2"] else req.deck_type_2_label  # a card in slot 2 means no Theme 2 label

        if cards["theme1"] is None and norm(theme1_label or "") == "random":  # Theme 1 is 'Random':
            theme1_label = self.random_theme(pool, rng, req)  # pick a real theme now

        if cards["theme2"] is None and norm(theme2_label or "") == "random":  # Theme 2 is 'Random':
            theme2_label = self.random_theme(pool, rng, req)  # pick a real theme now
        resolved_req = replace(req, deck_type_1_label=theme1_label, deck_type_2_label=theme2_label)  # the request with the picked themes in it
        deck = self._new_deck(resolved_req)                                               # 1. an empty deck with its type targets
        if any(cards.values()):                             # a card box is used:
            deck.generic_deck = False                                                 # a deck built around a card is never 'Generic'
        pre, wincon_bucket, wdebug = self._place_wincons(resolved_req, pool, rng, pre)  # 2. win-condition pieces, if a theme is one
        for info, n, why in (pre or []):                                              #    pre-placed cards use up slots first
            deck.add(info, n, "material", why)              # (marked 'material')
        for origin, card in cards.items():                                            #    Card 1 / Card 2 at 3 copies, before the slot plan
            if card is not None:                            # this box is used:
                self._place_card(deck, card, origin)        # put the card in
        t1, t2, s1, s2 = self._slot_plan(req, size, deck)                             # 3. slots for theme 1 / 2, support 1 / 2
        # what each theme slot shows in the report: the card, or the label
        shown = {o: (f"Card: {c.name}" if c else lbl) for o, c, lbl in (("theme1", cards["theme1"], theme1_label),
                                                                         # (for Theme 2)
                                                                         ("theme2", cards["theme2"], theme2_label))}
        debug = {"size": size, "slots": {"theme1": t1, "theme2": t2, "support1": s1, "support2": s2},  # the debug info: sizes and slots...
                 # ...and the category each slot really used
                 "selected_categories": {**shown, "support1": req.support_1_label, "support2": req.support_2_label}}
        # A slot with no cards at all (0% share) says so, instead of showing a label it never used. A card-box card
        # still shows (it is in the deck at 3 copies), and with the Support boxes off (support_on False) both
        # Support slots are 0 on purpose because automatic support fills them, so those keep their labels.
        unused = [o for o, n in (("theme1", t1), ("theme2", t2)) if n == 0 and cards[o] is None]   # empty theme slots
        if req.support_on:                                                            # the Support boxes are in use:
            unused += [o for o, n in (("support1", s1), ("support2", s2)) if n == 0]  # empty support slots too
        for o in unused:                                                              # each empty slot
            debug["selected_categories"][o] = "Not used (0% share)"                   # say so on the page
        if wdebug:                                          # a win-condition theme was set up:
            debug["wincon"] = wdebug                        # note it
        if any(cards.values()):                             # a card box is used:
            self._fill_themes_with_cards(deck, req, cards, (theme1_label, theme2_label), t1, t2, pool, rng,  # 4. fill the theme slots around the card(s)
                                         wincon_bucket, materials or {}, debug)  # (and their materials)
        else:                                                                         # no cards: the themes exactly as before
            for origin, slots, label in (("theme1", t1, theme1_label), ("theme2", t2, theme2_label)):  # each theme slot
                if slots > 0:                               # it has slots:
                    self._fill_label(deck, origin, slots, label, False, pool, rng, wincon_bucket, debug)  # 4. fill them from its label
        # each support slot
        for origin, slots, label in (("support1", s1, req.support_1_label), ("support2", s2, req.support_2_label)):
            if slots > 0:                                   # it has slots:
                self._fill_label(deck, origin, slots, label, True, pool, rng, wincon_bucket, debug)  # 4. fill them from its label
        # Dedicated generic allocation: selected independently of support, with legal standalone cards.
        # the Generic share of the deck (never more than the room left)
        generic_slots = min(max(0, round(max(0, size - len(pre or [])) * req.generic_overall_pct / 100)), size - len(deck.entries))
        if generic_slots:                                   # any:
            self._fill_generic_slots(deck, pool, generic_slots, rng)  # fill them with standalone cards
        self._auto_fill(deck, pool, size, rng)                                        # 5. staples, helpers, filler
        while len(deck.entries) > size:                                               # partners can overshoot slightly
            deck.entries.pop()                              # drop the last cards picked
        return deck, debug                                  # hand back the deck and its debug info

    def _fill_themes_with_cards(self, deck: State, req: Request, cards: dict, labels: tuple, t1: int, t2: int,  # method: step 4 for the theme slots when a card box is used
                                # (its inputs, continued)
                                pool: list[Info], rng: random.Random, wincon_bucket: dict, materials: dict, debug: dict) -> None:
        """Step 4 for the THEME slots when Card 1 and/or Card 2 is used. Each slot is filled around its card, or from its
           theme label as usual. Then:
             - slots Theme 1 couldn't fill go to Theme 2 (only if Theme 2 has a share at all),
             - slots Theme 2 still couldn't fill go back to Theme 1 (only if Theme 1 has a share),
             - anything left after that is left for the staples / filler at the end (build_main step 5).
           A card whose slot has a 0% share is still in the deck at 3 copies; nothing is built around it."""
        def fill_slot(origin: str, slots: int) -> int:      # inner function: fill one side's theme slots
            """Fill `slots` theme slots for one side; returns how many it could not fill."""
            if slots <= 0:                                  # nothing to fill:
                return 0                                    # (nothing short)
            card = cards[origin]                            # that side's card (or None)
            if card is not None:                                                      # a card: build around it
                return self._fill_card(deck, origin, slots, card, pool, rng, materials.get(origin, []), debug)
            label = labels[0] if origin == "theme1" else labels[1]  # that side's theme label
            before = len(deck.entries)                      # cards in the deck before
            self._fill_label(deck, origin, slots, label, False, pool, rng, wincon_bucket, debug)   # a normal theme
            return max(0, slots - (len(deck.entries) - before))  # slots it couldn't fill

        short1 = fill_slot("theme1", t1)                                              # Theme 1 / Card 1 first
        short2 = fill_slot("theme2", t2 + (short1 if t2 > 0 else 0))                  # Theme 2 / Card 2, plus Theme 1's leftovers
        if short1 and t2 <= 0:                              # Theme 1 was short and Theme 2 has no share:
            short2 += short1                                                          # (Theme 2 has no share: nowhere to cross to)
        back = fill_slot("theme1", short2) if (short2 and t1 > 0) else short2         # Theme 2's leftovers back to Theme 1
        if back:                                            # still short after all that:
            debug["theme_leftover"] = f"{back} theme slots left for staples / filler"  # note it (the filler takes those slots)

    def _new_deck(self, req: Request) -> State:             # method: step 1 - an empty deck
        """Step 1: an empty deck, with the monster / spell / trap targets (if any were set) and whether it is a
           Generic deck (no real theme at all)."""
        quotas = None                                       # no Monster / Spell / Trap targets yet
        if any(v is not None for v in (req.main_monster_target, req.main_spell_target, req.main_trap_target)):  # any target set?
            # every target (a blank one counts as 0)
            quotas = {"Monster": req.main_monster_target or 0, "Spell": req.main_spell_target or 0, "Trap": req.main_trap_target or 0}
        deck = State(quota=quotas)                          # the empty deck
        deck.generic_deck = (norm(req.deck_type_1_label or "") in ("generic", "")             # no real theme at all
                             and (t2_unused(req) or norm(req.deck_type_2_label or "") in ("generic", "")))
        return deck                                         # hand it back

    # method: step 2 - win-condition themes first
    def _place_wincons(self, req: Request, pool: list[Info], rng: random.Random, pre: Optional[list]) -> tuple:
        """Step 2: a win-condition theme (Exodia, Final Countdown ...) puts the win card and its pieces in first, and
           its helpers become that theme's cards. Returns (cards to place first, {theme label: helper cards}, notes)."""
        wincon_bucket = {}                                                            # theme label -> its helper bucket
        wdebug = []                                         # notes for the report
        for theme_label in (req.deck_type_1_label, req.deck_type_2_label):  # each theme label
            w = self.wincon_setup(theme_label or "", pool, rng)  # set it up (None = not a win-condition label)
            if w is None:                                   # not one:
                continue                                                              # not a win-condition label
            pieces, wb, note = w                            # the pieces, the helpers, a note
            wincon_bucket[theme_label] = wb                 # the helpers become this theme's cards
            wdebug.append(f"{theme_label}: {[p[0].name for p in pieces]} {note}".strip())  # note it
            pre = list(pre or []) + pieces                                            # the win card + its pieces go in first
        return pre, wincon_bucket, wdebug                   # hand back (cards to place first, helpers, notes)

    @staticmethod                                           # doesn't need the Builder itself
    def _slot_plan(req: Request, size: int, deck: State) -> tuple:  # method: step 3 - share out the slots
        """Step 3: how the slots left over are shared: theme vs support (the big slider), then theme 1 vs 2 and
           support 1 vs 2. With Support off, the support slots are left to the auto-fill (step 5)."""
        room = max(0, size - len(deck.entries))                                       # slots left for theme / support
        theme_total = round(room * req.deck_type_overall_pct / 100)  # theme share of the room
        generic_total = round(room * max(0, min(100, req.generic_overall_pct)) / 100)  # Generic share of the room (0-100%)
        theme_total = min(room - generic_total, theme_total)  # never more theme than what's left after Generic
        support_total = room - theme_total - generic_total  # the rest is support
        t1 = round(theme_total * req.deck_type_1_pct / 100); t2 = theme_total - t1  # Theme 1 / Theme 2 split
        s1 = round(support_total * req.support_1_pct / 100); s2 = support_total - s1  # Support 1 / Support 2 split
        if not req.support_on:                                                        # Support OFF: labels ignored, the top-up fills every support slot
            s1 = s2 = 0                                     # (no Support 1 / Support 2 slots)
        return t1, t2, s1, s2                               # hand back the four slot counts

    def _fill_label(self, deck: State, origin: str, slots: int, label: Optional[str], is_support: bool,  # method: step 4 - fill one label's slots
                    pool: list[Info], rng: random.Random, wincon_bucket: dict, debug: dict) -> None:  # (its inputs, continued)
        """Step 4, for one label: find the label's cards (its 'bucket') and fill `slots` with them. A theme that runs
           out (a small archetype / series) is topped up with its RELATED cards rather than random ones."""
        def real(lbl):                                                                # blank / Random / Generic are not real labels
            return lbl and norm(lbl) not in ("random", "generic")  # something other than blank / Random / Generic

        if not is_support and not real(label):                                        # Random theme -> pick an archetype or series
            label = self.random_theme(pool, rng) if norm(label or "random") == "random" else ""  # 'Random' picks a theme; anything else not real becomes Generic
        if is_support and norm(label or "") == "random":    # a Random support category:
            self._fill_auto_support(deck, pool, slots, rng, origin, debug)  # automatic support instead
            return
        if norm(label or "") == "generic" or not label:     # Generic / blank:
            bucket = [c for c in pool if (self.independent_generic(c) or self.loose_generic(c)) and self.rating_ok(c)]   # Strictly independent or previously accepted generic
        elif is_support:                                    # a support label:
            bucket = self.support_bucket(label, pool, frozenset(deck.arche))  # cards that support it (fitting the deck's archetypes)
        elif label in wincon_bucket:                        # a win-condition theme:
            bucket = wincon_bucket[label]                                             # wincon theme = cards that help the win card
        else:
            bucket = self.theme_bucket(label, pool)         # cards that are / perform the label
        if deck.generic_deck and is_support and norm(label or "") != "generic":       # Generic deck + a support mechanic:
            tidy = [c for c in bucket if not (c.row.get("required cards") or "").strip()      # skip cards that need named cards
                    and not ({"ritual", "pendulum"} & c.facts["trait"])]                        # and ones that need partners
            if len(tidy) >= 10:                             # enough left:
                bucket = tidy                               # use only those
        if not bucket:                                                                # no support: auto-detect instead of unrelated cards
            if is_support:                                  # a support slot:
                self._fill_auto_support(deck, pool, slots, rng, origin, debug)  # automatic support instead
                return
            bucket = [c for c in pool if self.rating_ok(c)] or pool  # a theme: any card inside the rating limits
        if not is_support and label and real(label):                                  # remember which archetype the label itself names
            at = parse_atom(label)                          # the label split into field / operator / value
            if at.field == "archetype" and not is_mechanic(at):  # an archetype label:
                deck.theme_arche.add(norm(at.value))        # note it as the theme's own archetype
        debug[origin] = f"{label or 'generic'} ({len(bucket)} cards)"  # the report line
        if not is_support and bucket is not pool:                                     # remember what "on theme" means
            deck.prefer |= {c.name for c in bucket}         # its cards count as 'on theme'
            deck.theme_cards.update({c.name: c for c in bucket})                      # the theme's cards: targets for its own searchers
        skip_normals = False                                                          # Normal Monsters allowed? (decided below)
        if not is_support and label and real(label):        # a real theme label:
            at = parse_atom(label)                          # split it
            if not is_mechanic(at):                         # a trait label (not a mechanic):
                deck.theme_atoms.add((at.field, norm_trait(at.value)))                # e.g. ('type', 'zombie')
            # A Type / Attribute / mechanic theme gets no vanilla Normals (Zombino in a Zombie deck) unless a card calls for them.
            # An archetype or series keeps its own Normals (Blue-Eyes White Dragon), and a 'Normal Monster' theme is all Normals.
            normal_theme = at.field == "trait" and "normal" in norm_trait(at.value)  # a 'Normal Monster' theme?
            skip_normals = at.field not in ("archetype", "series") and not normal_theme  # Type / Attribute / mechanic themes skip vanilla Normals
        wants_generic = norm(label or "") in ("generic", "")                          # a Generic label stays unlinked on purpose
        before = len(deck.entries)                          # cards in the deck before
        self.fill(deck, bucket, pool, slots, origin, label or "generic", rng,  # fill the slots
                  need_synergy=(is_support and not wants_generic), skip_normals=skip_normals)  # support picks must link to the deck (unless Generic)
        short = slots - (len(deck.entries) - before)                                  # theme slots the archetype could not fill
        if not is_support and short > 0 and label and real(label):  # a real theme came up short:
            at2 = parse_atom(label)                         # split the label
            if at2.field in ("archetype", "series") and not is_mechanic(at2):         # a small archetype / series ran out: use related cards
                got = 0                                     # related cards added
                for i, tier in enumerate(self.related_tiers(norm(at2.value), pool, at2.field)):  # each tier, best first
                    if short <= 0:                          # all slots filled:
                        break
                    b2 = len(deck.entries)                  # cards before this tier
                    self.fill(deck, tier, pool, short, origin, f"related to {label}", rng,  # fill from this tier
                              skip_normals=(i >= 1))                                  # tiers 2-3 = outside monsters of its Type / Attribute: no vanilla Normals
                    short -= len(deck.entries) - b2         # fewer slots short
                    got += len(deck.entries) - b2           # count them
                if got:                                     # any added:
                    debug[origin] += f" + {got} related cards"  # add it to the report line

    def _fill_generic_slots(self, deck: State, pool: list[Info], slots: int, rng: random.Random) -> None:  # method: fill the Generic share with standalone cards
        """Fill the explicit Generic share with standalone cards, not random archetype engines."""
        bucket = [c for c in pool if c.main_deck and self.rating_ok(c)  # Main Deck cards inside the rating limits...
                  and not self.normal_support_specific(c)   # ...that aren't Normal-only support...
                  and (c.clean or self.independent_generic(c) or self.loose_generic(c))]  # ...and work on their own
        self.fill(deck, bucket, pool, slots, "generic", "dedicated Generic share", rng)  # fill the slots, marked 'generic'

    def _support_detail(self, info: Info, deck: State) -> tuple[int, float, str]:  # method: the best support category a card has, and how much of the deck it covers
        """Best qualifying support category, coverage, and the actual supported label."""
        total = len(deck.entries)                           # cards in the deck
        if total < 6:                                       # too small a deck to judge:
            return 0, 0.0, ""                               # (nothing)
        best = (0, 0.0, "")                                 # (priority, share, label) of the best branch so far
        for branch in info.helps:                           # each help branch
            categories = []                                 # the category of each piece
            for atom in branch:                             # each piece
                if is_mechanic(atom): categories.append(5)  # a mechanic: priority 5
                elif atom.field == "type": categories.append(4)  # a Type: 4
                elif atom.field == "attribute": categories.append(3)  # an Attribute: 3
                elif atom.field == "archetype": categories.append(2)  # an archetype: 2
            if not categories:                              # none of those
                continue
            hits = 0                                        # deck copies this branch matches
            for other in deck.unique.values():              # each deck card
                if other.name == info.name:                 # not itself
                    continue
                matches = True                              # until a piece fails
                for atom in branch:                         # each piece of the branch
                    if is_mechanic(atom):                   # a mechanic piece:
                        want = self.tree.get(mechanic_label(atom), {mechanic_label(atom)})  # it plus every mechanic counted as it
                        if not (other.offered & want): matches = False; break  # the deck card doesn't perform any: no match
                    elif not card_matches([(atom,)], other.facts):  # a trait piece the deck card doesn't have:
                        matches = False; break              # no match
                if matches:                                 # every piece matched:
                    hits += deck.counts[other.name]         # count its copies
            share = hits / total                            # as a share of the deck
            if share >= BROAD_SHARE:                        # covers a third or more:
                label = " && ".join(f"{a.field.title()} {a.op} {str(a.value).title()}" for a in branch)  # the branch written out as a label
                candidate = (max(categories), share, label)  # (priority, share, label)
                if candidate[:2] > best[:2]:                # better priority, or same priority and more coverage
                    best = candidate                        # keep it
        return best                                         # hand back the best (or zeros)

    def _support_matches(self, info: Info, deck: State) -> tuple[int, float]:  # method: just the priority and the share
        """Priority and coverage, shared with broad-support weighting."""
        priority, share, _ = self._support_detail(info, deck)  # work it out
        return priority, share                              # hand back the two numbers

    def _fill_auto_support(self, deck: State, pool: list[Info], slots: int,  # method: automatic support (Support boxes off or Random)
                           rng: random.Random, origin: str, debug: dict) -> None:  # (its inputs, continued)
        """Automatic support, drawn from two buckets:
             Theme    : cards that support the deck's own theme, members included (Goblin Zombie, Pyramid Turtle,
                        Book of Life in a Zombie deck; see supports_theme). Gets about two-thirds of the slots.
             The rest : every other card whose strictSupports cover a third of the deck (mechanic, Type, Attribute and
                        archetype support all mixed together, no order between them). Gets the other third.
                        Cards that cover more of the deck are likelier, and mechanic support gets a mild edge
                        (AUTO_MECHANIC_EDGE) because it plays off what the deck already does.
           If one bucket runs out, its slots go to the other.
        """
        before = len(deck.entries)                          # cards in the deck before
        theme_target = round(slots * AUTO_THEME_SHARE)                                # e.g. 9 slots -> 6 Theme, 3 the rest
        theme_done = 0                                                                # copies added from the Theme bucket so far
        for _ in range(slots * 5):                                                    # (a safety limit on tries)
            added = len(deck.entries) - before              # copies added so far
            if added >= slots:                              # all slots filled
                break
            # --- the two buckets, rebuilt each time because the deck (and so what is useful) keeps changing ---
            theme_sup = [c for c in pool if c.main_deck and c.helps                    # Theme: supports the theme itself
                         and self.supports_theme(c, deck) and not self.junk(c, deck)]  # ...and isn't junk here
            theme_names = {c.name for c in theme_sup}       # their names
            ranked = [(c, self._support_matches(c, deck)) for c in pool  # every other card's (priority, share)
                      if c.main_deck and c.helps and c.name not in theme_names]       # the rest: everything else...
            ranked = [(c, rank) for c, rank in ranked if rank[0] and not self.junk(c, deck)]   # ...that covers a third of the deck
            rest = [c for c, _ in ranked]                   # just the cards
            rest_boost = {c.name: (1 + rank[1] * 3)                                   # more of the deck covered -> likelier,
                                  * (AUTO_MECHANIC_EDGE if rank[0] == 5 else 1.0)     # and mechanic support (priority 5) gets an edge
                          for c, rank in ranked}            # (for each of them)
            # --- which bucket this slot comes from: Theme until it has its share, then the rest ---
            order = [("theme", theme_sup), ("rest", rest)]  # Theme bucket first
            if theme_done >= theme_target:                                            # Theme has its two-thirds: the rest goes first
                order.reverse()                             # the rest first
            card, bucket_used = None, None                  # nothing picked yet
            for name, bucket in order:                                                # the first bucket that gives a card wins
                if bucket:                                  # it has cards:
                    card, _ = self.pick(bucket, deck, pool, rng, need_synergy=False,  # pick one
                                        boost=rest_boost if name == "rest" else None)  # (the rest bucket uses its boosts)
                    if card:                                # got one:
                        bucket_used = name                  # note which bucket
                        break
            if card is None:                                                          # both buckets empty: filler takes over
                break
            room = min(slots - added, self.cap(card.name) - deck.counts.get(card.name, 0))  # copies that fit (slots left, copy limit)
            if bucket_used == "theme":                      # from the Theme bucket:
                room = min(room, max(1, theme_target - theme_done))                   # don't overshoot the Theme share
            if room <= 0:                                   # nothing fits
                break
            copies = rng.randint(1, room)                   # 1 to that many copies
            if bucket_used == "theme":                      # from the Theme bucket:
                theme_done += copies                        # count them
                deck.add(card, copies, origin, "automatic support: supports the theme")  # add them
            else:
                _, coverage, selected_label = self._support_detail(card, deck)  # which category it supports, and how much of the deck
                # add them, with that reason
                deck.add(card, copies, origin, f"automatic support: {selected_label} ({coverage:.0%} of deck)")
        left = slots - (len(deck.entries) - before)         # slots still empty
        debug[origin] = f"automatic support ({slots-left}/{slots} matched; remaining slots use normal filler)"  # the report line

    def _auto_fill(self, deck: State, pool: list[Info], size: int, rng: random.Random) -> None:  # method: step 5 - fill every empty slot
        """Step 5: fill every slot still empty, trying these in order each time (the first that gives a card wins):
             0. format staples   what that format's decks really played (Format Library / today's rankings),
                                 at most STAPLE_CAP of the deck
             A. broad support    cards that help 1/3+ of the deck (rare traits like Rock first)
             B. linked cards     cards that support, or are needed by, at least one deck card
             C. generic cards    plain filler, at most GENERIC_CAP of the deck (unless nothing else is left)
           A Generic deck (no theme) is all staples: it uses generic + broad cards together instead."""
        rated_pool = [c for c in pool if self.rating_ok(c)] or pool                   # generic filler obeys the rating limits
        generic_pool = [c for c in rated_pool if c.main_deck  # Main Deck cards...
                        and not self.normal_support_specific(c)  # ...that aren't Normal-only support...
                        and (c.clean or self.independent_generic(c) or self.loose_generic(c))] or rated_pool   # independent generic filler
        by_name = {c.name: c for c in pool}                 # card name -> card
        guard = 0                                           # safety counter so the loop always ends
        generic_cap = round(size * GENERIC_CAP)                                       # how many generic copies we allow
        generic_used = 0                                    # generic copies used so far
        staple_cap = round(size * STAPLE_CAP)                                         # how many staple copies we allow
        staple_used = 0                                     # staple copies used so far
        staple_pool = [c for c in rated_pool if c.main_deck and c.name in self.staples  # the format's staples...
                       and self.staples[c.name][0] >= STAPLE_MIN]                     # step 0's cards (empty: no deck data)
        linked: list = []                                   # linked cards (rebuilt now and then)
        linked_at = -99                                                               # deck size when `linked` was last built
        while len(deck.entries) < size and guard < 500:     # until the deck is full (500 tries at most)
            guard += 1                                      # count this try
            boost = self.broad_support(deck, [c for c in pool if c.helps])            # A: cards that help >= 1/3 of the deck
            broad_cards = [by_name[n] for n in boost if n in by_name]  # those cards
            if len(deck.entries) - linked_at >= 6:                                    # B: rebuild now and then (the deck keeps changing)
                linked = [c for c in rated_pool if c.main_deck and not c.clean and c.name not in boost  # cards linked to the deck (not clean, not already broad support)...
                          and not self.junk(c, deck) and self.synergy(c, deck) > 0]  # ...that aren't junk here
                linked_at = len(deck.entries)               # remember when we built it
            linked_ok = [c for c in linked if deck.counts.get(c.name, 0) < self.cap(c.name)]  # the linked cards with copies left
            tiers = [(broad_cards, "broad support (helps 1/3+ of the deck)"),  # the tiers: broad support...
                     (linked_ok, "linked to the deck (supports or is needed by a deck card)")]  # ...then linked cards
            if staple_used < staple_cap:                                              # 0: format staples go first
                # staples with copies left that aren't junk here
                ok = [c for c in staple_pool if deck.counts.get(c.name, 0) < self.cap(c.name) and not self.junk(c, deck)]
                tiers.insert(0, (ok, "format staple"))      # put them first
            if deck.generic_deck:                                                     # a Generic deck IS staples: keep the old mix (staples + broad support)
                tiers = [(generic_pool + broad_cards, "generic top-up")]  # (one mixed tier)
            elif generic_used < generic_cap:                                          # generics only up to the cap...
                tiers.append((generic_pool, "generic top-up"))  # generics last
            else:                                                                     # ...unless nothing else is left
                tiers.append((generic_pool, "generic top-up (over cap, nothing else left)"))  # generics last, over the cap
            card = None                                     # nothing picked yet
            for bucket, why in tiers:                                                 # first tier that can give a card wins
                if bucket:                                  # it has cards:
                    # pick one (broad support uses its boosts)
                    card, _ = self.pick(bucket, deck, pool, rng, boost=boost if (why.startswith("broad") or deck.generic_deck) else None,
                                        staples=True)                                # filler leans on the format's staples
                    if card is not None:                    # got one:
                        break
            if card is None:
                break                                                                 # nothing can be added any more
            room = min(self.cap(card.name) - deck.counts.get(card.name, 0), size - len(deck.entries))  # copies that fit (copy limit, slots left)
            n = rng.randint(1, room)                        # 1 to that many
            if card.name in self.staples:                                             # a staple: as many copies as decks ran
                if self.staples[card.name][1] > 0:                                    # (0 = unknown, today's formats: keep 1-3)
                    n = max(1, min(self.staples[card.name][1], room))  # the usual copy count
                share = f"in {round(self.staples[card.name][0] * 100)}% of this format's decks"  # how often it was played
                # the reason, with that share
                why = f"format staple ({share})" if why == "format staple" else f"{why} (also a format staple: {share})"
            if why.startswith("format staple"):                                       # its own origin: trimmed after plain filler
                deck.add(card, n, "staple", why)            # add it, marked 'staple'
                staple_used += n                            # count it
            elif why.startswith(("broad support", "linked to the deck")):  # broad support / linked card:
                # Automatic support belongs to Support 1, not generic filler.
                _, coverage, selected_label = self._support_detail(card, deck)  # which category it supports, and how much of the deck
                detail = (f"{selected_label} ({coverage:.0%} of deck)" if selected_label  # the reason
                          else "supports existing deck cards")  # (or a general one)
                deck.add(card, n, "support1", f"{why}: {detail}")  # add it as Support 1
            else:
                deck.add(card, n, "generic", why)           # plain filler, marked 'generic'
            if why.startswith("generic"):                   # a generic card:
                generic_used += n                           # count it toward the cap

    # ---- quality check ---------------------------------------------------
    def quality(self, deck: State, pool: list[Info]) -> tuple[int, int]:  # method: how many cards have their needs covered (self-test)
        """(cards with needs, how many of those have every HARD need and SOFT need covered)."""
        needy = ok = 0                                      # cards with needs / how many are covered
        for c in deck.unique.values():                      # each different card
            if not c.needs: continue                        # no needs: skip it
            needy += 1                                      # count it
            for branch in c.needs:                          # each OR-option
                traits = tuple(a for a in branch if not is_mechanic(a))  # its trait part
                mechs = [a for a in branch if is_mechanic(a)]  # its mechanic part
                # trait part met by another deck card?
                trait_ok = (not traits) or any(o.name != c.name and card_matches([traits], o.facts) for o in deck.unique.values())
                mech_ok = all(need_status(m, deck.offered, deck.traits, self.tree) != "unmet" for m in mechs)  # mechanic part not 'unmet'?
                if trait_ok and mech_ok:                    # both:
                    ok += 1                                 # count it as covered
                    break
        return needy, ok                                    # hand back the two counts


def baseline_deck(builder: Builder, req: Request, theme_label: str) -> State:  # function: a plain random deck (only the self-test compares against it)
    """The OLD way, for comparison: random cards from the theme pool, ignoring needs entirely."""
    rng = random.Random(req.seed)                           # its own generator, same seed
    pool = builder.allowed(req)                             # the card pool
    bucket = builder.theme_bucket(theme_label, pool) or pool  # the theme's cards (or every card)
    deck = State()                                          # an empty deck
    while len(deck.entries) < req.main_deck_size:           # until it is full
        c = rng.choice(bucket if rng.random() < 0.6 else pool)  # 60%: a theme card, 40%: any card
        # 1 to the copies that fit
        n = rng.randint(1, min(builder.cap(c.name) - deck.counts.get(c.name, 0), req.main_deck_size - len(deck.entries)) or 1)
        if deck.counts.get(c.name, 0) < builder.cap(c.name): deck.add(c, n, "baseline", "")  # add them if the limit allows
    return deck                                             # hand back the deck


# --------------------------------------------------------------------------
# Self-test:  python ygo_builder.py
# --------------------------------------------------------------------------
if __name__ == "__main__":                                  # only when this file is run directly
    here = Path(__file__).resolve().parent / "data"         # the data folder (program/data)
    builder = Builder(read_csv(here / "cards.csv"), load_tree(here / "game_mechanics_tree.csv"))  # load every card and the mechanic tree
    pool_all = builder.allowed(Request())                   # every Main Deck card
    print(f"{len(pool_all)} main-deckable cards\n")         # how many

    # One deck printed in full, so you can read it.
    req = Request(seed=7, deck_type_1_label="Archetype = snake-eye", support_1_label="Self = Special Summon")  # a Snake-Eye deck with Special Summon support
    deck, dbg = builder.build_main(req)                     # build it
    print("DEBUG:", dbg)                                    # print the debug info
    for origin in ("theme1", "theme2", "support1", "support2", "partner", "staple", "generic"):  # the deck grouped by why each card is in it
        names = [e.info.name for e in deck.entries if e.origin == origin]  # the names with this origin
        if names:                                           # any?
            print(f"\n[{origin}] {len(names)} cards")       # a heading with the count
            for n in dict.fromkeys(names): print(f"   {names.count(n)}x {n}")  # each card as '2x Name'
    needy, ok = builder.quality(deck, pool_all)             # how many cards have their needs covered
    print(f"\nneeds covered: {ok}/{needy}")                 # print it

    # Compare against the old random method on many seeds and themes.
    # five themes to test
    themes = ["Archetype = snake-eye", "Archetype = blackwing", "Type = Dragon", "Attribute = WATER", "Archetype = laval"]
    new_r, old_r = [], []                                   # the covered shares: new builder / plain random
    for th in themes:                                       # each theme
        for seed in range(5):                               # five seeds each
            r = Request(seed=seed, deck_type_1_label=th)    # the request
            n_need, n_ok = builder.quality(builder.build_main(r)[0], pool_all)  # a deck from the real builder
            o_need, o_ok = builder.quality(baseline_deck(builder, r, th), pool_all)  # a plain random deck
            if n_need: new_r.append(n_ok / n_need)          # its share
            if o_need: old_r.append(o_ok / o_need)          # its share
    print(f"\navg share of cards with needs covered over {len(new_r)} new decks: {sum(new_r)/len(new_r):.0%}")  # the averages
    print(f"avg share over {len(old_r)} old-style random decks:               {sum(old_r)/len(old_r):.0%}")  # (the plain random one)
