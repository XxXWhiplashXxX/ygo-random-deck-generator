"""
ygo_data.py  --  STEP 1 of the new randomizer.

Three jobs:
  1. Load the filled master CSV (with the right encoding).
  2. Turn the support columns ("A && (B || C)") into a structure Python can test.
  3. Answer two questions about cards:
        a) Does this card MATCH a trait expression?   ("is it a WATER monster?")
        b) Does a mechanic that a card HELPS WITH cover a mechanic another card NEEDS?
           (uses game_mechanics_tree.csv: Special Summon (Deck) covers Special Summon)
"""
from __future__ import annotations                      # lets us write list[str] style hints on older Pythons

import csv                                              # reading the CSV files
import re                                               # small text clean-ups
from collections import namedtuple                      # a tiny "record" type for atoms
from pathlib import Path                                # file paths

# --------------------------------------------------------------------------
# 1. Small helpers
# --------------------------------------------------------------------------

# An Atom is one single label, split into three parts.
#   "Attribute = WATER"      -> Atom("attribute", "=",  "water")
#   "Level <= 4"             -> Atom("level",     "<=", 4)
#   "Self = Banish (GY)"     -> Atom("self",      "=",  "banish (gy)")
Atom = namedtuple("Atom", "field op value")                 # Atom = (field, operator, value)


def norm(text: str) -> str:                                 # function: lower-case and squeeze spaces
    """Lower-case and squeeze spaces, so 'Trait  = Fusion Monster' == 'trait = fusion monster'."""
    return re.sub(r"\s+", " ", str(text).strip().lower())   # collapse any run of whitespace into one space


def norm_trait(value: str) -> str:                          # function: normalise a trait value
    """Card traits and support labels word things differently ('Xyz' vs 'Xyz Monster').
    We drop a trailing ' monster' so both sides compare equal."""
    v = norm(value)                                      # start from the normalised text
    if v.endswith(" monster"):                           # 'xyz monster' -> 'xyz'
        v = v[: -len(" monster")]                           # drop the ' monster' ending
    return v                                                # hand it back


def read_csv(path: str | Path, encoding: str = "utf-8-sig") -> list[dict]:  # function: read a CSV file into a list of rows
    """Read a CSV into a list of dict rows. utf-8-sig removes the invisible BOM that
    Excel adds, which otherwise turns the first header into '\\ufeffCard Id'."""
    with open(path, newline="", encoding=encoding) as f:  # open with the chosen encoding
        return list(csv.DictReader(f))                    # one dict per row


# --------------------------------------------------------------------------
# 2. Parsing a support expression into OR-branches of AND-atoms
# --------------------------------------------------------------------------

# Operators we look for when splitting an atom into field / op / value (two-char ones first!).
OPS = ("!=", "<=", ">=", "=")                               # the operators, longest first ('<=' before '=')


def parse_atom(text: str) -> Atom:                          # function: split one label into an Atom
    """'Attribute = WATER' -> Atom('attribute', '=', 'water'). Numbers become ints."""
    text = text.strip()                                   # remove edge spaces
    for op in OPS:                                        # try each operator, longest first
        if op in text:                                    # found it
            field, value = text.split(op, 1)              # split only at the first one
            field, value = norm(field), norm(value)       # tidy both halves
            if re.fullmatch(r"-?\d+", value):             # purely digits -> real number
                return Atom(field, op, int(value))          # (as a number)
            return Atom(field, op, value)                 # otherwise keep as text
    return Atom("", "", norm(text))                       # no operator at all (shouldn't happen)


