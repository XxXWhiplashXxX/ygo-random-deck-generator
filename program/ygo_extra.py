"""
ygo_extra.py  --  the Extra Deck, and the two build orders.

WHERE THIS FITS: ygo_final.build_deck() calls generate_state() here, which builds the main deck (with
ygo_builder.py) and the Extra Deck in the chosen order; ygo_final.py then finishes and checks the deck.

THE FILE, TOP TO BOTTOM
  1. Reading the `materials` column        parse_materials(): text -> Slots ('2 Tuners', '1 Level 4 Dragon' ...)
  2. Can these monsters summon it?          summon_score(): 1.0 = the main deck has every material (with a spare)
                                            (a Fusion substitute - King of the Swamp ... - stands in for ONE copy
                                            of ONE material named in quotes; never for Contact Fusions)
  3. ExtraBuilder                           pool() (allowed Extra Deck monsters), extra_for_main() (main-first),
                                            extra_first() (extra-first), _pick_extra() (the weighted choosing)
  4. generate_state()                       the whole deck, in either order

NAMES USED HERE
  builder        the Builder from ygo_builder.py (knows every card, the format, the copy limits)
  deck           the main deck being built (a State: its entries, counts, archetypes ...)
  req            the Request: every setting from the page (sizes, themes, seed, format ...)
  info / c       one card (an Info: its row from cards.csv plus facts like traits and archetypes)
  slots          one Extra Deck monster's materials, as Slots

THE BUILD ORDERS

  MAIN-FIRST : build the main deck, then choose Extra Deck monsters the main deck can SUMMON
               (their materials are in the main deck). If too few are summonable we fall back
               to the loosest ones (generic requirements), then to anything.
  EXTRA-FIRST: choose the Extra Deck from the theme, then place the main-deck cards those
               monsters need as SOFT slots (at most MATERIAL_CAP card copies), then build the
               rest of the main deck around them.
  RANDOM     : flip a coin between the two.

Materials come from the `materials` column, which looks like:
    Synchro:\\ncount = 1: (trait = Tuner)\\ncount = 1: (trait != Tuner)\\nsumlevel = 10
    Fusion:\\ncount = 1: ("Dragonmaid" monster)\\ncount = 1: (Level 5 or higher Dragon monster)
Each "count = N: (...)" line is a SLOT: "N cards that look like this".
"""
from __future__ import annotations                          # lets type hints name classes that are defined further down the file

import itertools                                        # product() builds every combination of OR-options
import random                                               # seeded dice: the same seed always rolls the same numbers
import re                                                   # regular expressions (text patterns)
from dataclasses import dataclass, replace          # replace: a copy of a Request with some fields changed
from typing import NamedTuple, Optional                # NamedTuple: a small fixed record; Optional[X] = "an X or None"
from pathlib import Path                                    # file paths (used by the self-test at the bottom)

# card data helpers from the loader
from ygo_data import parse_expr, parse_atom, card_matches, norm, norm_trait, Atom, read_csv, load_tree, is_mechanic
from ygo_builder import Builder, Request, State, Info, Entry  # the main-deck engine
from ygo_names import NameResolver                          # finds the real card for an old / misspelled name

MATERIAL_CAP = 10             # extra-first: most main-deck card copies reserved for materials (soft limit)
EXTRA_SPECIFIC_CAP = 4           # a Generic deck takes at most this many archetype-bound / card-naming Extra Deck monsters
FULL_SCORE = 0.999            # a summon score at/above this counts as "fully summonable"
EXTRA_TRAITS = {"fusion", "synchro", "xyz", "link"}     # traits that mean "lives in the Extra Deck"

# --------------------------------------------------------------------------
# 1. Reading the `materials` column
# --------------------------------------------------------------------------
# every monster Type, as written in material text
TYPES = ["aqua", "beast-warrior", "beast", "creator god", "cyberse", "dinosaur", "divine-beast", "dragon", "fairy",
         "fiend", "fish", "illusion", "insect", "machine", "plant", "psychic", "pyro", "reptile", "rock",  # (more Types)
         "sea serpent", "spellcaster", "thunder", "warrior", "winged beast", "wyrm", "zombie"]  # (more Types)
ATTRS = ["light", "dark", "earth", "water", "fire", "wind", "divine"]  # every Attribute
# longest type names first so "beast-warrior" is tried before "beast"
# matches any Type name (plus an optional '-Type')
TYPE_RX = re.compile(r"\b(" + "|".join(sorted((re.escape(t) for t in TYPES), key=len, reverse=True)) + r")\b(?:-type)?", re.I)
ATTR_RX = re.compile(r"\b(" + "|".join(ATTRS) + r")\b", re.I)   # re.I = ignore upper/lower case, so 'WATER' matches 'water'


class Mats(list):                                           # class: one card's materials (a list of Slots plus a few numbers)
    """The materials of one Extra Deck card: a list of Slots plus the numbers written in the column.
         sumlevel = total Level the Synchro materials must add up to      (Synchro only)
         link     = the Link Rating (Link only)
         sub_for  = the card's archetypes if a Fusion substitute may stand in for one of its NAMED materials
                    (a Fusion Summoned Fusion Monster with at least one named material); None = substitutes can't help"""
    sumlevel: Optional[int] = None                          # (Synchro only)
    link: Optional[int] = None                              # (Link only)
    sub_for: Optional[set] = None                           # (Fusions a substitute can help)


# Fusion substitutes (King of the Swamp, Goddess with the Third Eye ...): the same wording ygo_fusion_substitutes.py looks for
SUBSTITUTE = re.compile(r"substitute (this card )?for (any )?(1|one) Fusion[- ]Material"  # 'substitute for 1 Fusion Material' ...
                        r"|as a substitute for (any )?(1|one) (Fusion )?material", re.I)  # ...or 'as a substitute for 1 material'
