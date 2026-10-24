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
  req            the Request: every setting from the window (sizes, themes, seed, format ...)
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
from __future__ import annotations

import itertools                                        # product() builds every combination of OR-options
import random
import re
from dataclasses import dataclass
from typing import NamedTuple
from pathlib import Path
from typing import Optional

from ygo_data import parse_expr, parse_atom, card_matches, norm, norm_trait, Atom, read_csv, load_tree, is_mechanic
from ygo_builder import Builder, Request, State, Info, Entry
from ygo_names import NameResolver

MATERIAL_CAP = 10             # extra-first: most main-deck card copies reserved for materials (soft limit)
EXTRA_SPECIFIC_CAP = 4           # a Generic deck takes at most this many archetype-bound / card-naming Extra Deck monsters
FULL_SCORE = 0.999            # a summon score at/above this counts as "fully summonable"
EXTRA_TRAITS = {"fusion", "synchro", "xyz", "link"}     # traits that mean "lives in the Extra Deck"

# --------------------------------------------------------------------------
# 1. Reading the `materials` column
# --------------------------------------------------------------------------
TYPES = ["aqua", "beast-warrior", "beast", "creator god", "cyberse", "dinosaur", "divine-beast", "dragon", "fairy",
         "fiend", "fish", "illusion", "insect", "machine", "plant", "psychic", "pyro", "reptile", "rock",
         "sea serpent", "spellcaster", "thunder", "warrior", "winged beast", "wyrm", "zombie"]
ATTRS = ["light", "dark", "earth", "water", "fire", "wind", "divine"]
# longest type names first so "beast-warrior" is tried before "beast"
TYPE_RX = re.compile(r"\b(" + "|".join(sorted((re.escape(t) for t in TYPES), key=len, reverse=True)) + r")\b(?:-type)?", re.I)
ATTR_RX = re.compile(r"\b(" + "|".join(ATTRS) + r")\b", re.I)   # re.I = ignore upper/lower case, so 'WATER' matches 'water'


class Mats(list):
    """The materials of one Extra Deck card: a list of Slots plus the numbers written in the column.
         sumlevel = total Level the Synchro materials must add up to      (Synchro only)
         link     = the Link Rating (Link only)
         sub_for  = the card's archetypes if a Fusion substitute may stand in for one of its NAMED materials
                    (a Fusion Summoned Fusion Monster with at least one named material); None = substitutes can't help"""
    sumlevel: Optional[int] = None
    link: Optional[int] = None
    sub_for: Optional[set] = None


# Fusion substitutes (King of the Swamp, Goddess with the Third Eye ...): the same wording ygo_fusion_substitutes.py looks for
SUBSTITUTE = re.compile(r"substitute (this card )?for (any )?(1|one) Fusion[- ]Material"
                        r"|as a substitute for (any )?(1|one) (Fusion )?material", re.I)
# a substitute that only works for one archetype:  'if you Fusion Summon a "HERO" monster'  /  'listed on a "Frightfur" Fusion Monster'
SUB_ONLY_FOR = re.compile(r'(?:Fusion Summon an?|listed on an?) "([^"]+)"', re.I)
# Contact Fusions (Neos, Gladiator Beasts ...) are not Fusion Summoned: the real listed cards are shuffled / sent, so no substitutes
CONTACT = re.compile(r'do not use "Polymerization"|must (?:first )?be Special Summoned[^.]*?\bby (?:shuffling|returning|sending|banishing|Tributing)', re.I)
_SUB_LIMIT: dict = {}                                   # card name -> what substitute_limit() worked out (each card is read once)


def substitute_limit(m: Info):
    """False if this main-deck monster is not a Fusion substitute; otherwise the archetype it is limited to ('' = any Fusion)."""
    if m.name not in _SUB_LIMIT:
        text = m.row.get("card text") or ""
        if m.kind != "Monster" or not m.main_deck or not SUBSTITUTE.search(text):
            _SUB_LIMIT[m.name] = False                                             # not a substitute (Muddy Mudragon is an Extra Deck card)
        else:
            only = SUB_ONLY_FOR.search(text)                                       # 'HERO', 'Frightfur' ... or nothing
            _SUB_LIMIT[m.name] = norm_trait(only.group(1)) if only else ""
    return _SUB_LIMIT[m.name]


