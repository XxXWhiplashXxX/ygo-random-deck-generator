"""
ygo_related.py  --  decides whether a Yugipedia "related" link is worth keeping.

Yugipedia lists some cards as RELATED to an archetype or series without being members of it.
Some of those links are real deck links (Release Restraint -> Gearfried, Fish Sonar -> Umi).
Many are only lore: "Recolored counterpart" (Kuriboh, Petit Moth ...), "Signature move",
"Zombie counterpart" - cards that look alike or belong to the same character but do nothing together.

A related link is KEPT only when the cards actually interact:
  1. the card's text names the archetype / series, or one of its member cards, in "quotes"
  2. a member card's text names this card in "quotes"
  3. a material link: the card's materials name the label or a member, or a member's materials name the card
  4. the card's Supports columns mention the label (e.g. 'Archetype = red dragon archfiend')
  5. specific support between them, either way, by at least a quarter of the members (and at least 2):
     members support the card (Transcendosaurus cards support Normal Dinosaurs -> Xeno Meteorus), or the card
     supports the members (Supersoaring revives Dinosaurs -> the Transcendosaurus cards). Only specific support
     counts: the branch must name a Type or a trait (Normal, Tuner, Fusion ...). A bare Attribute ('LIGHT
     monsters') or card type ('Spells') is too loose on its own.
Everything else is dropped. Real members are never touched.

Used by:
  ygo_wiki_labels.py     (the card updater, so new cards follow the same rule)
  ygo_series_pages.py    (the 'Related' sections of series pages)
  ygo_trim_related.py    (the one-off clean-up of the current cards.csv)
"""
import re                                  # regular expressions: finding "quoted" names in card text
from collections import defaultdict        # a dict that starts every new key with an empty set

from ygo_data import card_facts, card_matches, is_mechanic, parse_expr   # reading Supports columns and testing cards

QUOTED = re.compile(r'"([^"]+)"')                                  # a "quoted" name in card text

# Series that are only lore or naming: their cards look alike, share a character or an anime moment, but do
# nothing together in a deck. These are dropped completely, members AND related (lowercase label keys).
# Add more here if you find them.
LORE_SERIES = {                                             # lore-only series (lower-case names)
    "recolored counterpart",       # Kuriboh, Petit Moth, Dark Plant ... - recolors of other monsters
    "zombie counterpart",          # Zombie versions of other monsters
    "dark counterpart",            # DARK versions of other monsters
    "aged counterpart",            # older versions of other monsters
    "signature move",              # a character's signature card (Exodia, Jinzo #7, Meklord Emperor Wisel ...)
    "duel winner",                 # the card that won a famous anime duel
    "match winner",                # ...the same for Match Winner prize cards
    "konami arcade games",         # cards based on old Konami arcade games
    "25th anniversary monsters",   # anniversary artwork set
    "fairy tale (anime)",          # anime-only fairy-tale references
    "panik's monsters",            # cards one anime character used
    "uniform nomenclature",        # cards that only share a naming pattern
    "name-inverted spiritualist",  # ...the same
}
MIN_MEMBERS = 2          # check 5: at least this many members must be part of the support...
SUPPORT_SHARE = 0.25     # ...and at least this share of all the members
SUPPORT_COLS = ("strictSupports", "generalSupports", "strictSupportedBy", "generalSupportedBy")  # the four support columns


def label_key(name: str) -> str:                            # function: a wiki label name -> the card file's lower-case name
    """A wiki archetype / series name -> the lowercase name the card file uses.
       'Red Dragon Archfiend (archetype)' -> 'red dragon archfiend', 'Umi (series)' -> 'umi',
       'Number (spell/trap archetype)' -> 'number'."""
    name = re.sub(r"<!--.*|-->", "", name or "")                   # leftover wiki comment marks
    name = re.sub(r"\s*\((?:archetype|series|spell/trap archetype)\)$", "", name.strip(), flags=re.I)  # drop the '(archetype)' / '(series)' ending
    return name.strip().lower()                             # lower-case, no edge spaces