# a substitute that only works for one archetype:  'if you Fusion Summon a "HERO" monster'  /  'listed on a "Frightfur" Fusion Monster'
SUB_ONLY_FOR = re.compile(r'(?:Fusion Summon an?|listed on an?) "([^"]+)"', re.I)  # the archetype a substitute is limited to
# Contact Fusions (Neos, Gladiator Beasts ...) are not Fusion Summoned: the real listed cards are shuffled / sent, so no substitutes
# text that marks a Contact Fusion
CONTACT = re.compile(r'do not use "Polymerization"|must (?:first )?be Special Summoned[^.]*?\bby (?:shuffling|returning|sending|banishing|Tributing)', re.I)
_SUB_LIMIT: dict = {}                                   # card name -> what substitute_limit() worked out (each card is read once)


def substitute_limit(m: Info):                              # function: is this monster a Fusion substitute, and for what?
    """False if this main-deck monster is not a Fusion substitute; otherwise the archetype it is limited to ('' = any Fusion)."""
    if m.name not in _SUB_LIMIT:                            # not worked out yet:
        text = m.row.get("card text") or ""                 # its text
        if m.kind != "Monster" or not m.main_deck or not SUBSTITUTE.search(text):  # not a Main Deck monster, or no substitute wording
            _SUB_LIMIT[m.name] = False                                             # not a substitute (Muddy Mudragon is an Extra Deck card)
        else:
            only = SUB_ONLY_FOR.search(text)                                       # 'HERO', 'Frightfur' ... or nothing
            _SUB_LIMIT[m.name] = norm_trait(only.group(1)) if only else ""  # the archetype it is limited to, or '' for any
    return _SUB_LIMIT[m.name]                               # hand back the saved answer


def named_slot(s: Slot) -> bool:                            # function: does this material slot ask for a card by name?
    """True if this slot asks for a card BY NAME (every OR-option names a card): the only kind a substitute can fill."""
    return bool(s.branches) and all(any(a.field == "name" and a.op == "=" for a in b) for b in s.branches)  # every OR-option has a 'name = ...' piece


class Candidate(NamedTuple):                                # class: one Extra Deck candidate and its numbers
    """One Extra Deck monster being considered, with the numbers used to weigh it."""
    score: float                                        # summon score: how well the main deck can summon it (1.0 = fully)
    cost: Optional[float]                               # how well the deck covers the card's own needs (None = not checked)
    on_theme: bool                                      # part of the deck's theme?
    card: "Info"                                        # the card itself


@dataclass                                                  # makes a simple data container
class Slot:                                                 # class: one material slot ('2 Tuners')
    need: int                                           # how many cards this slot wants (the minimum if plus is True)
    plus: bool = False                                  # 'count = 1+': the minimum is `need`, more are allowed
    branches: Optional[list] = None                     # trait OR-branches (as in step 1), or None = "any monster"
    extra_kind: bool = False                            # asks for Extra Deck monsters (Link Monster ...): main deck can't give those


def parse_spec(spec: str) -> Optional[list]:                # function: read one material description into OR-branches
    """Turn the text inside 'count = N: ( ... )' into OR-branches of trait atoms, or None if we can't read it."""
    spec = spec.strip()                                     # without spaces around it
    # (a) already structured:  name = "X"   level = 4 && type = dragon   trait != Tuner
    if re.search(r"\b(name|level|trait|type|attribute|archetype|atk|def|rank)\s*(=|!=|<=|>=)", spec, re.I):  # it already uses 'field = value' form:
        try:                                                # (try to read it)
            branches = parse_expr(spec.replace('"', ""))          # quotes are not part of the value
            # keep only the pieces we understand
            branches = [tuple(a for a in b if a.field in ("name", "level", "trait", "type", "attribute", "archetype", "atk", "def", "rank"))
                        for b in branches]                  # (for every branch)
            return [b for b in branches if b] or None       # drop empty branches; None if nothing is left
        except Exception:                                   # couldn't read it:
            return None                                     # (unknown)
    # (b) plain English: '"Dragonmaid" monster', 'Level 5 or higher Dragon monster', '2 Dragon and/or Winged Beast monsters'
    low = spec.lower()                                      # lower-case, for searching
    arche = [norm_trait(m) for m in re.findall(r'"([^"]+)"', spec)]                # quoted names -> archetypes
    types = [m.group(1).lower() for m in TYPE_RX.finditer(spec)]                   # Dragon, Winged Beast ...
    attrs = [m.lower() for m in ATTR_RX.findall(spec)]                             # WATER, DARK ...
    traits: list[Atom] = []                                                        # extra requirements joined by AND
    m = re.search(r"level\s+(\d+)\s+or\s+(higher|lower)", low)  # 'Level N or higher / lower'
    if m:                                                   # found:
        traits.append(Atom("level", ">=" if m.group(2) == "higher" else "<=", int(m.group(1))))  # a Level limit
    elif (m := re.search(r"level\s+(\d+)\b", low)):         # or just 'Level N'
        traits.append(Atom("level", "=", int(m.group(1))))  # an exact Level
    # words that mean a card kind / trait...
    for word, trait in (("non-tuner", None), ("tuner", "tuner"), ("link monster", "link"), ("synchro monster", "synchro"),
                        ("xyz monster", "xyz"), ("fusion monster", "fusion"), ("ritual monster", "ritual"),  # (more)
                        # (more)
                        ("pendulum monster", "pendulum"), ("normal monster", "normal"), ("effect monster", "effect")):
        if word in low:                                     # the word is in the text:
            if word == "non-tuner": traits.append(Atom("trait", "!=", "tuner"))  # 'non-tuner' = not a Tuner
            elif not (word == "tuner" and "non-tuner" in low): traits.append(Atom("trait", "=", trait))  # the trait (but not 'tuner' inside 'non-tuner')
    if not (arche or types or attrs or traits):
        return None                                                                # nothing recognised
    branches = []                                           # the OR-branches we build
    for a, t, at in itertools.product(arche or [None], types or [None], attrs or [None]):   # OR inside a category
        atoms = list(traits)                                                                  # AND across categories
        if a:  atoms.append(Atom("archetype", "=", a))      # its archetype
        if t:  atoms.append(Atom("type", "=", t))           # its Type
        if at: atoms.append(Atom("attribute", "=", at))     # its Attribute
        branches.append(tuple(atoms))                       # one branch
    return branches                                         # hand them back