def named_slot(s: Slot) -> bool:
    """True if this slot asks for a card BY NAME (every OR-option names a card): the only kind a substitute can fill."""
    return bool(s.branches) and all(any(a.field == "name" and a.op == "=" for a in b) for b in s.branches)


class Candidate(NamedTuple):
    """One Extra Deck monster being considered, with the numbers used to weigh it."""
    score: float                                        # summon score: how well the main deck can summon it (1.0 = fully)
    cost: Optional[float]                               # how well the deck covers the card's own needs (None = not checked)
    on_theme: bool                                      # part of the deck's theme?
    card: "Info"                                        # the card itself


@dataclass
class Slot:
    need: int                                           # how many cards this slot wants (the minimum if plus is True)
    plus: bool = False                                  # 'count = 1+': the minimum is `need`, more are allowed
    branches: Optional[list] = None                     # trait OR-branches (as in step 1), or None = "any monster"
    extra_kind: bool = False                            # asks for Extra Deck monsters (Link Monster ...): main deck can't give those


def parse_spec(spec: str) -> Optional[list]:
    """Turn the text inside 'count = N: ( ... )' into OR-branches of trait atoms, or None if we can't read it."""
    spec = spec.strip()
    # (a) already structured:  name = "X"   level = 4 && type = dragon   trait != Tuner
    if re.search(r"\b(name|level|trait|type|attribute|archetype|atk|def|rank)\s*(=|!=|<=|>=)", spec, re.I):
        try:
            branches = parse_expr(spec.replace('"', ""))          # quotes are not part of the value
            branches = [tuple(a for a in b if a.field in ("name", "level", "trait", "type", "attribute", "archetype", "atk", "def", "rank"))
                        for b in branches]
            return [b for b in branches if b] or None
        except Exception:
            return None
    # (b) plain English: '"Dragonmaid" monster', 'Level 5 or higher Dragon monster', '2 Dragon and/or Winged Beast monsters'
    low = spec.lower()
    arche = [norm_trait(m) for m in re.findall(r'"([^"]+)"', spec)]                # quoted names -> archetypes
    types = [m.group(1).lower() for m in TYPE_RX.finditer(spec)]                   # Dragon, Winged Beast ...
    attrs = [m.lower() for m in ATTR_RX.findall(spec)]                             # WATER, DARK ...
    traits: list[Atom] = []                                                        # extra requirements joined by AND
    m = re.search(r"level\s+(\d+)\s+or\s+(higher|lower)", low)
    if m:
        traits.append(Atom("level", ">=" if m.group(2) == "higher" else "<=", int(m.group(1))))
    elif (m := re.search(r"level\s+(\d+)\b", low)):
        traits.append(Atom("level", "=", int(m.group(1))))
    for word, trait in (("non-tuner", None), ("tuner", "tuner"), ("link monster", "link"), ("synchro monster", "synchro"),
                        ("xyz monster", "xyz"), ("fusion monster", "fusion"), ("ritual monster", "ritual"),
                        ("pendulum monster", "pendulum"), ("normal monster", "normal"), ("effect monster", "effect")):
        if word in low:
            if word == "non-tuner": traits.append(Atom("trait", "!=", "tuner"))
            elif not (word == "tuner" and "non-tuner" in low): traits.append(Atom("trait", "=", trait))
    if not (arche or types or attrs or traits):
        return None                                                                # nothing recognised
    branches = []
    for a, t, at in itertools.product(arche or [None], types or [None], attrs or [None]):   # OR inside a category
        atoms = list(traits)                                                                  # AND across categories
        if a:  atoms.append(Atom("archetype", "=", a))
        if t:  atoms.append(Atom("type", "=", t))
        if at: atoms.append(Atom("attribute", "=", at))
        branches.append(tuple(atoms))
    return branches