def quoted_names(text: str) -> set:                         # function: every quoted name in a text, lower-case
    """Every "quoted" name in a card's text, lowercase."""
    return {q.strip().lower() for q in QUOTED.findall(text or "")}  # (as a set)


def names_label(quoted: set, key: str) -> bool:             # function: does any quoted name contain the label as whole words?
    """True if one of the quoted names IS the label or contains it as whole words
       ('Red Dragon Archfiend' inside "Red Dragon Archfiend Scar"). Whole words only, so 'Toy' does not match 'Toyota'."""
    pattern = re.compile(r"(?<![\w-])" + re.escape(key) + r"(?![\w-])")  # the label, not touching other letters or dashes
    return any(pattern.search(q) for q in quoted)           # any quoted name with it inside?


class RelatedChecker:                                       # class: answers 'should this related link stay?'
    """Built once from every card, then asked about one (card, label) link at a time.

         cards    : {card name: card row}  - each row needs 'card text'; 'materials' and the Supports columns are used if present
         members  : {label key: set of member card names}  - the REAL members (Yugipedia 'archseries'), NOT the related ones
    """

    def __init__(self, cards: dict, members: dict):         # set up once from every card
        self.cards = cards                                  # card name -> row
        self.members = members                              # label -> real member names
        self.quoted = {}                                            # card name -> its quoted names (worked out once each)
        self.named_by = defaultdict(set)                            # lowercase card name -> names of the cards that quote it
        for name, row in cards.items():                     # each card
            q = quoted_names(row.get("card text", ""))      # its quoted names
            self.quoted[name] = q                           # save them
            for other in q:                                         # remember who names whom
                self.named_by[other].add(name)              # note that this card names it
        self.material_names = {name: quoted_names(row.get("materials", "")) for name, row in cards.items()}   # names in materials
        self.facts = {name: card_facts(row) for name, row in cards.items()}    # what each card IS (Type, traits ...), worked out now
        self.specific = {}                                                     # card name -> its specific support branches (cached)

    def specific_support(self, name: str) -> list:          # method: a card's specific support branches (check 5)
        """The branches of a card's strictSupports that name a Type or a trait (check 5). Mechanics are left out of
           each branch (only what the cards ARE matters here), and branches that name an archetype, series or card
           are skipped (checks 1 to 4 already handle names). Worked out once per card."""
        if name not in self.specific:                       # not worked out yet:
            branches = []                                   # the branches we keep
            try:
                parsed = parse_expr(self.cards[name].get("strictSupports", "") or "")  # read its strictSupports
            except Exception:                                                  # an odd entry we can't read: no support
                parsed = []                                 # (no branches)
            for branch in parsed:                           # each branch
                atoms = tuple(a for a in branch if not is_mechanic(a))         # 'Self = ...' parts are actions, not traits
                if any(a.field in ("archetype", "series", "name") for a in atoms):
                    continue                                                   # about names: not check 5's job
                if any(a.field in ("type", "trait") for a in atoms):           # specific: a Type or a trait is named
                    branches.append(atoms)                  # keep it
            self.specific[name] = branches                  # save the answer
        return self.specific[name]                          # hand back the saved branches

    def keep(self, card: str, label: str) -> bool:          # method: should this related link stay?
        """True if the RELATED link card -> label should stay (the cards really interact)."""
        key = label_key(label)                              # the label's card-file name
        row = self.cards.get(card)                          # the card's row
        if row is None or not key or key in LORE_SERIES:            # unknown card, or a lore-only series: never kept
            return False                                    # (drop it)
        members = {m for m in self.members.get(key, set())            # the label's real member cards, not this one,
                   if m != card and m in self.cards}                    # and only real cards (a wiki link can be 'hand' or 'WIND')
        member_low = {m.lower() for m in members}           # member names, lower-case
        my_quotes = self.quoted.get(card, set())            # what this card's text quotes
        # 1. this card's text names the label or a member
        if names_label(my_quotes, key) or (my_quotes & member_low):  # the label, or a member, in quotes
            return True                                     # keep
        # 2. a member's text names this card
        if self.named_by.get(card.lower(), set()) & members:  # a member quotes this card
            return True                                     # keep
        # 3. materials: this card's materials name the label / a member, or a member's materials name this card
        my_mats = self.material_names.get(card, set())      # names in this card's materials
        if names_label(my_mats, key) or (my_mats & member_low):  # the label or a member is in them
            return True                                     # keep
        if any(card.lower() in self.material_names.get(m, set()) for m in members):  # a member's materials name this card
            return True                                     # keep
        # 4. its Supports columns mention the label ('Archetype = umi', 'Series = umi')
        support_text = " ".join(row.get(c, "") for c in SUPPORT_COLS).lower()  # its four support columns as one text
        if re.search(r"(?:archetype|series) = " + re.escape(key) + r"(?![\w-])", support_text):  # 'archetype = X' or 'series = X' in it
            return True                                     # keep
        # 5. specific support between the card and the archetype, in either direction, by enough of the members:
        #    a) members support this card (Transcendosaurus cards support Normal Dinosaurs -> Xeno Meteorus)
        #    b) this card supports the members (Supersoaring revives Dinosaurs -> every Transcendosaurus)
        #    'Enough' = at least MIN_MEMBERS members and SUPPORT_SHARE of them, so one HERO that supports Warriors
        #    doesn't pull in every Warrior Yugipedia ever listed as related.
        if members:                                         # the label has members:
            need = max(MIN_MEMBERS, SUPPORT_SHARE * len(members))  # how many members must take part
            my_facts = self.facts[card]                     # what this card is
            supported_by = sum(1 for m in members                               # a) members whose support fits this card
                               # (only members with specific support count)
                               if self.specific_support(m) and card_matches(self.specific_support(m), my_facts))
            if supported_by >= need:                        # enough of them:
                return True                                 # keep
            mine = self.specific_support(card)              # this card's specific support
            supports = sum(1 for m in members if mine and card_matches(mine, self.facts[m]))   # b) members this card supports
            if supports >= need:                            # enough of them:
                return True                                 # keep
        return False                                                # only a lore link: drop it


