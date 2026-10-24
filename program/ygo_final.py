"""
ygo_final.py  --  builds a WHOLE deck, then exports it.

WHERE THIS FITS: the window calls build_deck() here. It uses ygo_builder.py (the main deck) and ygo_extra.py
(the Extra Deck), then finishes the deck in the steps below. export_deck() writes the .ydk / .txt / .csv files.

THE NINE STEPS (_build_deck)                                  the functions that do them
  1. theme, support, staples, filler + the Extra Deck           ygo_extra.generate_state()
  2. alias names (Harpie Lady / Harpie Lady 1 ...): mixed, but  apply_aliases(), cap_copies()
     the whole group at one card's limit (checked again in 7b)
  3. cards the deck needs: required cards, deck rules           _add_needed_cards(): add_required(),
     (Ritual Spell, Pendulum pairs, low-Level monsters),           apply_rules(), apply_mentions()
     cards that name each other
  4. back to the right size, swap cards whose needs aren't met  _back_to_size(): trim(), swap_unmet()
  5. drop Extra Deck monsters the main deck can't summon        _drop_unsummonable()
  6. almost no Extra Deck left? swap in 'enabler' cards         add_enablers()
  7. fill the Extra Deck's gaps with summonable monsters        _refill_extra()
     7b. alias groups pushed over the limit by 3-7: cut back      cap_copies()
     7c. last check: swap out every card that still can't work    last_check(): useless_cards()
  8. Genesys: get under the point cap                           enforce_points()
  9. the checks shown in the preview's Debug section            _final_report()
  build_deck() runs it all, and widens the era / rating filters step by step if they leave no cards.

NAMES USED HERE
  builder / extra_builder   the main-deck and Extra Deck builders (they know every card and the format's limits)
  deck                      a State: the main deck being built (entries, counts, archetypes ...)
  entries / extra           the main deck / Extra Deck as plain lists of Entry (one per card copy)
  req                       the Request: every setting from the window
  rules                     a Rules: turns the 'required cards' column into requirements, finds cards by name
  origin                    why a card is in the deck: theme1, support1, staple, generic, required, material ...
                            (TRIM_ORDER: which origins go first when the deck is too big)
"""
from __future__ import annotations

import csv
import random
import re
from pathlib import Path
from typing import Optional

from ygo_data import card_matches, is_mechanic, need_status, norm, read_csv, load_tree, parse_expr
from ygo_builder import Builder, Entry, Info, Request, State, FLOODGATE_RULES, CLASH_SHARE, BROAD_SHARE
from ygo_extra import ExtraBuilder, FULL_SCORE, generate_state, summon_score, sums_ok, substitute_limit, named_slot
from ygo_names import NameResolver

STARTERS = 8                  # a deck wants at least this many Level 4-or-lower monsters (fewer if it has few monsters)
DROP_BELOW = 0.6              # Extra Deck cards the main deck can fill less than this share of materials for are dropped
MENTION_POPULAR = 12          # a card named by more cards than this counts as a staple
MENTION_CAP = 6               # soft limit on copies added only because cards name each other
REQUIRED_CAP = 8              # soft limit on main-deck copies added only because some card requires them
ENABLE_BELOW = 5              # an Extra Deck smaller than this gets 'enabler' cards in the main deck (see add_enablers)
ENABLE_BUDGET = 8             # most main-deck cards add_enablers may swap in, in total
ENABLE_PER_TARGET = 5         # most main-deck cards it may spend on unlocking one Extra Deck monster
SWAP_PASSES = 3               # how many times we look for cards with uncovered needs and replace them
TRIM_ORDER = [["generic"], ["staple"], ["support2", "support1"], ["theme2", "theme1"], ["partner", "mention"], ["material"], ["required"]]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def restate(entries: list[Entry], quota: Optional[dict] = None) -> State:
    """Rebuild a State (counts, mechanics, traits ...) from a plain list of entries after we edit the list."""
    deck = State(quota=None)
    groups: dict = {}                                                   # (name, origin, reason) -> [Info, copies]
    for e in entries:
        key = (e.info.name, e.origin, e.reason)
        groups.setdefault(key, [e.info, 0])[1] += 1
    for (name, origin, reason), (info, n) in groups.items():
        deck.add(info, n, origin, reason)
    # keep the ORIGINAL order of entries (State.add appends grouped copies, which is fine for counting)
    deck.entries = list(entries)
    return deck


def needs_covered(builder: Builder, c: Info, deck: State) -> bool:
    """True if the card has no needs, or at least one need-branch is fully covered by the deck."""
    if not c.needs:
        return True
    for branch in c.needs:
        traits = tuple(a for a in branch if not is_mechanic(a))
        mechs = [a for a in branch if is_mechanic(a)]
        trait_ok = (not traits) or any(o.name != c.name and card_matches([traits], o.facts) for o in deck.unique.values())
        mech_ok = all(need_status(m, deck.offered, deck.traits, builder.tree) != "unmet" for m in mechs)
        if trait_ok and mech_ok:
            return True
    return False


class Rules:
    """Everything needed to turn the `required cards` column into concrete requirements."""
    def __init__(self, builder: Builder):
        self.infos = builder.infos
        self.resolver = NameResolver([i.name for i in builder.infos])                 # finds the real card for old / garbled names
        self.by_name = {norm(i.name): i for i in builder.infos}
        self.archetypes = {a for i in builder.infos for a in i.facts["archetype"]}    # every archetype that exists
        self._graph = None                                                       # who names whom (built on first use)

    def graph(self) -> tuple[dict, dict]:
        """(mentions, mentioned_by): for every card, the cards it names in its text, and the cards that name it."""
        if self._graph is None:
            mentions: dict = {}
            mentioned_by: dict = {}
            for i in self.infos:
                found = mentioned_cards(i, self)
                mentions[i.name] = found
                for m in found:
                    mentioned_by.setdefault(m.name, []).append(i)
            self._graph = (mentions, mentioned_by)
        return self._graph


def quoted_cards(info: Info, rules: Rules) -> list[Info]:
    """Real cards whose names appear in quotes inside this card's text (not the card itself)."""
    found = []
    for q in re.findall(r'"([^"]+)"', info.row.get("card text", "")):
        c = rules.by_name.get(norm(q))
        if c is not None and c.name != info.name and c not in found:
            found.append(c)
    return found


# A name after these words is an EXCLUSION ("except 'X'"), not something the card wants.
EXCEPT_RX = re.compile(r'(?:except|other than|excluding)\s+(?:the\s+)?"[^"]+"', re.I)


def mentioned_cards(info: Info, rules: Rules) -> list[Info]:
    """Real cards named in quotes in this card's text, leaving out itself and any 'except "X"' exclusions."""
    text = EXCEPT_RX.sub("", info.row.get("card text", ""))
    found = []
    for q in re.findall(r'"([^"]+)"', text):
        c = rules.by_name.get(norm(q))
        if c is not None and c.name != info.name and c not in found:
            found.append(c)
    return found


def requirements(info: Info, rules: Rules) -> list[tuple]:
    """List what a card requires, as ('name', exact card name) or ('arche', archetype).
       Sources, in order:
         1. the `required cards` column. 'Name = Polymerization' is a card; 'Name = Dark Contract' is an
            archetype ('any Dark Contract card'); anything else (words like 'or') is ignored.
            Names the data cut off at an apostrophe ('Sprite' for "Sprite's Blessing") are completed
            from the quoted names in the card's own text.
         2. Ritual cards: a Ritual Monster that names its Ritual Spell in its text needs that spell,
            and a Ritual Spell that names a Ritual Monster needs that monster (just the first one named)."""
    out = []
    text_cards = quoted_cards(info, rules)
    for m in re.finditer(r"Name\s*=\s*([^&|]+)", info.row.get("required cards", "")):
        text = m.group(1).strip()
        real = rules.resolver.find(text)
        if not real:                                                        # maybe cut off at an apostrophe
            real = next((c.name for c in text_cards if norm(c.name).startswith(norm(text) + "'")), None)
        if real:
            out.append(("name", real))
        elif norm(text) in rules.archetypes:
            out.append(("arche", norm(text)))
    traits = info.facts["trait"]
    if info.kind == "Monster" and "ritual" in traits:                       # ritual monster -> the spell it names
        out += [("name", c.name) for c in text_cards if "ritual spell" in c.facts["trait"]]
    elif info.kind == "Spell" and "ritual spell" in traits:                 # ritual spell -> one monster it names
        monsters = [c for c in text_cards if c.kind == "Monster" and "ritual" in c.facts["trait"]]
        out += [("name", monsters[0].name)] if monsters else []
    return list(dict.fromkeys(out))


def satisfied(item: tuple, deck: State, extra: Optional[list] = None) -> bool:
    """Is this requirement already met by the cards in the deck? (`extra`: the Extra Deck entries count too,
       for a requirement that is itself an Extra Deck monster, like Black-Winged Dragon)"""
    kind, value = item
    if kind == "name":
        return deck.counts.get(value, 0) > 0 or any(e.info.name == value for e in extra or [])
    return any(value in c.facts["archetype"] for c in deck.unique.values())


# --------------------------------------------------------------------------
# 1. Aliases
# --------------------------------------------------------------------------
def alias_group(info: Info, rules: Rules) -> Optional[tuple]:
    """The card's alias group ('Harpie Lady||Harpie Lady 1||...' -> every real name in it, sorted), or None if it has none."""
    alias = info.row.get("alias", "").strip()
    if not alias:
        return None
    group = tuple(sorted(r for r in (rules.resolver.find(n) for n in alias.split("||") if n.strip()) if r))
    return group if len(group) >= 2 else None                              # a group of one is no group


