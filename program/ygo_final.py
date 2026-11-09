"""
ygo_final.py  --  builds a WHOLE deck.

WHERE THIS FITS: the website (web/app.py) calls build_deck() here. It uses ygo_builder.py (the main deck) and
ygo_extra.py (the Extra Deck), then finishes the deck in the steps below. (The page itself does the exports.)

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
  9. the checks saved in the deck's debug notes            _final_report()
  build_deck() runs it all, and widens the era / rating filters step by step if they leave no cards.

NAMES USED HERE
  builder / extra_builder   the main-deck and Extra Deck builders (they know every card and the format's limits)
  deck                      a State: the main deck being built (entries, counts, archetypes ...)
  entries / extra           the main deck / Extra Deck as plain lists of Entry (one per card copy)
  req                       the Request: every setting from the page
  rules                     a Rules: turns the 'required cards' column into requirements, finds cards by name
  origin                    why a card is in the deck: theme1, support1, staple, generic, required, material ...
                            (TRIM_ORDER: which origins go first when the deck is too big)
"""
from __future__ import annotations                          # lets type hints name classes that are defined further down the file

import random                                               # seeded dice: the same seed always rolls the same numbers
import re                                                   # regular expressions (text patterns)
from collections import Counter            # counting Types and archetypes
from pathlib import Path                                    # file paths (used by the self-test at the bottom)
from typing import Optional                                 # Optional[X] = "an X or None"

from ygo_data import card_matches, is_mechanic, need_status, norm, read_csv, load_tree, parse_expr  # card-matching helpers from the data loader
from ygo_builder import Builder, Entry, Info, Request, State, FLOODGATE_RULES, CLASH_SHARE, BROAD_SHARE  # the main-deck engine and its shared constants
# the Extra Deck engine and its helpers
from ygo_extra import ExtraBuilder, FULL_SCORE, generate_state, summon_score, sums_ok, substitute_limit, named_slot
from ygo_names import NameResolver                          # finds the real card for an old / misspelled name
from ygo_fusion_searchers import RESTRICTIONS as ARCHETYPE_LOCKED_FUSION_SEARCHERS  # Fusion searchers that only fetch one archetype's Polymerization

STARTERS = 8                  # a deck wants at least this many Level 4-or-lower monsters (fewer if it has few monsters)
DROP_BELOW = 0.6              # Extra Deck cards the main deck can fill less than this share of materials for are dropped
MENTION_POPULAR = 12          # a card named by more cards than this counts as a staple
MENTION_CAP = 6               # soft limit on copies added only because cards name each other
STAPLE_SEARCHER_CHANCE = 0.35  # chance a deck gets ONE card that names a staple it has (a Polymerization searcher: Vyon, King of the Swamp ...)
SUBSTITUTE_EDGE = 5.0          # ...and a searcher that is also a Fusion substitute the deck can use (King of the Swamp) is 5x as likely
REQUIRED_CAP = 8              # soft limit on main-deck copies added only because some card requires them
ENABLE_BELOW = 5              # an Extra Deck smaller than this gets 'enabler' cards in the main deck (see add_enablers)
ENABLE_BUDGET = 8             # most main-deck cards add_enablers may swap in, in total
ENABLE_PER_TARGET = 5         # most main-deck cards it may spend on unlocking one Extra Deck monster
SWAP_PASSES = 3               # how many times we look for cards with uncovered needs and replace them
# which origins are cut first when the deck is too big (first group = cut first; 'required' = cut last)
TRIM_ORDER = [["generic"], ["staple"], ["support2", "support1"], ["theme2", "theme1"], ["partner", "mention"], ["material"], ["required"]]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def restate(entries: list[Entry], quota: Optional[dict] = None) -> State:  # function: rebuild a deck State from a plain list of card copies
    """Rebuild a State (counts, mechanics, traits ...) from a plain list of entries after we edit the list."""
    deck = State(quota=None)                                # start from an empty deck with no type targets
    groups: dict = {}                                                   # (name, origin, reason) -> [Info, copies]
    for e in entries:                                       # go through every card copy
        key = (e.info.name, e.origin, e.reason)             # copies with the same name, origin and reason are one group
        groups.setdefault(key, [e.info, 0])[1] += 1         # count the copies in each group
    for (_name, origin, reason), (info, n) in groups.items():  # add each group back to the new State
        deck.add(info, n, origin, reason)                   # (this updates counts, mechanics offered, traits, archetypes)
    # keep the ORIGINAL order of entries (State.add appends grouped copies, which is fine for counting)
    deck.entries = list(entries)                            # replace the grouped order with the original order
    return deck                                             # hand back the rebuilt deck


def needs_covered(builder: Builder, c: Info, deck: State) -> bool:  # function: are this card's needs met by the deck?
    """True if the card has no needs, or at least one need-branch is fully covered by the deck."""
    if not c.needs:                                         # a card that needs nothing is always fine
        return True                                         # (no needs: covered)
    for branch in c.needs:                                  # each branch is one way of being satisfied ('needs A and B' OR 'needs C')
        traits = tuple(a for a in branch if not is_mechanic(a))  # the trait part of the branch (e.g. Archetype = Blackwing): needs a real card in the deck
        mechs = [a for a in branch if is_mechanic(a)]       # the mechanic part (e.g. Self = Special Summon): needs a card that offers it
        # trait part met if ANOTHER card in the deck matches it (the card itself doesn't count)
        trait_ok = (not traits) or any(o.name != c.name and card_matches([traits], o.facts) for o in deck.unique.values())
        mech_ok = all(need_status(m, deck.offered, deck.traits, builder.tree) != "unmet" for m in mechs)  # mechanic part met unless the deck has nothing that offers it
        if trait_ok and mech_ok:                            # both halves met: this branch works
            return True                                     # so the card's needs are covered
    return False                                            # no branch worked


class Rules:                                                # class: everything needed to read the 'required cards' column
    """Everything needed to turn the `required cards` column into concrete requirements."""  # (docstring)
    def __init__(self, builder: Builder):                   # set up from the builder's card list
        self.infos = builder.infos                          # every card, pre-digested
        self.resolver = NameResolver([i.name for i in builder.infos])                 # finds the real card for old / garbled names
        self.by_name = {norm(i.name): i for i in builder.infos}  # normalised name -> card, for quick lookups
        self.archetypes = {a for i in builder.infos for a in i.facts["archetype"]}    # every archetype that exists
        self._graph = None                                                       # who names whom (built on first use)

    def graph(self) -> tuple[dict, dict]:                   # method: who names whom (built once, then reused)
        """(mentions, mentioned_by): for every card, the cards it names in its text, and the cards that name it."""
        if self._graph is None:                             # not built yet?
            mentions: dict = {}                             # card name -> cards it names
            mentioned_by: dict = {}                         # card name -> cards that name it
            for i in self.infos:                            # go through every card
                found = mentioned_cards(i, self)            # the real cards named in quotes in its text
                mentions[i.name] = found                    # remember what this card names
                for m in found:                             # and for each named card...
                    mentioned_by.setdefault(m.name, []).append(i)  # ...remember that this card names it
            self._graph = (mentions, mentioned_by)          # save both tables so the next call is instant
        return self._graph                                  # hand back (mentions, mentioned_by)


def quoted_cards(info: Info, rules: Rules) -> list[Info]:   # function: the cards quoted in a card's text
    """Real cards whose names appear in quotes inside this card's text (not the card itself)."""
    found = []                                              # the list we build
    for q in re.findall(r'"([^"]+)"', info.row.get("card text", "")):  # every "quoted" piece of the card's text
        c = rules.by_name.get(norm(q))                      # is it a real card name?
        if c is not None and c.name != info.name and c not in found:  # a real card, not the card itself, not already listed
            found.append(c)                                 # keep it
    return found                                            # hand back the list


# A name after these words is an EXCLUSION ("except 'X'"), not something the card wants.
EXCEPT_RX = re.compile(r'(?:except|other than|excluding)\s+(?:the\s+)?"[^"]+"', re.I)  # 'except "X"' / 'other than "X"' / 'excluding "X"' (with an optional 'the')


def mentioned_cards(info: Info, rules: Rules) -> list[Info]:  # function: cards a card's text names, without the excluded ones
    """Real cards named in quotes in this card's text, leaving out itself and any 'except "X"' exclusions."""
    text = EXCEPT_RX.sub("", info.row.get("card text", ""))  # delete every exclusion from the text first
    found = []                                              # the list we build
    for q in re.findall(r'"([^"]+)"', text):                # every "quoted" piece left
        c = rules.by_name.get(norm(q))                      # is it a real card name?
        if c is not None and c.name != info.name and c not in found:  # real, not itself, not listed yet
            found.append(c)                                 # keep it
    return found                                            # hand back the list


def requirements(info: Info, rules: Rules) -> list[tuple]:  # function: what a card requires, as a list of (kind, value)
    """List what a card requires, as ('name', exact card name) or ('arche', archetype).
       Sources, in order:
         1. the `required cards` column. 'Name = Polymerization' is a card; 'Name = Dark Contract' is an
            archetype ('any Dark Contract card'); anything else (words like 'or') is ignored.
            Names the data cut off at an apostrophe ('Sprite' for "Sprite's Blessing") are completed
            from the quoted names in the card's own text.
         2. Ritual cards: a Ritual Monster that names its Ritual Spell in its text needs that spell,
            and a Ritual Spell that names a Ritual Monster needs that monster (just the first one named)."""
    out = []                                                # the list we build
    text_cards = quoted_cards(info, rules)                  # cards quoted in its text (used to complete cut-off names)
    for m in re.finditer(r"Name\s*=\s*([^&|]+)", info.row.get("required cards", "")):  # every 'Name = ...' in the required-cards column
        text = m.group(1).strip()                           # the name, without spaces around it
        real = rules.resolver.find(text)                    # find the real card (handles renamed / garbled names)
        if not real:                                                        # maybe cut off at an apostrophe
            real = next((c.name for c in text_cards if norm(c.name).startswith(norm(text) + "'")), None)  # the quoted card whose name starts with this text plus an apostrophe
        if real:                                            # found a card:
            out.append(("name", real))                      # require that card by name
        elif norm(text) in rules.archetypes:                # not a card, but an archetype name:
            out.append(("arche", norm(text)))               # require any card of that archetype
    traits = info.facts["trait"]                            # the card's traits (ritual, ritual spell, ...)
    if info.kind == "Monster" and "ritual" in traits:                       # ritual monster -> the spell it names
        out += [("name", c.name) for c in text_cards if "ritual spell" in c.facts["trait"]]  # every Ritual Spell it names
    elif info.kind == "Spell" and "ritual spell" in traits:                 # ritual spell -> one monster it names
        monsters = [c for c in text_cards if c.kind == "Monster" and "ritual" in c.facts["trait"]]  # the Ritual Monsters it names
        out += [("name", monsters[0].name)] if monsters else []  # just the first one (or nothing)
    return list(dict.fromkeys(out))                         # drop duplicates, keep the order


def counts_as(info: Info, name: str) -> bool:               # function: is this card the named card, or treated as it?
    """True if this card IS `name`, or is always treated as `name` (its alias column lists it):
       Fusion Substitute counts as Polymerization, Harpie Lady 1 counts as Harpie Lady."""
    if info.name == name:                                   # the same name: yes
        return True                                         # (yes)
    return norm(name) in {norm(a) for a in info.aliases}                   # same-deck aliases only (see Info.aliases)


# function: every card that could fill a 'needs this card' slot right now
def same_card_choices(info: Info, rules: Rules, builder: Builder, counts: dict, pool_names: set) -> list[Info]:
    """Every main-deck card that counts as `info` and could be added now: `info` itself (as before), plus the other
       names of its alias group that this format allows (in `pool_names`), with copies to spare and not blocked
       by the Normal Monster toggle. Picking from these treats e.g. Polymerization and Fusion Substitute as one card."""
    group = alias_group(info, rules) or (info.name,)                       # just itself if it has no other names
    out = []                                                # the list we build
    for name in group:                                      # each name in the group
        c = rules.by_name.get(norm(name))                   # the card with that name
        if c is None or not c.main_deck or not builder.normal_ok(c):  # not a card, not a Main Deck card, or blocked by the Normal Monsters toggle
            continue
        if c.name != info.name and c.name not in pool_names:  # a different name from the one asked for
            continue                                                       # another name: only if this format allows it
        if counts.get(c.name, 0) < builder.cap(c.name):                    # room for another copy
            out.append(c)                                   # this name can go in
    return out                                              # hand back the choices


def pick_one(choices: list[Info], rng: random.Random) -> Info:  # function: pick one card from same_card_choices()
    """One card from same_card_choices(). The dice are only rolled when there really is a choice, so decks
       without such cards come out exactly as they did before."""
    return choices[0] if len(choices) == 1 else rng.choice(choices)  # one choice: take it without rolling (keeps old decks identical); more: pick at random


def satisfied(item: tuple, deck: State, extra: Optional[list] = None) -> bool:  # function: is a requirement already met?
    """Is this requirement already met by the cards in the deck? (`extra`: the Extra Deck entries count too,
       for a requirement that is itself an Extra Deck monster, like Black-Winged Dragon).
       A card that counts as the named card (counts_as) meets it too: Fusion Substitute meets 'needs Polymerization'."""
    kind, value = item                                      # ('name', card name) or ('arche', archetype)
    if kind == "name":                                      # a specific card:
        # any deck card (or Extra Deck card) that is it or counts as it
        return any(counts_as(c, value) for c in deck.unique.values()) or any(counts_as(e.info, value) for e in extra or [])
    return any(value in c.facts["archetype"] for c in deck.unique.values())  # an archetype: any deck card of that archetype