def parse_materials(text: str) -> list[Slot]:
    """The whole `materials` cell -> list of Slots (one per 'count = N: (...)' line)."""
    slots = Mats()
    for line in (text or "").splitlines():
        sm = re.match(r"\s*(sumlevel|link)\s*=\s*(\d+)", line)                     # 'sumlevel = 10' / 'link = 3'
        if sm:
            setattr(slots, "sumlevel" if sm.group(1) == "sumlevel" else "link", int(sm.group(2)))
            continue
        m = re.match(r"\s*count\s*=\s*(\d+)(\+?)\s*:\s*\((.*)\)\s*$", line)       # 'count = 2+: (...)'
        if not m:
            continue                                                               # header line ('Synchro:')
        need, plus, spec = int(m.group(1)), bool(m.group(2)), m.group(3)
        branches = parse_spec(spec)
        extra_kind = bool(branches) and all(any(a.field == "trait" and a.op == "=" and a.value in EXTRA_TRAITS
                                                for a in b) for b in branches)
        slots.append(Slot(need, plus, branches, extra_kind))
    return slots


def fix_names(slots: list[Slot], resolver: NameResolver) -> list[Slot]:
    """Replace card names inside slots by the real card name when the data used an old or garbled one."""
    for s in slots:
        if not s.branches:
            continue
        new = []
        for br in s.branches:
            atoms = []
            for a in br:
                if a.field == "name":
                    real = resolver.find(str(a.value))
                    if real:
                        a = Atom("name", a.op, norm_trait(real))                 # same atom, corrected value
                atoms.append(a)
            new.append(tuple(atoms))
        s.branches = new
    return slots


# --------------------------------------------------------------------------
# 2. Can a set of monsters summon this Extra Deck card?
# --------------------------------------------------------------------------
def reachable_sums(levels: list[int], low: int, high: int) -> set:
    """Every total Level you can make by choosing between `low` and `high` cards from `levels`
       (each entry is one card copy and can be used once). A small knapsack: sums[k] = totals using k cards."""
    sums = [set() for _ in range(high + 1)]
    sums[0].add(0)
    for lv in levels:                                                              # add the cards one at a time
        for k in range(high - 1, -1, -1):                                          # k downwards so a card is used once
            sums[k + 1] |= {s + lv for s in sums[k]}
    out = set()
    for k in range(low, high + 1):
        out |= sums[k]
    return out


def sums_ok(slots: Mats, monsters: list[Info]) -> bool:
    """Synchro rule: can we pick materials for every slot so their Levels add up to `sumlevel`?"""
    total = {0}                                                                    # totals reachable so far
    for s in slots:
        cand = [m.level for m in monsters if s.branches is None or card_matches(s.branches, m.facts)]
        here = reachable_sums(cand, s.need, s.need + (3 if s.plus else 0))         # a '+' slot may take up to 3 more
        if not here:
            return False
        total = {a + b for a in total for b in here}
    return slots.sumlevel in total


def summon_score(slots: list[Slot], monsters: list[Info]) -> float:
    """Share of material slots the given monsters can fill (1.0 = fully summonable).
       `monsters` has one Info per COPY. Slots that ask for Extra Deck monsters count as filled
       (the Extra Deck itself can supply those). Unreadable slots accept any monster."""
    if not slots:
        return 0.5                                                                 # no information: middling
    met = 0
    total = len(slots)
    sl = getattr(slots, "sumlevel", None)
    if sl and not any(s.extra_kind for s in slots):                                # Synchro: the Levels must also add up
        total += 1
        met += 1 if sums_ok(slots, monsters) else 0
    lk = getattr(slots, "link", None)
    if lk and sum(s.need for s in slots) > lk:                                     # Link: more materials than the rating allows
        total += 1                                                                 # (counts as one unmet condition)
    haves = [sum(1 for m in monsters if card_matches(s.branches, m.facts)) if s.branches else 0 for s in slots]   # copies that fit each slot
    allowed = getattr(slots, "sub_for", None)                                      # can a Fusion substitute help this card?
    if allowed is not None and any(lim is not False and (lim == "" or lim in allowed)
                                   for lim in map(substitute_limit, monsters)):   # ...and the deck has one that works for it
        named = [i for i, s in enumerate(slots) if named_slot(s)]                  # the slots that ask for a card by name
        if named:                                                                  # the substitute stands in for ONE copy of ONE of them:
            best = min(named, key=lambda i: (haves[i] >= slots[i].need, haves[i] / (slots[i].need + 1)))   # a short slot first, else the least reliable
            haves[best] += 1
    weakest = 1.0                                                                  # least reliable slot (see below)
    for s, have in zip(slots, haves):
        if s.extra_kind:
            met += 1
        elif s.branches is None:
            met += 1 if len(monsters) >= s.need else 0
        else:
            met += 1 if have >= s.need else 0
            # RELIABILITY: having exactly `need` copies of the right material means you must draw ALL of them.
            # Want at least one spare: need+1 copies is fully reliable, fewer scales down (one lone Tuner = 0.5).
            weakest = min(weakest, min(1.0, have / (s.need + 1)))
    return (met / total) * weakest