def group_cap(group: tuple, cap) -> int:
    """Copies the WHOLE group may have together: they all count as one card, so the same limit as one card (3, or 1 in
       Highlander). The highest limit of its names is used: a Limited name only limits its own copies (see apply_aliases)."""
    return max(cap(n) for n in group)


def apply_aliases(entries: list[Entry], rules: Rules, rng: random.Random, chosen: dict, cap=None) -> None:
    """Cards with an `alias` list ('Harpie Lady||Cyber Harpie Lady||...') count as the same card under different names,
       like a real deck may run 1 Harpie Lady 1 + 2 Harpie Lady 2. Every copy gets a random name from its group, but no
       name goes over its OWN copy limit (a Limited one stays at 1). The group's total is cut back by cap_copies().
       `chosen` remembers the names used per group, for the preview's debug info. `cap` = the builder's copy-limit function."""
    used: dict = {}                                                        # name -> copies given that name so far
    for e in entries:
        group = alias_group(e.info, rules)
        if group is None:
            continue                                                       # not an alias card
        ok = [n for n in group if cap is None or cap(n) > 0]               # only names this format allows
        room = [n for n in ok if cap is None or used.get(n, 0) < cap(n)]   # ...that still have room under their own limit
        name = rng.choice(room or ok or [e.info.name])                     # a random one (all full: any - cap_copies trims)
        pick = rules.by_name.get(norm(name))
        if pick is not None:
            e.info = pick
            used[pick.name] = used.get(pick.name, 0) + 1
            chosen.setdefault(group, [])
            chosen[group].append(pick.name)


def cap_copies(builder: Builder, deck: State, pool: list[Info], size: int, rng: random.Random, rules: Optional[Rules] = None) -> State:
    """Cut every card back to its copy limit - and every alias group (Harpie Lady, Harpie Lady 1, ...) back to ONE card's
       limit, since its names all count as the same card - then refill the gap, preferring cards of the deck's own
       archetype (so a Harpie deck gets other Harpie cards back, not random ones)."""
    groups = {e.info.name: g for e in deck.entries if rules is not None and (g := alias_group(e.info, rules))}   # name -> its alias group
    in_group: dict = {}                                                    # group -> copies of any of its names so far

    def room(name: str, counts: dict) -> int:
        """How many more copies of `name` fit: its own limit, and (for an alias card) its group's limit."""
        left = builder.cap(name) - counts.get(name, 0)
        info = rules.by_name.get(norm(name)) if rules is not None else None    # the card itself (to read its alias list)
        g = groups.get(name) or (alias_group(info, rules) if info is not None else None)
        if g:
            left = min(left, group_cap(g, builder.cap) - sum(counts.get(n, 0) for n in g))
        return left

    kept, seen = [], {}
    for e in deck.entries:
        seen[e.info.name] = seen.get(e.info.name, 0) + 1
        g = groups.get(e.info.name)
        if g:
            in_group[g] = in_group.get(g, 0) + 1
        if seen[e.info.name] <= builder.cap(e.info.name) and (not g or in_group[g] <= group_cap(g, builder.cap)):
            kept.append(e)                                                 # inside its own limit and its group's limit
    if len(kept) == len(deck.entries):
        return deck                                                        # nothing was over the limit
    deck = restate(kept)
    on_theme = [c for c in pool if c.facts["archetype"] & deck.arche and c.main_deck]
    candidates = on_theme or [c for c in pool if c.clean] or [c for c in pool if c.generic] or pool
    guard = 0
    while len(deck.entries) < size and guard < 200:
        guard += 1
        card, _ = builder.pick(candidates, deck, pool, rng)
        if card is None:
            break
        n = room(card.name, deck.counts)
        if n <= 0:
            continue
        deck.add(card, rng.randint(1, min(n, size - len(deck.entries))), "theme1" if on_theme else "generic", "refill after alias merge")
    return deck


# --------------------------------------------------------------------------
# 2. Required cards
# --------------------------------------------------------------------------
def add_required(builder: Builder, extra_builder: ExtraBuilder, deck: State, extra: list[Entry], req: Request,
                 rules: Rules, rng: random.Random, debug: dict, pool: list[Info]) -> tuple[State, list[Entry]]:
    """Add cards the deck's cards require. Main-deck cards' requirements come first (they are a must);
       Extra Deck monsters come next, and any that no longer fit the budget are replaced."""
    added: dict = {}                                                       # card name -> copies added
    used = 0                                                               # copies added so far
    extra_ok = {c.name for c in extra_builder.pool(req)}                              # Extra Deck monsters this format + the type switches allow

    busy: set = set()                                                      # Extra Deck cards being added right now (no loops)

    def add_to_extra(info: Info, who: str) -> bool:
        """A required card that is itself an Extra Deck monster goes in the EXTRA Deck (1 copy), together with
           what IT requires (Gaia the Dragon Champion needs Gaia The Fierce Knight + Curse of Dragon in the main
           deck). If the Extra Deck is full, an ordinary Extra Deck card makes room. False if it can't be done:
           not allowed here, or its own requirements don't fit."""
        if info.name not in extra_ok or builder.cap(info.name) <= 0 or info.name in busy:
            return False                                                   # Forbidden here, type switched off, or a loop
        own = [i for i in requirements(info, rules) if not satisfied(i, deck, extra)]   # what this card needs itself
        if used + len(own) > REQUIRED_CAP:
            return False                                                   # would drag in too many cards
        if len(extra) >= req.extra_deck_size:                              # full: take out one ordinary card
            spare = [i for i, e in enumerate(extra) if e.origin != "required" and e.info.name != who]
            if not spare:
                return False
            extra.pop(spare[-1])
        entry = Entry(info, "required", f"required by {who}")              # 'required' = never pruned later
        extra.append(entry)
        busy.add(info.name)
        ok = all([add_item(i, info.name) for i in own])                    # its own requirements
        busy.discard(info.name)
        if not ok:                                                         # something it needs can't be added:
            extra.remove(entry)                                            # take it back out
            return False
        added[info.name] = added.get(info.name, 0) + 1
        return True

    def add_item(item: tuple, who: str) -> bool:
        """Put one card that satisfies the requirement into the deck. False if it couldn't be added."""
        nonlocal used
        kind, value = item
        if kind == "name":
            info = rules.by_name[norm(value)]
            if not builder.normal_ok(info):
                return False                                               # a Normal Monster, and the Normal toggle is off
            if not info.main_deck:                                         # an Extra Deck monster (e.g. the Xyz a CXyz is made from)
                return add_to_extra(info, who)                             # goes in the Extra Deck, not the main deck
        else:                                                              # archetype: choose a card of that archetype
            cands = [c for c in pool if value in c.facts["archetype"] and c.main_deck      # main-deck cards only
                     and deck.counts.get(c.name, 0) < builder.cap(c.name) and builder.normal_ok(c)]
            if not cands:
                return False
            cands = [c for c in cands if works_in(builder, rules, c, deck.entries, extra)] or cands   # Step 2: cards that work here (if any do)
            weights = [(3.0 if c.kind != "Monster" else 1.0) * (2.0 if needs_covered(builder, c, deck) else 0.3) for c in cands]
            info = rng.choices(cands, weights=weights)[0]               # requirement cards are usually Spells / Traps
        room = builder.cap(info.name) - deck.counts.get(info.name, 0)                  # copies still allowed (0 if Forbidden here)
        if room > 0:
            # a required card is a normal deck card too: play 1-3 copies, not always 1.
            # Win-by-pieces cards (Exodia parts, Spirit Messages) are the exception: a second copy does nothing.
            one_only = "forbidden one" in norm(info.name) or "spirit message" in norm(info.name)
            n = 1 if one_only else rng.randint(1, min(3, room))
            deck.add(info, n, "required", f"required by {who}")
            added[info.name] = added.get(info.name, 0) + n
            used += n
            return True
        return False

    # (a) requirements of MAIN-deck cards: always added
    for c in list(deck.unique.values()):
        for item in requirements(c, rules):
            if not satisfied(item, deck, extra):
                add_item(item, c.name)

    # (b) requirements of EXTRA-deck monsters: added while the budget lasts
    unique_extra = list(dict.fromkeys(e.info.name for e in extra))
    infos = {e.info.name: e.info for e in extra}
    reqs = {n: requirements(infos[n], rules) for n in unique_extra}
    dropped = []
    order = sorted(unique_extra, key=lambda x: (sum(1 for i in reqs[x] if not satisfied(i, deck, extra)), rng.random()))
    wincon_extra = {e.info.name for e in extra if e.origin == "required"}   # Extra Deck wincon cards (Number 88 ...): never dropped here
    for n in order:
        if n in wincon_extra:
            continue                                                       # its requirements are other Extra Deck cards, already placed
        if not any(e.info.name == n for e in extra):
            continue                                                       # it made room for another card's requirement
        need = [i for i in reqs[n] if not satisfied(i, deck, extra)]         # still missing from the deck
        on_theme = bool(infos[n].facts["archetype"] & deck.arche)            # an Extra Deck monster of the deck's own theme?
        if need and not on_theme:
            dropped.append(n)                                              # a foreign extra must not drag random cards into the main deck
        elif used + len(need) <= REQUIRED_CAP:
            if not all([add_item(i, n) for i in need]):                    # something it needs can't be added here:
                dropped.append(n)                                          # it goes (and is replaced below)
        else:
            dropped.append(n)
    debug["required_added"] = dict(added)
    debug["extra_dropped_for_requirements"] = dropped

    # (c) replace dropped Extra Deck monsters by ones whose requirements are already satisfied
    if dropped:
        copies_lost = sum(1 for e in extra if e.info.name in dropped)
        extra = [e for e in extra if e.info.name not in dropped]
        have = {e.info.name for e in extra}
        monsters = [e.info for e in deck.entries if e.info.kind == "Monster"]
        cands = []
        for c in extra_builder.pool(req):
            if c.name in have or c.name in dropped:
                continue
            if any(not satisfied(i, deck, extra) for i in requirements(c, rules)):
                continue                                                   # its requirements are not in the deck
            sc = summon_score(extra_builder.slots[c.name], monsters)
            fit = 1.0 if (not c.facts["archetype"] or not deck.arche or (c.facts["archetype"] & deck.arche)) else 0.15
            cands.append((max(sc, 0.02) ** 3 * fit, c))
        while copies_lost > 0 and cands:
            w, c = rng.choices(cands, weights=[x[0] for x in cands])[0]
            n = min(extra_builder.copies(rng), copies_lost, builder.cap(c.name))           # never past its copy limit
            for _ in range(n):
                extra.append(Entry(c, "extra", "extra: replacement (requirements already in deck)"))
            copies_lost -= n
            cands = [x for x in cands if x[1].name != c.name]
    return deck, extra