def _tokenize(expr: str) -> list[str]:                      # function: cut an expression into tokens
    """Cut the expression into tokens: '(' ')' '&&' '||' and atom text.
    Tricky part: labels contain their own brackets, e.g. 'Self = Send to GY (Hand)'.
    A '(' is a GROUPING bracket only at the start or right after an operator / another
    grouping '('. Anywhere else it belongs to the atom."""
    tokens: list[str] = []                                # finished tokens
    buf = ""                                              # atom text we are collecting
    i, n = 0, len(expr)                                   # scan position and length
    atom_depth = 0                                        # how many atom-brackets are open right now
    while i < n:                                          # walk through every character
        ch = expr[i]                                      # current character
        if expr.startswith("&&", i) or expr.startswith("||", i):   # an operator
            if buf.strip(): tokens.append(buf.strip())    # flush the atom before it
            buf = ""                                      # start a fresh atom
            tokens.append(expr[i:i + 2])                  # keep the operator itself
            i += 2                                        # skip both characters
            continue
        if ch == "(":                                     # an opening bracket
            starts_group = (buf.strip() == "") and atom_depth == 0   # nothing collected yet -> grouping
            if starts_group:                                # it starts a group:
                tokens.append("(")                        # grouping bracket
            else:
                buf += ch                                 # bracket inside a label, e.g. '(Hand)'
                atom_depth += 1                           # remember we are inside it
            i += 1                                          # next character
            continue
        if ch == ")":                                     # a closing bracket
            if atom_depth > 0:                            # closes a label bracket like '(Hand)'
                buf += ch                                   # keep it in the atom
                atom_depth -= 1                             # one fewer label bracket open
            else:                                         # closes a grouping bracket
                if buf.strip(): tokens.append(buf.strip())   # flush the atom first
                buf = ""                                    # start a fresh atom
                tokens.append(")")                          # the closing bracket token
            i += 1                                          # next character
            continue
        buf += ch                                         # ordinary character -> part of the atom
        i += 1                                              # next character
    if buf.strip(): tokens.append(buf.strip())            # flush the last atom
    return tokens                                           # hand back the tokens


def parse_expr(expr: str) -> list[tuple[Atom, ...]]:        # function: read an expression into OR-branches of AND-ed atoms
    """Turn a support expression into 'disjunctive normal form':
         a list of BRANCHES (joined by OR), each branch a tuple of ATOMS (joined by AND).
       '(A || B) && C'  ->  [(A, C), (B, C)]
       An empty string gives []  (meaning: no requirement / no help)."""
    if not expr or not expr.strip():                      # blank cell
        return []                                           # (no branches)
    tokens = _tokenize(expr)                              # step 1: tokens
    pos = 0                                               # where we are in the token list

    def parse_or() -> list[tuple[Atom, ...]]:             # expr := and ( '||' and )*
        nonlocal pos                                        # we move `pos` here
        branches = parse_and()                            # first AND-group
        while pos < len(tokens) and tokens[pos] == "||":  # each '||' adds more branches
            pos += 1                                        # skip the '||'
            branches = branches + parse_and()             # OR = just collect the branches
        return branches                                     # hand back every branch

    def parse_and() -> list[tuple[Atom, ...]]:            # and := factor ( '&&' factor )*
        nonlocal pos                                        # we move `pos` here
        branches = parse_factor()                         # first factor
        while pos < len(tokens) and tokens[pos] == "&&":  # each '&&' multiplies branches together
            pos += 1                                        # skip the '&&'
            right = parse_factor()                          # the next factor
            branches = [a + b for a in branches for b in right]   # every left branch x every right branch
        return branches                                     # hand back the combined branches

    def parse_factor() -> list[tuple[Atom, ...]]:         # factor := '(' expr ')' | atom
        nonlocal pos                                        # we move `pos` here
        tok = tokens[pos]                                 # look at the next token
        if tok == "(":                                    # a grouped sub-expression
            pos += 1                                        # skip the '('
            inner = parse_or()                            # parse what is inside
            if pos < len(tokens) and tokens[pos] == ")":  # consume the closing bracket
                pos += 1                                    # skip the ')'
            return inner                                    # hand back what was inside
        pos += 1                                          # a plain atom
        return [(parse_atom(tok),)]                       # one branch holding one atom

    return parse_or()                                     # start at the top level