def parse_materials(text: str) -> list[Slot]:               # function: read the whole materials cell
    """The whole `materials` cell -> list of Slots (one per 'count = N: (...)' line)."""
    slots = Mats()                                          # the Slots we build
    for line in (text or "").splitlines():                  # each line of the cell
        sm = re.match(r"\s*(sumlevel|link)\s*=\s*(\d+)", line)                     # 'sumlevel = 10' / 'link = 3'
        if sm:                                              # found one:
            setattr(slots, "sumlevel" if sm.group(1) == "sumlevel" else "link", int(sm.group(2)))  # save it on the Mats
            continue
        m = re.match(r"\s*count\s*=\s*(\d+)(\+?)\s*:\s*\((.*)\)\s*$", line)       # 'count = 2+: (...)'
        if not m:
            continue                                                               # header line ('Synchro:')
        need, plus, spec = int(m.group(1)), bool(m.group(2)), m.group(3)  # the numbers and the description
        branches = parse_spec(spec)                         # read the description
        extra_kind = bool(branches) and all(any(a.field == "trait" and a.op == "=" and a.value in EXTRA_TRAITS  # every option asks for Fusion / Synchro / Xyz / Link monsters
                                                for a in b) for b in branches)  # (looking at each option)
        slots.append(Slot(need, plus, branches, extra_kind))  # add the Slot
    return slots                                            # hand back the Slots


def fix_names(slots: list[Slot], resolver: NameResolver) -> list[Slot]:  # function: fix old / garbled card names inside material slots
    """Replace card names inside slots by the real card name when the data used an old or garbled one."""
    for s in slots:                                         # each slot
        if not s.branches:                                  # no options:
            continue
        new = []                                            # the corrected options
        for br in s.branches:                               # each option
            atoms = []                                      # its pieces
            for a in br:                                    # each piece
                if a.field == "name":                       # a card name:
                    real = resolver.find(str(a.value))      # find the real card
                    if real:                                # found it:
                        a = Atom("name", a.op, norm_trait(real))                 # same atom, corrected value
                atoms.append(a)                             # keep the piece
            new.append(tuple(atoms))                        # keep the option
        s.branches = new                                    # put the corrected options back
    return slots                                            # hand back the slots


# --------------------------------------------------------------------------
# 2. Can a set of monsters summon this Extra Deck card?
# --------------------------------------------------------------------------
def reachable_sums(levels: list[int], low: int, high: int) -> set:  # function: every Level total some cards can add up to
    """Every total Level you can make by choosing between `low` and `high` cards from `levels`
       (each entry is one card copy and can be used once). A small knapsack: sums[k] = totals using k cards."""
    sums = [set() for _ in range(high + 1)]                 # one set of totals per number of cards used
    sums[0].add(0)                                          # using 0 cards gives 0
    for lv in levels:                                                              # add the cards one at a time
        for k in range(high - 1, -1, -1):                                          # k downwards so a card is used once
            sums[k + 1] |= {s + lv for s in sums[k]}        # every total with k cards, plus this card
    out = set()                                             # the answer
    for k in range(low, high + 1):                          # only the allowed card counts
        out |= sums[k]                                      # add their totals
    return out                                              # hand back the totals


def sums_ok(slots: Mats, monsters: list[Info]) -> bool:     # function: can the Synchro's Levels add up?
    """Synchro rule: can we pick materials for every slot so their Levels add up to `sumlevel`?"""
    total = {0}                                                                    # totals reachable so far
    for s in slots:                                         # each slot
        cand = [m.level for m in monsters if s.branches is None or card_matches(s.branches, m.facts)]  # Levels of the monsters that fit it
        here = reachable_sums(cand, s.need, s.need + (3 if s.plus else 0))         # a '+' slot may take up to 3 more
        if not here:                                        # this slot can't be filled
            return False                                    # (no)
        total = {a + b for a in total for b in here}        # every total so far plus this slot's totals
    return slots.sumlevel in total                          # is the Synchro's Level among them?


def summon_score(slots: list[Slot], monsters: list[Info]) -> float:  # function: how well some monsters can summon an Extra Deck card (0-1)
    """Share of material slots the given monsters can fill (1.0 = fully summonable).
       `monsters` has one Info per COPY. Slots that ask for Extra Deck monsters count as filled
       (the Extra Deck itself can supply those). Unreadable slots accept any monster."""
    if not slots:                                           # no material info:
        return 0.5                                                                 # no information: middling
    met = 0                                                 # conditions met
    total = len(slots)                                      # conditions to meet
    sl = getattr(slots, "sumlevel", None)                   # the Synchro Level total (if any)
    if sl and not any(s.extra_kind for s in slots):                                # Synchro: the Levels must also add up
        total += 1                                          # one more condition
        met += 1 if sums_ok(slots, monsters) else 0         # met if the Levels can add up
    lk = getattr(slots, "link", None)                       # the Link Rating (if any)
    if lk and sum(s.need for s in slots) > lk:                                     # Link: more materials than the rating allows
        total += 1                                                                 # (counts as one unmet condition)
    haves = [sum(1 for m in monsters if card_matches(s.branches, m.facts)) if s.branches else 0 for s in slots]   # copies that fit each slot
    allowed = getattr(slots, "sub_for", None)                                      # can a Fusion substitute help this card?
    if allowed is not None and any(lim is not False and (lim == "" or lim in allowed)
                                   for lim in map(substitute_limit, monsters)):   # ...and the deck has one that works for it
        named = [i for i, s in enumerate(slots) if named_slot(s)]                  # the slots that ask for a card by name
        if named:                                                                  # the substitute stands in for ONE copy of ONE of them:
            best = min(named, key=lambda i: (haves[i] >= slots[i].need, haves[i] / (slots[i].need + 1)))   # a short slot first, else the least reliable
            haves[best] += 1                                # count one more copy for it
    weakest = 1.0                                                                  # least reliable slot (see below)
    for s, have in zip(slots, haves):                       # each slot and its copies
        if s.extra_kind:                                    # an Extra Deck slot:
            met += 1                                        # counts as filled
        elif s.branches is None:                            # 'any monster':
            met += 1 if len(monsters) >= s.need else 0      # filled if the deck has enough monsters
        else:
            met += 1 if have >= s.need else 0               # filled if enough copies fit
            # RELIABILITY: having exactly `need` copies of the right material means you must draw ALL of them.
            # Want at least one spare: need+1 copies is fully reliable, fewer scales down (one lone Tuner = 0.5).
            weakest = min(weakest, min(1.0, have / (s.need + 1)))  # the weakest slot's reliability
    return (met / total) * weakest                          # share filled x the weakest slot's reliability