# --------------------------------------------------------------------------
# 3. The two Extra Deck builders
# --------------------------------------------------------------------------
class ExtraBuilder:
    def __init__(self, builder: Builder):
        self.builder = builder
        resolver = NameResolver([i.name for i in builder.infos])                       # fixes old / garbled card names in `name = "..."`
        self.slots = {i.name: fix_names(parse_materials(i.row.get("materials", "")), resolver)
                      for i in builder.infos if not i.main_deck and i.kind == "Monster"}
        for i in builder.infos:                                                         # which Fusions a substitute can help:
            mats = self.slots.get(i.name)
            if mats is not None and "fusion" in i.facts["trait"] and any(named_slot(s) for s in mats) \
               and not CONTACT.search(i.row.get("card text") or ""):                    # Fusion Summoned, with a named material
                mats.sub_for = set(i.facts["archetype"])                                # (an archetype-limited substitute checks these)

    def pool(self, req: Request) -> list[Info]:
        """Extra Deck monsters allowed by the era/rating filters and the Fusion/Synchro/Xyz/Link switches."""
        want = {t for t, on in (("fusion", req.allow_fusion), ("synchro", req.allow_synchro),
                                ("xyz", req.allow_xyz), ("link", req.allow_link)) if on}
        theme = self.builder.theme_archetypes(req)                                      # on-theme Extra Deck monsters ignore the rating limits
        return [i for i in self.builder.allowed(req, extra=True, rated=False) if i.facts["trait"] & want
                and (self.builder.rating_ok(i, req) or (i.facts["archetype"] & theme))]

    @staticmethod
    def copies(rng: random.Random) -> int:
        """Most Extra Deck monsters are played as 1-of."""
        return rng.choices([1, 2, 3], weights=[75, 20, 5])[0]

    def theme_match(self, label: Optional[str], info: Info) -> bool:
        """Is this Extra Deck card part of the theme label?"""
        if not label or norm(label) in ("random", "generic"):
            return False
        atom = parse_atom(label)
        if atom.field in ("self", "opponent"):
            want = self.builder.tree.get(f"{atom.field} {atom.op} {atom.value}", {f"{atom.field} {atom.op} {atom.value}"})
            return bool(info.offered & want)
        return card_matches([(atom,)], info.facts)

    # ---- MAIN-FIRST ------------------------------------------------------
    def extra_for_main(self, req: Request, deck: State, rng: random.Random, labels: list) -> list[Entry]:
        """Choose Extra Deck monsters that the finished main deck can summon."""
        pool = self.pool(req)
        monsters = [e.info for e in deck.entries if e.info.kind == "Monster"]       # every monster copy in the main deck
        deck_pool = self.builder.allowed(req)
        scored = []
        for c in pool:
            sc = summon_score(self.slots[c.name], monsters)
            f, _ = self.builder.eval_needs(c, deck, deck_pool, scan_partners=False)                              # soft check of the card's own costs
            theme = any(self.theme_match(l, c) for l in labels)
            scored.append(Candidate(sc, f, theme, c))
        return self._pick_extra(scored, req.extra_deck_size, rng, "extra (main-first)", deck.arche, generic=deck.generic_deck)

    def _pick_extra(self, scored: list[Candidate], size: int, rng: random.Random, why: str, arche: set = frozenset(), generic: bool = False) -> list[Entry]:
        """Two phases, like a real Extra Deck (theme cards first, a few staples after):
             1. ON-THEME cards, weighted by summon score. (Theme cards are used up before anything else.)
             2. Everything else, weighted by score^3 x cost factor, with cards of a DIFFERENT archetype
                made much less likely. When every fully-summonable card is gone, partial ones take over:
                that is the 'fall back to loose / generic' behaviour."""
        out: list[Entry] = []
        live = list(scored)
        if generic:                                                                  # Generic deck: no extra that needs named cards
            live = [x for x in live if not ((x.card.row.get("required cards") or "").strip() or '"' in (x.card.row.get("materials") or ""))]
        loose_taken = 0                                                              # Generic deck: archetype-bound extras picked so far

        def take(idx: int, tag: str) -> None:
            """Add the chosen card (1-3 copies) and remove it from the list of live candidates."""
            sc, f, th, c = live[idx]
            nonlocal loose_taken
            if generic and not c.clean:
                loose_taken += 1
            n = min(self.copies(rng), self.builder.cap(c.name), size - len(out))   # its copy limit (banlist / Highlander)
            for _ in range(n):
                out.append(Entry(c, "extra", f"{why}: summon score {sc:.2f}" + (", on-theme" if th else "") + tag))
            live.pop(idx)                                                            # each Extra Deck card is chosen once

        while len(out) < size:                                                       # phase 1: theme cards
            idxs = [i for i, x in enumerate(live) if x.on_theme and x.score > 0]
            if not idxs: break
            near = {c.name for c in self.builder.rating_tier([live[i].card for i in idxs])}   # nearest the rating limits first
            idxs = [i for i in idxs if live[i].card.name in near]
            weights = [max(live[i].score, 0.02) ** 3 for i in idxs]
            take(rng.choices(idxs, weights=weights)[0], "")
        while len(out) < size and live:                                              # phase 2: the rest
            weights = []
            for sc, f, th, c in live:                                                # (score, cost, on_theme, card)
                a = c.facts["archetype"]
                fit = 1.0 if (not a or not arche or (a & arche)) else 0.15           # off-archetype extras are rare picks
                if generic:
                    fit = 1.0 if c.clean else (0.2 if loose_taken < EXTRA_SPECIFIC_CAP else 0.0)   # staples first; specific extras capped
                weights.append(max(sc, 0.02) ** 3 * fit * (max(f, 0.1) if f is not None else 1.0))
            if not any(w > 0 for w in weights):                                      # only capped cards left (Generic deck): stop short
                break
            take(rng.choices(range(len(live)), weights=weights)[0], "")
        return out

    # ---- EXTRA-FIRST -----------------------------------------------------
    def extra_first(self, req: Request, labels: list, rng: random.Random) -> tuple[list[Entry], list]:
        """Choose the Extra Deck from the theme (no main deck yet) and return it with the material cards it wants
           as (Info, copies, reason) triples for Builder.build_main(pre=...)."""
        pool = self.pool(req)
        scored = []
        for c in pool:
            theme = any(self.theme_match(l, c) for l in labels)
            scored.append(Candidate(1.0, None, theme, c))                           # no main deck yet: everyone scores 1.0
        prefer = set()
        theme_arche = set()
        theme_cards: list[Info] = []                                                # the theme's own cards (for material_ok below)
        for l in labels:                                                            # names that count as "on theme"
            if l and norm(l) not in ("random", "generic"):
                bucket = self.builder.theme_bucket(l, self.builder.allowed(req))
                prefer |= {c.name for c in bucket}
                theme_cards += bucket
                for c in bucket: theme_arche |= c.facts["archetype"]               # archetypes that belong in this deck
        extra = self._pick_extra(scored, req.extra_deck_size, rng, "extra (extra-first)", theme_arche,
                                  generic=all(norm(l or "") in ("generic", "") for l in labels))
        main_pool = [i for i in self.builder.allowed(req) if i.kind == "Monster"]
        reserved: dict = {}                                                         # name -> copies already reserved
        placed: list[Info] = []                                                     # one Info per reserved copy
        used = 0
        order = list(dict.fromkeys(e.info.name for e in extra))
        rng.shuffle(order)                                                          # no extra monster gets first pick every time
        by_name = {e.info.name: e.info for e in extra}
        friends = theme_cards + list(by_name.values())                              # cards that will surely be in this deck
        ok_cache: dict = {}                                                         # card name -> material_ok answer

        def material_ok(c: Info) -> bool:
            """Will this material card WORK in the deck? There is no main deck yet, so we judge from what will surely be in it:
               True if it has no needs, or one of its strict needs' trait part is met by a theme card or a chosen Extra Deck
               monster. Cards with a 'required cards' entry are left out (the card they require may never come)."""
            if c.name not in ok_cache:
                if (c.row.get("required cards") or "").strip():
                    ok_cache[c.name] = False
                elif not c.needs:
                    ok_cache[c.name] = True                                         # needs nothing
                else:
                    branches = [tuple(a for a in br if not is_mechanic(a)) for br in parse_expr(c.row.get("strictSupportedBy", ""))]
                    ok_cache[c.name] = (not branches) or any(
                        not t or any(f.name != c.name and card_matches([t], f.facts) for f in friends) for t in branches)
            return ok_cache[c.name]

        for name in order:
            for slot in self.slots[name]:
                if used >= MATERIAL_CAP: break
                if slot.extra_kind or slot.branches is None: continue               # nothing concrete to place
                if sum(1 for m in placed if card_matches(slot.branches, m.facts)) >= slot.need:
                    continue                                                        # already covered by cards we reserved
                options = [c for c in main_pool if card_matches(slot.branches, c.facts)
                           and reserved.get(c.name, 0) < self.builder.cap(c.name)
                           and self.builder.normal_ok(c)]                           # Normal Monsters toggle
                if not options: continue
                options = [c for c in options if material_ok(c)] or options             # Step 2: materials that will work (if any do)
                weights = [self.builder.level_bias(c) * (6.0 if c.name in prefer else 1.0) for c in options]
                pick = rng.choices(options, weights=weights)[0]
                n = min(slot.need, self.builder.cap(pick.name) - reserved.get(pick.name, 0), MATERIAL_CAP - used)
                reserved[pick.name] = reserved.get(pick.name, 0) + n
                placed += [pick] * n
                used += n
        unique = {c.name: c for c in placed}                                        # one Info per name (Info itself is not hashable)
        pre = [(c, reserved[n], "material for the Extra Deck") for n, c in unique.items()]
        return extra, pre