# --------------------------------------------------------------------------
# 2b. Deck rules: Ritual Monsters need a Ritual Spell, Pendulum Monsters come in pairs
# --------------------------------------------------------------------------
def _fit_weight(c: Info, wanted: set) -> float:
    """How well a helper card (a Ritual Spell, a Pendulum monster) fits the deck's archetypes:
       6x if it shares one, 2x if it has no archetype (generic), 0.3x if it belongs to some other archetype."""
    a = c.facts["archetype"]
    if a & wanted:
        return 6.0
    return 2.0 if not a else 0.3


def apply_rules(builder: Builder, deck: State, pool: list[Info], rng: random.Random, debug: dict, generic: bool = False,
                rules: Optional[Rules] = None) -> None:
    """Add the cards the deck rules ask for (they are trimmed from elsewhere later)."""
    added = []
    deck_arche = set().union(*[c.facts["archetype"] for c in deck.unique.values()]) if deck.unique else set()

    # Rule 1: a Ritual Monster in the main deck needs a Ritual Spell to be summoned.
    rituals = [c for c in deck.unique.values() if "ritual" in c.facts["trait"] and c.kind == "Monster"]
    has_spell = any("ritual spell" in c.facts["trait"] for c in deck.unique.values())
    if rituals and not has_spell:
        wanted = set().union(*[c.facts["archetype"] for c in rituals])             # prefer a spell of the monsters' archetype
        cands = [c for c in pool if "ritual spell" in c.facts["trait"] and deck.counts.get(c.name, 0) < builder.cap(c.name)]
        # Step 2: the spell a deck Ritual Monster names ("You can Ritual Summon this card with 'Black Magic Ritual'"), or a spell
        # that names one of the deck's Ritual Monsters, beats a random one. Only if none is allowed here do we take any spell.
        named = [c for c in cands if any(f'"{norm(c.name)}"' in norm(r.row.get("card text", "")) for r in rituals)
                 or any(f'"{norm(r.name)}"' in norm(c.row.get("card text", "")) for r in rituals)]
        cands = named or cands
        if cands:
            pick = rng.choices(cands, weights=[_fit_weight(c, wanted) * (2.0 if needs_covered(builder, c, deck) else 0.5) for c in cands])[0]
            n = min(2 if len(rituals) >= 3 else 1, builder.cap(pick.name))                # more rituals -> more copies of the spell
            deck.add(pick, n, "required", f"rule: Ritual Monsters need a Ritual Spell ({', '.join(c.name for c in rituals[:2])})")
            added.append(pick.name)

    # Rule 2: Pendulum Monsters come in pairs (you need two to set the scales).
    pend = [e for e in deck.entries if "pendulum" in e.info.facts["trait"]]
    if len(pend) == 1:
        wanted = pend[0].info.facts["archetype"] | deck_arche
        cands = [c for c in pool if "pendulum" in c.facts["trait"] and deck.counts.get(c.name, 0) < builder.cap(c.name)
                 and c.name != pend[0].info.name and builder.normal_ok(c)]   # (a Normal Pendulum obeys the Normal toggle)
        cands = [c for c in cands if works_in(builder, rules, c, deck.entries, [])] or cands   # Step 2: a partner that works here
        if cands:
            pick = rng.choices(cands, weights=[_fit_weight(c, wanted) * builder.level_bias(c, deck) for c in cands])[0]
            deck.add(pick, 1, "required", f"rule: Pendulum Monsters come in pairs (with {pend[0].info.name})")
            added.append(pick.name)

    # Rule 3: the deck needs monsters you can actually Normal Summon (Level 4 or lower, no tribute). A deck of only
    # Level 5+ monsters (Monarchs with no fodder, B.E.S. ...) can't get started. Add on-theme or generic low-level monsters.
    mons = [e for e in deck.entries if e.info.kind == "Monster" and "link" not in e.info.facts["trait"]]
    easy = sum(1 for e in mons if (e.info.facts.get("level") or 0) <= 4)
    need = builder.starters_needed(len(mons))                                 # 8, or 40% of the monsters if fewer (never above the Level 1-4 slider)
    if mons and easy < need:
        low = [c for c in pool if c.kind == "Monster" and (c.facts.get("level") or 99) <= 4 and "link" not in c.facts["trait"]
               and c.main_deck and deck.counts.get(c.name, 0) < builder.cap(c.name) and (c.clean or not generic)   # Generic deck: clean only
               and builder.normal_ok(c)]                                       # Normal Monsters toggle
        low = rng.sample(low, min(150, len(low)))                              # sample first: checking needs for every card is slow
        low = [c for c in low if works_in(builder, rules, c, deck.entries, [])] or low   # Step 2: monsters that work here
        wanted = deck_arche
        got = 0
        while easy + got < builder.starters_needed(len(mons) + got) and low:         # target grows as monsters are added
            w = [_fit_weight(c, wanted) * (2.0 if needs_covered(builder, c, deck) else 0.2) for c in low]
            pick = rng.choices(low, weights=w)[0]
            n = min(2, builder.cap(pick.name) - deck.counts.get(pick.name, 0))
            deck.add(pick, n, "required", "rule: the deck needs monsters it can Normal Summon (Level 4 or lower)")
            added.append(pick.name)
            got += n
            low = [c for c in low if c.name != pick.name]
    debug.setdefault("rule_cards_added", []).extend(added)


def apply_mentions(builder: Builder, deck: State, pool: list[Info], rules: Rules, rng: random.Random, debug: dict) -> None:
    """Direct-mention rule. If a deck card NAMES another card (or is named by one), bring that card in too:
         forward : deck card quotes X  -> X is a candidate ("named by A")
         reverse : card Y quotes a deck card -> Y is a candidate ("names A")
       Candidates must be main-deck cards in the allowed pool, must not clash with the deck's archetypes,
       and must WORK in the deck (works_in: needs met, required cards present - both directions). At most MENTION_CAP copies are added, chosen
       by weight: the more deck cards a candidate is linked to, the likelier it is."""
    mentions, mentioned_by = rules.graph()
    allowed = {c.name for c in pool}
    arche = set().union(*[c.facts["archetype"] for c in deck.unique.values()]) if deck.unique else set()
    score: dict = {}                                                       # name -> [links, Info, reasons]

    def consider(card: Info, why: str) -> None:
        if card.name in deck.counts or card.name not in allowed or not card.main_deck or not builder.normal_ok(card):
            return                                                         # already in the deck / not allowed / Extra Deck / Normal toggle
        a = card.facts["archetype"]
        if a and arche and not (a & arche):
            return                                                         # belongs to some other archetype
        if card.name not in score and not works_in(builder, rules, card, deck.entries, []):
            return                                                         # Step 2: it would be useless here (checked once per card)
        entry = score.setdefault(card.name, [0, card, []])
        entry[0] += 1
        entry[2].append(why)

    for c in list(deck.unique.values()):
        for m in mentions.get(c.name, []):                                 # forward: cards this card names
            consider(m, f"named by {c.name}")
        namers = mentioned_by.get(c.name, [])
        for y in namers:                                                   # reverse: cards that name this card
            # a staple named by dozens of cards (Polymerization...) would drag in random cards, so only
            # follow it when few cards name it, or when the namer shares an archetype with it
            if len(namers) > MENTION_POPULAR and not ((y.facts["archetype"] & c.facts["archetype"]) - {norm(c.name)}):
                continue
            consider(y, f"names {c.name}")                                 # (consider() now checks its needs, both ways)
    cands = list(score.values())
    added, used = [], 0
    while cands and used < MENTION_CAP:
        weights = [(n ** 2) * builder.level_bias(c, deck) *(2.0 if (c.generic or needs_covered(builder, c, deck)) else 0.3) for n, c, _ in cands]
        n, card, why = rng.choices(cands, weights=weights)[0]
        copies = min(2 if n >= 3 else 1, builder.cap(card.name), MENTION_CAP - used)
        deck.add(card, copies, "mention", "mention: " + "; ".join(why[:3]))
        added.append(card.name)
        used += copies
        cands = [x for x in cands if x[1].name != card.name]
    debug["mention_cards_added"] = added