# --------------------------------------------------------------------------
# 3. The two Extra Deck builders
# --------------------------------------------------------------------------
class ExtraBuilder:                                         # class: the Extra Deck engine
    def __init__(self, builder: Builder):                   # set up once, from the main-deck builder
        self.builder = builder                              # keep the builder
        resolver = NameResolver([i.name for i in builder.infos])                       # fixes old / garbled card names in `name = "..."`
        self.slots = {i.name: fix_names(parse_materials(i.row.get("materials", "")), resolver)  # card name -> its material slots
                      for i in builder.infos if not i.main_deck and i.kind == "Monster"}  # (for every Extra Deck monster)
        for i in builder.infos:                                                         # which Fusions a substitute can help:
            mats = self.slots.get(i.name)                   # its slots
            # a Fusion with a named material...
            if mats is not None and "fusion" in i.facts["trait"] and any(named_slot(s) for s in mats) \
               and not CONTACT.search(i.row.get("card text") or ""):                    # Fusion Summoned, with a named material
                mats.sub_for = set(i.facts["archetype"])                                # (an archetype-limited substitute checks these)

    def pool(self, req: Request) -> list[Info]:             # method: the Extra Deck monsters this deck may use
        """Extra Deck monsters allowed by the era/rating filters and the Fusion/Synchro/Xyz/Link switches."""
        percentages = dict(zip(("fusion", "synchro", "xyz", "link"), req.extra_type_pcts))  # kind -> its share
        want = {t for t, on in (("fusion", req.allow_fusion), ("synchro", req.allow_synchro),  # the kinds switched on with a share above 0
                                # (the four switches)
                                ("xyz", req.allow_xyz), ("link", req.allow_link)) if on and percentages[t] > 0}
        theme = self.builder.theme_archetypes(req)                                      # on-theme Extra Deck monsters ignore the rating limits
        return [i for i in self.builder.allowed(req, extra=True, rated=False) if i.facts["trait"] & want  # allowed Extra Deck monsters of those kinds...
                and (self.builder.rating_ok(i, req) or (i.facts["archetype"] & theme))]  # ...inside the rating limits, or of the theme's archetype

    @staticmethod                                           # doesn't need the ExtraBuilder itself
    def copies(rng: random.Random) -> int:                  # method: copies of one Extra Deck monster
        """Most Extra Deck monsters are played as 1-of."""
        return rng.choices([1, 2, 3], weights=[75, 20, 5])[0]  # 1 (75%), 2 (20%) or 3 (5%)

    def theme_match(self, label: Optional[str], info: Info) -> bool:  # method: is this Extra Deck card part of a theme label?
        """Is this Extra Deck card part of the theme label?"""
        if not label or norm(label) in ("random", "generic"):  # no real label:
            return False                                    # (no)
        atom = parse_atom(label)                            # the label split into field / operator / value
        if atom.field in ("self", "opponent"):              # a mechanic label:
            # it plus every mechanic counted as it
            want = self.builder.tree.get(f"{atom.field} {atom.op} {atom.value}", {f"{atom.field} {atom.op} {atom.value}"})
            return bool(info.offered & want)                # does the card perform any?
        return card_matches([(atom,)], info.facts)          # a trait label: does the card have it?

    # ---- MAIN-FIRST ------------------------------------------------------
    def extra_for_main(self, req: Request, deck: State, rng: random.Random, labels: list) -> list[Entry]:  # method: main-first - pick Extra Deck monsters the main deck can summon
        """Choose Extra Deck monsters that the finished main deck can summon."""
        pool = self.pool(req)                               # allowed Extra Deck monsters
        monsters = [e.info for e in deck.entries if e.info.kind == "Monster"]       # every monster copy in the main deck
        deck_pool = self.builder.allowed(req)               # allowed Main Deck cards (for the needs check)
        scored = []                                         # the candidates and their numbers
        for c in pool:                                      # each candidate
            sc = summon_score(self.slots[c.name], monsters)  # how well the main deck can summon it
            f, _ = self.builder.eval_needs(c, deck, deck_pool, scan_partners=False)                              # soft check of the card's own costs
            theme = any(self.theme_match(l, c) for l in labels)  # part of a theme?
            scored.append(Candidate(sc, f, theme, c))       # keep its numbers
        return self._pick_extra(scored, req.extra_deck_size, rng, "extra (main-first)", deck.arche,  # pick the Extra Deck from them
                                generic=deck.generic_deck, req=req)  # (generic deck flag, request)

    # method: choose the Extra Deck from scored candidates
    # (its inputs, continued)
    def _pick_extra(self, scored: list[Candidate], size: int, rng: random.Random, why: str, arche: set = frozenset(), generic: bool = False, req: Optional[Request] = None) -> list[Entry]:
        """Two phases, like a real Extra Deck (theme cards first, a few staples after):
             1. ON-THEME cards, weighted by summon score. (Theme cards are used up before anything else.)
             2. Everything else, weighted by score^3 x cost factor, with cards of a DIFFERENT archetype
                made much less likely. When every fully-summonable card is gone, partial ones take over:
                that is the 'fall back to loose / generic' behaviour."""
        out: list[Entry] = []                               # the Extra Deck we build
        live = list(scored)                                 # candidates still available
        if generic:                                                                  # Generic deck: no extra that needs named cards
            # drop ones with required cards or named materials
            live = [x for x in live if not ((x.card.row.get("required cards") or "").strip() or '"' in (x.card.row.get("materials") or ""))]
        # Target counts for each Extra Deck monster kind. Allocate rounding fairly.
        kinds = ("fusion", "synchro", "xyz", "link")        # the four Extra Deck kinds
        percentages = dict(zip(kinds, req.extra_type_pcts if req else (25, 25, 25, 25)))  # kind -> its share
        enabled = [k for k in kinds if percentages[k] > 0]  # kinds with a share above 0
        total = sum(percentages[k] for k in enabled)        # their shares added up
        raw = {k: size * percentages[k] / total for k in enabled} if total else {}  # each kind's exact share of the Extra Deck size
        quotas = {k: int(raw[k]) for k in enabled}          # rounded down
        # hand the leftover slots to the kinds that lost most to rounding
        for k in sorted(enabled, key=lambda k: raw[k] - quotas[k], reverse=True)[:size - sum(quotas.values())]:
            quotas[k] += 1                                  # (one each)
        picked = {k: 0 for k in kinds}                      # copies picked of each kind
        # A theme/generic ratio of 50:20 becomes ~71% themed Extra cards.
        # Support has no dedicated Extra Deck category, so it is not part of this ratio.
        theme_target = None                                 # how many theme cards the Extra Deck wants (None = no target)
        if req and req.extra_match_main_ratio:              # match the main deck's theme/generic split:
            numerator = req.deck_type_overall_pct           # the theme share
            denominator = numerator + req.generic_overall_pct  # theme + generic shares
            theme_target = round(size * numerator / denominator) if denominator else 0  # the theme part of the Extra Deck
        theme_taken = 0                                     # theme copies picked so far
        loose_taken = 0                                                              # Generic deck: archetype-bound extras picked so far

        def kind_of(candidate: Candidate) -> str:           # inner function: which kind a candidate is
            return next((k for k in kinds if k in candidate.card.facts["trait"]), "")  # the first kind in its traits ('' if none)

        def available(idxs: list[int]) -> list[int]:        # inner function: prefer candidates whose kind still needs cards
            """Respect percentage targets if a fitting candidate exists; otherwise fall back."""
            within = [i for i in idxs if picked[kind_of(live[i])] < quotas.get(kind_of(live[i]), 0)]  # candidates whose kind is still under its target
            return within or idxs                           # those, or all of them if none

        def take(idx: int, tag: str) -> None:               # inner function: add one candidate to the Extra Deck
            """Add a weighted card without overrunning an unfilled type quota."""
            nonlocal loose_taken, theme_taken               # these belong to _pick_extra; we change them here
            sc, f, th, c = live[idx]                        # its numbers and the card
            kind = kind_of(live[idx])                       # its kind
            if generic and not c.clean:                     # a specific card in a Generic deck:
                loose_taken += 1                            # count it toward the cap
            n = min(self.copies(rng), self.builder.cap(c.name), size - len(out))  # 1-3 copies (never past its limit or the Extra Deck size)
            left = quotas.get(kind, 0) - picked[kind]       # its kind's slots still open
            if left > 0:                                    # some open:
                n = min(n, left)                            # don't overrun them
            for _ in range(n):                              # add that many copies
                # (with the reason and score)
                out.append(Entry(c, "extra", f"{why}: summon score {sc:.2f}" + (", on-theme" if th else "") + tag))
            picked[kind] += n                               # count them for its kind
            if th:                                          # a theme card:
                theme_taken += n                            # count it
            live.pop(idx)                                   # it can't be picked again

        while len(out) < size:                                                       # phase 1: theme cards
            idxs = [i for i, x in enumerate(live) if x.on_theme and x.score > 0]  # theme candidates the deck can summon at all
            if theme_target is not None and theme_taken >= theme_target:  # the theme target is reached:
                break
            if not idxs: break                              # none left
            idxs = available(idxs)                          # prefer kinds still under their target
            near = {c.name for c in self.builder.rating_tier([live[i].card for i in idxs])}   # nearest the rating limits first
            idxs = [i for i in idxs if live[i].card.name in near]  # keep only those
            weights = [max(live[i].score, 0.02) ** 3 for i in idxs]  # summon score cubed: summonable cards are much likelier
            take(rng.choices(idxs, weights=weights)[0], "")  # pick one and add it
        while len(out) < size and live:                                              # phase 2: the rest
            idxs = available(list(range(len(live))))        # every candidate left (kinds under target first)
            if theme_target is not None:                    # there is a theme target:
                remaining_theme = theme_target - theme_taken  # theme copies still wanted
                preferred = [i for i in idxs if live[i].on_theme == (remaining_theme > 0)]  # theme cards while some are wanted, otherwise the others
                if preferred:                               # any:
                    idxs = preferred                        # use only those
            weights = []                                    # one weight per candidate
            for i in idxs:                                  # each candidate
                sc, f, th, c = live[i]                                                 # (score, cost, on_theme, card)
                a = c.facts["archetype"]                    # its archetypes
                fit = 1.0 if (not a or not arche or (a & arche)) else 0.15           # off-archetype extras are rare picks
                if generic:
                    fit = 1.0 if c.clean else (0.2 if loose_taken < EXTRA_SPECIFIC_CAP else 0.0)   # staples first; specific extras capped
                weights.append(max(sc, 0.02) ** 3 * fit * (max(f, 0.1) if f is not None else 1.0))  # score cubed x fit x how well its own needs are met
            if not any(w > 0 for w in weights):                                      # only capped cards left (Generic deck): stop short
                break
            take(rng.choices(idxs, weights=weights)[0], "")  # pick one and add it
        return out                                          # hand back the Extra Deck

    # ---- EXTRA-FIRST -----------------------------------------------------
    def extra_first(self, req: Request, labels: list, rng: random.Random) -> tuple[list[Entry], list]:  # method: extra-first - pick the Extra Deck from the theme, then its materials
        """Choose the Extra Deck from the theme (no main deck yet) and return it with the material cards it wants
           as (Info, copies, reason) triples for Builder.build_main(pre=...)."""
        pool = self.pool(req)                               # allowed Extra Deck monsters
        scored = []                                         # the candidates
        for c in pool:                                      # each candidate
            theme = any(self.theme_match(l, c) for l in labels)  # part of a theme?
            scored.append(Candidate(1.0, None, theme, c))                           # no main deck yet: everyone scores 1.0
        prefer = set()                                      # names of the theme's cards
        theme_arche = set()                                 # the theme's archetypes
        theme_cards: list[Info] = []                                                # the theme's own cards (for material_ok below)
        for l in labels:                                                            # names that count as "on theme"
            if l and norm(l) not in ("random", "generic"):  # a real theme label:
                bucket = self.builder.theme_bucket(l, self.builder.allowed(req))  # its cards
                prefer |= {c.name for c in bucket}          # note their names
                theme_cards += bucket                       # and keep them
                for c in bucket: theme_arche |= c.facts["archetype"]               # archetypes that belong in this deck
        extra = self._pick_extra(scored, req.extra_deck_size, rng, "extra (extra-first)", theme_arche,  # pick the Extra Deck
                                  generic=all(norm(l or "") in ("generic", "") for l in labels), req=req)  # (Generic deck when every label is Generic / blank)
        main_pool = [i for i in self.builder.allowed(req) if i.kind == "Monster"]  # Main Deck monsters (possible materials)
        reserved: dict = {}                                                         # name -> copies already reserved
        placed: list[Info] = []                                                     # one Info per reserved copy
        used = 0                                            # copies reserved so far
        order = list(dict.fromkeys(e.info.name for e in extra))  # the Extra Deck's names, each once
        rng.shuffle(order)                                                          # no extra monster gets first pick every time
        by_name = {e.info.name: e.info for e in extra}      # name -> card
        friends = theme_cards + list(by_name.values())                              # cards that will surely be in this deck
        ok_cache: dict = {}                                                         # card name -> material_ok answer

        def material_ok(c: Info) -> bool:                   # inner function: will this material card work in the deck?
            """Will this material card WORK in the deck? There is no main deck yet, so we judge from what will surely be in it:
               True if it has no needs, or one of its strict needs' trait part is met by a theme card or a chosen Extra Deck
               monster. Cards with a 'required cards' entry are left out (the card they require may never come)."""
            if c.name not in ok_cache:                      # not worked out yet:
                if (c.row.get("required cards") or "").strip():  # it lists required cards:
                    ok_cache[c.name] = False                # (no)
                elif not c.needs:
                    ok_cache[c.name] = True                                         # needs nothing
                else:
                    # the trait part of each strict-need option
                    branches = [tuple(a for a in br if not is_mechanic(a)) for br in parse_expr(c.row.get("strictSupportedBy", ""))]
                    ok_cache[c.name] = (not branches) or any(  # no trait part, or a theme card / chosen Extra Deck monster meets one
                        # (looking at each option)
                        not t or any(f.name != c.name and card_matches([t], f.facts) for f in friends) for t in branches)
            return ok_cache[c.name]                         # hand back the saved answer

        for name in order:                                  # each Extra Deck monster
            for slot in self.slots[name]:                   # each of its material slots
                if used >= MATERIAL_CAP: break              # the material budget is used up
                if slot.extra_kind or slot.branches is None: continue               # nothing concrete to place
                if sum(1 for m in placed if card_matches(slot.branches, m.facts)) >= slot.need:  # copies already reserved that fit this slot
                    continue                                                        # already covered by cards we reserved
                options = [c for c in main_pool if card_matches(slot.branches, c.facts)  # Main Deck monsters that fit the slot...
                           and reserved.get(c.name, 0) < self.builder.cap(c.name)  # ...with copies left to reserve
                           and self.builder.normal_ok(c)]                           # Normal Monsters toggle
                if not options: continue                    # nothing fits
                options = [c for c in options if material_ok(c)] or options             # Step 2: materials that will work (if any do)
                weights = [self.builder.level_bias(c) * (6.0 if c.name in prefer else 1.0) for c in options]  # weight: Level, and 6x for the theme's own cards
                pick = rng.choices(options, weights=weights)[0]  # pick one
                # as many as the slot needs (never past its limit or the budget)
                n = min(slot.need, self.builder.cap(pick.name) - reserved.get(pick.name, 0), MATERIAL_CAP - used)
                reserved[pick.name] = reserved.get(pick.name, 0) + n  # reserve them
                placed += [pick] * n                        # one Info per copy
                used += n                                   # count them
        unique = {c.name: c for c in placed}                                        # one Info per name (Info itself is not hashable)
        pre = [(c, reserved[n], "material for the Extra Deck") for n, c in unique.items()]  # (card, copies, reason) for each reserved card
        return extra, pre                                   # hand back the Extra Deck and the materials to place