# --------------------------------------------------------------------------
# 1. Aliases
# --------------------------------------------------------------------------
def alias_group(info: Info, rules: Rules) -> Optional[tuple]:  # function: every name in this card's alias group (or None)
    """Every name that counts as the same card as this one, itself included, sorted ('Harpie Lady', 'Harpie Lady 1', ...),
       or None if it has no other names. Only cards in the SAME deck section are in it (Info.aliases): Neo-Spacian
       Marine Dolphin is 'treated as' Aqua Dolphin, but it is an Extra Deck card, so the two are never mixed."""
    return tuple(sorted({info.name, *info.aliases})) if info.aliases else None  # the card's own name plus its aliases, sorted; None if it has no aliases


def group_cap(group: tuple, cap) -> int:                    # function: how many copies a whole alias group may have together
    """Copies the WHOLE group may have together: they all count as one card, so the same limit as one card (3, or 1 in
       Highlander). The highest limit of its names is used: a Limited name only limits its own copies (see apply_aliases)."""
    return max(cap(n) for n in group)                       # the highest single limit among its names


def apply_aliases(entries: list[Entry], rules: Rules, rng: random.Random, chosen: dict, cap=None) -> None:  # function: give each alias card a random name from its group
    """Cards with an `alias` list ('Harpie Lady||Cyber Harpie Lady||...') count as the same card under different names,
       like a real deck may run 1 Harpie Lady 1 + 2 Harpie Lady 2. Every copy gets a random name from its group, but no
       name goes over its OWN copy limit (a Limited one stays at 1). The group's total is cut back by cap_copies().
       `chosen` remembers the names used per group, for the debug notes. `cap` = the builder's copy-limit function."""
    used: dict = {}                                                        # name -> copies given that name so far
    for e in entries:                                       # every card copy in the deck
        group = alias_group(e.info, rules)                  # its alias group, if it has one
        if group is None:                                   # (no group)
            continue                                                       # not an alias card
        ok = [n for n in group if cap is None or cap(n) > 0]               # only names this format allows
        room = [n for n in ok if cap is None or used.get(n, 0) < cap(n)]   # ...that still have room under their own limit
        name = rng.choice(room or ok or [e.info.name])                     # any name of the group (Polymerization / Fusion Substitute too)
        pick = rules.by_name.get(norm(name))                # the card with the chosen name
        if pick is not None:                                # found it:
            e.info = pick                                   # this copy now becomes that name
            used[pick.name] = used.get(pick.name, 0) + 1    # one more copy under that name
            chosen.setdefault(group, [])                    # make sure the group has a list in `chosen`
            chosen[group].append(pick.name)                 # note the name used (for the debug info)


# function: cut cards back to their copy limits, then refill the gap
def cap_copies(builder: Builder, deck: State, pool: list[Info], size: int, rng: random.Random, rules: Optional[Rules] = None) -> State:
    """Cut every card back to its copy limit - and every alias group (Harpie Lady, Harpie Lady 1, ...) back to ONE card's
       limit, since its names all count as the same card - then refill the gap, preferring cards of the deck's own
       archetype (so a Harpie deck gets other Harpie cards back, not random ones)."""
    groups = {e.info.name: g for e in deck.entries if rules is not None and (g := alias_group(e.info, rules))}   # name -> its alias group
    in_group: dict = {}                                                    # group -> copies of any of its names so far

    def room(name: str, counts: dict) -> int:               # inner function: how many more copies of a card fit
        """How many more copies of `name` fit: its own limit, and (for an alias card) its group's limit."""
        left = builder.cap(name) - counts.get(name, 0)      # its own limit minus the copies already in the deck
        info = rules.by_name.get(norm(name)) if rules is not None else None    # the card itself (to read its alias list)
        g = groups.get(name) or (alias_group(info, rules) if info is not None else None)  # its alias group (from the deck, or worked out from the card)
        if g:                                               # an alias card:
            left = min(left, group_cap(g, builder.cap) - sum(counts.get(n, 0) for n in g))  # also no more than what the group has left
        return left                                         # hand back the room left

    kept, seen = [], {}                                     # kept = copies that stay; seen = copies of each name so far
    for e in deck.entries:                                  # go through the deck in order
        seen[e.info.name] = seen.get(e.info.name, 0) + 1    # one more copy of this name
        g = groups.get(e.info.name)                         # its alias group, if any
        if g:                                               # an alias card:
            in_group[g] = in_group.get(g, 0) + 1            # one more copy of the group
        # still inside both limits?
        if seen[e.info.name] <= builder.cap(e.info.name) and (not g or in_group[g] <= group_cap(g, builder.cap)):
            kept.append(e)                                                 # inside its own limit and its group's limit
    if len(kept) == len(deck.entries):                      # every copy was kept?
        return deck                                                        # nothing was over the limit
    deck = restate(kept)                                    # rebuild the deck from the copies we kept
    on_theme = [c for c in pool if c.facts["archetype"] & deck.arche and c.main_deck]  # refill candidates: Main Deck cards of the deck's own archetypes
    candidates = on_theme or [c for c in pool if c.clean] or [c for c in pool if c.generic] or pool  # or clean cards, or generic cards, or anything in the pool
    guard = 0                                               # safety counter so the loop always ends
    while len(deck.entries) < size and guard < 200:         # until the deck is full again (200 tries at most)
        guard += 1                                          # count this try
        card, _ = builder.pick(candidates, deck, pool, rng)  # let the builder pick a card it likes
        if card is None:                                    # nothing left to pick
            break
        n = room(card.name, deck.counts)                    # copies of it that still fit
        if n <= 0:                                          # none fit
            continue
        # add 1 to all that fit (never past the deck size)
        deck.add(card, rng.randint(1, min(n, size - len(deck.entries))), "theme1" if on_theme else "generic", "refill after alias merge")
    return deck                                             # hand back the fixed deck


# --------------------------------------------------------------------------
# 2. Required cards
# --------------------------------------------------------------------------
def add_required(builder: Builder, extra_builder: ExtraBuilder, deck: State, extra: list[Entry], req: Request,  # function: add the cards the deck's cards require
                 # (the rest of its inputs)
                 rules: Rules, rng: random.Random, debug: dict, pool: list[Info]) -> tuple[State, list[Entry]]:
    """Add cards the deck's cards require. Main-deck cards' requirements come first (they are a must);
       Extra Deck monsters come next, and any that no longer fit the budget are replaced."""
    added: dict = {}                                                       # card name -> copies added
    used = 0                                                               # copies added so far
    extra_ok = {c.name for c in extra_builder.pool(req)}                              # Extra Deck monsters this format + the type switches allow

    busy: set = set()                                                      # Extra Deck cards being added right now (no loops)
    pool_names = {c.name for c in pool}                                    # cards this format allows (for same_card_choices)

    def add_to_extra(info: Info, who: str) -> bool:         # inner function: put a required Extra Deck monster into the Extra Deck
        """A required card that is itself an Extra Deck monster goes in the EXTRA Deck (1 copy), together with
           what IT requires (Gaia the Dragon Champion needs Gaia The Fierce Knight + Curse of Dragon in the main
           deck). If the Extra Deck is full, an ordinary Extra Deck card makes room. False if it can't be done:
           not allowed here, or its own requirements don't fit."""
        if info.name not in extra_ok or builder.cap(info.name) <= 0 or info.name in busy:  # not allowed in this format, banned, or already being added
            return False                                                   # Forbidden here, type switched off, or a loop
        own = [i for i in requirements(info, rules) if not satisfied(i, deck, extra)]   # what this card needs itself
        if used + len(own) > REQUIRED_CAP:                  # adding its own needs would go past the cap
            return False                                                   # would drag in too many cards
        if len(extra) >= req.extra_deck_size:                              # full: take out one ordinary card
            spare = [i for i, e in enumerate(extra) if e.origin != "required" and e.info.name != who]  # positions of cards that aren't required and aren't the asking card
            if not spare:                                   # nothing can make room
                return False                                # (can't add it)
            extra.pop(spare[-1])                            # remove the last ordinary one
        entry = Entry(info, "required", f"required by {who}")              # 'required' = never pruned later
        extra.append(entry)                                 # put the required card in
        busy.add(info.name)                                 # mark it as 'being added' (stops loops)
        ok = all([add_item(i, info.name) for i in own])                    # its own requirements
        busy.discard(info.name)                             # done adding it
        if not ok:                                                         # something it needs can't be added:
            extra.remove(entry)                                            # take it back out
            return False                                    # (can't add it)
        added[info.name] = added.get(info.name, 0) + 1      # count the copy added
        return True                                         # it worked

    def add_item(item: tuple, who: str) -> bool:            # inner function: add one card that meets a requirement
        """Put one card that satisfies the requirement into the deck. False if it couldn't be added."""
        nonlocal used                                       # `used` belongs to add_required: we change it here
        kind, value = item                                  # ('name', card name) or ('arche', archetype)
        if kind == "name":                                  # a specific card:
            info = rules.by_name[norm(value)]               # the card itself
            if not info.main_deck:                                         # an Extra Deck monster (e.g. the Xyz a CXyz is made from)
                return add_to_extra(info, who) if builder.normal_ok(info) else False   # goes in the Extra Deck, not the main deck
            choices = same_card_choices(info, rules, builder, deck.counts, pool_names)  # it, or another name that counts as it, with room for a copy
            if not choices:
                return False                                               # Normal toggle off / no copies left / not allowed
            info = pick_one(choices, rng)                                  # Polymerization OR Fusion Substitute, equally likely
        else:                                                              # archetype: choose a card of that archetype
            cands = [c for c in pool if value in c.facts["archetype"] and c.main_deck      # main-deck cards only
                     and deck.counts.get(c.name, 0) < builder.cap(c.name) and builder.normal_ok(c)]  # with copies left and allowed by the Normal Monsters toggle
            if not cands:                                   # no card of that archetype can go in
                return False                                # (can't add it)
            cands = [c for c in cands if works_in(builder, rules, c, deck.entries, extra)] or cands   # Step 2: cards that work here (if any do)
            # Spells/Traps 3x as likely; cards whose own needs are met are much likelier
            weights = [(3.0 if c.kind != "Monster" else 1.0) * (2.0 if needs_covered(builder, c, deck) else 0.3) for c in cands]
            info = rng.choices(cands, weights=weights)[0]               # requirement cards are usually Spells / Traps
        room = builder.cap(info.name) - deck.counts.get(info.name, 0)                  # copies still allowed (0 if Forbidden here)
        if room > 0:                                        # at least one copy fits:
            # a required card is a normal deck card too: play 1-3 copies, not always 1.
            # Win-by-pieces cards (Exodia parts, Spirit Messages) are the exception: a second copy does nothing.
            one_only = "forbidden one" in norm(info.name) or "spirit message" in norm(info.name)  # Exodia pieces and Spirit Messages: one copy only
            n = 1 if one_only else rng.randint(1, min(3, room))  # otherwise 1 to 3 copies (never past the room left)
            deck.add(info, n, "required", f"required by {who}")  # add them, marked 'required'
            added[info.name] = added.get(info.name, 0) + n  # count the copies added
            used += n                                       # (and towards the REQUIRED_CAP budget)
            return True                                     # it worked
        return False                                        # no room: couldn't add it

    # (a) requirements of MAIN-deck cards: always added
    for c in list(deck.unique.values()):                    # every different card in the main deck (a copy of the list, since we add to it)
        for item in requirements(c, rules):                 # everything it requires
            if not satisfied(item, deck, extra):            # not in the deck yet?
                add_item(item, c.name)                      # add it

    # (b) requirements of EXTRA-deck monsters: added while the budget lasts
    unique_extra = list(dict.fromkeys(e.info.name for e in extra))  # the Extra Deck's card names, each once, in order
    infos = {e.info.name: e.info for e in extra}            # name -> card
    reqs = {n: requirements(infos[n], rules) for n in unique_extra}  # name -> what it requires
    dropped = []                                            # Extra Deck monsters we take out
    # cheapest first (fewest missing cards), ties broken at random
    order = sorted(unique_extra, key=lambda x: (sum(1 for i in reqs[x] if not satisfied(i, deck, extra)), rng.random()))
    wincon_extra = {e.info.name for e in extra if e.origin == "required"}   # Extra Deck wincon cards (Number 88 ...): never dropped here
    for n in order:                                         # each Extra Deck monster in that order
        if n in wincon_extra:                               # an Extra Deck win-condition card:
            continue                                                       # its requirements are other Extra Deck cards, already placed
        if not any(e.info.name == n for e in extra):
            continue                                                       # it made room for another card's requirement
        need = [i for i in reqs[n] if not satisfied(i, deck, extra)]         # still missing from the deck
        on_theme = bool(infos[n].facts["archetype"] & deck.arche)            # an Extra Deck monster of the deck's own theme?
        if need and not on_theme:                           # it needs cards, and it isn't the deck's theme:
            dropped.append(n)                                              # a foreign extra must not drag random cards into the main deck
        elif used + len(need) <= REQUIRED_CAP:              # its missing cards still fit the budget:
            if not all([add_item(i, n) for i in need]):                    # something it needs can't be added here:
                dropped.append(n)                                          # it goes (and is replaced below)
        else:
            dropped.append(n)                               # over the budget: it goes
    debug["required_added"] = dict(added)                   # save what was added (debug info)
    debug["extra_dropped_for_requirements"] = dropped       # save what was dropped (debug info)

    # (c) replace dropped Extra Deck monsters by ones whose requirements are already satisfied
    if dropped:                                             # anything dropped?
        copies_lost = sum(1 for e in extra if e.info.name in dropped)  # how many Extra Deck copies we lost
        extra = [e for e in extra if e.info.name not in dropped]  # take them out
        have = {e.info.name for e in extra}                 # names still in the Extra Deck
        monsters = [e.info for e in deck.entries if e.info.kind == "Monster"]  # the main deck's monsters (materials)
        cands = []                                          # (weight, card) replacement candidates
        for c in extra_builder.pool(req):                   # every Extra Deck monster this format allows
            if c.name in have or c.name in dropped:         # already in, or one we just dropped
                continue
            if any(not satisfied(i, deck, extra) for i in requirements(c, rules)):  # any of its requirements missing from the deck?
                continue                                                   # its requirements are not in the deck
            sc = summon_score(extra_builder.slots[c.name], monsters)  # how well the main deck can summon it (0-1)
            # full weight if generic / same archetype as the deck, 0.15 if some other archetype
            fit = 1.0 if (not c.facts["archetype"] or not deck.arche or (c.facts["archetype"] & deck.arche)) else 0.15
            cands.append((max(sc, 0.02) ** 3 * fit, c))     # summonable cards are MUCH likelier (score cubed)
        while copies_lost > 0 and cands:                    # until the lost copies are replaced (or no candidates left)
            w, c = rng.choices(cands, weights=[x[0] for x in cands])[0]  # pick one by weight
            n = min(extra_builder.copies(rng), copies_lost, builder.cap(c.name))           # never past its copy limit
            for _ in range(n):                              # add that many copies
                extra.append(Entry(c, "extra", "extra: replacement (requirements already in deck)"))  # (marked as a replacement)
            copies_lost -= n                                # fewer copies left to replace
            cands = [x for x in cands if x[1].name != c.name]  # don't pick the same card again
    return deck, extra                                      # hand back the updated main deck and Extra Deck