def is_mechanic(atom: Atom) -> bool:                        # function: is this atom a mechanic (an action) rather than a trait?
    """Mechanics start with 'Self', 'Opponent' or 'Floodgate' (they describe actions, not card traits)."""
    return atom.field in ("self", "opponent", "floodgate")  # Self / Opponent / Floodgate labels are mechanics


# --------------------------------------------------------------------------
# 3a. Does a card MATCH a trait expression?
# --------------------------------------------------------------------------

def card_facts(card: dict) -> dict:                         # function: everything a card IS, ready for quick tests
    """Collect everything a card IS, in a form that is quick to test.
       sets: type / attribute / archetype / series / trait / card type      numbers: level, rank, ..."""
    facts: dict = {"type": set(), "attribute": set(), "archetype": set(), "series": set(), "trait": set(),  # one set per kind of fact...
                   "card type": set(),                      # ...plus the card type...
                   "name": {norm_trait(card.get("name", ""))}}          # lets material rules say name = "Elemental HERO Neos"
    for other in (card.get("alias") or "").split("||"):                 # an alias group (Harpie Lady 1 is always treated as "Harpie Lady"):
        if other.strip():                                   # a real name:
            facts["name"].add(norm_trait(other))                        # the card answers to every name in its group
    if card.get("type"):      facts["type"].add(norm(card["type"]))             # e.g. 'dragon'
    if card.get("attribute"): facts["attribute"].add(norm(card["attribute"]))   # e.g. 'water'
    # archetype / series / traits columns look like 'Archetype = a || Archetype = b'.
    # Here the '||' means "has ALL of these", so we just collect the values into a set.
    for a in parse_expr(card.get("archetype", "")):         # each archetype label
        for atom in a: facts["archetype"].add(norm_trait(atom.value))  # add each archetype value
    for a in parse_expr(card.get("series", "")):             # 'Series = railway' -> a 'Series = Railway' theme finds it
        for atom in a: facts["series"].add(norm_trait(atom.value))  # add each series value
    for a in parse_expr(card.get("traits", "")):            # each trait label
        for atom in a:                                      # each piece
            facts["trait"].add(norm_trait(atom.value))      # add the trait
            facts["card type"].add(norm_trait(atom.value))   # 'Card Type != Continuous Spell' is used in the data
    if card.get("primary card type"):                        # Monster / Spell / Trap
        facts["card type"].add(norm(card["primary card type"]))  # (also counts as a card type)
    for key in ("level", "rank", "link rating", "atk", "def"):   # numeric stats (blank for cards without them)
        raw = str(card.get(key, "")).strip()                # the cell as text
        facts[key] = int(raw) if re.fullmatch(r"-?\d+", raw) else None  # a whole number, or None if blank
    return facts                                            # hand back the facts


def trait_atom_true(atom: Atom, facts: dict) -> bool:       # function: is one trait atom true for a card?
    """Is this single trait atom true for a card with these facts?"""
    f, op, v = atom.field, atom.op, atom.value              # its three parts
    if f in ("level", "rank", "link rating", "atk", "def"):          # numeric comparison
        have = facts.get(f)                                 # the card's number (or None)
        if have is None or not isinstance(v, int):                   # card has no such stat -> false
            return False                                    # (false)
        return {"=": have == v, "!=": have != v, "<=": have <= v, ">=": have >= v}[op]  # compare with the operator
    pool = facts.get(f)                                              # set of values for this field
    if pool is None:                                                 # unknown field name -> can't say
        return False                                        # (false)
    present = norm_trait(v) in pool                                  # does the card have that value?
    return present if op == "=" else (not present)                   # '!=' flips it


def card_matches(branches: list[tuple[Atom, ...]], facts: dict) -> bool:  # function: does a card match an expression?
    """True if ANY branch has ALL its trait atoms true. Mechanic atoms (Self = ...) are
    ignored here: they are actions, handled separately. An empty expression matches everything."""
    if not branches:                                        # nothing asked:
        return True                                         # everything matches
    for branch in branches:                                          # OR over branches
        traits = [a for a in branch if not is_mechanic(a)]           # only the trait atoms
        if all(trait_atom_true(a, facts) for a in traits):           # AND inside the branch
            return True                                     # this branch works
    return False                                            # no branch worked