def rule_violations(deck_main: list[Entry], builder: Optional[Builder] = None) -> list[str]:
    """Check the main deck against the rules; returns a list of problems (empty = fine)."""
    deck = restate(deck_main)
    problems = []
    if any("ritual" in c.facts["trait"] and c.kind == "Monster" for c in deck.unique.values()) \
            and not any("ritual spell" in c.facts["trait"] for c in deck.unique.values()):
        problems.append("Ritual Monster without a Ritual Spell")
    if sum(1 for e in deck_main if "pendulum" in e.info.facts["trait"]) == 1:
        problems.append("only one Pendulum Monster")
    mons = [e for e in deck_main if e.info.kind == "Monster" and "link" not in e.info.facts["trait"]]
    need = builder.starters_needed(len(mons)) if builder is not None else min(STARTERS, -(-len(mons) * 2 // 5))   # (follows the Level 1-4 slider)
    if mons and sum(1 for e in mons if (e.info.facts.get("level") or 0) <= 4) < need:
        problems.append("too few Level 4 or lower monsters")
    return problems


# --------------------------------------------------------------------------
# 3. Trim
# --------------------------------------------------------------------------
def trim(entries: list[Entry], size: int, rng: random.Random, plan: dict | None = None) -> None:
    """Remove cards until the list has `size` entries, least important origin first.
       Theme and support slots shrink TOGETHER, in proportion to the plan, so a crowd of required/mention/partner
       cards cannot wipe out the support slots while the theme stays full."""
    plan = plan or {}                                                       # {"theme1": 14, "support1": 16, ...}
    shared = ("support2", "support1", "theme2", "theme1")                   # the group that shrinks proportionally
    while len(entries) > size:
        for group in TRIM_ORDER:
            if "support1" in group:                                         # the old support group: widen to theme + support
                counts = {o: sum(1 for e in entries if e.origin == o) for o in shared}
                live = [o for o in shared if counts[o] > 0]                 # origins that still have cards
                if live:
                    # ratio of cards left to cards planned; the most over-represented origin loses a card
                    # (an origin with no plan counts as over-represented, so it goes first)
                    worst = max(live, key=lambda o: (counts[o] / plan[o]) if plan.get(o) else 99.0)
                    idx = [i for i, e in enumerate(entries) if e.origin == worst]
                    entries.pop(rng.choice(idx))
                    break
                continue
            if "theme1" in group:                                           # theme is already handled by the shared group
                continue
            idx = [i for i, e in enumerate(entries) if e.origin in group]
            if idx:
                entries.pop(rng.choice(idx))
                break
        else:
            entries.pop()                                                  # nothing matched (should not happen)


# --------------------------------------------------------------------------
# 4. Swap cards whose needs are not covered
# --------------------------------------------------------------------------
def swap_unmet(builder: Builder, entries: list[Entry], pool: list[Info], rng: random.Random, debug: dict) -> list[Entry]:
    """Replace every card whose needs are uncovered by a card of the same kind whose needs ARE covered
       (or that has none), keeping the deck size. Runs a few passes because a swap can change other cards' needs."""
    swaps = []
    for _ in range(SWAP_PASSES):
        deck = restate(entries)
        origin_of = {e.info.name: e.origin for e in entries}
        bad = [c for c in deck.unique.values()
               if (not needs_covered(builder, c, deck) or (origin_of.get(c.name, "").startswith("support") or origin_of.get(c.name) in ("generic", "staple")) and builder.junk(c, deck))
               and not any(e.origin in ("required", "material", "mention") and e.info.name == c.name for e in entries)]
        if not bad:
            break
        for c in bad:
            slots = [i for i, e in enumerate(entries) if e.info.name == c.name]
            origin = entries[slots[0]].origin
            support = origin.startswith("support")
            base = [x for x in rng.sample(pool, min(600, len(pool))) if x.kind == c.kind and x.name != c.name
                    and deck.counts.get(x.name, 0) < builder.cap(x.name) and builder.normal_ok(x)
                    and (origin != "generic" or builder.rating_ok(x))]               # only generic filler obeys the rating limits
            cand = []
            for x in base:                                                  # only cards that are fine in this deck
                f, partner = builder.eval_needs(x, deck, pool, scan_partners=False)
                fit = builder.archetype_fit(x, deck) == 1.0
                if f >= 1.0 and partner is None and fit and not builder.junk(x, deck) and (not support or builder.synergy(x, deck) > 0 or x.generic):
                    cand.append((builder.level_bias(x, deck) * f * builder.help_factor(x, deck), x))
            near = {x.name for x in builder.rating_tier([x for _, x in cand])}  # nearest the rating limits first
            cand = [y for y in cand if y[1].name in near]
            if not cand:
                continue                                                    # nothing better: leave the card in place
            left = len(slots)
            removed = {i for i in slots}
            new_entries = [e for i, e in enumerate(entries) if i not in removed]
            while left > 0 and cand:
                w, x = rng.choices(cand, weights=[y[0] for y in cand])[0]
                n = rng.randint(1, min(builder.cap(x.name) - deck.counts.get(x.name, 0), left))
                for _ in range(n):
                    new_entries.append(Entry(x, origin, f"swapped in for {c.name} (needs uncovered)"))
                deck.counts[x.name] = deck.counts.get(x.name, 0) + n
                left -= n
                cand = [y for y in cand if y[1].name != x.name]
            entries = new_entries
            swaps.append((c.name, "->", [e.info.name for e in entries if e.reason.startswith(f"swapped in for {c.name}")][:3]))
            deck = restate(entries)                                           # refresh before judging the next card
    debug["swaps"] = swaps
    return entries


# --------------------------------------------------------------------------
# 4b. Enablers: when almost nothing in the Extra Deck can be summoned
# --------------------------------------------------------------------------
REMOVABLE = ("generic", "support2", "support1", "staple")              # main-deck cards an enabler may replace, least important first


def _plan_enablers(builder: Builder, extra_builder: ExtraBuilder, target: Info, entries: list[Entry], pool: list[Info],
                   rules: Rules, rng: random.Random, wanted: set) -> Optional[list[Info]]:
    """The main-deck cards (one Info per copy) that would make `target` fully summonable, or None if it would
       cost more than ENABLE_PER_TARGET cards or can't be done. Tries a few random picks (a Synchro's Levels
       must also add up, so the first Tuner chosen might not work)."""
    slots = extra_builder.slots.get(target.name)
    if not slots:
        return None                                                    # no material info: can't plan for it
    base = [e.info for e in entries if e.info.kind == "Monster"]       # the deck's monsters now, one per copy
    counts = {}
    for e in entries:
        counts[e.info.name] = counts.get(e.info.name, 0) + 1
    base_deck = restate(entries)                                       # the deck as it is (to check enablers' own needs)
    for _ in range(5):                                                 # a few attempts with different random picks
        add: list[Info] = []
        for sl in slots:
            if sl.extra_kind or sl.branches is None:
                continue                                               # Extra Deck materials / 'any monster': nothing to add
            have = sum(1 for m in base + add if card_matches(sl.branches, m.facts))
            missing = sl.need + 1 - have                               # want one spare copy (see summon_score)
            if missing <= 0:
                continue
            cands = [x for x in pool if x.main_deck and x.kind == "Monster" and card_matches(sl.branches, x.facts)
                     and counts.get(x.name, 0) + add.count(x) < builder.cap(x.name) and builder.normal_ok(x)]
            if not cands:
                break                                                  # nothing in this format fits the slot
            cands = [x for x in cands if not (x.row.get("required cards") or "").strip()
                     and strict_need_ok(builder, x, base_deck, [])] or cands   # Step 2: enablers whose own needs the deck meets
            x = rng.choices(cands, weights=[_fit_weight(c, wanted) for c in cands])[0]
            n = min(missing, builder.cap(x.name) - counts.get(x.name, 0) - add.count(x))
            add += [x] * n
        planned = restate(entries + [Entry(x, "material") for x in add])
        for item in requirements(target, rules):                       # e.g. a Fusion that needs Polymerization
            if satisfied(item, planned):
                continue
            kind, value = item
            if kind != "name" or norm(value) not in rules.by_name:
                add = None                                             # an archetype requirement: too vague to plan
                break
            need = rules.by_name[norm(value)]
            if not need.main_deck or builder.cap(need.name) <= counts.get(need.name, 0) or not builder.normal_ok(need):
                add = None                                             # not allowed in this format (or the Normal toggle is off)
                break
            add.append(need)
        if add is None or len(add) > ENABLE_PER_TARGET:
            continue                                                   # too expensive: try other picks
        if summon_score(slots, base + [x for x in add if x.kind == "Monster"]) >= FULL_SCORE:
            return add                                                 # this works
    return None


def add_enablers(builder: Builder, extra_builder: ExtraBuilder, entries: list[Entry], extra: list[Entry], req: Request,
                 pool: list[Info], rules: Rules, rng: random.Random, debug: dict, generic: bool) -> list[Entry]:
    """When the Extra Deck ended up with fewer than ENABLE_BELOW cards, find Extra Deck monsters the deck can
       ALMOST summon and swap in the few main-deck cards that unlock them (Tuners for a Synchro, the Fusion
       materials + Polymerization, same-Level monsters for an Xyz). Each one replaces a filler or support card,
       never a theme card, so the deck keeps its size. Returns the new main deck; adds the monsters to `extra`."""
    added = []                                                         # report: (Extra Deck monster, [cards added])
    if len(extra) >= ENABLE_BELOW or req.extra_deck_size < ENABLE_BELOW:
        debug["extra_enablers"] = added
        return entries                                                 # the Extra Deck is fine (or meant to be tiny)
    wanted = set().union(*[e.info.facts["archetype"] for e in entries]) if entries else set()
    in_extra = {e.info.name for e in extra}
    targets = [c for c in extra_builder.pool(req) if c.name not in in_extra and builder.cap(c.name) > 0]
    # (unlike the refill below, a Generic deck may take monsters that name their materials here:
    #  the enablers ARE those named cards, so nothing is left unplayable)
    rng.shuffle(targets)
    targets.sort(key=lambda c: -_fit_weight(c, wanted))                # the deck's own archetype first, then generic ones
    budget = ENABLE_BUDGET
    for target in targets[:60]:                                        # (looking at 60 is plenty, and keeps it fast)
        if budget <= 0 or len(extra) >= ENABLE_BELOW:
            break
        plan = _plan_enablers(builder, extra_builder, target, entries, pool, rules, rng, wanted)
        if not plan or len(plan) > budget:
            continue
        removable = [i for o in REMOVABLE for i, e in enumerate(entries) if e.origin == o]   # least important first
        if len(removable) < len(plan):
            continue                                                   # not enough filler to replace
        gone = set(removable[:len(plan)])
        entries = [e for i, e in enumerate(entries) if i not in gone]
        entries += [Entry(x, "material", f"added so the deck can summon {target.name}") for x in plan]
        extra.append(Entry(target, "generic", "Extra Deck monster unlocked by enabler cards"))
        added.append((target.name, [x.name for x in plan]))
        budget -= len(plan)
    debug["extra_enablers"] = added
    return entries


# --------------------------------------------------------------------------
# 4c. Genesys: keep the deck's points under the cap
# --------------------------------------------------------------------------
def enforce_points(builder: Builder, entries: list[Entry], extra: list[Entry], pool: list[Info],
                   rng: random.Random, debug: dict) -> list[Entry]:
    """Genesys only: while the Main + Extra Deck cost more points than the cap, take out one costly copy
       (least important origin first, then the most points) and, in the main deck, put a 0-point card of the
       same kind (Monster / Spell / Trap) in its place. Returns the new main deck; changes `extra` in place."""
    if builder.point_cap is None:
        return entries                                                     # not a Genesys deck
    order = {o: i for i, group in enumerate(TRIM_ORDER) for o in group}    # least important origin = smallest number
    removed = []
    while builder.deck_points(entries + extra) > builder.point_cap:
        costly = [(order.get(e.origin, 0), -builder.card_points(e.info.name), i, where)
                  for where, cards in (("main", entries), ("extra", extra))
                  for i, e in enumerate(cards) if builder.card_points(e.info.name) > 0]
        _, _, i, where = min(costly)                                       # least important, then most expensive
        cards = entries if where == "main" else extra
        gone = cards.pop(i)
        removed.append(gone.info.name)
        if where == "extra":
            continue                                                       # the Extra Deck just gets one card smaller
        counts = {}
        for e in entries:
            counts[e.info.name] = counts.get(e.info.name, 0) + 1
        # a 0-point card of the same kind: another copy of a deck card first, else a plain generic card
        same = [c for c in {e.info.name: e.info for e in entries}.values()
                if c.kind == gone.info.kind and builder.card_points(c.name) == 0 and counts.get(c.name, 0) < builder.cap(c.name)]
        others = [c for c in pool if c.kind == gone.info.kind and c.clean and c.main_deck and builder.normal_ok(c)
                  and builder.card_points(c.name) == 0 and counts.get(c.name, 0) < builder.cap(c.name)]
        choice = same or others
        if choice:
            entries.append(Entry(rng.choice(choice), gone.origin if same else "generic",
                                 f"in place of {gone.info.name} (Genesys points)"))
    debug["genesys_points"] = (builder.deck_points(entries + extra), builder.point_cap)
    debug["genesys_removed"] = removed
    return entries


# --------------------------------------------------------------------------
# 5. The whole thing
# --------------------------------------------------------------------------
class Deck:
    """A finished deck: main entries, extra entries and a debug dictionary."""
    def __init__(self, main, extra, debug):
        self.main, self.extra, self.debug = main, extra, debug


def _add_needed_cards(builder: Builder, extra_builder: ExtraBuilder, deck: State, extra: list[Entry], req: Request,
                      rules: Rules, rng: random.Random, debug: dict, pool: list[Info], generic: bool) -> tuple:
    """Step 3: cards the deck needs. Required cards (Polymerization ...), then the deck rules (a Ritual Spell
       for Ritual Monsters, Pendulum pairs, enough low-Level monsters), then cards that name each other. A card
       added by a rule or a mention may need cards of its own, so the required cards are checked once more."""
    deck, extra = add_required(builder, extra_builder, deck, extra, req, rules, rng, debug, pool)
    apply_rules(builder, deck, pool, rng, debug, generic, rules)
    if generic:
        debug["mention_cards_added"] = []                                   # a Generic deck has no archetype to pull named cards into
    else:
        apply_mentions(builder, deck, pool, rules, rng, debug)
    if debug.get("rule_cards_added") or debug.get("mention_cards_added"):   # a rule/mention card may have requirements of its own
        before = dict(debug["required_added"])
        deck, extra = add_required(builder, extra_builder, deck, extra, req, rules, rng, debug, pool)
        for k, v in before.items():                                         # report both rounds together
            debug["required_added"][k] = debug["required_added"].get(k, 0) + v
    return deck, extra


def _back_to_size(builder: Builder, deck: State, size: int, pool: list[Info], rng: random.Random,
                  debug: dict, generic: bool, rules: Optional[Rules] = None) -> list[Entry]:
    """Step 4: trim the main deck back to its size, replace cards whose needs aren't covered, and - since that can
       break a deck rule - re-apply the rules (up to twice). Returns the main deck as a list of entries."""
    entries = list(deck.entries)
    trim(entries, size, rng, debug.get("slots"))                            # back to the target size
    entries = swap_unmet(builder, entries, pool, rng, debug)                # fix uncovered needs
    trim(entries, size, rng, debug.get("slots"))
    for _ in range(2):                                                      # trimming/swapping may have broken a rule: re-check
        if not rule_violations(entries, builder):
            break
        recheck = restate(entries)
        apply_rules(builder, recheck, pool, rng, debug, generic, rules)
        entries = list(recheck.entries)
        trim(entries, size, rng, debug.get("slots"))
    return entries


def _drop_unsummonable(extra_builder: ExtraBuilder, entries: list[Entry], extra: list[Entry], rules: Rules,
                       debug: dict) -> tuple:
    """Step 5: Extra Deck monsters the finished main deck can't summon (summon score < DROP_BELOW) are dead weight:
       drop them - and any monster made FROM a dropped one (Curse of Dragon, the Magical Knight Dragon needs Gaia
       the Dragon Champion). Cards required by name stay. Returns (the Extra Deck kept, the cards dropped)."""
    monsters = [e.info for e in entries if e.info.kind == "Monster"]       # one Info per monster copy
    keep, dropped = [], []
    for e in extra:
        slots = extra_builder.slots.get(e.info.name)
        dead = slots is not None and e.origin != "required" and summon_score(slots, monsters) < DROP_BELOW
        (dropped if dead else keep).append(e)
    while True:                                                             # a monster made from a dropped one goes too
        gone = {e.info.name for e in dropped}
        stuck = [e for e in keep if e.origin != "required" and any(
                 kind == "name" and value in gone and not any(k.info.name == value for k in keep)
                 for kind, value in requirements(e.info, rules))]
        if not stuck:
            break
        keep = [e for e in keep if e not in stuck]
        dropped += stuck
    debug["extra_dropped_unsummonable"] = list(dict.fromkeys(e.info.name for e in dropped))
    return keep, dropped


def _refill_extra(builder: Builder, extra_builder: ExtraBuilder, entries: list[Entry], extra: list[Entry],
                  dropped: list[Entry], deck_arche: set, req: Request, rules: Rules, rng: random.Random,
                  debug: dict, generic: bool) -> None:
    """Step 7: fill the Extra Deck's gaps with monsters the main deck CAN fully summon - generic ones (no archetype)
       and the deck's own archetype first, other archetypes are unlikely picks. If none are fully summonable it
       stays short. Changes `extra` in place."""
    refill = []
    monsters = [e.info for e in entries if e.info.kind == "Monster"]       # (the enablers may have changed the deck)
    if (dropped or debug["extra_enablers"]) and len(extra) < req.extra_deck_size:
        have = {}                                                           # card name -> copies in the Extra Deck
        for e in extra:
            have[e.info.name] = have.get(e.info.name, 0) + 1
        current = restate(entries)                                          # a refill card must not need a card the main deck lacks
        cands = [c for c in extra_builder.pool(req) if summon_score(extra_builder.slots[c.name], monsters) >= FULL_SCORE
                 and have.get(c.name, 0) < builder.cap(c.name) and c.name not in {x.info.name for x in dropped}
                 and all(satisfied(i, current, extra) for i in requirements(c, rules))
                 and (c.clean or not generic)]                                # Generic deck: refill with clean extras only
        while cands and len(extra) < req.extra_deck_size:
            card = rng.choices(cands, weights=[_fit_weight(c, deck_arche) for c in cands])[0]
            n = min(extra_builder.copies(rng), builder.cap(card.name) - have.get(card.name, 0), req.extra_deck_size - len(extra))
            for _ in range(n):
                extra.append(Entry(card, "generic", "extra refill after dropping uncastable cards"))
            have[card.name] = have.get(card.name, 0) + n
            refill.append(card.name)
            cands = [c for c in cands if c.name != card.name]
    debug["extra_refilled"] = refill


# --------------------------------------------------------------------------
# 7c. Last check: no useless cards in the finished deck
# --------------------------------------------------------------------------
# A card is USELESS when (the same five rules playability_test.py measures):
#   strict need     its strictSupportedBy is not met by any other card (main or Extra Deck)
#   required card   a card it requires is missing ('required cards' column, Ritual spell / monster by name)
#   unsummonable    an Extra Deck monster whose materials the main deck can't supply
#   ritual          a Ritual Monster with no Ritual Spell, or a Ritual Spell with no Ritual Monster
#   floodgate       a floodgate that stops what at least CLASH_SHARE of its own deck does
# (generalSupportedBy needs do NOT count: they are 'nice to have', not 'can't work without')
LAST_PASSES = 3               # how many times the last check looks again (a swap can break another card)
LAST_SAMPLE = 600             # how many random pool cards are tried as replacements for one card (speed)
REPAIR_CAP = 4                # most card copies the last check may ADD to repair a card (instead of removing it), per deck
REPAIR_FROM = ("generic", "staple", "support2", "support1")   # which cards a repair may replace, least important first


def strict_need_ok(builder: Builder, c: Info, deck: State, extra_infos: list) -> bool:
    """True if the card has no strict need, or one of its strictSupportedBy branches is met:
       the TRAIT part by another card in the main or Extra Deck, and no MECHANIC part 'unmet'."""
    branches = parse_expr(c.row.get("strictSupportedBy", ""))
    if not branches or not c.needs:                                         # no strict need (or flavour text the builder ignores)
        return True
    others = [o for o in list(deck.unique.values()) + extra_infos if o.name != c.name]   # every OTHER card (one Info per name)
    for br in branches:                                                     # one OR-option at a time
        traits = tuple(a for a in br if not is_mechanic(a))                 # 'is it a WATER monster?' part   (hard)
        mechs = [a for a in br if is_mechanic(a)]                           # 'Self = Banish (GY)' part       (soft)
        t_ok = (not traits) or any(card_matches([traits], o.facts) for o in others)
        m_ok = all(need_status(m, deck.offered, deck.traits, builder.tree) != "unmet" for m in mechs)
        if t_ok and m_ok:
            return True                                                     # this branch works: the need is met
    return False


def can_summon(extra_builder: ExtraBuilder, name: str, monsters: list) -> bool:
    """True if the main deck's monsters (one Info per copy) can supply EVERY material slot of this Extra Deck monster -
       just enough copies, no spare needed - and a Synchro's Levels can add up. A Fusion substitute may stand in for
       one named material, as in summon_score()."""
    slots = extra_builder.slots.get(name)
    if not slots:
        return True                                                         # no material info: we can't judge it, so keep it
    haves = [sum(1 for m in monsters if card_matches(s.branches, m.facts)) if s.branches else len(monsters) for s in slots]   # copies that fit each slot
    if slots.sub_for is not None and any(lim is not False and (lim == "" or lim in slots.sub_for)
                                         for lim in map(substitute_limit, monsters)):   # the deck has a substitute that works here
        short = [i for i, s in enumerate(slots) if named_slot(s) and haves[i] < s.need]
        if short:
            haves[short[0]] += 1                                            # it fills ONE missing named material
    if any(not s.extra_kind and h < s.need for s, h in zip(slots, haves)):
        return False                                                        # some slot can't be filled
    if slots.sumlevel and not any(s.extra_kind for s in slots) and not sums_ok(slots, monsters):
        return False                                                        # Synchro: the Levels never add up
    if slots.link and sum(s.need for s in slots) > slots.link:
        return False                                                        # Link: more materials than its rating allows
    return True


def floodgate_clash(builder: Builder, c: Info, deck: State) -> bool:
    """True for a floodgate that stops BOTH players and whose 'clashes' cover CLASH_SHARE of the deck's copies
       (unless its own exceptions cover BROAD_SHARE of the deck, e.g. Barrier Statue of the Inferno in a FIRE deck)."""
    total = len(deck.entries)
    if total == 0 or builder.one_sided(c):
        return False                                                        # empty deck, or it only stops the opponent
    for gate in (g for g in builder.tags(c) if g in FLOODGATE_RULES):
        clashing = sum(deck.counts[o.name] for o in deck.unique.values()
                       if o.name != c.name and builder.hits(builder.tags(o), FLOODGATE_RULES[gate]["clashes"]))
        if clashing / total >= CLASH_SHARE and builder.coverage(c, deck) < BROAD_SHARE:
            return True
    return False


def main_card_problem(builder: Builder, rules: Rules, c: Info, deck: State, extra: list[Entry], extra_infos: list) -> str:
    """Why this MAIN deck card is useless in `deck` ('' = it works). `deck` is a State that already holds the card."""
    if not strict_need_ok(builder, c, deck, extra_infos):
        return "strict need"
    if any(not satisfied(i, deck, extra) for i in requirements(c, rules)):
        return "required card"
    if c.kind == "Monster" and "ritual" in c.facts["trait"] \
            and not any("ritual spell" in o.facts["trait"] for o in deck.unique.values()):
        return "ritual"                                                     # a Ritual Monster with no Ritual Spell
    if "ritual spell" in c.facts["trait"] \
            and not any("ritual" in o.facts["trait"] and o.kind == "Monster" for o in deck.unique.values()):
        return "ritual"                                                     # a Ritual Spell with nothing to summon
    if floodgate_clash(builder, c, deck):
        return "floodgate"
    return ""


def works_in(builder: Builder, rules: Optional[Rules], c: Info, entries: list[Entry], extra: list[Entry]) -> bool:
    """Step 2: would this card WORK if it were added to the deck (`entries`)? Used by every step that adds cards, so they
       stop adding cards the last check would only have to swap out again. Without `rules` (no name finder) we can only check
       the strict need, so a card with a 'required cards' entry counts as not working."""
    deck = restate(list(entries) + [Entry(c, "test")])                    # the deck WITH the card in it
    extra_infos = list({e.info.name: e.info for e in extra}.values())
    if rules is None:
        return strict_need_ok(builder, c, deck, extra_infos) and not (c.row.get("required cards") or "").strip()
    return not main_card_problem(builder, rules, c, deck, extra, extra_infos)


def useless_cards(builder: Builder, extra_builder: ExtraBuilder, rules: Rules, entries: list[Entry],
                  extra: list[Entry]) -> tuple[dict, dict]:
    """Every useless card in a finished deck: ({main card name: why}, {Extra Deck card name: why})."""
    deck = restate(entries)
    extra_infos = list({e.info.name: e.info for e in extra}.values())       # one Info per Extra Deck name
    monsters = [e.info for e in entries if e.info.kind == "Monster"]        # one Info per main-deck monster copy
    bad_main = {}
    for c in deck.unique.values():
        why = main_card_problem(builder, rules, c, deck, extra, extra_infos)
        if why:
            bad_main[c.name] = why
    bad_extra = {}
    for c in extra_infos:
        if not can_summon(extra_builder, c.name, monsters):
            bad_extra[c.name] = "unsummonable"
        elif any(not satisfied(i, deck, extra) for i in requirements(c, rules)):
            bad_extra[c.name] = "required card"
    return bad_main, bad_extra


def _dependents(builder: Builder, rules: Rules, entries: list[Entry], extra: list[Entry], gone: set) -> set:
    """Cards that WORK now but would stop working once the cards in `gone` are removed (Levia-Dragon - Daedalus when
       the deck's only Umi goes). Repeats until nothing new breaks, so a chain of dependents goes together."""
    def broken(deck_entries: list[Entry]) -> set:                           # names failing the strict-need / required checks
        deck = restate(deck_entries)
        extra_infos = list({e.info.name: e.info for e in extra}.values())
        return {c.name for c in deck.unique.values()
                if not strict_need_ok(builder, c, deck, extra_infos) or any(not satisfied(i, deck, extra) for i in requirements(c, rules))}
    fine_before = {e.info.name for e in entries} - broken(entries) - gone   # the cards that work right now
    gone = set(gone)
    while True:
        left = [e for e in entries if e.info.name not in gone]
        newly = (broken(left) & fine_before) - gone                         # worked before, broken without `gone`
        if not newly:
            return gone
        gone |= newly


def _replacement(builder: Builder, rules: Rules, removed: Info, origin: str, entries: list[Entry], extra: list[Entry],
                 pool: list[Info], rng: random.Random, generic: bool) -> Optional[Info]:
    """A card of the same kind (Monster / Spell / Trap) that WORKS in this deck, chosen by the builder's usual weights
       (the deck's own archetype is far likelier). If none works, a plain clean card of that kind (no needs at all)."""
    deck = restate(entries)
    extra_infos = list({e.info.name: e.info for e in extra}.values())
    base = [x for x in rng.sample(pool, min(LAST_SAMPLE, len(pool)))
            if x.kind == removed.kind and x.main_deck and x.name != removed.name
            and deck.counts.get(x.name, 0) < builder.cap(x.name) and builder.normal_ok(x)
            and (origin != "generic" or builder.rating_ok(x))]              # only generic filler obeys the rating limits
    good = []
    for x in base:
        if builder.junk(x, deck) or (generic and not x.clean):
            continue                                                        # can't really work here / Generic deck: clean cards only
        test = restate(entries + [Entry(x, origin)])                        # the deck WITH the candidate in it
        if main_card_problem(builder, rules, x, test, extra, extra_infos):
            continue                                                        # it would be useless too
        good.append((builder.level_bias(x, deck) * builder.archetype_fit(x, deck) * builder.help_factor(x, deck)
                     * builder.floodgate_factor(x, deck), x))
    near = {x.name for x in builder.rating_tier([x for _, x in good])}      # nearest the rating limits first
    good = [g for g in good if g[1].name in near]
    if not good:                                                            # nothing fits: a plain card that works anywhere
        plain = [x for x in pool if x.kind == removed.kind and x.main_deck and x.clean and x.name != removed.name
                 and deck.counts.get(x.name, 0) < builder.cap(x.name) and builder.rating_ok(x) and builder.normal_ok(x)
                 and not floodgate_clash(builder, x, restate(entries + [Entry(x, origin)]))]
        good = [(builder.level_bias(x, deck), x) for x in plain]
    if not good:
        return None
    return rng.choices([x for _, x in good], weights=[max(w, 1e-6) for w, _ in good])[0]


def _repair(builder: Builder, rules: Rules, entries: list[Entry], extra: list[Entry], bad_main: dict, pool: list[Info],
            rng: random.Random, budget: int, repairs: list) -> tuple[list[Entry], int]:
    """Repair before removing: a card whose ONLY problem is a missing required card (or a Ritual Monster missing the
       spell it names) gets that card added, in place of a filler / staple / support card that nothing depends on.
       Spends at most `budget` copies. Returns (the new main deck, budget left); notes each repair in `repairs`."""
    allowed = {c.name for c in pool}                                        # cards this format allows
    deck = restate(entries)
    needed_by = {v for c in deck.unique.values() for k, v in requirements(c, rules) if k == "name"}   # names some deck card requires
    safe: dict = {}                                                         # card name -> can it go without breaking anything?
    for name, why in bad_main.items():
        if budget <= 0:
            break                                                           # repair budget used up
        c = deck.unique.get(name)
        if why not in ("required card", "ritual") or c is None:
            continue                                                        # only these two problems can be repaired
        missing = [i for i in requirements(c, rules) if not satisfied(i, deck, extra)]
        if not missing or len(missing) > budget:
            continue                                                        # nothing named to add (or too much)
        adds = []                                                           # the cards to add, one copy each
        for kind, value in missing:
            if kind == "name":                                              # a card by name: that exact card
                info = rules.by_name.get(norm(value))
                if info is None or not info.main_deck or info.name not in allowed or deck.counts.get(info.name, 0) >= builder.cap(info.name) \
                        or not builder.normal_ok(info):
                    adds = None                                             # Extra Deck card / not allowed / at its limit / Normal toggle
                    break
            else:                                                           # 'any card of archetype X': one that works here
                cands = [x for x in pool if value in x.facts["archetype"] and x.main_deck and deck.counts.get(x.name, 0) < builder.cap(x.name)
                         and builder.normal_ok(x)]
                cands = [x for x in cands if works_in(builder, rules, x, entries, extra)]
                if not cands:
                    adds = None
                    break
                info = rng.choice(cands)
            adds.append(info)
        if not adds:
            continue                                                        # can't repair this one: the swap below handles it
        victims = []                                                        # positions of the copies the added cards replace
        for origin in REPAIR_FROM:                                          # least important origin first
            for i, e in enumerate(entries):
                n = e.info.name
                if len(victims) == len(adds):
                    break
                if e.origin != origin or n == name or n in needed_by or i in victims:
                    continue                                                # wrong kind of card / the card we repair / required
                if n not in safe:                                           # would anything stop working without it?
                    safe[n] = _dependents(builder, rules, entries, extra, {n}) == {n}
                if safe[n]:
                    victims.append(i)
        if len(victims) < len(adds):
            continue                                                        # not enough filler to replace
        for i, info in zip(victims, adds):
            repairs.append(f"{entries[i].info.name} -> {info.name} (needed by {name})")
            entries[i] = Entry(info, "required", f"last check: repair, required by {name}")
        budget -= len(adds)
        deck = restate(entries)                                             # refresh before the next card
        needed_by = {v for c2 in deck.unique.values() for k, v in requirements(c2, rules) if k == "name"}
        safe = {}
    return entries, budget


def last_check(builder: Builder, extra_builder: ExtraBuilder, entries: list[Entry], extra: list[Entry], req: Request,
               pool: list[Info], rules: Rules, rng: random.Random, debug: dict, generic: bool) -> list[Entry]:
    """Step 7c: swap out every useless card (see the rules above), whatever step put it in - required, material and
       mention cards included. First it tries a REPAIR (_repair: add the required card that is missing, up to REPAIR_CAP). Cards that only worked BECAUSE of a removed card go with it. Each removed copy is replaced
       by a card of the same kind that works here; the Extra Deck's gaps are refilled with fully summonable monsters.
       Runs up to LAST_PASSES times. Returns the new main deck; changes `extra` in place."""
    swaps = []                                                              # report: 'X -> Y (why)'
    repairs = []                                                            # report: 'filler -> missing card (needed by X)'
    repair_left = REPAIR_CAP                                                # copies a repair may still add
    deck_arche = set().union(*[e.info.facts["archetype"] for e in entries]) if entries else set()   # for the Extra Deck refill
    for _ in range(LAST_PASSES):
        bad_main, bad_extra = useless_cards(builder, extra_builder, rules, entries, extra)
        if not bad_main and not bad_extra:
            break                                                           # every card works: done
        if repair_left > 0 and bad_main:                                    # (0) repair before removing: add missing required cards
            entries, repair_left = _repair(builder, rules, entries, extra, bad_main, pool, rng, repair_left, repairs)
            bad_main, bad_extra = useless_cards(builder, extra_builder, rules, entries, extra)
            if not bad_main and not bad_extra:
                break                                                       # the repairs fixed everything
        # (1) Extra Deck: take out the monsters that can't work; the refill at the end fills their places
        dropped = [e for e in extra if e.info.name in bad_extra]
        extra[:] = [e for e in extra if e.info.name not in bad_extra]       # [:] = change the caller's list itself
        for n, why in bad_extra.items():
            swaps.append(f"Extra Deck: {n} removed ({why})")
        # (2) main deck: the useless cards plus anything that only worked because of them
        gone = _dependents(builder, rules, entries, extra, set(bad_main))
        removed = [e for e in entries if e.info.name in gone]               # every copy that goes, with its origin
        entries = [e for e in entries if e.info.name not in gone]
        for e in removed:                                                   # one replacement per removed copy
            new = _replacement(builder, rules, e.info, e.origin, entries, extra, pool, rng, generic)
            why = bad_main.get(e.info.name, "depended on a removed card")
            if new is None:
                entries.append(e)                                           # nothing at all fits: keep it (reported below)
                continue
            entries.append(Entry(new, e.origin, f"last check: replaced {e.info.name} ({why})"))
            swaps.append(f"{e.info.name} -> {new.name} ({why})")
        # (3) Extra Deck gaps: monsters this main deck can fully summon
        _last_refill(builder, extra_builder, entries, extra, req, rules, rng, deck_arche, generic, {e.info.name for e in dropped})
    bad_main, bad_extra = useless_cards(builder, extra_builder, rules, entries, extra)
    debug["last_check"] = swaps
    debug["last_check_repairs"] = repairs
    debug["still_useless"] = {**bad_main, **{f"{n} (Extra Deck)": w for n, w in bad_extra.items()}}
    return entries


def _last_refill(builder: Builder, extra_builder: ExtraBuilder, entries: list[Entry], extra: list[Entry], req: Request,
                 rules: Rules, rng: random.Random, deck_arche: set, generic: bool, banned: set) -> None:
    """Fill the Extra Deck back up with monsters the main deck can FULLY summon whose requirements are all in the deck
       (the deck's own archetype and generic ones first). `banned` = monsters the last check just removed."""
    monsters = [e.info for e in entries if e.info.kind == "Monster"]
    current = restate(entries)
    have = {}                                                               # card name -> copies already in the Extra Deck
    for e in extra:
        have[e.info.name] = have.get(e.info.name, 0) + 1
    cands = [c for c in extra_builder.pool(req) if c.name not in banned and have.get(c.name, 0) < builder.cap(c.name)
             and (c.clean or not generic)                                   # Generic deck: clean extras only
             and summon_score(extra_builder.slots[c.name], monsters) >= FULL_SCORE
             and all(satisfied(i, current, extra) for i in requirements(c, rules))]
    while cands and len(extra) < req.extra_deck_size:
        card = rng.choices(cands, weights=[_fit_weight(c, deck_arche) for c in cands])[0]
        n = min(extra_builder.copies(rng), builder.cap(card.name) - have.get(card.name, 0), req.extra_deck_size - len(extra))
        for _ in range(n):
            extra.append(Entry(card, "generic", "last check: Extra Deck refill (fully summonable)"))
        have[card.name] = have.get(card.name, 0) + n
        cands = [c for c in cands if c.name != card.name]


def _final_report(builder: Builder, entries: list[Entry], extra: list[Entry], req: Request, chosen: dict,
                  debug: dict) -> None:
    """Step 9: the checks shown in the preview's Debug section - copy limits, sizes, rules, needs, junk."""
    final_deck = restate(entries)
    over = {n: c for n, c in final_deck.counts.items() if c > builder.cap(n)}   # over its copy limit (3 / banlist / Highlander)
    for e in extra:                                                         # the Extra Deck counts too
        k = sum(1 for x in extra if x.info.name == e.info.name)
        if k > builder.cap(e.info.name):
            over[e.info.name] = k
    debug["over_copy_limit"] = over
    debug["format"] = {"banlist": req.banlist or "Unlimited", "release-date pool": bool(req.banlist and req.date_pool)}
    debug["main_size"], debug["extra_size"] = len(entries), len(extra)
    debug["rule_violations"] = rule_violations(entries, builder)
    debug["unmet_needs"] = [c.name for c in final_deck.unique.values() if not needs_covered(builder, c, final_deck)]
    debug["junk_cards"] = [c.name for c in final_deck.unique.values() if builder.junk(c, final_deck)]   # cards that can't really work here
    debug["aliases"] = {" / ".join(k): ", ".join(f"{n} x{v.count(n)}" for n in dict.fromkeys(v)) for k, v in chosen.items()}   # 'Harpie Lady 1 x1, Harpie Lady 2 x2' 


def _build_deck(builder: Builder, extra_builder: ExtraBuilder, req: Request) -> Deck:
    """One whole deck, in nine steps (each one is a function in this file or ygo_extra.py):"""
    # 1. theme, support, staples and filler (ygo_builder.py), plus the Extra Deck, in the chosen order (ygo_extra.py)
    deck, extra, debug = generate_state(builder, extra_builder, req)
    generic = deck.generic_deck                                             # remembered: restate() makes fresh States
    rng = random.Random(f"{req.seed}-final")                                # separate, reproducible stream for the final pass
    rules = Rules(builder)                                                  # name resolver + archetype list
    size = max(40, min(60, req.main_deck_size))
    pool = builder.allowed(req, rated=False)                                # theme / synergy cards ignore the rating limits

    # 2. alias names (Harpie Lady / Harpie Lady 1 ...) mixed - in BOTH decks - then back under the copy limits
    chosen: dict = {}                                                       # alias names used, shared by main + extra
    apply_aliases(deck.entries, rules, rng, chosen, builder.cap)
    apply_aliases(extra, rules, rng, chosen, builder.cap)
    deck = restate(deck.entries)
    deck = cap_copies(builder, deck, pool, size, rng, rules)

    deck, extra = _add_needed_cards(builder, extra_builder, deck, extra, req, rules, rng, debug, pool, generic)   # 3.
    entries = _back_to_size(builder, deck, size, pool, rng, debug, generic, rules)                                    # 4.
    deck_arche = set().union(*[e.info.facts["archetype"] for e in entries]) if entries else set()   # (for step 7)
    extra, dropped = _drop_unsummonable(extra_builder, entries, extra, rules, debug)                             # 5.
    # 6. almost no Extra Deck left? swap a few main-deck cards for ones that unlock some (Tuners, Polymerization ...)
    entries = add_enablers(builder, extra_builder, entries, extra, req, pool, rules, rng, debug, generic)
    _refill_extra(builder, extra_builder, entries, extra, dropped, deck_arche, req, rules, rng, debug, generic)   # 7.
    # 7b. steps 3-7 only check each NAME's limit, so they can add Harpie Lady 1 to a deck that already has 3 Harpie Lady:
    #     cut every alias group back to one card's limit again (a deck that is fine is left exactly as it is)
    entries = cap_copies(builder, restate(entries), pool, size, rng, rules).entries
    # 7c. last check: any card that still can't work (unmet strict need, missing required card, unsummonable Extra Deck
    #     monster, Ritual without its partner, a floodgate that stops its own deck) is swapped for one that can
    entries = last_check(builder, extra_builder, entries, extra, req, pool, rules, rng, debug, generic)
    entries = enforce_points(builder, entries, extra, pool, rng, debug)     # 8. Genesys: under the points cap
    _final_report(builder, entries, extra, req, chosen, debug)              # 9.
    return Deck(entries, extra, debug)


def build_deck(builder: Builder, extra_builder: ExtraBuilder, req: Request) -> Deck:
    """Build a deck. If the era / rating filters leave no cards, widen them step by step instead of failing:
       first drop the rating limits, then the era limit. What was widened is reported in debug['filters_widened']."""
    from dataclasses import replace                                         # copy the request with some fields changed
    steps = [("rating limits", dict(min_card_rating=None, max_card_rating=None)),
             ("era limit", dict(max_era=None))]
    widened = []
    while True:
        try:
            deck = _build_deck(builder, extra_builder, req)
            deck.debug["filters_widened"] = widened
            return deck
        except ValueError as e:
            if "No cards left" not in str(e) or not steps:
                raise                                                       # a different error, or nothing left to widen
            name, change = steps.pop(0)
            widened.append(name)
            req = replace(req, **change)


# --------------------------------------------------------------------------
# 6. Export (.ydk for EDOPro / Project Ignis, .txt for reading, .csv for spreadsheets)
# --------------------------------------------------------------------------
def export_deck(deck: Deck, prefix: str, verbose: bool = True) -> tuple[str, str, str]:
    """Write <prefix>.ydk/.txt/.csv (verbose=False leaves the reasons out). Never overwrites: if files exist we add _(1), _(2) ... Returns the three paths."""
    base = Path(prefix) if str(prefix).strip() else Path("random_deck")
    if base.is_dir():
        base = base / "random_deck"
    base.parent.mkdir(parents=True, exist_ok=True)
    i = 0
    while True:
        cand = base if i == 0 else Path(f"{base}_({i})")
        if not any(cand.with_suffix(x).exists() for x in (".ydk", ".txt", ".csv")):
            base = cand
            break
        i += 1

    def cid(e: Entry) -> str:
        return (next(iter(e.info.row.values()), "") or "").strip()         # 'Card Id' is the first column

    with open(base.with_suffix(".ydk"), "w", encoding="utf-8", newline="\n") as f:       # EDOPro format
        f.write("#created by ygo_randomizer\n#main\n")
        f.writelines(f"{cid(e)}\n" for e in deck.main if cid(e))
        f.write("#extra\n")
        f.writelines(f"{cid(e)}\n" for e in deck.extra if cid(e))
        f.write("!side\n")
    with open(base.with_suffix(".txt"), "w", encoding="utf-8", newline="\n") as f:       # readable list with reasons
        f.write(f"Main Deck ({len(deck.main)} cards):\n")
        for e in deck.main:
            f.write(f"- {e.info.name} [{e.origin}]" + (f" :: {e.reason}" if verbose else "") + "\n")
        f.write(f"\nExtra Deck ({len(deck.extra)} cards):\n")
        for e in deck.extra:
            f.write(f"- {e.info.name}" + (f" :: {e.reason}" if verbose else "") + "\n")
    with open(base.with_suffix(".csv"), "w", encoding="utf-8", newline="") as f:         # spreadsheet
        w = csv.writer(f)
        w.writerow(["section", "name", "origin", "card_id", "reason"])
        for sec, lst in (("main", deck.main), ("extra", deck.extra)):
            for e in lst:
                w.writerow([sec, e.info.name, e.origin, cid(e), e.reason if verbose else ""])
    return str(base.with_suffix(".ydk")), str(base.with_suffix(".txt")), str(base.with_suffix(".csv"))


# --------------------------------------------------------------------------
# Self-test:  python ygo_final.py
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile
    here = Path(__file__).resolve().parent / "data"                         # the data folder (program/data)
    builder = Builder(read_csv(here / "cards.csv"), load_tree(here / "game_mechanics_tree.csv"))
    extra_builder = ExtraBuilder(builder)

    # One deck in full.
    req = Request(seed=21, deck_type_1_label="Archetype = blackwing", support_1_label="Random")
    d = build_deck(builder, extra_builder, req)
    print("required cards added:", d.debug["required_added"])
    print("extra dropped for requirements:", d.debug["extra_dropped_for_requirements"])
    print("swaps:", d.debug["swaps"])
    print("sizes:", d.debug["main_size"], d.debug["extra_size"], "| over copy limit:", d.debug["over_copy_limit"], "| unmet:", d.debug["unmet_needs"])
    for origin in ("theme1", "support1", "partner", "material", "required", "staple", "generic"):
        names = [e.info.name for e in d.main if e.origin == origin]
        if names:
            print(f"  [{origin}] " + ", ".join(f"{names.count(n)}x {n}" for n in dict.fromkeys(names)))
    print("  [extra] " + ", ".join(dict.fromkeys(e.info.name for e in d.extra)))

    # Many decks: every rule must hold.
    bad = 0
    stats = {"unmet_before": 0, "unmet_after": 0}
    tests = [dict(deck_type_1_label=t, build_order=o) for t in ("Archetype = blackwing", "Archetype = laval", "Archetype = gishki", "Archetype = nekroz", "Archetype = performapal", "Random")
             for o in ("main-first", "extra-first")]
    for kw in tests:
        for seed in range(3):
            r = Request(seed=seed, **kw)
            dk = build_deck(builder, extra_builder, r)
            final_deck = restate(dk.main)
            ok = (len(dk.main) == 40 and len(dk.extra) <= 15 and not dk.debug["over_copy_limit"])
            # every extra monster's required cards should be in the main deck now
            rules = Rules(builder)
            miss = [i for e in dk.extra for i in requirements(e.info, rules) if not satisfied(i, final_deck, dk.extra)]
            if dk.debug["rule_violations"]:
                bad += 1
                print("RULE BROKEN", kw, seed, dk.debug["rule_violations"])
            if not ok or miss:
                bad += 1
                print("PROBLEM", kw, seed, len(dk.main), len(dk.extra), dk.debug["over_copy_limit"], miss[:3])
            stats["unmet_after"] += len(dk.debug["unmet_needs"])
    print(f"\n{len(tests)*3} decks checked, {bad} with problems; leftover cards with uncovered needs in total: {stats['unmet_after']}")

    # Reproducible?
    a = [e.info.name for e in build_deck(builder, extra_builder, Request(seed=5, deck_type_1_label="Random", build_order="random")).main]
    c = [e.info.name for e in build_deck(builder, extra_builder, Request(seed=5, deck_type_1_label="Random", build_order="random")).main]
    print("same seed identical:", a == c)

    # Export test (into a temp folder).
    with tempfile.TemporaryDirectory() as tmp:
        paths = export_deck(d, str(Path(tmp) / "test_deck"))
        paths2 = export_deck(d, str(Path(tmp) / "test_deck"))               # second export must not overwrite
        print("export:", [Path(p).name for p in paths], [Path(p).name for p in paths2])
        print("ydk first lines:", Path(paths[0]).read_text().splitlines()[:5])