# --------------------------------------------------------------------------
# 2b. Deck rules: Ritual Monsters need a Ritual Spell, Pendulum Monsters come in pairs
# --------------------------------------------------------------------------
def _fit_weight(c: Info, wanted: set) -> float:             # function: how well a helper card fits the deck's archetypes (a weight)
    """How well a helper card (a Ritual Spell, a Pendulum monster) fits the deck's archetypes:
       6x if it shares one, 2x if it has no archetype (generic), 0.3x if it belongs to some other archetype."""
    a = c.facts["archetype"]                                # the card's archetypes
    if a & wanted:                                          # shares one with the deck:
        return 6.0                                          # strong fit
    return 2.0 if not a else 0.3                            # generic: medium; another archetype's card: weak


# function: add the cards the deck rules ask for
def apply_rules(builder: Builder, deck: State, pool: list[Info], rng: random.Random, debug: dict, generic: bool = False,
                rules: Optional[Rules] = None) -> None:     # (its inputs, continued)
    """Add the cards the deck rules ask for (they are trimmed from elsewhere later)."""
    added = []                                              # names of the cards added (debug info)
    deck_arche = set().union(*[c.facts["archetype"] for c in deck.unique.values()]) if deck.unique else set()  # every archetype in the deck (empty if the deck is empty)

    # Rule 1: a Ritual Monster in the main deck needs a Ritual Spell to be summoned.
    rituals = [c for c in deck.unique.values() if "ritual" in c.facts["trait"] and c.kind == "Monster"]  # the deck's Ritual Monsters
    has_spell = any("ritual spell" in c.facts["trait"] for c in deck.unique.values())  # does it already have a Ritual Spell?
    if rituals and not has_spell:                           # Ritual Monsters but no spell:
        wanted = set().union(*[c.facts["archetype"] for c in rituals])             # prefer a spell of the monsters' archetype
        # Ritual Spells with copies left
        cands = [c for c in pool if "ritual spell" in c.facts["trait"] and deck.counts.get(c.name, 0) < builder.cap(c.name)]
        # Step 2: the spell a deck Ritual Monster names ("You can Ritual Summon this card with 'Black Magic Ritual'"), or a spell
        # that names one of the deck's Ritual Monsters, beats a random one. Only if none is allowed here do we take any spell.
        # spells a deck Ritual Monster names...
        named = [c for c in cands if any(f'"{norm(c.name)}"' in norm(r.row.get("card text", "")) for r in rituals)
                 or any(f'"{norm(r.name)}"' in norm(c.row.get("card text", "")) for r in rituals)]  # ...or spells that name one of the deck's Ritual Monsters
        cands = named or cands                              # named ones if there are any, else every Ritual Spell
        if cands:                                           # any spell possible?
            # weight: archetype fit x (does the spell's own needs work here?)
            pick = rng.choices(cands, weights=[_fit_weight(c, wanted) * (2.0 if needs_covered(builder, c, deck) else 0.5) for c in cands])[0]
            n = min(2 if len(rituals) >= 3 else 1, builder.cap(pick.name))                # more rituals -> more copies of the spell
            # add it, marked 'required'
            deck.add(pick, n, "required", f"rule: Ritual Monsters need a Ritual Spell ({', '.join(c.name for c in rituals[:2])})")
            added.append(pick.name)                         # note it (debug info)

    # Rule 2: Pendulum Monsters come in pairs (you need two to set the scales).
    pend = [e for e in deck.entries if "pendulum" in e.info.facts["trait"]]  # every Pendulum copy in the deck
    if len(pend) == 1:                                      # exactly one: it needs a partner
        wanted = pend[0].info.facts["archetype"] | deck_arche  # archetypes to prefer: that Pendulum's plus the deck's
        # other Pendulum Monsters with copies left
        cands = [c for c in pool if "pendulum" in c.facts["trait"] and deck.counts.get(c.name, 0) < builder.cap(c.name)
                 and c.name != pend[0].info.name and builder.normal_ok(c)]   # (a Normal Pendulum obeys the Normal toggle)
        cands = [c for c in cands if works_in(builder, rules, c, deck.entries, [])] or cands   # Step 2: a partner that works here
        if cands:                                           # any partner possible?
            # weight: archetype fit x how well its Level suits the deck
            pick = rng.choices(cands, weights=[_fit_weight(c, wanted) * builder.level_bias(c, deck) for c in cands])[0]
            deck.add(pick, 1, "required", f"rule: Pendulum Monsters come in pairs (with {pend[0].info.name})")  # add one copy
            added.append(pick.name)                         # note it (debug info)

    # Rule 3: the deck needs monsters you can actually Normal Summon (Level 4 or lower, no tribute). A deck of only
    # Level 5+ monsters (Monarchs with no fodder, B.E.S. ...) can't get started. Add on-theme or generic low-level monsters.
    mons = [e for e in deck.entries if e.info.kind == "Monster" and "link" not in e.info.facts["trait"]]  # the main deck's monsters (Link Monsters have no Level)
    easy = sum(1 for e in mons if (e.info.facts.get("level") or 0) <= 4)  # how many are Level 4 or lower
    need = builder.starters_needed(len(mons))                                 # 8, or 40% of the monsters if fewer (never above the Level 1-4 slider)
    if mons and easy < need:                                # too few easy monsters:
        # every low-Level monster that could go in
        low = [c for c in pool if c.kind == "Monster" and (c.facts.get("level") or 99) <= 4 and "link" not in c.facts["trait"]
               and c.main_deck and deck.counts.get(c.name, 0) < builder.cap(c.name) and (c.clean or not generic)   # Generic deck: clean only
               and builder.normal_ok(c)]                                       # Normal Monsters toggle
        # Where to look, in order - a later pile is only used once the one before it has nothing left:
        #   1. the theme's own low-Level monsters (Little D, Beatraptor ... for Transcendosaurus)
        #   2. low-Level monsters of the deck's main Type(s): a Type at least 40% of the deck's monsters share (Dinosaur)
        #   3. anything else (only if the deck has nothing of its own to offer)
        type_count = Counter(t for e in mons for t in e.info.facts["type"])  # how many deck monsters have each Type
        main_types = {t for t, n in type_count.items() if n >= 0.4 * len(mons)}  # Types at least 40% of the monsters share
        piles = [[c for c in low if c.name in builder.theme_names],  # pile 1: the theme's own
                 [c for c in low if c.name not in builder.theme_names and c.facts["type"] & main_types],  # pile 2: the deck's main Type(s)
                 [c for c in low if c.name not in builder.theme_names and not (c.facts["type"] & main_types)]]  # pile 3: everything else
        wanted = deck_arche                                 # prefer the deck's archetypes
        got = 0                                             # copies added so far
        for pile in piles:                                  # try each pile in order
            pile = rng.sample(pile, min(150, len(pile)))                      # sample first: checking needs for every card is slow
            pile = [c for c in pile if works_in(builder, rules, c, deck.entries, [])] or pile   # Step 2: monsters that work here
            while easy + got < builder.starters_needed(len(mons) + got) and pile:   # target grows as monsters are added
                w = [_fit_weight(c, wanted) * (2.0 if needs_covered(builder, c, deck) else 0.2) for c in pile]  # weight: archetype fit x (do its needs work here?)
                pick = rng.choices(pile, weights=w)[0]      # pick one
                n = min(2, builder.cap(pick.name) - deck.counts.get(pick.name, 0))  # up to 2 copies (never past its limit)
                # add them, marked 'required'
                deck.add(pick, n, "required", "rule: the deck needs monsters it can Normal Summon (Level 4 or lower)")
                added.append(pick.name)                     # note it (debug info)
                got += n                                    # count the copies
                pile = [c for c in pile if c.name != pick.name]  # don't pick it again
    debug.setdefault("rule_cards_added", []).extend(added)  # add the names to the debug info


# function: bring in cards that name / are named by deck cards
def apply_mentions(builder: Builder, deck: State, pool: list[Info], rules: Rules, rng: random.Random, debug: dict) -> None:
    """Direct-mention rule. If a deck card NAMES another card (or is named by one), bring that card in too:
         forward : deck card quotes X  -> X is a candidate ("named by A")
         reverse : card Y quotes a deck card -> Y is a candidate ("names A")
       Candidates must be main-deck cards in the allowed pool, must not clash with the deck's archetypes,
       and must WORK in the deck (works_in: needs met, required cards present - both directions). At most MENTION_CAP copies are added, chosen
       by weight: the more deck cards a candidate is linked to, the likelier it is."""
    mentions, mentioned_by = rules.graph()                  # who names whom (built once for all cards)
    allowed = {c.name for c in pool}                        # every card this format allows
    # The theme's own archetypes: ones at least half of the theme's cards belong to ('transcendosaurus').
    # (Not every archetype some deck card has: Polymerization alone carries 18, from Yugipedia's related lists.)
    theme_cards = [rules.by_name[norm(n)] for n in builder.theme_names if norm(n) in rules.by_name]  # the theme's own cards
    arche_count = Counter(a for c in theme_cards for a in c.facts["archetype"])  # how many theme cards share each archetype
    theme_arche = {a for a, n in arche_count.items() if theme_cards and n >= 0.5 * len(theme_cards)}  # the archetypes at least half of the theme's cards have
    arche = set().union(*[c.facts["archetype"] for c in deck.unique.values()]) if deck.unique else set()  # every archetype in the deck right now
    score: dict = {}                                                       # name -> [links, Info, reasons]
    staple_namers: dict = {}                                               # name -> (Info, staple it names): Polymerization searchers ...

    def consider(card: Info, why: str) -> None:             # inner function: count one link to a candidate card
        if any(counts_as(c, card.name) for c in deck.unique.values()) or card.name not in allowed \
                or not card.main_deck or not builder.normal_ok(card):
            return                                                         # already in the deck (or a card that counts as it) / not allowed / Extra Deck / Normal toggle
        a = card.facts["archetype"]                         # the candidate's archetypes
        if a and arche and not (a & arche):                 # it has archetypes, the deck has archetypes, and none are shared
            return                                                         # belongs to some other archetype
        if card.name not in score and not works_in(builder, rules, card, deck.entries, []):  # first time we see it, and it wouldn't work here
            return                                                         # Step 2: it would be useless here (checked once per card)
        entry = score.setdefault(card.name, [0, card, []])  # its score entry (made on first use)
        entry[0] += 1                                       # one more link
        entry[2].append(why)                                # and why

    for c in list(deck.unique.values()):                    # every different card in the deck
        for m in mentions.get(c.name, []):                                 # forward: cards this card names
            consider(m, f"named by {c.name}")               # count the link
        namers = mentioned_by.get(c.name, [])               # cards that name this deck card
        for y in namers:                                                   # reverse: cards that name this card
            # a staple named by dozens of cards (Polymerization...) would drag in random cards, so only
            # follow it when few cards name it, or when the namer shares one of the THEME's archetypes with it
            # (Dark Magician named by Dark Magician cards in a Dark Magician deck - not HEROes naming Polymerization)
            shared = (y.facts["archetype"] & c.facts["archetype"] & theme_arche) - {norm(c.name)}
            if len(namers) > MENTION_POPULAR and not shared:
                staple_namers.setdefault(y.name, (y, c.name))                 # kept aside: at most ONE of these, sometimes (below)
                continue
            consider(y, f"names {c.name}")                                 # (consider() now checks its needs, both ways)
    cands = list(score.values())                            # the candidates as a list
    added, used = [], 0                                     # names added / copies added so far
    while cands and used < MENTION_CAP:                     # until no candidates or the cap is reached
        # weight: links squared x Level fit x (does it work here?)
        weights = [(n ** 2) * builder.level_bias(c, deck) *(2.0 if (c.generic or needs_covered(builder, c, deck)) else 0.3) for n, c, _ in cands]
        n, card, why = rng.choices(cands, weights=weights)[0]  # pick one
        named = card                                                       # the card the links point at
        card = pick_one(same_card_choices(named, rules, builder, deck.counts, allowed) or [named], rng)   # or one that counts as it
        copies = min(2 if n >= 3 else 1, builder.cap(card.name), MENTION_CAP - used)  # 2 copies if 3+ links, else 1 (never past the limits)
        deck.add(card, copies, "mention", "mention: " + "; ".join(why[:3]))  # add them, marked 'mention'
        added.append(card.name)                             # note it (debug info)
        used += copies                                      # count the copies
        cands = [x for x in cands if x[1].name != named.name]  # don't pick the same card again
    # A card that names a STAPLE the deck has (Vision HERO Vyon, Fluffal Cat, King of the Swamp -> Polymerization):
    # fine now and then, so sometimes ONE of them goes in (1 copy). A Fusion substitute the deck's Fusions can use
    # (King of the Swamp) is much likelier, because it does two jobs.
    if staple_namers and rng.random() < STAPLE_SEARCHER_CHANCE:  # the deck has such cards, and the dice say yes this time
        ok = [(y, staple) for y, staple in staple_namers.values()  # the namers that could go in:
              if y.name in allowed and y.main_deck and builder.normal_ok(y)  # allowed here, a Main Deck card, Normal toggle ok
              and deck.counts.get(y.name, 0) < builder.cap(y.name)  # copies left
              and not any(counts_as(c, y.name) for c in deck.unique.values())  # not already in the deck
              and works_in(builder, rules, y, deck.entries, [])]             # it must still work here
        if ok:                                              # any left?
            weights = [SUBSTITUTE_EDGE if substitute_limit(y) is not False else 1.0 for y, _ in ok]  # Fusion substitutes are SUBSTITUTE_EDGE times as likely
            y, staple = rng.choices(ok, weights=weights)[0]  # pick one
            deck.add(y, 1, "mention", f"mention: names {staple} (a staple - only now and then)")  # add 1 copy
            added.append(y.name)                            # note it (debug info)
    debug["mention_cards_added"] = added                    # save every name added (debug info)