# --------------------------------------------------------------------------
# 4. One call that builds a whole deck in either order
# --------------------------------------------------------------------------
def generate_state(builder: Builder, extra_builder: ExtraBuilder, req: Request):
    """Returns (main State, extra_entries, debug)."""
    master = random.Random(req.seed)                                                # ONE generator drives every choice
    order = req.build_order if req.build_order in ("main-first", "extra-first") else master.choice(["main-first", "extra-first"])
    pool = builder.allowed(req)
    # Resolve "Random" themes to a real archetype once, so both halves of the deck agree on the theme.
    def resolve(lbl):                                                               # None / "Random" -> a real archetype
        return builder.random_theme(pool, master) if (lbl is None or norm(lbl) == "random") else lbl
    labels = [resolve(req.deck_type_1_label)]
    if req.deck_type_2_pct > 0 and req.deck_type_1_pct < 100:                       # a second theme only if it gets slots
        labels.append(resolve(req.deck_type_2_label))
    # the theme's own cards: the Normal Monsters toggle never removes these (Dark Magician in a Dark Magician deck)
    theme_pool = builder.allowed(req, rated=False)                                  # (theme cards ignore the rating limits)
    builder.theme_names = {c.name for l in labels if l and norm(l) not in ("random", "generic")
                           for c in builder.theme_bucket(l, theme_pool)}
    main_req = Request(**{**req.__dict__, "deck_type_1_label": labels[0],
                          "deck_type_2_label": labels[1] if len(labels) > 1 else req.deck_type_2_label,
                          "seed": master.getrandbits(32)})
    rng_extra = random.Random(master.getrandbits(32))
    debug = {"order": order, "themes": labels}
    if order == "main-first":
        deck, d = builder.build_main(main_req)
        extra = extra_builder.extra_for_main(req, deck, rng_extra, labels)
    else:
        extra, pre = extra_builder.extra_first(req, labels, rng_extra)
        deck, d = builder.build_main(main_req, pre=pre)
        debug["materials_reserved"] = sum(n for _, n, _ in pre)
    debug.update(d)
    forced = list({c.name: c for l in labels for c in builder.wincon_extras(l or "")}.values())   # one Info per name (Info is not hashable)                  # Extra Deck wincons (Number 88 ...) must be in the deck
    if forced:
        have = {e.info.name for e in extra}
        keep = [e for e in extra if e.info.name not in {c.name for c in forced}]
        room = max(0, req.extra_deck_size - len(forced))
        extra = [Entry(c, "required", f"Extra Deck wincon: {c.name}") for c in forced] + keep[:room]   # 'required' = never pruned
        debug["extra_wincon"] = [c.name for c in forced]
    return deck, extra, debug