# --------------------------------------------------------------------------
# 4. One call that builds a whole deck in either order
# --------------------------------------------------------------------------
def anchor_materials(builder: Builder, extra_builder: ExtraBuilder, anchor: Info, req: Request) -> list[Info]:  # function: Main Deck monsters that can be materials for an Extra Deck card-box card
    """Main Deck monsters that can be materials for an Extra Deck anchor (read from its materials line).
       A slot that takes 'any monster' or another Extra Deck monster adds nothing here."""
    pool = [c for c in builder.allowed(req, rated=False) if c.kind == "Monster"]   # Main Deck monsters this format allows
    found: dict = {}                                        # card name -> card
    for slot in extra_builder.slots.get(anchor.name, []):   # each of its material slots
        if slot.extra_kind or slot.branches is None:        # no concrete requirement:
            continue                                                                # nothing concrete to look for
        for c in pool:                                      # each Main Deck monster
            if c.name != anchor.name and card_matches(slot.branches, c.facts):  # it fits the slot (and isn't the card itself)
                found[c.name] = c                           # keep it
    return list(found.values())                             # hand back the materials


def card_labels(card: Info) -> list:                        # function: what 'on theme' means for a card-box card
    """What counts as 'on theme' for the Extra Deck when a theme slot is a card: its archetype(s), or its Type if it has none."""
    own = sorted(card.facts["archetype"] - {norm(card.name)})  # its archetypes (not counting its own name)
    if own:                                                 # it has some:
        return [f"Archetype = {a}" for a in own]            # one archetype label each
    return [f"Type = {next(iter(card.facts['type']))}"] if card.facts["type"] else []  # otherwise its Type (or nothing)