# --------------------------------------------------------------------------
# 3b. Mechanic tree: does a help cover a need?
# --------------------------------------------------------------------------

def load_tree(path: str | Path) -> dict[str, set[str]]:     # function: read the mechanic tree
    """Read Parent,Child rows and return {label: {itself + every descendant}} (normalised text).
    Labels in the tree are the exact 'Self = ...' labels, e.g. 'Self = Special Summon'."""
    children: dict[str, set[str]] = {}                               # direct children only
    for row in read_csv(path):                              # each Parent,Child row
        p, c = norm(row["Parent"]), norm(row["Child"])      # both sides normalised
        children.setdefault(p, set()).add(c)                # note the child under its parent
    closure: dict[str, set[str]] = {}                                # children of children, etc.
    for node in set(children) | {c for s in children.values() for c in s}:  # every label in the tree
        seen, stack = {node}, [node]                                 # depth-first walk down the tree
        while stack:                                        # until nothing is left to visit
            for ch in children.get(stack.pop(), ()):        # each child of the next label
                if ch not in seen:                          # not seen yet:
                    seen.add(ch); stack.append(ch)          # add it and visit it later
        closure[node] = seen                                # the label plus everything under it
    return closure                                          # hand back label -> descendants


def mechanic_label(atom: Atom) -> str:                      # function: an atom written back as label text
    """Rebuild the normalised label text, e.g. Atom('self','=','banish (gy)') -> 'self = banish (gy)'."""
    return f"{atom.field} {atom.op} {atom.value}"           # 'field op value'


def mechanic_covers(need: Atom, offered: set[str], tree: dict[str, set[str]]) -> bool:  # function: does an offered mechanic cover a need?
    """A NEED is covered if some offered mechanic is the same label or one of its descendants.
       need 'self = special summon'  is covered by offered 'self = special summon (deck)'.
       The other way round does NOT count (needing Deck-summons is not met by a generic one)."""
    label = mechanic_label(need)                            # the need as label text
    allowed = tree.get(label, {label})                               # label plus its descendants
    return any(o in allowed for o in offered)               # any offered mechanic in that set?


def offered_mechanics(card: dict) -> set[str]:              # function: the mechanics a card performs
    """Every mechanic a card PERFORMS for the deck (labels in its two 'Supports' columns)."""
    out: set[str] = set()                                   # the set we build
    for col in ("strictSupports", "generalSupports"):       # its two Supports columns
        for branch in parse_expr(card.get(col, "")):        # each branch
            for atom in branch:                             # each piece
                if is_mechanic(atom): out.add(mechanic_label(atom))  # mechanics only
    return out                                              # hand back the set


# --------------------------------------------------------------------------
# 3c. Enablers: what must be in the deck so that a mechanic NEED can actually happen
# --------------------------------------------------------------------------
# Rule (agreed): the trait part of a need is HARD, the mechanic part is SOFT.
# Soft means: if the deck has an enabler the card gets a weight bonus; if not, it is
# NOT banned. Any mechanic that is not listed below counts as "always possible"
# (cards in hand, Life Points, the Deck itself, ...).

# Groups of enablers, named so the table below stays short.
GY_FILLERS = ["self = send to gy", "self = self-mill", "self = discard", "self = destroy"]       # the tree adds their children
FIELD_FILLERS = ["self = special summon", "self = normal summon", "self = token", "self = set monster"]  # cards on the field
FUSION_ETC = ["self = fusion summon", "self = synchro summon", "self = xyz summon", "self = link summon"]  # Extra Deck summons
# battle help
BATTLE_HELP = ["self = gain atk", "self = direct attack", "self = piercing battle damage", "self = multi-attack"]