def members_from_wiki(wiki_rows: list) -> dict:             # function: every label's real members, from the wiki notes
    """{label key: set of member card names} from card_archseries.csv rows (the 'archseries' column only)."""
    members = defaultdict(set)                              # label -> member names
    for r in wiki_rows:                                     # each card's wiki row
        for value in r.get("archseries", "").split(" | "):  # each label its page lists as a member label
            if value.strip():                               # not empty:
                members[label_key(value)].add(r["name"])    # add the card to that label
    return members                                          # hand back the members


def kept_related(wiki_rows: list, checker: RelatedChecker) -> set:  # function: every related link worth keeping (two passes)
    """Every related link worth keeping, as a set of (card name, wiki label) pairs. Done in two passes:
         pass 1: the normal checks against the archetype's real members
         pass 2: the links kept in pass 1 now count as members too, and the dropped links are checked again.
       Pass 2 catches cards that work with the archetype's other related cards: Ground Xeno searches Normal Dinosaurs,
       and those Normal Dinosaurs (Xeno Meteorus ...) only became part of Transcendosaurus in pass 1."""
    links = [(r["name"], v.strip()) for r in wiki_rows if r.get("status", "ok") == "ok"  # every (card, related label) from pages we could read
             for v in r.get("related_to_archseries", "").split(" | ") if v.strip()]   # every (card, related label)
    kept = {(card, label) for card, label in links if checker.keep(card, label)}         # pass 1
    for card, label in kept:                                                             # pass-1 cards join the members
        checker.members.setdefault(label_key(label), set()).add(card)  # add it to that label's members
    kept |= {(card, label) for card, label in links                                      # pass 2: the dropped ones again
             if (card, label) not in kept and checker.keep(card, label)}  # (pass 2 checks)
    return kept                                             # hand back the kept links