def pick_random_cards(builder: Builder, req: Request, master: random.Random) -> tuple[Request, dict]:  # function: roll the 'Random card' boxes
    """'Random card' tick boxes: pick Card 1 / Card 2 with the deck's seeded generator (same seed -> same card), and return
       the Request with the picked names filled in, plus {'anchor_card': name, 'anchor_card_2': name} for the page to show.
       A ticked box always rolls, even with a 0% theme share (then the card is just in at 3 copies). The two never match."""
    picked = {}                                             # box -> the name picked
    if req.rand_card_1:                                     # Card 1 is Random:
        picked["anchor_card"] = builder.random_card(req, master, {req.anchor_card_2 or ""})  # pick one (not the Card 2 name)
    if req.rand_card_2:                                     # Card 2 is Random:
        # pick one (not Card 1)
        picked["anchor_card_2"] = builder.random_card(req, master, {picked.get("anchor_card", req.anchor_card or "")})
    return replace(req, **picked), picked                   # the request with the names filled in, and the picks


def generate_state(builder: Builder, extra_builder: ExtraBuilder, req: Request):  # function: build the first draft of the whole deck
    """Returns (main State, extra_entries, debug)."""
    master = random.Random(req.seed)                                                # ONE generator drives every choice
    req, picked = pick_random_cards(builder, req, master)                           # 'Random card' boxes (seeded)
    cards = builder.anchor_cards(req)                                               # Card 1 / Card 2 (None = that theme is normal)
    using_cards = any(cards.values())                       # is a card box in use?
    # A card slot is always built main-first: the Main Deck is built around the card, then the Extra Deck fits it.
    if using_cards:                                         # a card box:
        order = "main-first"                                # always main-first
    else:
        # the chosen order (Random flips a coin)
        order = req.build_order if req.build_order in ("main-first", "extra-first") else master.choice(["main-first", "extra-first"])
    pool = builder.allowed(req)                             # allowed Main Deck cards
    # Resolve "Random" themes to a real archetype once, so both halves of the deck agree on the theme.
    def resolve(lbl):                                                               # None / "Random" -> a real archetype
        return builder.random_theme(pool, master, req) if (lbl is None or norm(lbl) == "random") else lbl  # pick a real theme for None / Random
    second = req.deck_type_2_pct > 0 and req.deck_type_1_pct < 100                  # does Theme 2 get any slots?
    # labels = the theme labels; a card slot has none (shown as None), its card stands in for it
    labels = [None if cards["theme1"] else resolve(req.deck_type_1_label)]  # Theme 1's label (None when Card 1 is used)
    if second or cards["theme2"]:                                                   # a second theme only if it gets slots (or holds a card)
        labels.append(None if cards["theme2"] else (resolve(req.deck_type_2_label) if second else None))  # Theme 2's label, the same way
    real = [l for l in labels if l and norm(l) not in ("random", "generic")]       # the real theme labels
    # the theme's own cards: the Normal Monsters toggle never removes these (Dark Magician in a Dark Magician deck)
    theme_pool = builder.allowed(req, rated=False)                                  # (theme cards ignore the rating limits)
    builder.theme_names = {c.name for l in real for c in builder.theme_bucket(l, theme_pool)}  # every card of the real themes
    builder.anchor_names = set()                                                    # the card-box cards: ygo_final never swaps these out
    extra_labels = list(real)                                                       # 'on theme' for the Extra Deck picks
    for card in (c for c in cards.values() if c is not None):  # each card box in use
        own = card.facts["archetype"] - {norm(card.name)}   # its archetypes (not counting its own name)
        builder.theme_names |= {card.name} | {c.name for c in theme_pool if c.facts["archetype"] & own}  # it and its archetype count as the theme's own
        # it and its alias names are never swapped out
        builder.anchor_names |= {card.name} | {n.strip() for n in (card.row.get("alias") or "").split("||") if n.strip()}
        extra_labels += card_labels(card)                   # its archetype / Type counts as on theme for the Extra Deck
    # the request for the main deck: the resolved themes...
    main_req = Request(**{**req.__dict__, "deck_type_1_label": labels[0] if labels[0] else req.deck_type_1_label,
                          # (Theme 2)
                          "deck_type_2_label": labels[1] if len(labels) > 1 and labels[1] else req.deck_type_2_label,
                          "seed": master.getrandbits(32)})  # ...and its own seed
    rng_extra = random.Random(master.getrandbits(32))       # the Extra Deck's own generator
    debug = {"order": order, "themes": labels}              # the debug info
    if picked:                                              # 'Random card' picked something:
        debug["random_cards"] = picked                                              # web/app.py shows these in the card boxes
    if order == "main-first":                               # main-first:
        mats = {o: anchor_materials(builder, extra_builder, c, req)                 # Extra Deck card: its Main Deck materials
                for o, c in cards.items() if c is not None and not c.main_deck}  # (for each Extra Deck card box)
        deck, d = builder.build_main(main_req, materials=mats)  # build the main deck
        extra_req = replace(req, extra_match_main_ratio=False) if using_cards else req   # (a card slot has no theme ratio)
        extra = extra_builder.extra_for_main(extra_req, deck, rng_extra, extra_labels)  # then the Extra Deck it can summon
    else:
        extra, pre = extra_builder.extra_first(req, labels, rng_extra)  # extra-first: the Extra Deck and its materials first
        deck, d = builder.build_main(main_req, pre=pre)     # then the main deck around those materials
        debug["materials_reserved"] = sum(n for _, n, _ in pre)  # note how many material copies were reserved
    debug.update(d)                                         # add the main deck's debug info
    # Extra Deck card-box cards go in the Extra Deck at 3 copies (or the format's limit) and are never pruned.
    forced = []                                             # Extra Deck cards that must be in
    for origin, card in cards.items():                      # each card box
        if card is not None and not card.main_deck:         # an Extra Deck card:
            n = builder.anchor_copies(card)                 # 3, or the format's limit
            who = "Card 1" if origin == "theme1" else "Card 2"  # which box
            # add its copies
            forced += [Entry(card, "required", f"{who}: the card this deck is built around ({n} copies)") for _ in range(n)]
    # Extra Deck wincons (Number 88 ...) must be in the deck too. A dict keyed by name keeps one Info per name
    # (Info is not hashable, so a set can't be used).
    wins = list({c.name: c for l in real for c in builder.wincon_extras(l)}.values())  # the theme's Extra Deck win-condition cards
    if wins:                                                # any:
        debug["extra_wincon"] = [c.name for c in wins]      # note them
    forced += [Entry(c, "required", f"Extra Deck wincon: {c.name}") for c in wins]   # 'required' = never pruned
    forced = forced[:req.extra_deck_size]                                           # never more than the Extra Deck size (0 = none)
    if forced:                                              # any must-have cards:
        forced_names = {e.info.name for e in forced}                                # built once, not once per card
        keep = [e for e in extra if e.info.name not in forced_names]               # the rest of the Extra Deck, minus the forced ones
        extra = forced + keep[:max(0, req.extra_deck_size - len(forced))]  # must-haves first, then the rest up to the size
    return deck, extra, debug                               # hand back (main deck, Extra Deck, debug info)