# need label -> {"mech": [mechanic labels that enable it], "trait": [card traits that enable it]}
ENABLERS: dict[str, dict[str, list[str]]] = {               # need label -> what makes it possible
    # needs a filled graveyard
    "self = banish (gy)":                {"mech": GY_FILLERS, "trait": []},  # (banish from the GY)
    "self = banish face-down (gy)":      {"mech": GY_FILLERS, "trait": []},  # (banish face-down from the GY)
    "self = shuffle into deck (from gy)": {"mech": GY_FILLERS, "trait": []},  # (shuffle from the GY into the Deck)
    # needs a monster / card on the field
    "self = tribute":                    {"mech": FIELD_FILLERS, "trait": []},  # (Tribute)
    "self = send to gy (field)":         {"mech": FIELD_FILLERS, "trait": []},  # (send from the field)
    "self = banish (field)":             {"mech": FIELD_FILLERS, "trait": []},  # (banish from the field)
    "self = self-bounce":                {"mech": FIELD_FILLERS, "trait": []},  # (return itself to the hand)
    "self = shuffle into deck (from field)": {"mech": FIELD_FILLERS, "trait": []},  # (shuffle from the field into the Deck)
    # equipping needs equip cards (your ruling): Equip Spells, Union monsters, or cards that equip
    # Equip Spells / Union monsters / equip effects
    "self = equip":                      {"mech": ["self = equip", "self = union equip"], "trait": ["equip spell", "union"]},
    # Xyz / Extra Deck mechanics
    # needs Xyz Monsters / attached materials
    "self = detach xyz material":        {"mech": ["self = xyz summon", "self = attach xyz material"], "trait": []},
    "self = return to extra deck":       {"mech": FUSION_ETC, "trait": []},  # needs Extra Deck summons
    # counters, flips and battle
    "self = remove counters":            {"mech": ["self = place counters"], "trait": []},  # needs counters placed
    # needs set / flipped monsters
    "self = flip":                       {"mech": ["self = set monster", "self = change battle position", "self = flip summon"], "trait": []},
    "opponent = destroys monster by battle":            {"mech": BATTLE_HELP, "trait": []},  # needs a monster that can win battles
    "opponent = inflicts battle damage to your opponent": {"mech": BATTLE_HELP, "trait": []},  # (the same)
}


def need_status(need: Atom, offered: set[str], deck_traits: set[str], tree: dict[str, set[str]]) -> str:  # function: is a mechanic need always possible, met by the deck, or unmet?
    """Return 'always' (nothing needed), 'met' (an enabler is in the deck) or 'unmet'.
       offered     = mechanics the deck's cards perform (union of offered_mechanics)
       deck_traits = traits the deck's cards have (union of facts['trait'])"""
    label = mechanic_label(need)                          # e.g. 'self = banish (gy)'
    rule = ENABLERS.get(label)                            # look up what enables it
    if rule is None:                                      # not listed -> always possible
        return "always"                                     # (nothing needed)
    for m in rule["mech"]:                                # any enabling mechanic, or one of its children
        if any(o in tree.get(m, {m}) for o in offered):     # the deck offers it (or something under it)
            return "met"                                    # (met)
    if any(t in deck_traits for t in rule["trait"]):      # or an enabling card trait (Equip Spell ...)
        return "met"                                        # (met)
    return "unmet"                                          # nothing in the deck makes it possible