def add_substitute(builder: Builder, extra_builder: ExtraBuilder, deck: State, extra: list[Entry],  # function: add a Fusion substitute when a Fusion is short of a named material
                   pool: list[Info], rng: random.Random, debug: dict) -> None:  # (its inputs, continued)
    """Fusion substitute rule. If a Fusion in the Extra Deck is SHORT of a material it names, or has no spare copy of it
       (Thousand Dragon with only 1 Time Wizard: you must draw that exact card), add 1-2 copies of a Fusion substitute that may
       stand in for it (King of the Swamp, Goddess with the Third Eye ...).
         - only Fusions that are Fusion Summoned with a named material (extra_builder sets their sub_for)
         - an archetype-limited substitute (HERO-only ...) only for that archetype's Fusions
         - the Normal Monsters toggle and copy caps apply; nothing is added if the deck already has a substitute
         - better-rated substitutes are likelier (King of the Swamp is the best one)"""
    debug["substitute_added"] = []                          # nothing added yet (debug info)
    monsters = [e.info for e in deck.entries if e.info.kind == "Monster"]  # the main deck's monsters
    if any(substitute_limit(m) is not False for m in monsters):  # any of them a Fusion substitute already?
        return                                                             # the deck already has one
    wanted = set()                                                         # archetypes of the Fusions that are short ('' = any)
    for e in extra:                                         # each Extra Deck card
        slots = extra_builder.slots.get(e.info.name)        # its material slots
        allowed_for = getattr(slots, "sub_for", None) if slots is not None else None  # which archetypes' substitutes may stand in for it (None = none can)
        if allowed_for is None:
            continue                                                       # not a Fusion a substitute can help
        # any named material slot with fewer copies than it needs + 1 spare
        short = any(named_slot(sl) and sum(1 for m in monsters if card_matches(sl.branches, m.facts)) < sl.need + 1
                    for sl in slots)                                       # a named material it has no spare copy of (or too few)
        if short:                                           # it is short:
            wanted |= allowed_for | {""}                                   # '' = an unlimited substitute works too
    if not wanted:                                          # no Fusion is short
        return                                                             # every Fusion has its named materials
    cands = []                                              # the substitutes that could go in
    for c in pool:                                          # every card this format allows
        lim = substitute_limit(c)                           # False = not a substitute; '' = any Fusion; 'hero' = HERO Fusions only ...
        if lim is False or (lim != "" and lim not in wanted):  # not a substitute, or limited to an archetype no short Fusion has
            continue                                                       # not a substitute, or limited to another archetype
        if deck.counts.get(c.name, 0) >= builder.cap(c.name) or not builder.normal_ok(c):  # no copies left, or blocked by the Normal Monsters toggle
            continue                                                       # banlist cap / Normal Monsters toggle
        cands.append(c)                                     # keep it
    if not cands:                                           # none can go in
        return
    weights = [(c.rating or 0) ** 2 + 0.5 for c in cands]                   # rating 5 -> 25.5, rating 2 -> 4.5
    pick = rng.choices(cands, weights=weights)[0]           # pick one by rating
    n = min(rng.randint(1, 2), builder.cap(pick.name) - deck.counts.get(pick.name, 0))  # 1 or 2 copies (never past its limit)
    deck.add(pick, n, "required", "rule: a Fusion substitute for a Fusion Monster missing a named material")  # add them, marked 'required'
    debug["substitute_added"].append(pick.name)             # note it (debug info)