def generate(builder: Builder, extra_builder: ExtraBuilder, req: Request):
    """Same as generate_state but returns the main deck as a plain list of entries."""
    deck, extra, debug = generate_state(builder, extra_builder, req)
    return deck.entries, extra, debug


# --------------------------------------------------------------------------
# Self-test:  python ygo_extra.py
# --------------------------------------------------------------------------
if __name__ == "__main__":
    here = Path(__file__).resolve().parent / "data"         # the data folder (program/data)
    builder = Builder(read_csv(here / "cards.csv"), load_tree(here / "game_mechanics_tree.csv"))
    extra_builder = ExtraBuilder(builder)

    # (1) How well do we read materials?
    total = readable = 0
    for name, slots in extra_builder.slots.items():
        for s in slots:
            total += 1
            readable += s.branches is not None or s.extra_kind
    print(f"{len(extra_builder.slots)} Extra Deck monsters, {total} material slots, {readable} readable ({readable/total:.0%}); the rest accept any monster")
    by = {i.name: i for i in builder.infos}
    for n in ("Dragonmaid Sheou", "Dragocytos Corrupted Nethersoul Dragon", "Galaxy Stealth Dragon", "Dragunity Knight - Romulus"):
        print(" ", n, [(s.need, [[f"{a.field}{a.op}{a.value}" for a in br] for br in s.branches] if s.branches else None, s.extra_kind) for s in extra_builder.slots[n]])

    # (2) Build the same theme in both orders and show the result.
    for order in ("main-first", "extra-first"):
        req = Request(seed=11, deck_type_1_label="Archetype = blackwing", support_1_label="Random", build_order=order)
        main, extra, dbg = generate(builder, extra_builder, req)
        print(f"\n===== {order}: {len(main)} main, {len(extra)} extra | reserved materials: {dbg.get('materials_reserved', '-')}")
        print("main  :", ", ".join(f"{sum(1 for e in main if e.info.name == n)}x {n}" for n in dict.fromkeys(e.info.name for e in main) if True)[:700])
        print("extra :")
        for n in dict.fromkeys(e.info.name for e in extra):
            e = next(x for x in extra if x.info.name == n)
            print(f"   {sum(1 for x in extra if x.info.name == n)}x {n}  [{e.reason.split(': ', 1)[1]}]")
        monsters = [e.info for e in main if e.info.kind == "Monster"]
        full = sum(1 for n in dict.fromkeys(e.info.name for e in extra) if summon_score(extra_builder.slots[n], monsters) >= FULL_SCORE)
        print(f"fully summonable Extra Deck cards: {full}/{len(set(e.info.name for e in extra))}")

    # (3) Many decks: share of Extra Deck cards that the main deck can fully summon, new vs random.
    themes = ["Archetype = blackwing", "Archetype = laval", "Type = Dragon", "Attribute = WATER", "Archetype = snake-eye"]
    res = {"main-first": [], "extra-first": [], "random extra": []}
    for th in themes:
        for seed in range(4):
            for order in ("main-first", "extra-first"):
                main, extra, _ = generate(builder, extra_builder, Request(seed=seed, deck_type_1_label=th, build_order=order))
                mons = [e.info for e in main if e.info.kind == "Monster"]
                names = list(dict.fromkeys(e.info.name for e in extra))
                res[order].append(sum(summon_score(extra_builder.slots[n], mons) >= FULL_SCORE for n in names) / max(1, len(names)))
            rr = random.Random(seed); rp = rr.sample(extra_builder.pool(Request()), 15)         # old style: any Extra Deck cards at all
            res["random extra"].append(sum(summon_score(extra_builder.slots[c.name], mons) >= FULL_SCORE for c in rp) / 15)
    for k, v in res.items():
        print(f"{k:14s} avg fully-summonable share: {sum(v)/len(v):.0%}")