# --------------------------------------------------------------------------
# Quick self-test:  python ygo_data.py
# --------------------------------------------------------------------------
if __name__ == "__main__":                                  # only when this file is run directly
    here = Path(__file__).resolve().parent / "data"         # the data folder (program/data)
    cards = read_csv(here / "cards.csv")                             # the filled master
    tree = load_tree(here / "game_mechanics_tree.csv")               # the new mechanic tree
    print(f"{len(cards)} cards, {len(tree)} tree nodes, first header = {list(cards[0])[0]!r}")  # the summary line

    # (1) Every support cell must parse without error and give sensible branches.
    cols = ("strictSupports", "generalSupports", "strictSupportedBy", "generalSupportedBy")  # the four support columns
    bad = 0                                                 # problems found
    for c in cards:                                         # each card
        for col in cols:                                    # each column
            try:
                for br in parse_expr(c[col]):               # each branch
                    for atom in br:                         # each piece
                        if atom.field == "": bad += 1                # an atom with no operator = parse problem
            except Exception as e:                                   # any crash is a bug to fix
                bad += 1; print("PARSE ERROR", c["name"], col, e)  # count it and print it
    print("atoms that failed to parse:", bad)               # the total

    # (2) Show how a tricky expression splits up.
    by_name = {c["name"]: c for c in cards}                 # name -> card row
    for name in ("Snake-Eye Birch", "Marincess Coral Triangle"):  # two tricky cards
        print("\n", name)                                   # its name
        for br in parse_expr(by_name[name]["strictSupportedBy"]):  # each branch of its strict need
            print("   branch:", [f"{a.field} {a.op} {a.value}" for a in br])  # print its pieces

    # (3) Which cards satisfy 'Attribute = FIRE && Card Type = Monster'?  (Birch's first need)
    need = parse_expr("Attribute = FIRE && Card Type = Monster")  # 'FIRE monster'
    hits = [c["name"] for c in cards if card_matches(need, card_facts(c))]  # every card that matches
    print("\nFIRE monsters:", len(hits), hits[:5])          # how many, and the first five

    # (3b) Series themes: which cards are in the Railway series?
    rail = [c["name"] for c in cards if card_matches(parse_expr("Series = Railway"), card_facts(c))]  # every Railway series card
    print("Railway series:", len(rail), rail[:5])           # how many, and the first five

    # (4) Tree check: a card that needs 'Self = Special Summon' versus helpers.
    need_atom = parse_atom("Self = Special Summon")         # 'needs Special Summon'
    # a Deck summon covers it
    print("\nSpecial Summon (Deck) covers 'Special Summon':", mechanic_covers(need_atom, {"self = special summon (deck)"}, tree))
    # a banish doesn't
    print("Banish (GY) covers 'Special Summon':          ", mechanic_covers(need_atom, {"self = banish (gy)"}, tree))
    # a plain summon doesn't cover a Deck summon
    print("Special Summon covers 'Special Summon (Deck)':", mechanic_covers(parse_atom("Self = Special Summon (Deck)"), {"self = special summon"}, tree))
    print("Zoodiac Ratpier offers:", sorted(offered_mechanics(by_name["Zoodiac Ratpier"])))  # what one card performs

    # (5) Enabler checks: sample decks made of just a few cards.
    def deck_info(names):                                            # offered mechanics + traits of a mini deck
        off, tr = set(), set()                              # mechanics / traits of the mini deck
        for n in names:                                     # each card in it
            off |= offered_mechanics(by_name[n]); tr |= card_facts(by_name[n])["trait"]  # add its mechanics and traits
        return off, tr                                      # hand them back
    for label, deck in (("self = banish (gy)", ["Zoodiac Ratpier"]),                # Ratpier sends to GY -> met
                        ("self = banish (gy)", ["Pot of Greed"]),                   # nothing fills GY -> unmet
                        ("self = equip", ["Pot of Greed"]),                         # no equip cards -> unmet
                        ("self = equip", ["Fossil Warrior Skull Bone"]),            # not an equip card -> unmet
                        ("self = send to gy (hand)", ["Pot of Greed"])):            # cards in hand -> always
        off, tr = deck_info(deck)                           # the mini deck's mechanics / traits
        print(f"need {label!r} with {deck}: {need_status(parse_atom(label), off, tr, tree)}")  # print the answer
    equip_spell = next(c["name"] for c in cards if "Equip Spell" in c["traits"])    # any real Equip Spell
    off, tr = deck_info([equip_spell])                      # a deck of just that Equip Spell
    # print the answer
    print(f"need 'self = equip' with [{equip_spell}]: {need_status(parse_atom('self = equip'), off, tr, tree)}")