def rule_violations(deck_main: list[Entry], builder: Optional[Builder] = None) -> list[str]:  # function: list the deck-rule problems in a finished main deck
    """Check the main deck against the rules; returns a list of problems (empty = fine)."""
    deck = restate(deck_main)                               # rebuild a State so we can look at the unique cards
    problems = []                                           # the problems found
    # a Ritual Monster in the deck...
    if any("ritual" in c.facts["trait"] and c.kind == "Monster" for c in deck.unique.values()) \
            and not any("ritual spell" in c.facts["trait"] for c in deck.unique.values()):  # ...and no Ritual Spell
        problems.append("Ritual Monster without a Ritual Spell")  # (problem)
    if sum(1 for e in deck_main if "pendulum" in e.info.facts["trait"]) == 1:  # exactly one Pendulum copy
        problems.append("only one Pendulum Monster")        # (problem)
    mons = [e for e in deck_main if e.info.kind == "Monster" and "link" not in e.info.facts["trait"]]  # the main deck's monsters (Link Monsters have no Level)
    need = builder.starters_needed(len(mons)) if builder is not None else min(STARTERS, -(-len(mons) * 2 // 5))   # (follows the Level 1-4 slider)
    if mons and sum(1 for e in mons if (e.info.facts.get("level") or 0) <= 4) < need:  # fewer Level 1-4 monsters than the rule wants
        problems.append("too few Level 4 or lower monsters")  # (problem)
    return problems                                         # empty list = everything is fine


# --------------------------------------------------------------------------
# 3. Trim
# --------------------------------------------------------------------------
def trim(entries: list[Entry], size: int, rng: random.Random, plan: dict | None = None) -> None:  # function: cut the main deck down to `size` cards
    """Remove cards until the list has `size` entries, least important origin first.
       Theme and support slots shrink TOGETHER, in proportion to the plan, so a crowd of required/mention/partner
       cards cannot wipe out the support slots while the theme stays full."""
    plan = plan or {}                                                       # {"theme1": 14, "support1": 16, ...}
    shared = ("support2", "support1", "theme2", "theme1")                   # the group that shrinks proportionally
    while len(entries) > size:                              # one card is removed each time round
        for group in TRIM_ORDER:                            # groups in order, least important first
            if "support1" in group:                                         # the support group: theme + support shrink together here
                counts = {o: sum(1 for e in entries if e.origin == o) for o in shared}  # how many cards each theme / support origin has now
                live = [o for o in shared if counts[o] > 0]                 # origins that still have cards
                if live:
                    # ratio of cards left to cards planned; the most over-represented origin loses a card
                    # (an origin with no plan counts as over-represented, so it goes first)
                    worst = max(live, key=lambda o: (counts[o] / plan[o]) if plan.get(o) else 99.0)  # the origin furthest over its planned share
                    idx = [i for i, e in enumerate(entries) if e.origin == worst]  # positions of that origin's cards
                    entries.pop(rng.choice(idx))            # remove one of them at random
                    break
                continue
            if "theme1" in group:                                           # theme is already handled by the shared group
                continue
            idx = [i for i, e in enumerate(entries) if e.origin in group]  # positions of cards in this group
            if idx:                                         # any?
                entries.pop(rng.choice(idx))                # remove one at random
                break
        else:
            entries.pop()                                                  # nothing matched (should not happen)


# --------------------------------------------------------------------------
# 4. Swap cards whose needs are not covered
# --------------------------------------------------------------------------
def _theme_swaps(builder: Builder, card: Info, deck: State, pool: list[Info]) -> list[tuple]:  # function: replacement candidates for a theme card that can't work
    """Replacements for a THEME card whose needs aren't met: other cards of the same theme (builder.theme_names),
       of the same kind (Monster / Spell / Trap, so the deck's mix stays), whose needs ARE met.
       [(weight, Info)]; empty = no such card, so the theme card stays where it is.
       (Before this, any card of the same kind could take its place, and a theme could lose its own cards.)"""
    out = []                                                # (weight, card) pairs
    for x in pool:                                                          # the whole pool, in its fixed order (no sampling)
        if x.name not in builder.theme_names or x.name == card.name or x.kind != card.kind:  # skip: another theme, the card itself, or a different kind
            continue                                                        # not this theme / the same card / other kind
        # skip: no copies left, Normal toggle, or useless here
        if deck.counts.get(x.name, 0) >= builder.cap(x.name) or not builder.normal_ok(x) or builder.junk(x, deck):
            continue                                                        # no copies left / Normal toggle / can't work here
        f, partner = builder.eval_needs(x, deck, pool, scan_partners=False)  # how well its needs are met (f = 1.0 means fully)
        if f >= 1.0 and partner is None:                                    # its needs are met by this deck
            out.append((builder.level_bias(x, deck) * builder.help_factor(x, deck), x))  # weight: Level fit x how many deck cards it helps
    return out                                              # empty = no replacement


# function: swap out cards whose needs aren't met
def swap_unmet(builder: Builder, entries: list[Entry], pool: list[Info], rng: random.Random, debug: dict) -> list[Entry]:
    """Replace every card whose needs are uncovered by a card of the same kind whose needs ARE covered
       (or that has none), keeping the deck size. Runs a few passes because a swap can change other cards' needs."""
    swaps = []                                              # report: (card, '->', its replacements)
    for _ in range(SWAP_PASSES):                            # a few passes
        deck = restate(entries)                             # rebuild the deck State from the current list
        origin_of = {e.info.name: e.origin for e in entries}  # card name -> why it is in the deck
        bad = [c for c in deck.unique.values()              # the cards to replace:
               # needs not met, OR a support / generic / staple card that is junk here
               if (not needs_covered(builder, c, deck) or (origin_of.get(c.name, "").startswith("support") or origin_of.get(c.name) in ("generic", "staple")) and builder.junk(c, deck))
               # ...but never a required / material / mention card (those were added on purpose)
               and not any(e.origin in ("required", "material", "mention") and e.info.name == c.name for e in entries)]
        if not bad:                                         # nothing to fix
            break
        for c in bad:                                       # each card to replace
            slots = [i for i, e in enumerate(entries) if e.info.name == c.name]  # positions of its copies
            origin = entries[slots[0]].origin               # why it was in the deck (its replacements get the same origin)
            support = origin.startswith("support")          # is it a support card?
            if c.name in builder.theme_names:                               # a real member of the deck's theme:
                cand = _theme_swaps(builder, c, deck, pool)                 # only another card of the SAME theme may replace it
            else:
                # up to 600 random cards of the same kind, not itself
                base = [x for x in rng.sample(pool, min(600, len(pool))) if x.kind == c.kind and x.name != c.name
                        and deck.counts.get(x.name, 0) < builder.cap(x.name) and builder.normal_ok(x)  # with copies left, Normal toggle ok
                        and (origin != "generic" or builder.rating_ok(x))]           # only generic filler obeys the rating limits
                cand = []                                   # (weight, card) pairs
                for x in base:                                              # only cards that are fine in this deck
                    f, partner = builder.eval_needs(x, deck, pool, scan_partners=False)  # how well its needs are met
                    fit = builder.archetype_fit(x, deck) == 1.0  # fits the deck's archetypes fully?
                    # needs met, fits, not junk, and (for support) actually helps the deck or is generic
                    if f >= 1.0 and partner is None and fit and not builder.junk(x, deck) and (not support or builder.synergy(x, deck) > 0 or x.generic):
                        cand.append((builder.level_bias(x, deck) * f * builder.help_factor(x, deck), x))  # weight: Level fit x needs x help
                near = {x.name for x in builder.rating_tier([x for _, x in cand])}   # nearest the rating limits first
                cand = [y for y in cand if y[1].name in near]  # keep only the ones nearest the rating limits
            if not cand:                                    # no replacement found
                continue                                                    # nothing better: leave the card in place (theme cards stay)
            # Only take out as many copies as the candidates can replace (a small theme may have room for just 1-2),
            # so the deck never shrinks; any copies left over simply stay.
            room = sum(max(0, builder.cap(x.name) - deck.counts.get(x.name, 0)) for _, x in cand)  # how many copies all the candidates could add together
            left = min(len(slots), room)                    # replace no more copies than that
            if left <= 0:                                   # none can be replaced
                continue                                                    # no copies can be replaced: leave it
            removed = set(slots[:left])                                     # the copies being replaced
            new_entries = [e for i, e in enumerate(entries) if i not in removed]  # the deck without the removed copies
            while left > 0 and cand:                        # add replacements until every removed copy is replaced
                w, x = rng.choices(cand, weights=[y[0] for y in cand])[0]  # pick one by weight
                n = rng.randint(1, min(builder.cap(x.name) - deck.counts.get(x.name, 0), left))  # 1 to (room left, copies still to replace)
                for _ in range(n):                          # add that many copies
                    new_entries.append(Entry(x, origin, f"swapped in for {c.name} (needs uncovered)"))  # (with a reason saying what it replaced)
                deck.counts[x.name] = deck.counts.get(x.name, 0) + n  # count them so the room is right next time
                left -= n                                   # fewer copies left to replace
                cand = [y for y in cand if y[1].name != x.name]  # don't pick it again
            if left > 0:                                                    # ran out of replacements: those copies stay,
                new_entries += [entries[i] for i in sorted(removed)][:left] # so the deck keeps its size
            entries = new_entries                           # the new list
            # note the swap (debug info)
            swaps.append((c.name, "->", [e.info.name for e in entries if e.reason.startswith(f"swapped in for {c.name}")][:3]))
            deck = restate(entries)                                           # refresh before judging the next card
    debug["swaps"] = swaps                                  # save every swap (debug info)
    return entries                                          # hand back the new main deck


# --------------------------------------------------------------------------
# 4b. Enablers: when almost nothing in the Extra Deck can be summoned
# --------------------------------------------------------------------------
REMOVABLE = ("generic", "support2", "support1", "staple")              # main-deck cards an enabler may replace, least important first


# function: which main-deck cards would make one Extra Deck monster summonable
def _plan_enablers(builder: Builder, extra_builder: ExtraBuilder, target: Info, entries: list[Entry], pool: list[Info],
                   rules: Rules, rng: random.Random, wanted: set) -> Optional[list[Info]]:  # (its inputs, continued)
    """The main-deck cards (one Info per copy) that would make `target` fully summonable, or None if it would
       cost more than ENABLE_PER_TARGET cards or can't be done. Tries a few random picks (a Synchro's Levels
       must also add up, so the first Tuner chosen might not work)."""
    slots = extra_builder.slots.get(target.name)            # the monster's material slots
    if not slots:                                           # unknown
        return None                                                    # no material info: can't plan for it
    base = [e.info for e in entries if e.info.kind == "Monster"]       # the deck's monsters now, one per copy
    counts = {}                                             # card name -> copies in the deck
    for e in entries:                                       # count them
        counts[e.info.name] = counts.get(e.info.name, 0) + 1  # (one more copy)
    base_deck = restate(entries)                                       # the deck as it is (to check enablers' own needs)
    for _ in range(5):                                                 # a few attempts with different random picks
        add: list[Info] = []                                # the cards we plan to add (one per copy)
        for sl in slots:                                    # each material slot
            if sl.extra_kind or sl.branches is None:
                continue                                               # Extra Deck materials / 'any monster': nothing to add
            have = sum(1 for m in base + add if card_matches(sl.branches, m.facts))  # deck monsters (plus planned ones) that fit this slot
            missing = sl.need + 1 - have                               # want one spare copy (see summon_score)
            if missing <= 0:                                # already enough
                continue
            # Main Deck monsters that fit the slot, with copies left
            cands = [x for x in pool if x.main_deck and x.kind == "Monster" and card_matches(sl.branches, x.facts)
                     and counts.get(x.name, 0) + add.count(x) < builder.cap(x.name) and builder.normal_ok(x)]  # (and the Normal Monsters toggle allows them)
            if not cands:                                   # nothing fits
                break                                                  # nothing in this format fits the slot
            cands = [x for x in cands if not (x.row.get("required cards") or "").strip()
                     and strict_need_ok(builder, x, base_deck, [])] or cands   # Step 2: enablers whose own needs the deck meets
            x = rng.choices(cands, weights=[_fit_weight(c, wanted) for c in cands])[0]  # pick one, preferring the deck's archetypes
            n = min(missing, builder.cap(x.name) - counts.get(x.name, 0) - add.count(x))  # as many copies as are missing (never past its limit)
            add += [x] * n                                  # plan to add them
        planned = restate(entries + [Entry(x, "material") for x in add])  # the deck as it would be with the planned cards
        for item in requirements(target, rules):                       # e.g. a Fusion that needs Polymerization
            if satisfied(item, planned):                    # already met
                continue
            kind, value = item                              # ('name', card) or ('arche', archetype)
            if kind != "name" or norm(value) not in rules.by_name:
                add = None                                             # an archetype requirement: too vague to plan
                break
            need = rules.by_name[norm(value)]               # the required card
            # not a Main Deck card, no copies left, or Normal toggle off
            if not need.main_deck or builder.cap(need.name) <= counts.get(need.name, 0) or not builder.normal_ok(need):
                add = None                                             # not allowed in this format (or the Normal toggle is off)
                break
            pool_names = {c.name for c in pool}             # every card this format allows
            add.append(pick_one(same_card_choices(need, rules, builder, counts, pool_names) or [need], rng))   # Polymerization OR Fusion Substitute
        if add is None or len(add) > ENABLE_PER_TARGET:     # failed, or it costs too many cards
            continue                                                   # too expensive: try other picks
        if summon_score(slots, base + [x for x in add if x.kind == "Monster"]) >= FULL_SCORE:  # do the planned cards make it fully summonable?
            return add                                                 # this works
    return None                                             # every attempt failed


# function: unlock a few Extra Deck monsters when the Extra Deck is nearly empty
def add_enablers(builder: Builder, extra_builder: ExtraBuilder, entries: list[Entry], extra: list[Entry], req: Request,
                 # (its inputs, continued)
                 pool: list[Info], rules: Rules, rng: random.Random, debug: dict, generic: bool) -> list[Entry]:
    """When the Extra Deck ended up with fewer than ENABLE_BELOW cards, find Extra Deck monsters the deck can
       ALMOST summon and swap in the few main-deck cards that unlock them (Tuners for a Synchro, the Fusion
       materials + Polymerization, same-Level monsters for an Xyz). Each one replaces a filler or support card,
       never a theme card, so the deck keeps its size. Returns the new main deck; adds the monsters to `extra`."""
    added = []                                                         # report: (Extra Deck monster, [cards added])
    if len(extra) >= ENABLE_BELOW or req.extra_deck_size < ENABLE_BELOW:  # Extra Deck big enough, or the user asked for a tiny one
        debug["extra_enablers"] = added                     # nothing added (debug info)
        return entries                                                 # the Extra Deck is fine (or meant to be tiny)
    wanted = set().union(*[e.info.facts["archetype"] for e in entries]) if entries else set()  # every archetype in the main deck
    in_extra = {e.info.name for e in extra}                 # names already in the Extra Deck
    targets = [c for c in extra_builder.pool(req) if c.name not in in_extra and builder.cap(c.name) > 0]  # Extra Deck monsters this format allows that aren't in yet
    # (unlike the refill below, a Generic deck may take monsters that name their materials here:
    #  the enablers ARE those named cards, so nothing is left unplayable)
    rng.shuffle(targets)                                    # random order first...
    targets.sort(key=lambda c: -_fit_weight(c, wanted))                # the deck's own archetype first, then generic ones
    budget = ENABLE_BUDGET                                  # most main-deck cards we may swap in, in total
    for target in targets[:60]:                                        # (looking at 60 is plenty, and keeps it fast)
        if budget <= 0 or len(extra) >= ENABLE_BELOW:       # out of budget, or the Extra Deck is big enough now
            break
        plan = _plan_enablers(builder, extra_builder, target, entries, pool, rules, rng, wanted)  # the cards that would unlock this monster
        if not plan or len(plan) > budget:                  # can't be done, or too expensive
            continue
        removable = [i for o in REMOVABLE for i, e in enumerate(entries) if e.origin == o]   # least important first
        if len(removable) < len(plan):                      # (not enough to replace)
            continue                                                   # not enough filler to replace
        gone = set(removable[:len(plan)])                   # the filler cards to take out
        entries = [e for i, e in enumerate(entries) if i not in gone]  # the deck without them
        entries += [Entry(x, "material", f"added so the deck can summon {target.name}") for x in plan]  # add the enabler cards in their place
        extra.append(Entry(target, "generic", "Extra Deck monster unlocked by enabler cards"))  # add the unlocked monster to the Extra Deck
        added.append((target.name, [x.name for x in plan]))  # note it (debug info)
        budget -= len(plan)                                 # less budget left
    debug["extra_enablers"] = added                         # save what was added (debug info)
    return entries                                          # hand back the new main deck


# --------------------------------------------------------------------------
# 4c. Genesys: keep the deck's points under the cap
# --------------------------------------------------------------------------
def enforce_points(builder: Builder, entries: list[Entry], extra: list[Entry], pool: list[Info],  # function: Genesys - get the deck under its point cap
                   rng: random.Random, debug: dict) -> list[Entry]:  # (its inputs, continued)
    """Genesys only: while the Main + Extra Deck cost more points than the cap, take out one costly copy
       (least important origin first, then the most points) and, in the main deck, put a 0-point card of the
       same kind (Monster / Spell / Trap) in its place. Returns the new main deck; changes `extra` in place."""
    if builder.point_cap is None:                           # no point cap
        return entries                                                     # not a Genesys deck
    order = {o: i for i, group in enumerate(TRIM_ORDER) for o in group}    # least important origin = smallest number
    removed = []                                            # names of the copies taken out (debug info)
    while builder.deck_points(entries + extra) > builder.point_cap:  # while over the cap
        costly = [(order.get(e.origin, 0), -builder.card_points(e.info.name), i, where)  # every copy that costs points: (origin rank, -points, position, which deck)
                  for where, cards in (("main", entries), ("extra", extra))  # in both decks
                  for i, e in enumerate(cards) if builder.card_points(e.info.name) > 0]  # (only cards that cost points)
        _, _, i, where = min(costly)                                       # least important, then most expensive
        cards = entries if where == "main" else extra       # the list it came from
        gone = cards.pop(i)                                 # take it out
        removed.append(gone.info.name)                      # note it
        if where == "extra":                                # from the Extra Deck:
            continue                                                       # the Extra Deck just gets one card smaller
        counts = {}                                         # card name -> copies in the main deck
        for e in entries:                                   # count them
            counts[e.info.name] = counts.get(e.info.name, 0) + 1  # (one more copy)
        # a 0-point card of the same kind: another copy of a deck card first, else a plain generic card
        same = [c for c in {e.info.name: e.info for e in entries}.values()  # deck cards of the same kind that cost 0 points and have copies left
                if c.kind == gone.info.kind and builder.card_points(c.name) == 0 and counts.get(c.name, 0) < builder.cap(c.name)]
        # or 0-point clean generic Main Deck cards of the same kind
        others = [c for c in pool if c.kind == gone.info.kind and c.clean and c.main_deck and builder.normal_ok(c)
                  and builder.card_points(c.name) == 0 and counts.get(c.name, 0) < builder.cap(c.name)]
        choice = same or others                             # deck cards first
        if choice:                                          # anything found?
            entries.append(Entry(rng.choice(choice), gone.origin if same else "generic",  # put one in the removed copy's place
                                 f"in place of {gone.info.name} (Genesys points)"))  # (with the reason)
    debug["genesys_points"] = (builder.deck_points(entries + extra), builder.point_cap)  # points used / cap (debug info)
    debug["genesys_removed"] = removed                      # what was taken out (debug info)
    return entries                                          # hand back the new main deck


# --------------------------------------------------------------------------
# 5. The whole thing
# --------------------------------------------------------------------------
class Deck:                                                 # class: a finished deck
    """A finished deck: main entries, extra entries and a debug dictionary."""
    def __init__(self, main, extra, debug):                 # store the three parts
        self.main, self.extra, self.debug = main, extra, debug  # (main list, Extra Deck list, debug dictionary)


# function: step 3 - add the cards the deck needs
def _add_needed_cards(builder: Builder, extra_builder: ExtraBuilder, deck: State, extra: list[Entry], req: Request,
                      # (its inputs, continued)
                      rules: Rules, rng: random.Random, debug: dict, pool: list[Info], generic: bool) -> tuple:
    """Step 3: cards the deck needs. Required cards (Polymerization ...), then the deck rules (a Ritual Spell
       for Ritual Monsters, Pendulum pairs, enough low-Level monsters), then cards that name each other. A card
       added by a rule or a mention may need cards of its own, so the required cards are checked once more."""
    deck, extra = add_required(builder, extra_builder, deck, extra, req, rules, rng, debug, pool)  # required cards first
    add_substitute(builder, extra_builder, deck, extra, pool, rng, debug)  # a Fusion short of a named material: a substitute
    apply_rules(builder, deck, pool, rng, debug, generic, rules)  # then the deck rules
    if generic:                                             # a Generic deck:
        debug["mention_cards_added"] = []                                   # a Generic deck has no archetype to pull named cards into
    else:
        apply_mentions(builder, deck, pool, rules, rng, debug)  # cards that name / are named by deck cards
    if debug.get("rule_cards_added") or debug.get("mention_cards_added"):   # a rule/mention card may have requirements of its own
        before = dict(debug["required_added"])              # what the first round added
        deck, extra = add_required(builder, extra_builder, deck, extra, req, rules, rng, debug, pool)  # check the required cards again (for the new cards)
        for k, v in before.items():                                         # report both rounds together
            debug["required_added"][k] = debug["required_added"].get(k, 0) + v  # add the first round's counts back in
    return deck, extra                                      # hand back the main deck and Extra Deck


def _back_to_size(builder: Builder, deck: State, size: int, pool: list[Info], rng: random.Random,  # function: step 4 - back to size, then fix cards that can't work
                  debug: dict, generic: bool, rules: Optional[Rules] = None) -> list[Entry]:  # (its inputs, continued)
    """Step 4: trim the main deck back to its size, replace cards whose needs aren't covered, and - since that can
       break a deck rule - re-apply the rules (up to twice). Returns the main deck as a list of entries."""
    entries = list(deck.entries)                            # a plain list we can cut
    trim(entries, size, rng, debug.get("slots"))                            # back to the target size
    entries = swap_unmet(builder, entries, pool, rng, debug)                # fix uncovered needs
    trim(entries, size, rng, debug.get("slots"))            # trim again (a swap can add more than it removed)
    for _ in range(2):                                                      # trimming/swapping may have broken a rule: re-check
        if not rule_violations(entries, builder):           # no rule broken: done
            break
        recheck = restate(entries)                          # rebuild the deck State
        apply_rules(builder, recheck, pool, rng, debug, generic, rules)  # re-apply the deck rules
        entries = list(recheck.entries)                     # back to a plain list
        trim(entries, size, rng, debug.get("slots"))        # and back to size again
    return entries                                          # hand back the main deck


def _drop_unsummonable(extra_builder: ExtraBuilder, entries: list[Entry], extra: list[Entry], rules: Rules,  # function: step 5 - drop Extra Deck monsters the main deck can't summon
                       debug: dict) -> tuple:               # (its inputs, continued)
    """Step 5: Extra Deck monsters the finished main deck can't summon (summon score < DROP_BELOW) are dead weight:
       drop them - and any monster made FROM a dropped one (Curse of Dragon, the Magical Knight Dragon needs Gaia
       the Dragon Champion). Cards required by name stay. Returns (the Extra Deck kept, the cards dropped)."""
    monsters = [e.info for e in entries if e.info.kind == "Monster"]       # one Info per monster copy
    keep, dropped = [], []                                  # the Extra Deck cards we keep / drop
    for e in extra:                                         # each Extra Deck card
        slots = extra_builder.slots.get(e.info.name)        # its material slots
        dead = slots is not None and e.origin != "required" and summon_score(slots, monsters) < DROP_BELOW  # dead = known materials, not required by name, and too hard to summon
        (dropped if dead else keep).append(e)               # put it in the right list
    while True:                                                             # a monster made from a dropped one goes too
        gone = {e.info.name for e in dropped}               # names dropped so far
        stuck = [e for e in keep if e.origin != "required" and any(  # cards that need a dropped card by name...
                 kind == "name" and value in gone and not any(counts_as(k.info, value) for k in keep)  # ...that nothing kept counts as
                 for kind, value in requirements(e.info, rules))]  # (looking at every requirement of the card)
        if not stuck:                                       # nothing else to drop
            break
        keep = [e for e in keep if e not in stuck]          # drop them from the kept list
        dropped += stuck                                    # and add them to the dropped list
    debug["extra_dropped_unsummonable"] = list(dict.fromkeys(e.info.name for e in dropped))  # names dropped (debug info)
    return keep, dropped                                    # hand back (kept, dropped)


def _refill_extra(builder: Builder, extra_builder: ExtraBuilder, entries: list[Entry], extra: list[Entry],  # function: step 7 - fill the Extra Deck's gaps with summonable monsters
                  dropped: list[Entry], deck_arche: set, req: Request, rules: Rules, rng: random.Random,  # (its inputs, continued)
                  debug: dict, generic: bool) -> None:      # (its inputs, continued)
    """Step 7: fill the Extra Deck's gaps with monsters the main deck CAN fully summon - generic ones (no archetype)
       and the deck's own archetype first, other archetypes are unlikely picks. If none are fully summonable it
       stays short. Changes `extra` in place."""
    refill = []                                             # names added (debug info)
    monsters = [e.info for e in entries if e.info.kind == "Monster"]       # (the enablers may have changed the deck)
    if (dropped or debug["extra_enablers"]) and len(extra) < req.extra_deck_size:  # something was dropped / unlocked, and the Extra Deck has room
        have = {}                                                           # card name -> copies in the Extra Deck
        for e in extra:                                     # count the copies already in
            have[e.info.name] = have.get(e.info.name, 0) + 1  # (one more copy)
        current = restate(entries)                                          # a refill card must not need a card the main deck lacks
        # Extra Deck monsters the main deck can fully summon
        cands = [c for c in extra_builder.pool(req) if summon_score(extra_builder.slots[c.name], monsters) >= FULL_SCORE
                 # with copies left, and not one we just dropped
                 and have.get(c.name, 0) < builder.cap(c.name) and c.name not in {x.info.name for x in dropped}
                 and all(satisfied(i, current, extra) for i in requirements(c, rules))  # whose own required cards are all in the deck
                 and (c.clean or not generic)]                                # Generic deck: refill with clean extras only
        while cands and len(extra) < req.extra_deck_size:   # until the Extra Deck is full (or no candidates left)
            card = rng.choices(cands, weights=[_fit_weight(c, deck_arche) for c in cands])[0]  # pick one, the deck's archetypes first
            # 1-3 copies (never past its limit or the Extra Deck size)
            n = min(extra_builder.copies(rng), builder.cap(card.name) - have.get(card.name, 0), req.extra_deck_size - len(extra))
            for _ in range(n):                              # add that many copies
                extra.append(Entry(card, "generic", "extra refill after dropping uncastable cards"))  # (with the reason)
            have[card.name] = have.get(card.name, 0) + n    # count them
            refill.append(card.name)                        # note it (debug info)
            cands = [c for c in cands if c.name != card.name]  # don't pick it again
    debug["extra_refilled"] = refill                        # save what was added (debug info)


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


def strict_need_ok(builder: Builder, c: Info, deck: State, extra_infos: list) -> bool:  # function: is this card's strict need met?
    """True if the card has no strict need, or one of its strictSupportedBy branches is met:
       the TRAIT part by another card in the main or Extra Deck, and no MECHANIC part 'unmet'."""
    # Some searchers mention Polymerization, but the effect only works with their
    # own archetype.  The parser can mistake the named card for a complete support
    # relationship (or offer an alternate Fusion Summon branch). Treat the actual
    # archetype requirement as mandatory, even with an older cards.csv file.
    # This is checked by mention selection AND by the final deck validation.
    restriction = ARCHETYPE_LOCKED_FUSION_SEARCHERS.get(c.name)  # is it an archetype-locked Fusion searcher? (its restriction text, or None)
    if restriction:                                         # it is:
        others = [o for o in list(deck.unique.values()) + extra_infos if o.name != c.name]  # every other card in the main deck and Extra Deck
        # the archetypes its restriction allows
        permitted = {a.value for branch in parse_expr(restriction) for a in branch if a.field == "archetype" and a.op == "="}
        # The generated archetype column also lists archetypes that a card
        # SUPPORTS. Polymerization therefore carries "melodious", "amazoness",
        # etc., even though it is not a member of those themes. Use the actual
        # card name for these named archetype requirements instead.
        if not any(any(arche in norm(o.name) for arche in permitted) for o in others):  # no other card's NAME contains one of those archetypes
            return False                                    # so it can't work here

    branches = parse_expr(c.row.get("strictSupportedBy", ""))  # its strictSupportedBy column, read into OR-branches
    if not branches or not c.needs:                                         # no strict need (or flavour text the builder ignores)
        return True                                         # (nothing needed)
    others = [o for o in list(deck.unique.values()) + extra_infos if o.name != c.name]   # every OTHER card (one Info per name)
    for br in branches:                                                     # one OR-option at a time
        traits = tuple(a for a in br if not is_mechanic(a))                 # 'is it a WATER monster?' part   (hard)
        mechs = [a for a in br if is_mechanic(a)]                           # 'Self = Banish (GY)' part       (soft)
        t_ok = (not traits) or any(card_matches([traits], o.facts) for o in others)  # trait part met by some other card?
        m_ok = all(need_status(m, deck.offered, deck.traits, builder.tree) != "unmet" for m in mechs)  # mechanic part not 'unmet'?
        if t_ok and m_ok:                                   # both met:
            return True                                                     # this branch works: the need is met
    return False                                            # no branch worked


def can_summon(extra_builder: ExtraBuilder, name: str, monsters: list) -> bool:  # function: can the main deck summon this Extra Deck monster?
    """True if the main deck's monsters (one Info per copy) can supply EVERY material slot of this Extra Deck monster -
       just enough copies, no spare needed - and a Synchro's Levels can add up. A Fusion substitute may stand in for
       one named material, as in summon_score()."""
    slots = extra_builder.slots.get(name)                   # its material slots
    if not slots:                                           # unknown
        return True                                                         # no material info: we can't judge it, so keep it
    haves = [sum(1 for m in monsters if card_matches(s.branches, m.facts)) if s.branches else len(monsters) for s in slots]   # copies that fit each slot
    if slots.sub_for is not None and any(lim is not False and (lim == "" or lim in slots.sub_for)  # it takes a substitute, and the deck has one that works for it:
                                         for lim in map(substitute_limit, monsters)):   # the deck has a substitute that works here
        short = [i for i, s in enumerate(slots) if named_slot(s) and haves[i] < s.need]  # named material slots that are short
        if short:                                           # any?
            haves[short[0]] += 1                                            # it fills ONE missing named material
    if any(not s.extra_kind and h < s.need for s, h in zip(slots, haves)):  # a Main Deck material slot that can't be filled
        return False                                                        # some slot can't be filled
    if slots.sumlevel and not any(s.extra_kind for s in slots) and not sums_ok(slots, monsters):  # a Synchro (Levels must add up) with only Main Deck materials
        return False                                                        # Synchro: the Levels never add up
    if slots.link and sum(s.need for s in slots) > slots.link:  # a Link Monster
        return False                                                        # Link: more materials than its rating allows
    return True                                             # every check passed


def floodgate_clash(builder: Builder, c: Info, deck: State) -> bool:  # function: does this floodgate stop its own deck?
    """True for a floodgate that stops BOTH players and whose 'clashes' cover CLASH_SHARE of the deck's copies
       (unless its own exceptions cover BROAD_SHARE of the deck, e.g. Barrier Statue of the Inferno in a FIRE deck)."""
    total = len(deck.entries)                               # cards in the deck
    if total == 0 or builder.one_sided(c):
        return False                                                        # empty deck, or it only stops the opponent
    for gate in (g for g in builder.tags(c) if g in FLOODGATE_RULES):  # each floodgate kind this card has
        clashing = sum(deck.counts[o.name] for o in deck.unique.values()  # copies of OTHER deck cards that this floodgate would stop
                       # (using the floodgate's 'clashes' list)
                       if o.name != c.name and builder.hits(builder.tags(o), FLOODGATE_RULES[gate]["clashes"]))
        if clashing / total >= CLASH_SHARE and builder.coverage(c, deck) < BROAD_SHARE:  # enough of the deck is stopped, and its exceptions don't save it
            return True                                     # it clashes
    return False                                            # no floodgate clash


# function: why a Main Deck card can't work here ('' = it works)
def main_card_problem(builder: Builder, rules: Rules, c: Info, deck: State, extra: list[Entry], extra_infos: list) -> str:
    """Why this MAIN deck card is useless in `deck` ('' = it works). `deck` is a State that already holds the card."""
    if not c.main_deck:                                     # an Extra Deck card in the main deck
        return "extra deck card"                                            # Fusion / Synchro / Xyz / Link: can't be in the Main Deck
    if not strict_need_ok(builder, c, deck, extra_infos):   # its strict need isn't met
        return "strict need"                                # (reason)
    if any(not satisfied(i, deck, extra) for i in requirements(c, rules)):  # a card it requires is missing
        return "required card"                              # (reason)
    # a Ritual Monster...
    if c.kind == "Monster" and "ritual" in c.facts["trait"] \
            and not any("ritual spell" in o.facts["trait"] for o in deck.unique.values()):  # ...with no Ritual Spell in the deck
        return "ritual"                                                     # a Ritual Monster with no Ritual Spell
    # a Ritual Spell...
    if "ritual spell" in c.facts["trait"] \
            and not any("ritual" in o.facts["trait"] and o.kind == "Monster" for o in deck.unique.values()):  # ...with no Ritual Monster in the deck
        return "ritual"                                                     # a Ritual Spell with nothing to summon
    if floodgate_clash(builder, c, deck):                   # a floodgate that stops its own deck
        return "floodgate"                                  # (reason)
    return ""                                               # nothing wrong: it works


# function: would this card work if added? (used before adding a card)
def works_in(builder: Builder, rules: Optional[Rules], c: Info, entries: list[Entry], extra: list[Entry]) -> bool:
    """Step 2: would this card WORK if it were added to the deck (`entries`)? Used by every step that adds cards, so they
       stop adding cards the last check would only have to swap out again. Without `rules` (no name finder) we can only check
       the strict need, so a card with a 'required cards' entry counts as not working."""
    deck = restate(list(entries) + [Entry(c, "test")])                    # the deck WITH the card in it
    extra_infos = list({e.info.name: e.info for e in extra}.values())  # one Info per Extra Deck name
    if rules is None:                                       # no name finder:
        # only the strict need can be checked; a card with required cards counts as not working
        return strict_need_ok(builder, c, deck, extra_infos) and not (c.row.get("required cards") or "").strip()
    return not main_card_problem(builder, rules, c, deck, extra, extra_infos)  # works = no problem found


def useless_cards(builder: Builder, extra_builder: ExtraBuilder, rules: Rules, entries: list[Entry],  # function: every useless card in a finished deck
                  extra: list[Entry]) -> tuple[dict, dict]:  # (its inputs, continued)
    """Every useless card in a finished deck: ({main card name: why}, {Extra Deck card name: why})."""
    deck = restate(entries)                                 # the deck as a State
    extra_infos = list({e.info.name: e.info for e in extra}.values())       # one Info per Extra Deck name
    monsters = [e.info for e in entries if e.info.kind == "Monster"]        # one Info per main-deck monster copy
    bad_main = {}                                           # Main Deck card name -> why
    for c in deck.unique.values():                          # each different main-deck card
        if c.name in builder.anchor_names:                  # a Card 1 / Card 2 card
            continue                                                        # the card the deck is built around always stays
        why = main_card_problem(builder, rules, c, deck, extra, extra_infos)  # why it can't work ('' = it works)
        if why:                                             # a problem:
            bad_main[c.name] = why                          # note it
    bad_extra = {}                                          # Extra Deck card name -> why
    for c in extra_infos:                                   # each different Extra Deck card
        if c.name in builder.anchor_names:                  # a Card 1 / Card 2 card
            continue                                                        # (an Extra Deck anchor stays too)
        if not can_summon(extra_builder, c.name, monsters):  # materials can't be supplied
            bad_extra[c.name] = "unsummonable"              # (reason)
        elif any(not satisfied(i, deck, extra) for i in requirements(c, rules)):  # a card it requires is missing
            bad_extra[c.name] = "required card"             # (reason)
    return bad_main, bad_extra                              # hand back both


def _dependents(builder: Builder, rules: Rules, entries: list[Entry], extra: list[Entry], gone: set) -> set:  # function: cards that only work because of the cards being removed
    """Cards that WORK now but would stop working once the cards in `gone` are removed (Levia-Dragon - Daedalus when
       the deck's only Umi goes). Repeats until nothing new breaks, so a chain of dependents goes together."""
    def broken(deck_entries: list[Entry]) -> set:                           # names failing the strict-need / required checks
        deck = restate(deck_entries)                        # the deck as a State
        extra_infos = list({e.info.name: e.info for e in extra}.values())  # one Info per Extra Deck name
        return {c.name for c in deck.unique.values()        # the names that fail...
                # ...the strict-need check or the required-card check
                if not strict_need_ok(builder, c, deck, extra_infos) or any(not satisfied(i, deck, extra) for i in requirements(c, rules))}
    fine_before = {e.info.name for e in entries} - broken(entries) - gone   # the cards that work right now
    gone = set(gone)                                        # a copy, so the caller's set isn't changed
    while True:                                             # repeat until nothing new breaks
        left = [e for e in entries if e.info.name not in gone]  # the deck without the removed cards
        newly = (broken(left) & fine_before) - gone                         # worked before, broken without `gone`
        if not newly:                                       # nothing new broke
            return gone                                     # hand back everything that has to go
        gone |= newly                                       # these go too; check again


# function: pick a working card to replace a useless one
def _replacement(builder: Builder, rules: Rules, removed: Info, origin: str, entries: list[Entry], extra: list[Entry],
                 pool: list[Info], rng: random.Random, generic: bool) -> Optional[Info]:  # (its inputs, continued)
    """A card of the same kind (Monster / Spell / Trap) that WORKS in this deck, chosen by the builder's usual weights
       (the deck's own archetype is far likelier). If none works, a plain clean card of that kind (no needs at all)."""
    deck = restate(entries)                                 # the deck as a State
    extra_infos = list({e.info.name: e.info for e in extra}.values())  # one Info per Extra Deck name
    if removed.name in builder.theme_names:                                 # a theme card has to go: try its own theme first
        same = []                                           # (weight, card) pairs from the theme
        for x in pool:                                                      # every card of the theme, same kind, that works here
            # a theme card of the same kind, a Main Deck card, not the removed card
            if (x.name in builder.theme_names and x.kind == removed.kind and x.main_deck and x.name != removed.name
                    # with copies left, Normal toggle ok, not junk here
                    and deck.counts.get(x.name, 0) < builder.cap(x.name) and builder.normal_ok(x) and not builder.junk(x, deck)
                    # and it would actually work in the deck
                    and not main_card_problem(builder, rules, x, restate(entries + [Entry(x, origin)]), extra, extra_infos)):
                same.append((builder.level_bias(x, deck) * builder.help_factor(x, deck), x))  # weight: Level fit x help
        if same:                                            # found theme cards:
            return rng.choices([x for _, x in same], weights=[max(w, 1e-6) for w, _ in same])[0]  # pick one by weight
    base = [x for x in rng.sample(pool, min(LAST_SAMPLE, len(pool)))  # otherwise: up to LAST_SAMPLE random cards from the pool...
            if x.kind == removed.kind and x.main_deck and x.name != removed.name  # ...of the same kind, Main Deck, not the removed card
            and deck.counts.get(x.name, 0) < builder.cap(x.name) and builder.normal_ok(x)  # ...with copies left, Normal toggle ok
            and (origin != "generic" or builder.rating_ok(x))]              # only generic filler obeys the rating limits
    good = []                                               # (weight, card) pairs that work
    for x in base:                                          # each candidate
        if builder.junk(x, deck) or (generic and not x.clean):  # junk here, or a Generic deck and the card isn't clean
            continue                                                        # can't really work here / Generic deck: clean cards only
        test = restate(entries + [Entry(x, origin)])                        # the deck WITH the candidate in it
        if main_card_problem(builder, rules, x, test, extra, extra_infos):  # the candidate's own problem check
            continue                                                        # it would be useless too
        # weight: Level fit x archetype fit x help
        # weight: Level fit x archetype fit x help
        good.append((builder.level_bias(x, deck) * builder.archetype_fit(x, deck) * builder.help_factor(x, deck)
                     * builder.floodgate_factor(x, deck), x))  # x floodgate penalty
    near = {x.name for x in builder.rating_tier([x for _, x in good])}      # nearest the rating limits first
    good = [g for g in good if g[1].name in near]           # keep only the ones nearest the rating limits
    if not good:                                                            # nothing fits: a plain card that works anywhere
        # clean cards of the same kind, not the removed card
        plain = [x for x in pool if x.kind == removed.kind and x.main_deck and x.clean and x.name != removed.name
                 # with copies left, inside the rating limits, Normal toggle ok
                 and deck.counts.get(x.name, 0) < builder.cap(x.name) and builder.rating_ok(x) and builder.normal_ok(x)
                 and not floodgate_clash(builder, x, restate(entries + [Entry(x, origin)]))]  # and not a floodgate that would stop this deck
        good = [(builder.level_bias(x, deck), x) for x in plain]  # weight: just the Level fit
    if not good:                                            # nothing at all
        return None                                         # (the caller keeps the old card)
    return rng.choices([x for _, x in good], weights=[max(w, 1e-6) for w, _ in good])[0]  # pick one by weight (never a 0 weight)


# function: add a missing required card instead of removing the card that needs it
def _repair(builder: Builder, rules: Rules, entries: list[Entry], extra: list[Entry], bad_main: dict, pool: list[Info],
            rng: random.Random, budget: int, repairs: list) -> tuple[list[Entry], int]:  # (its inputs, continued)
    """Repair before removing: a card whose ONLY problem is a missing required card (or a Ritual Monster missing the
       spell it names) gets that card added, in place of a filler / staple / support card that nothing depends on.
       Spends at most `budget` copies. Returns (the new main deck, budget left); notes each repair in `repairs`."""
    allowed = {c.name for c in pool}                                        # cards this format allows
    deck = restate(entries)                                 # the deck as a State
    needed_by = {v for c in deck.unique.values() for k, v in requirements(c, rules) if k == "name"}   # names some deck card requires
    safe: dict = {}                                                         # card name -> can it go without breaking anything?
    for name, why in bad_main.items():                      # each useless card and why
        if budget <= 0:
            break                                                           # repair budget used up
        c = deck.unique.get(name)                           # the card itself
        if why not in ("required card", "ritual") or c is None:
            continue                                                        # only these two problems can be repaired
        missing = [i for i in requirements(c, rules) if not satisfied(i, deck, extra)]  # its requirements that aren't met
        if not missing or len(missing) > budget:            # nothing missing, or more than the budget
            continue                                                        # nothing named to add (or too much)
        adds = []                                                           # the cards to add, one copy each
        for kind, value in missing:                         # each missing requirement
            if kind == "name":                                              # a card by name: it, or a card that counts as it
                info = rules.by_name.get(norm(value))       # the required card
                # it, or a card that counts as it, that could go in
                choices = same_card_choices(info, rules, builder, deck.counts, allowed) if info is not None else []
                choices = [x for x in choices if x.name in allowed]  # only cards this format allows
                if not choices:                             # none:
                    adds = None                                             # Extra Deck card / not allowed / at its limit / Normal toggle
                    break
                info = pick_one(choices, rng)                               # Polymerization OR Fusion Substitute
            else:                                                           # 'any card of archetype X': one that works here
                # Main Deck cards of that archetype with copies left
                cands = [x for x in pool if value in x.facts["archetype"] and x.main_deck and deck.counts.get(x.name, 0) < builder.cap(x.name)
                         and builder.normal_ok(x)]          # (and the Normal Monsters toggle allows them)
                cands = [x for x in cands if works_in(builder, rules, x, entries, extra)]  # only ones that work here
                if not cands:                               # none:
                    adds = None                             # can't repair
                    break
                info = rng.choice(cands)                    # pick one at random
            adds.append(info)                               # plan to add it
        if not adds:                                        # nothing can be added
            continue                                                        # can't repair this one: the swap below handles it
        victims = []                                                        # positions of the copies the added cards replace
        for origin in REPAIR_FROM:                                          # least important origin first
            for i, e in enumerate(entries):                 # every copy in the deck
                n = e.info.name                             # its name
                if len(victims) == len(adds):               # enough found
                    break
                if e.origin != origin or n == name or i in victims or any(counts_as(e.info, v) for v in needed_by):
                    continue                                                # wrong kind of card / the card we repair / required
                if n not in safe:                                           # would anything stop working without it?
                    safe[n] = _dependents(builder, rules, entries, extra, {n}) == {n}  # safe = removing it breaks nothing else
                if safe[n]:                                 # safe to replace:
                    victims.append(i)                       # use this position
        if len(victims) < len(adds):                        # (not enough)
            continue                                                        # not enough filler to replace
        for i, info in zip(victims, adds):                  # swap each chosen copy for a card to add
            repairs.append(f"{entries[i].info.name} -> {info.name} (needed by {name})")  # note the repair (debug info)
            entries[i] = Entry(info, "required", f"last check: repair, required by {name}")  # the new card, marked 'required'
        budget -= len(adds)                                 # less budget left
        deck = restate(entries)                                             # refresh before the next card
        needed_by = {v for c2 in deck.unique.values() for k, v in requirements(c2, rules) if k == "name"}  # names the deck requires now
        safe = {}                                           # forget the old 'safe' answers (the deck changed)
    return entries, budget                                  # hand back the new main deck and the budget left


# function: step 7c - swap out every card that still can't work
def last_check(builder: Builder, extra_builder: ExtraBuilder, entries: list[Entry], extra: list[Entry], req: Request,
               pool: list[Info], rules: Rules, rng: random.Random, debug: dict, generic: bool) -> list[Entry]:  # (its inputs, continued)
    """Step 7c: swap out every useless card (see the rules above), whatever step put it in - required, material and
       mention cards included. First it tries a REPAIR (_repair: add the required card that is missing, up to REPAIR_CAP). Cards that only worked BECAUSE of a removed card go with it. Each removed copy is replaced
       by a card of the same kind that works here; the Extra Deck's gaps are refilled with fully summonable monsters.
       Runs up to LAST_PASSES times. Returns the new main deck; changes `extra` in place."""
    swaps = []                                                              # report: 'X -> Y (why)'
    repairs = []                                                            # report: 'filler -> missing card (needed by X)'
    repair_left = REPAIR_CAP                                                # copies a repair may still add
    deck_arche = set().union(*[e.info.facts["archetype"] for e in entries]) if entries else set()   # for the Extra Deck refill
    for _ in range(LAST_PASSES):                            # a few passes
        bad_main, bad_extra = useless_cards(builder, extra_builder, rules, entries, extra)  # every useless card, main and Extra Deck
        if not bad_main and not bad_extra:                  # none:
            break                                                           # every card works: done
        if repair_left > 0 and bad_main:                                    # (0) repair before removing: add missing required cards
            # add what's missing
            entries, repair_left = _repair(builder, rules, entries, extra, bad_main, pool, rng, repair_left, repairs)
            bad_main, bad_extra = useless_cards(builder, extra_builder, rules, entries, extra)  # look again
            if not bad_main and not bad_extra:              # none left:
                break                                                       # the repairs fixed everything
        # (1) Extra Deck: take out the monsters that can't work; the refill at the end fills their places
        dropped = [e for e in extra if e.info.name in bad_extra]  # the Extra Deck cards that go
        extra[:] = [e for e in extra if e.info.name not in bad_extra]       # [:] = change the caller's list itself
        for n, why in bad_extra.items():                    # each one
            swaps.append(f"Extra Deck: {n} removed ({why})")  # note it (debug info)
        # (2) main deck: the useless cards plus anything that only worked because of them
        gone = _dependents(builder, rules, entries, extra, set(bad_main)) - builder.anchor_names   # (never the anchor card)
        removed = [e for e in entries if e.info.name in gone]               # every copy that goes, with its origin
        entries = [e for e in entries if e.info.name not in gone]  # the deck without them
        for e in removed:                                                   # one replacement per removed copy
            new = _replacement(builder, rules, e.info, e.origin, entries, extra, pool, rng, generic)  # a working card of the same kind
            why = bad_main.get(e.info.name, "depended on a removed card")  # the reason (its own problem, or it depended on one)
            if new is None:                                 # nothing fits:
                entries.append(e)                                           # nothing at all fits: keep it (reported below)
                continue
            entries.append(Entry(new, e.origin, f"last check: replaced {e.info.name} ({why})"))  # add the replacement with the same origin
            swaps.append(f"{e.info.name} -> {new.name} ({why})")  # note the swap (debug info)
        # (3) Extra Deck gaps: monsters this main deck can fully summon
        # refill, but never with a monster just removed
        _last_refill(builder, extra_builder, entries, extra, req, rules, rng, deck_arche, generic, {e.info.name for e in dropped})
    bad_main, bad_extra = useless_cards(builder, extra_builder, rules, entries, extra)  # what is still useless after the passes
    debug["last_check"] = swaps                             # every swap (debug info)
    debug["last_check_repairs"] = repairs                   # every repair (debug info)
    debug["still_useless"] = {**bad_main, **{f"{n} (Extra Deck)": w for n, w in bad_extra.items()}}  # anything still useless (debug info)
    return entries                                          # hand back the new main deck


# function: refill the Extra Deck with fully summonable monsters
def _last_refill(builder: Builder, extra_builder: ExtraBuilder, entries: list[Entry], extra: list[Entry], req: Request,
                 rules: Rules, rng: random.Random, deck_arche: set, generic: bool, banned: set) -> None:  # (its inputs, continued)
    """Fill the Extra Deck back up with monsters the main deck can FULLY summon whose requirements are all in the deck
       (the deck's own archetype and generic ones first). `banned` = monsters the last check just removed."""
    monsters = [e.info for e in entries if e.info.kind == "Monster"]  # the main deck's monsters (one per copy)
    current = restate(entries)                              # the main deck as a State
    have = {}                                                               # card name -> copies already in the Extra Deck
    for e in extra:                                         # count the copies already in
        have[e.info.name] = have.get(e.info.name, 0) + 1    # (one more copy)
    # candidates: not just removed, copies left
    cands = [c for c in extra_builder.pool(req) if c.name not in banned and have.get(c.name, 0) < builder.cap(c.name)
             and (c.clean or not generic)                                   # Generic deck: clean extras only
             and summon_score(extra_builder.slots[c.name], monsters) >= FULL_SCORE  # fully summonable
             and all(satisfied(i, current, extra) for i in requirements(c, rules))]  # and their required cards are all in the deck
    while cands and len(extra) < req.extra_deck_size:       # until full (or no candidates left)
        card = rng.choices(cands, weights=[_fit_weight(c, deck_arche) for c in cands])[0]  # pick one, the deck's archetypes first
        # 1-3 copies (never past its limit or the Extra Deck size)
        n = min(extra_builder.copies(rng), builder.cap(card.name) - have.get(card.name, 0), req.extra_deck_size - len(extra))
        for _ in range(n):                                  # add that many copies
            extra.append(Entry(card, "generic", "last check: Extra Deck refill (fully summonable)"))  # (with the reason)
        have[card.name] = have.get(card.name, 0) + n        # count them
        cands = [c for c in cands if c.name != card.name]   # don't pick it again


def _final_report(builder: Builder, entries: list[Entry], extra: list[Entry], req: Request, chosen: dict,  # function: step 9 - the final checks, saved in the debug notes
                  debug: dict) -> None:                     # (its inputs, continued)
    """Step 9: the checks saved in the deck's debug notes - copy limits, sizes, rules, needs, junk."""
    final_deck = restate(entries)                           # the finished main deck as a State
    over = {n: c for n, c in final_deck.counts.items() if c > builder.cap(n)}   # over its copy limit (3 / banlist / Highlander)
    for e in extra:                                                         # the Extra Deck counts too
        k = sum(1 for x in extra if x.info.name == e.info.name)  # copies of this Extra Deck card
        if k > builder.cap(e.info.name):                    # over its limit?
            over[e.info.name] = k                           # note it
    debug["over_copy_limit"] = over                         # everything over its limit
    # which format the deck was built for
    debug["format"] = {"banlist": req.banlist or "Unlimited", "release-date pool": bool(req.banlist and req.date_pool)}
    debug["main_size"], debug["extra_size"] = len(entries), len(extra)  # the final sizes
    debug["rule_violations"] = rule_violations(entries, builder)  # any deck rule broken
    # cards whose needs aren't covered
    debug["unmet_needs"] = [c.name for c in final_deck.unique.values() if not needs_covered(builder, c, final_deck)]
    debug["junk_cards"] = [c.name for c in final_deck.unique.values() if builder.junk(c, final_deck)]   # cards that can't really work here
    debug["aliases"] = {" / ".join(k): ", ".join(f"{n} x{v.count(n)}" for n in dict.fromkeys(v)) for k, v in chosen.items()}   # 'Harpie Lady 1 x1, Harpie Lady 2 x2' 


def _build_deck(builder: Builder, extra_builder: ExtraBuilder, req: Request) -> Deck:  # function: build one deck, all nine steps
    """One whole deck, in nine steps (each one is a function in this file or ygo_extra.py):"""
    # 1. theme, support, staples and filler (ygo_builder.py), plus the Extra Deck, in the chosen order (ygo_extra.py)
    deck, extra, debug = generate_state(builder, extra_builder, req)  # step 1: the first draft of the main deck and Extra Deck
    generic = deck.generic_deck                                             # remembered: restate() makes fresh States
    rng = random.Random(f"{req.seed}-final")                                # separate, reproducible stream for the final pass
    rules = Rules(builder)                                                  # name resolver + archetype list
    size = max(40, min(60, req.main_deck_size))             # the main deck size, kept between 40 and 60
    pool = builder.allowed(req, rated=False)                                # theme / synergy cards ignore the rating limits

    # 2. alias names (Harpie Lady / Harpie Lady 1 ...) mixed - in BOTH decks - then back under the copy limits
    chosen: dict = {}                                                       # alias names used, shared by main + extra
    apply_aliases(deck.entries, rules, rng, chosen, builder.cap)  # give main-deck alias cards their random names
    apply_aliases(extra, rules, rng, chosen, builder.cap)   # and Extra Deck alias cards
    deck = restate(deck.entries)                            # rebuild the deck State
    deck = cap_copies(builder, deck, pool, size, rng, rules)  # cut alias groups back to one card's limit

    deck, extra = _add_needed_cards(builder, extra_builder, deck, extra, req, rules, rng, debug, pool, generic)   # 3.
    entries = _back_to_size(builder, deck, size, pool, rng, debug, generic, rules)                                    # 4.
    deck_arche = set().union(*[e.info.facts["archetype"] for e in entries]) if entries else set()   # (for step 7)
    extra, dropped = _drop_unsummonable(extra_builder, entries, extra, rules, debug)                             # 5.
    # 6. almost no Extra Deck left? swap a few main-deck cards for ones that unlock some (Tuners, Polymerization ...)
    entries = add_enablers(builder, extra_builder, entries, extra, req, pool, rules, rng, debug, generic)  # step 6
    _refill_extra(builder, extra_builder, entries, extra, dropped, deck_arche, req, rules, rng, debug, generic)   # 7.
    # 7b. steps 3-7 only check each NAME's limit, so they can add Harpie Lady 1 to a deck that already has 3 Harpie Lady:
    #     cut every alias group back to one card's limit again (a deck that is fine is left exactly as it is)
    entries = cap_copies(builder, restate(entries), pool, size, rng, rules).entries  # step 7b
    # 7c. last check: any card that still can't work (unmet strict need, missing required card, unsummonable Extra Deck
    #     monster, Ritual without its partner, a floodgate that stops its own deck) is swapped for one that can
    entries = last_check(builder, extra_builder, entries, extra, req, pool, rules, rng, debug, generic)  # step 7c
    entries = enforce_points(builder, entries, extra, pool, rng, debug)     # 8. Genesys: under the points cap
    _final_report(builder, entries, extra, req, chosen, debug)              # 9.
    return Deck(entries, extra, debug)                      # the finished deck


def build_deck(builder: Builder, extra_builder: ExtraBuilder, req: Request) -> Deck:  # function: build a deck, widening the filters if nothing is left
    """Build a deck. If the era / rating filters leave no cards, widen them step by step instead of failing:
       first drop the rating limits, then the era limit. What was widened is reported in debug['filters_widened']."""
    from dataclasses import replace                                         # copy the request with some fields changed
    steps = [("rating limits", dict(min_card_rating=None, max_card_rating=None)),  # what to widen, in order: (name, the settings to change)
             ("era limit", dict(max_era=None))]             # (then the era limit)
    widened = []                                            # what was widened (debug info)
    while True:                                             # try until it works or nothing is left to widen
        try:
            deck = _build_deck(builder, extra_builder, req)  # try to build it
            deck.debug["filters_widened"] = widened         # note what was widened
            return deck                                     # done
        except ValueError as e:                             # the build failed:
            if "No cards left" not in str(e) or not steps:
                raise                                                       # a different error, or nothing left to widen
            name, change = steps.pop(0)                     # the next thing to widen
            widened.append(name)                            # note it
            req = replace(req, **change)                    # the same request with that filter removed; try again


# --------------------------------------------------------------------------
# Self-test:  python ygo_final.py
# --------------------------------------------------------------------------
if __name__ == "__main__":                                  # only when this file is run directly
    here = Path(__file__).resolve().parent / "data"                         # the data folder (program/data)
    builder = Builder(read_csv(here / "cards.csv"), load_tree(here / "game_mechanics_tree.csv"))  # load every card and the mechanic tree
    extra_builder = ExtraBuilder(builder)                   # the Extra Deck engine

    # One deck in full.
    req = Request(seed=21, deck_type_1_label="Archetype = blackwing", support_1_label="Random")  # a Blackwing deck with a random support category
    d = build_deck(builder, extra_builder, req)             # build it
    print("required cards added:", d.debug["required_added"])  # print what the steps did
    print("extra dropped for requirements:", d.debug["extra_dropped_for_requirements"])  # (Extra Deck monsters dropped)
    print("swaps:", d.debug["swaps"])                       # (swaps)
    # (sizes and problems)
    print("sizes:", d.debug["main_size"], d.debug["extra_size"], "| over copy limit:", d.debug["over_copy_limit"], "| unmet:", d.debug["unmet_needs"])
    for origin in ("theme1", "support1", "partner", "material", "required", "staple", "generic"):  # the main deck, grouped by why each card is in it
        names = [e.info.name for e in d.main if e.origin == origin]  # the names with this origin
        if names:                                           # any?
            print(f"  [{origin}] " + ", ".join(f"{names.count(n)}x {n}" for n in dict.fromkeys(names)))  # print them as '2x Name'
    print("  [extra] " + ", ".join(dict.fromkeys(e.info.name for e in d.extra)))  # the Extra Deck

    # Many decks: every rule must hold.
    bad = 0                                                 # decks with problems
    stats = {"unmet_before": 0, "unmet_after": 0}           # running totals
    # 5 archetypes + Random, each in both build orders
    tests = [dict(deck_type_1_label=t, build_order=o) for t in ("Archetype = blackwing", "Archetype = laval", "Archetype = gishki", "Archetype = nekroz", "Archetype = performapal", "Random")
             for o in ("main-first", "extra-first")]        # (the two build orders)
    for kw in tests:                                        # each test setting
        for seed in range(3):                               # three seeds each
            r = Request(seed=seed, **kw)                    # the request
            dk = build_deck(builder, extra_builder, r)      # build it
            final_deck = restate(dk.main)                   # the finished main deck as a State
            ok = (len(dk.main) == 40 and len(dk.extra) <= 15 and not dk.debug["over_copy_limit"])  # right sizes and no card over its limit
            # every extra monster's required cards should be in the main deck now
            rules = Rules(builder)                          # the requirement reader
            # Extra Deck requirements still missing
            miss = [i for e in dk.extra for i in requirements(e.info, rules) if not satisfied(i, final_deck, dk.extra)]
            if dk.debug["rule_violations"]:                 # a deck rule broken?
                bad += 1                                    # count it
                print("RULE BROKEN", kw, seed, dk.debug["rule_violations"])  # and print it
            if not ok or miss:                              # wrong size / over a limit / missing requirement?
                bad += 1                                    # count it
                print("PROBLEM", kw, seed, len(dk.main), len(dk.extra), dk.debug["over_copy_limit"], miss[:3])  # and print it
            stats["unmet_after"] += len(dk.debug["unmet_needs"])  # add up the uncovered needs
    # the summary line
    print(f"\n{len(tests)*3} decks checked, {bad} with problems; leftover cards with uncovered needs in total: {stats['unmet_after']}")

    # Reproducible?
    # a deck from seed 5...
    a = [e.info.name for e in build_deck(builder, extra_builder, Request(seed=5, deck_type_1_label="Random", build_order="random")).main]
    # ...and the same again
    c = [e.info.name for e in build_deck(builder, extra_builder, Request(seed=5, deck_type_1_label="Random", build_order="random")).main]
    print("same seed identical:", a == c)                   # must be the same list