def generate(builder: Builder, extra_builder: ExtraBuilder, req: Request):  # function: like generate_state, with the main deck as a plain list
    """Same as generate_state but returns the main deck as a plain list of entries."""
    deck, extra, debug = generate_state(builder, extra_builder, req)  # build it
    return deck.entries, extra, debug                       # (main list, Extra Deck, debug info)


# --------------------------------------------------------------------------
# Self-test:  python ygo_extra.py
# --------------------------------------------------------------------------
if __name__ == "__main__":                                  # only when this file is run directly
    here = Path(__file__).resolve().parent / "data"         # the data folder (program/data)
    builder = Builder(read_csv(here / "cards.csv"), load_tree(here / "game_mechanics_tree.csv"))  # load every card and the mechanic tree
    extra_builder = ExtraBuilder(builder)                   # the Extra Deck engine

    # (1) How well do we read materials?
    total = readable = 0                                    # slots in total / slots we could read
    for _name, slots in extra_builder.slots.items():        # every Extra Deck monster
        for s in slots:                                     # each of its slots
            total += 1                                      # count it
            readable += s.branches is not None or s.extra_kind  # count it if we read it
    # the summary
    print(f"{len(extra_builder.slots)} Extra Deck monsters, {total} material slots, {readable} readable ({readable/total:.0%}); the rest accept any monster")
    by = {i.name: i for i in builder.infos}                 # name -> card
    # four tricky cards
    for n in ("Dragonmaid Sheou", "Dragocytos Corrupted Nethersoul Dragon", "Galaxy Stealth Dragon", "Dragunity Knight - Romulus"):
        # print how we read each one's materials
        print(" ", n, [(s.need, [[f"{a.field}{a.op}{a.value}" for a in br] for br in s.branches] if s.branches else None, s.extra_kind) for s in extra_builder.slots[n]])

    # (2) Build the same theme in both orders and show the result.
    for order in ("main-first", "extra-first"):             # both orders
        # a Blackwing deck
        req = Request(seed=11, deck_type_1_label="Archetype = blackwing", support_1_label="Random", build_order=order)
        main, extra, dbg = generate(builder, extra_builder, req)  # build it
        # the heading
        print(f"\n===== {order}: {len(main)} main, {len(extra)} extra | reserved materials: {dbg.get('materials_reserved', '-')}")
        # the main deck as '2x Name'
        print("main  :", ", ".join(f"{sum(1 for e in main if e.info.name == n)}x {n}" for n in dict.fromkeys(e.info.name for e in main))[:700])
        print("extra :")                                    # the Extra Deck:
        for n in dict.fromkeys(e.info.name for e in extra):  # each different card
            e = next(x for x in extra if x.info.name == n)  # its first copy
            print(f"   {sum(1 for x in extra if x.info.name == n)}x {n}  [{e.reason.split(': ', 1)[1]}]")  # '1x Name [why]'
        monsters = [e.info for e in main if e.info.kind == "Monster"]  # the main deck's monsters
        # how many it can fully summon
        full = sum(1 for n in dict.fromkeys(e.info.name for e in extra) if summon_score(extra_builder.slots[n], monsters) >= FULL_SCORE)
        print(f"fully summonable Extra Deck cards: {full}/{len(set(e.info.name for e in extra))}")  # print it

    # (3) Many decks: share of Extra Deck cards that the main deck can fully summon, new vs random.
    # five themes
    themes = ["Archetype = blackwing", "Archetype = laval", "Type = Dragon", "Attribute = WATER", "Archetype = snake-eye"]
    res = {"main-first": [], "extra-first": [], "random extra": []}  # the share for each method
    for th in themes:                                       # each theme
        for seed in range(4):                               # four seeds
            for order in ("main-first", "extra-first"):     # both orders
                # build a deck
                main, extra, _ = generate(builder, extra_builder, Request(seed=seed, deck_type_1_label=th, build_order=order))
                mons = [e.info for e in main if e.info.kind == "Monster"]  # its monsters
                names = list(dict.fromkeys(e.info.name for e in extra))  # the Extra Deck's names
                # share of them fully summonable
                res[order].append(sum(summon_score(extra_builder.slots[n], mons) >= FULL_SCORE for n in names) / max(1, len(names)))
            rr = random.Random(seed); rp = rr.sample(extra_builder.pool(Request()), 15)         # old style: any Extra Deck cards at all
            res["random extra"].append(sum(summon_score(extra_builder.slots[c.name], mons) >= FULL_SCORE for c in rp) / 15)
    for k, v in res.items():                                # each method
        print(f"{k:14s} avg fully-summonable share: {sum(v)/len(v):.0%}")  # its average
