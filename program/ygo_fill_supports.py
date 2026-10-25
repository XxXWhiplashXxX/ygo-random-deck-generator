#!/usr/bin/env python3
"""
Yu-Gi-Oh support-column filler
==============================
Fills the four support columns of cards.csv - who a card helps and what it needs:
    strictSupports      traits / mechanics the card helps that only some decks have   (Archetype = Blackwing, Self = Fusion Summon)
    generalSupports     plain mechanics the card does for any deck                    (Self = Special Summon (GY), Opponent = Destroy)
    strictSupportedBy   what the card needs, with the kind of card it needs           (Attribute = DARK && Self = Discard)
    generalSupportedBy  plain mechanics the card needs                                (Self = Pay LP)

    1. split every card text into (condition, cost, effect) pieces
    2. find trait expressions (Archetype / Type / Attribute / Level ...) for who is helped and what is needed
    3. find mechanics (Special Summon, Discard, Banish, Negation ...) and put them in the right column
    4. merge everything and check that every label really exists in the label CSVs

Usage (all arguments are optional, the defaults are the file names in the current folder):

    python ygo_fill_supports.py --cards cards.csv --stats Stats.csv --mechanics Mechanics.csv --out cards_filled.csv

The card file is never changed; the result is written to --out.

Map of this file
    PART 1  labels, text splitter, trait words
        LABELS, find_label()             the trait labels from Stats.csv
        split_card_text(), parse()       card text -> (condition, cost, effect) pieces
        tokens(), extract_terms()        trait words -> expressions like "Attribute = DARK && Card Type = Monster"
        not_my_cards(), skip_token()     drop trait words about the opponent's cards or cards the effect hits
    PART 2  mechanic matchers
        MECH, mech_label()               the mechanic labels from Mechanics.csv and the columns each may go in
        keep_allowed(), cost_target(), need_exprs() ...   small helpers every matcher shares
        special_summon ... flip_coin_dice   one matcher per group of mechanics; each returns the four lists (ss, gs, sb, gb)
        fill_card()                      one card: tidy its pieces, run every matcher in MATCHERS, build the four columns
    PART 3  main()                       read the cards, fill every card, check every label exists, write --out

Short names used everywhere:  ss = strictSupports, gs = generalSupports, sb = strictSupportedBy, gb = generalSupportedBy
"""
import csv, io, re, sys, argparse, collections, functools                      # standard library only, nothing to install

# ---------------- configuration: which files to read and write ----------------
_ap = argparse.ArgumentParser(description="Fill the four support columns of the Yu-Gi-Oh master CSV")   # create the command-line parser (lets you pass file names when running the script)
_ap.add_argument("--cards", default="cards.csv", help="the master card CSV")   # --cards: the master card CSV to read
_ap.add_argument("--stats", default="Stats.csv", help="label CSV with traits / archetypes / stats")   # --stats: the label CSV with traits, archetypes and stats
_ap.add_argument("--mechanics", default="Mechanics.csv", help="label CSV with the mechanic labels and their column flags")   # --mechanics: the label CSV with mechanic labels and which columns each may go in
_ap.add_argument("--out", default="All Cards Master filled.csv", help="where to write the filled copy")   # --out: where the filled copy is written
_args = _ap.parse_args()                                             # read the command line
CARD_CSV, LABEL_CSV, MECH_CSV, OUT_CSV = _args.cards, _args.stats, _args.mechanics, _args.out   # the four paths used below

# ================================================================
# PART 1: label loading, text splitter and trait extraction
# ================================================================

# ---------------- labels ----------------
def norm(s): return re.sub(r"\s+", "", s.lower())   # norm(): lowercase and delete all spaces, so "Level = 4" and "level=4" compare equal
LABELS = {}   # LABELS will map normalised label -> label written exactly as it must appear in the output
for r in csv.DictReader(open(LABEL_CSV, encoding="utf-8-sig", newline="")):   # loop over every row of the label CSV (Stats and Archetypes)
    lab = r["Label"].strip()   # the label text of this row, without stray spaces
    tidy = re.sub(r"\s+", " ", re.sub(r"\s*(=|!=|<=|>=)\s*", r" \1 ", lab))   # put exactly one space around =, !=, <=, >= and collapse repeated spaces
    LABELS[norm(lab)] = ("Trait" + tidy[5:] if tidy.lower().startswith("trait ") else ("DEF" + tidy[3:] if tidy.lower().startswith("def ") else tidy))   # force the spelling "Trait ..." / "DEF ..." (the CSV mixes cases); every other label is kept as tidied

STAT_VALUES = {k: sorted(int(m.group(1)) for lab in LABELS.values() for m in [re.match(k + r" = (\d+)$", lab)] if m)   # for ATK and DEF: a sorted list of every number that has a label ("ATK = 1000" -> 1000)
               for k in ("ATK", "DEF")}   # build that list once for each of the two stats

def stat_label(stat, num, op):   # stat_label(): find a label for "ATK 1800 or less" style text (op is =, <= or >=)
    exact = find_label(stat, num, op)   # first try the exact number
    if exact or op == "=": return exact   # found, or an exact match was required anyway: stop here
    n, vals = int(num), STAT_VALUES[stat]   # otherwise get the number and the list of numbers that do have labels
    if op == "<=":   # "or less": the nearest labelled number at or below is a safe stand-in
        ok = [v for v in vals if v <= n]   # all labelled numbers that are not above n
        return find_label(stat, str(ok[-1]), op) if ok else None   # take the biggest of them (the list is sorted), or nothing if none exist
    ok = [v for v in vals if v >= n]   # "or more": labelled numbers that are not below n
    return find_label(stat, str(ok[0]), op) if ok else None   # take the smallest of them, or nothing if none exist

def find_label(kind, value, op="="):   # find_label(): look up "<kind> = <value>" and swap the operator if another one is wanted
    base = LABELS.get(norm(kind + " = " + value))   # labels only exist with "=", so look that form up first
    if base is None: return None   # no such label in the CSV
    return base if op == "=" else base.replace(" = ", " " + op + " ", 1)   # for <=, >=, != turn the first " = " into the wanted operator

# ---------------- splitter ----------------
START_WORDS = (r"\b(?:When|If|During|Once per|You can|Cannot|Must|Gains|Neither|Your opponent|Each|All|Monsters|"   # START_WORDS: words that normally start the real effect text (used to skip material lines)
               r"This card|Unaffected|Negate|Any|At the|At|After|While|Select|You cannot|Once during)\b")   # (second half of the same regular expression)
MANUAL_TEXT = {"Archfiend Zombie-Skull": "Zombie monsters you control cannot be destroyed by card effects.",   # MANUAL_TEXT: card name -> the text the parser reads instead of the real text
    # --- single cards whose real wording the parser cannot read; the text below says the same thing in standard (PSCT) form ---
    "Lava Dragon": "Tribute this face-up Defense Position card; Special Summon 2 Level 3 or lower Dragon-Type monsters from your hand and/or Graveyard.",   # old text: the Tribute is the cost, two Special Summons are the effect
    "Yum☆Yum☆Yummys": 'When 2 or more "Yummy" monsters are Special Summoned to your field at the same time: You gain 500 LP, and if you do, your opponent loses 500 LP. You can pay 500 LP, then target 2 face-up monsters on the field including a "Yummy Snatchy"; send both to the GY, and if you do, Special Summon 1 "Yummy" Synchro Monster from your Extra Deck. If your opponent activates a card or effect (except during the Damage Step): You can pay 100 LP; immediately after this effect resolves, Link Summon 1 "Yummy" Link Monster.',   # the comma after "same time" confused the condition split
    "Gonogo": "At the end of the Damage Step, if this card battled an opponent's monster: Change 1 monster your opponent controls to face-down Defense Position.",   # "that monster" is the opponent's battled monster
    "Regulus": "Once per turn: You can select 1 Field Spell Card in your Graveyard; return it to the Deck.",   # the select is the cost, so "it" is a card in the GY
    "Bombardment Beetle": "FLIP: Select 1 face-down Defense Position Monster Card your opponent controls; destroy it if it is an Effect Monster.",   # the opponent's face-down monster is destroyed
    "Restoration of the Monarchs": "Target 1 monster in your GY with 2400 ATK and 1000 DEF or with 2800 ATK and 1000 DEF; Special Summon it in Defense Position, but negate its effects, also equip it with this card. The equipped monster can be treated as 2 Tributes for a Tribute Summon.",   # the two stat pairs are the requirement
    "All-Out Attacks": "When your opponent Special Summons a monster(s): Change all monsters your opponent controls to Attack Position; your opponent's monsters must attack, if able.",   # the Special Summoned monster is changed to Attack Position and must attack
    "Gozen Match": "Each player sends all other face-up monsters they control to the GY.",   # both players send their extra monsters
}   # MANUAL_TEXT: hand-written effect text for a card whose real text is missing or damaged

def clean_text(t):   # clean_text(): repair the damaged bullet character found in some card texts
    return t.replace('?Ã‚Â\x8f', '●').replace('?Â\x8f', '●')   # replace the two damaged byte patterns with the real bullet "●"
def protect_quotes(t):   # protect_quotes(): hide : . ; inside "quoted card names" so they do not split sentences
    return re.sub(r'"[^"]*"', lambda m: m.group(0).replace(":", "\x00").replace(".", "\x01").replace(";", "\x02"), t)   # inside each quoted name, swap those three characters for invisible placeholders
def restore_quotes(t): return t.replace("\x00", ":").replace("\x01", ".").replace("\x02", ";")   # restore_quotes(): turn the placeholders back into : . ;
TYPES = ("Beast-Warrior|Winged Beast|Sea Serpent|Divine-Beast|Creator God|Spellcaster|Dinosaur|Warrior|Machine|Reptile|"   # TYPES: every monster Type, longer names first so "Beast-Warrior" is not read as "Beast"
         "Psychic|Zombie|Dragon|Insect|Cyberse|Illusion|Thunder|Fairy|Fiend|Plant|Beast|Aqua|Fish|Pyro|Rock|Wyrm")   # (rest of the Type list)
MAT_WORDS = (TYPES + r"|"   # every monster Type, then ...
             r"LIGHT|DARK|EARTH|WATER|FIRE|WIND|DIVINE|Tuners?|Effect|Normal|Fusion|Synchro|Xyz|Link|Pendulum|Ritual|Spell|Traps?|Monsters?|Level|Rank|Extra|Deck|Special|Summoned|GY|Graveyard|Main|Tokens?|Cards?|Flip|Toon|Spirit|Union|Gemini|Non-effect|Attack|Defense|Position|Field")   # capital words that can appear INSIDE a material line (types, attributes, kinds of monster ...)
FIRST_SENTENCE = re.compile(r"(?<=[a-z+\d\)\"] )(?=(?!(?:" + MAT_WORDS + r")\b)[A-Z][a-z]+\b[^.]{0,200}?\b(?:gains?|loses?|cannot|can|must|take|never|would|banish|Banish|you control|are|is)\b)|(?:(?<=monsters )|(?<=monster )|(?<=Tuners )|(?<=Tuner ))(?=(?:[A-Z][\w-]+|\"[^\"]+\") (?:non-\w+ )?(?:monsters?|cards?|Tokens?) (?:you control|your opponent controls|on the field)\b)")   # where a first effect sentence starts right after the material line
SKIP_FIRST = re.compile(r"(?:This (?:monster|card) (?:can only|cannot) be (?:Special )?Summoned|Types and Attributes|For this card's|A (?:Fusion|Synchro|Xyz|Link) Summon of (?:this|the)|This (?:monster|card) cannot be Special Summoned except)", re.I)   # sentences about how this card is Summoned: not an effect
def strip_materials(text, materials, traits):   # strip_materials(): cut the summoning-material line off Fusion/Synchro/Xyz/Link cards
    extra = any(w in traits.lower() for w in ("fusion", "synchro", "xyz", "link"))   # extra is True when the card is a Fusion, Synchro, Xyz or Link
    if not (materials.strip() or extra): return text   # nothing to cut when there are no materials and it is not such a card
    m = re.search(START_WORDS, text)   # find the first word that starts the real effect text
    end = m.start() if m else len(text)   # the material line (and maybe a first effect sentence) lies before this point
    b = FIRST_SENTENCE.search(re.sub(r'"[^"]*"', lambda q: '"' + "q" * (len(q.group(0)) - 2) + '"', text[:end]))   # (quoted card names are hidden first so words inside them are not mistaken for a sentence start)   # a continuous effect written right after the material line ("2 Level 4 monsters Other LIGHT monsters you control gain 500 ATK.")
    if b and (m is None or b.start() < m.start()):   # found one before the usual start words
        first = text[b.start():]   # keep it
        if not SKIP_FIRST.match(first): return first   # unless it is only a rule about Summoning this card ("A Fusion Summon of this card can only be ...")
        return text[m.start():] if m else ""   # then skip that sentence like before
    return text[m.start():] if m else ""   # keep the text from there on (or nothing if no such word was found)
def is_restriction(s): return bool(re.match(r"you can only (use|activate)", s, re.I))   # is_restriction(): True for "You can only use/activate ... once per turn" sentences, which are not effects

COST_VERBS = r"(?:tribute|discard|reveal|send|banish|remove from play|return)"   # COST_VERBS: verbs that mean paying a cost
NOT_TARGET = r"(?!the\b|your\b|you\b|that\b|its\b|their\b|his\b|a\b|an\b|each\b|any\b|\d)"   # NOT_TARGET: the text after "to" must NOT start with these words (then "to" is not a cost/effect divider)
PAY_RE = re.compile(r"^(pay (?:\d+|half your) (?:life points|lp))\s*(?:[,.]|to\b|and\b)?\s*(.*)$", re.I)   # PAY_RE: "Pay 1000 LP" or "Pay half your LP" at the start, then the rest of the sentence
TO_RE = re.compile(r"^(?:(?P<cond>.*?,\s+))?(?:you can\s+)?(?P<cost>" + COST_VERBS + r"\b[^,.]*?)\s+to\s+" + NOT_TARGET + r"(?P<eff>.+)$", re.I)   # TO_RE: "(condition,) [you can] <cost verb> ... to <effect>" i.e. old-style text where "to" separates cost and effect
BY_RE = re.compile(r"^(?P<eff>.*?),?\s+by\s+(?P<cost>(?:discarding|tributing|banishing|sending|removing|paying|returning)\b.+)$", re.I)   # BY_RE: "<effect> by discarding/Tributing/... <cost>" i.e. a cost written after "by"
BY_CUT = re.compile(r",\s*(?=you can\b|and cannot\b|also\b|then\b|and if you do\b)", re.I)   # BY_CUT: where the cost after "by" ends and a new clause begins

LEAD_BY = re.compile(r"^by\s+(?P<cost>(?:discarding|tributing|banishing|sending|removing|paying|returning)\b[^,;]*),\s*(?P<eff>.+)$", re.I)   # LEAD_BY: old text that starts with the cost: "By Tributing 1 Normal Monster, inflict 800 damage"

def split_old_cost(cond, cost, eff):   # split_old_cost(): old card wording has no ";" so find cost and effect inside the effect text
    if cost: return cond, cost, eff   # a cost is already known: change nothing
    m = LEAD_BY.match(eff)   # first the leading "By Tributing 1 monster, <effect>" pattern
    if m: return cond, m.group("cost").strip(), m.group("eff").strip()   # the "By ..." part is the cost, the rest the effect
    m = PAY_RE.match(eff)   # try the "Pay LP" pattern first
    if m and m.group(2): return cond, m.group(1), m.group(2)   # "Pay X LP ..." gives the payment as the cost and the rest as the effect
    m = TO_RE.match(eff)   # next try "<cost verb> ... to <effect>"
    if m:   # matched
        c2 = (m.group("cond") or "").strip().rstrip(",")   # any condition found before the cost (without the trailing comma)
        return (cond + " " + c2).strip(), m.group("cost").strip(), m.group("eff").strip()   # return the merged condition, the cost and the effect
    m = BY_RE.match(eff)   # last try "<effect> by <cost>"
    if m:   # matched
        cst, tail = m.group("cost"), ""   # the cost text after "by", and an empty tail
        cut = BY_CUT.search(cst)   # look for the point where the cost ends
        if cut: cst, tail = cst[:cut.start()], cst[cut.end():]   # if found, the cost stops there and the rest becomes the tail
        return cond, cst.strip(), (m.group("eff") + (", " + tail if tail else "")).strip()   # return the condition, the cost, and the effect with the tail added back
    return cond, cost, eff   # nothing matched: return the three parts unchanged

MAIN_CLAUSE = (r"you can|you may|you cannot|you must|you take|you draw|your opponent|this card|that |it |negate|destroy|add |"   # MAIN_CLAUSE: words that begin a main clause (used to find where an inline condition ends)
               r"special summon|normal summon|banish|draw|inflict|each|all |both|neither|place|send|take|target|select|gain|equip|"   # (continued list of words)
               r"return|shuffle|change|activate|attach|detach|set |discard|tribute|pay|skip|face-up|monsters|the |also|then")   # (continued list of words)
INLINE_COND = re.compile(r"^(?:If|When|While|Each time|As long as|After|Whenever|During|Before|Until)\b(?P<c>.*?),\s+(?=(?:" + MAIN_CLAUSE + r")\b)(?P<e>.+)$", re.I)   # INLINE_COND: "If/When/While ... , <main clause>" written in one sentence without a colon

def split_inline_condition(cond, cost, eff):   # split_inline_condition(): turn "If X, do Y" into condition "If X" and effect "do Y"
    if cond or cost: return cond, eff   # a condition or cost already exists: leave it alone
    m = INLINE_COND.match(eff)   # try the pattern on the effect text
    if not m: return cond, eff   # no inline condition found: unchanged
    return eff[:m.end("c")].strip(), m.group("e").strip()   # condition = text up to the comma, effect = what follows it

TRAIL_IF = re.compile(r"^(?P<e>(?:change|destroy|send|banish|Special Summon|add|draw|return|switch|negate|shuffle|discard|take|place|equip|inflict|gain|target)\b[^;:]*?),?\s+(?P<c>if (?!you do\b|so\b|not\b)(?:your|the|that|a|an|this|you control)\b[^;:,]*?)\.?$", re.I)   # TRAIL_IF: "<do something>, if <condition>" -> effect and condition

def split_trailing_condition(cond, cost, eff):   # split_trailing_condition(): move a closing ", if <condition>" out of the effect and into the condition
    if cost: return cond, eff   # a cost exists: leave the piece alone
    m = TRAIL_IF.match(eff)   # does the effect end with an "if ..." condition?
    if not m: return cond, eff   # no: unchanged
    return ((cond + ", " if cond else "") + m.group("c")).strip(), m.group("e").strip()   # condition gets the new "if ..." text, effect keeps the action

def split_card_text(text, materials="", traits=""):   # split_card_text(): cut a whole card text into (condition, cost, effect) pieces
    text = clean_text(text).replace("\r", "")   # repair the text and remove carriage returns
    sections = [p.strip() for p in re.split(r"\[\s*(?:Pendulum Effect|Monster Effect)\s*\]", text) if p.strip()]   # split into Pendulum / Monster effect sections and drop empty ones
    out = []   # out will collect the finished pieces
    for sec in sections:   # handle each section on its own
        if sec.lstrip().startswith("''"): continue   # a Pendulum monster's flavor text (written between double apostrophes) is not an effect
        sec = strip_materials(sec.replace("\n", " "), materials, traits)   # join lines, and drop the materials line for Extra Deck monsters
        sec = re.sub(r"\((?:This|Quick Effect|Transfer|You do not use|in which case you do not use)[^)]*\)", "", sec, flags=re.I)   # remove reminder text in brackets like "(This card is always treated as ...)"
        sec = protect_quotes(sec)   # hide : . ; inside quoted card names
        for s in re.split(r"(?<=\.)\s+|\s+(?=●)", sec):   # split into sentences at ". " and before every bullet "●"
            s = s.strip().lstrip("●").strip()   # trim the sentence and its leading bullet
            if s and is_restriction(s):   # "You can only use ... once per turn" is not an effect ...
                lock = re.search(r"\byou cannot\b[^.;]*\bexcept\b[^.;]*", s, re.I)   # ... but "..., also you cannot Special Summon ..., except X" in it is a lock
                if lock and SUMMON_LOCK.search(lock.group(0)): out.append(("", "", restore_quotes(lock.group(0).strip())))   # keep just the lock
                continue   # the rest of the restriction is skipped
            if not s: continue   # skip empty sentences
            colon, semi = s.find(":"), s.find(";")   # positions of the first ":" and the first ";" in the sentence
            cond, rest = "", s   # by default there is no condition and everything is the "rest"
            if colon != -1 and (semi == -1 or colon < semi): cond, rest = s[:colon], s[colon + 1:]   # a ":" that comes before any ";" ends the condition (PSCT rule)
            cost, eff = rest.split(";", 1) if ";" in rest else ("", rest)   # a ";" splits the rest into cost (before) and effect (after); no ";" means no cost
            cond, cost, eff = (restore_quotes(x.strip()) for x in (cond, cost, eff))   # trim the three parts and restore the protected characters
            if not eff and not cost and cond: cond, cost = "", cond   # 'You can X: ● ...' -> the lead-in is the cost of the bullets
            if not eff and not cost: continue       # keep 'cost; ' pieces whose effect is the bullets that follow
            cond, eff = split_inline_condition(cond, cost, eff)   # move an "If/When ... ," lead-in from the effect into the condition
            cond, eff = split_trailing_condition(cond, cost, eff)   # move a closing ", if ..." condition from the effect into the condition
            out.append(split_old_cost(cond, cost, eff))   # apply the old-wording split, then store the finished piece
    return out   # return all pieces of the card

RULES_WORDS = re.compile(r"\b(?:your opponent|opponent's|you|your|this card|all (?:face-up )?(?:monsters|cards)|Tribut\w*|Summon\w*|Graveyard|Life Points|LP|ATK|DEF|Level|inflict\w*|effects?|negate\w*|counters?|Position|Extra Deck|Deck|hand|field|battle damage|destroyed|equip\w*)\b")   # RULES_WORDS: words that real card effects use but flavor text almost never does

def FLAVOR_CANDIDATE(r, txt):   # FLAVOR_CANDIDATE(): a Normal (non-effect) monster with no Pendulum Effect section - its text is only flavor
    t = r["traits"] or ""   # the traits column (from YGOPRODeck, so "Normal" really means no effect)
    return "Trait = Normal" in t and "Non-effect" in t and "Pendulum Effect" not in txt   # (flavor text may contain colons: "IMPORTANT: Capturing ...")

def merge_lead_cost(pieces, r, txt):   # merge_lead_cost(): in old (Duel Monsters era) text, a stand-alone first sentence like "Tribute 1 monster." is the cost of the next sentence
    if (r.get("era") or "") != "Duel Monsters" or ":" in txt or ";" in txt or len(pieces) < 2: return pieces   # only old-era cards with no ":" / ";" and at least two sentences
    c0, k0, e0 = pieces[0]   # the first piece: condition, cost, effect
    c1, k1, e1 = pieces[1]   # the second piece
    if c0 or k0 or c1 or k1: return pieces   # either piece already has a condition or cost: leave everything alone
    if not re.match(r"(?:tribute|discard|send|banish|reveal|pay (?:\d+|half your))\b", e0, re.I): return pieces   # the first sentence must start with a cost verb (Tribute, Discard, Pay ...)
    if re.match(r"(?:" + COST_VERBS + r")\b", e1, re.I): return pieces   # two cost-like sentences in a row: too unclear, keep as is
    return [(c1, e0, e1)] + pieces[2:]   # first sentence becomes the cost of the second; later pieces stay unchanged

def parse(r):   # parse(): split one CSV row (one card) into pieces
    name = r["name"]   # the card's name
    if name in MANUAL_TEXT:   # some cards use hand-written text instead of the CSV text
        return split_card_text(MANUAL_TEXT[name], "", "")   # split that manual text (no materials, no traits)
    txt = r["card text"] or ""   # the card text
    if FLAVOR_CANDIDATE(r, txt): return []   # a Normal (non-effect) monster only has flavor text (traits come from YGOPRODeck, so "Normal" really means no effect)
    return merge_lead_cost(split_card_text(txt, r["materials"] or "", r["traits"] or ""), r, txt)   # otherwise split the card text, giving the materials and traits columns for stripping

# ---------------- traits ----------------
CMP = r"(?:\s+or\s+(?P<{}>lower|higher|less|more))?"   # CMP: optional "or lower / or higher / or less / or more" after a number (named group set by format)
OPS = {None: "=", "lower": "<=", "less": "<=", "higher": ">=", "more": ">="}   # OPS: map those words to the comparison operators
TOKEN = re.compile(   # TOKEN: one big regular expression whose named groups recognise each kind of trait word
    r'"(?P<q>[^"]+)"'   # a "Quoted Name" (archetype or card name)
    r"|(?P<nst>no\s+Spell(?:/|\s+or\s+)Trap\s+Cards?)"   # "no Spell/Trap Cards" (a Spell/Trap restriction)
    r"|(?P<n1>non-)?(?P<attr>LIGHT|DARK|EARTH|WATER|FIRE|WIND|DIVINE)\b"   # an Attribute, optionally with "non-" in front
    r"|(?P<n2>non-)?(?P<type>" + TYPES + r")(?:-Type)?\b"   # a monster Type, optionally "non-" and "-Type"
    r"|(?P<n3>non-)?(?P<tr1>Fusion|Synchro|Xyz|Link|Ritual|Pendulum|Normal|Effect|Gemini|Toon|Spirit|Union|Flip)\s+[Mm]onsters?\b"   # a monster trait such as "Fusion Monster", "Effect Monster", "Spirit monster"
    r"|(?P<n4>non-)?(?P<tr2>Tuner)\b(?:\s+Monsters?\b)?"   # "Tuner" (optionally followed by "Monster")
    r"|(?P<st>Normal|Continuous|Quick-Play|Equip|Field|Ritual|Counter)\s+(?P<sttype>Spell|Trap)s?(?:/(?P<sttype2>Spell|Trap))?(?:\s+Cards?)?\b"   # a Spell/Trap subtype such as "Continuous Trap" or "Equip Spell"
    r"|with\s+(?P<a1>\d+)\s+(?P<s1>ATK|DEF)(?:\s+and\s+(?P<a2>\d+)\s+(?P<s2>ATK|DEF))?"   # "with 1000 ATK" (and optionally "and 500 DEF")
    r"|(?:with\s+)?(?P<s3>ATK|DEF)\s+(?:of\s+)?(?P<a3>\d+)\s+or\s+(?P<c3>lower|higher|less|more)"   # "ATK of 1000 or lower" wording
    r"|(?:with\s+)?(?P<a4>\d+)\s+or\s+(?P<c4>lower|higher|less|more)\s+(?P<s4>ATK|DEF)"   # "1000 or lower ATK" wording
    r"|Level\s+(?P<lv>\d+)(?:\s+or\s+(?P<lv2>\d+)\b(?!\s*(?:ATK|DEF|%)))?" + CMP.format("lc") +   # "Level 4", optionally "or lower/higher"
    r"|Rank\s+(?P<rk>\d+)" + CMP.format("rc") +   # "Rank 4", optionally "or lower/higher"
    r"|Link-(?P<lk>\d)" + CMP.format("kc") +   # "Link-2", optionally "or lower/higher"
    r"|(?P<noun>Spell/Trap|monsters?|Spell Cards?|Trap Cards?|Spells?|Traps?)\b")   # plain nouns: monster, Spell, Trap


OPP_AFTER = re.compile(r"^[^,;.]*?(?:opponent controls|(?:in|from|on|of) your opponent's)", re.I)   # OPP_AFTER: text after a word that says it belongs to the opponent ("opponent controls", "in your opponent's")
OPP_BEFORE = re.compile(r"(?:opponent's|opponent controls(?: a| an| no| 1)?|opponent has(?: a| an| no)?"
                        r"|opponent (?:activates|Special Summons|Normal Summons|Tribute Summons|Flip Summons|Summons|Sets)(?: exactly)?(?: a| an| 1| \d+)?(?: monster effect, or| monster effect or)?(?: a| an)?)\s*(?:face-up |face-down |banished )*(?:\d+ )?$", re.I)   # ("your opponent activates a Normal Trap": THEIR card; "your opponent targets a LIGHT monster" stays ours)   # OPP_BEFORE: text before a word that says it belongs to the opponent ("opponent's", "opponent controls a")
POSSESSIVE = re.compile(r"^'s\s+(?:Level|Rank|ATK|DEF|original|Attribute|Type|name|current)", re.I)   # POSSESSIVE: "'s Level / ATK / name ..." (a trait word used as a property, not a requirement)

# ---- WHOSE card is the trait about?  (own / opponent's / hostile) ----------------------------------------------------
# A trait word only counts when it describes cards in YOUR deck that the card helps or needs. It is dropped when it describes
#   (a) the opponent's cards that are not named as such: "If this card destroys a LIGHT monster by battle" (a monster you
#       destroy in battle is always the opponent's), or
#   (b) cards the effect works AGAINST, even when no owner is named: "Effects of face-up LIGHT monsters are negated"
#       (Brain Golem) -- negating / destroying / banishing / bouncing / weakening a trait is not support for it.
OWN_MARK = re.compile(r"\byou control\b|\byour (?!opponent)[\w\x22]|\byour (?:GY|Graveyard|hand|Deck|Extra Deck|field|banished|side)\b|\b(?:from|in|to|into) your\b|\bequipped\b|\bthis card\b|\bpoints? to\b", re.I)   # OWN_MARK: words that show the cards belong to the player of this card
BATTLE_OPP = re.compile(r"\b(?:this (?:face-up )?(?:card|monster)|it)\s+(?:destroys|destroyed|attacks|attacked|battles|battled|is attacked by|inflicts?\b[^,;:.]{0,30}\bto)\b[^,;:.]*$", re.I)   # BATTLE_OPP: "this card destroys / attacks / battles ..." right before the trait (the monster it fights is the opponent's)
HOSTILE_VERB = re.compile(r"\b(?:negates?|destroys?|banish(?:es)?|bounces?|loses?|halves?|take control|switch control|return(?:s)?\b[^,;:.]*\bhand|shuffles?\b[^,;:.]*\bDeck|(?:is|are|be|been) (?:negated|destroyed|banished|returned|shuffled)|(?:cannot|can't) (?:attack|activate|be Special Summoned|be Normal Summoned)|inflicts?\b[^,;:.]*\bdamage|changed? to (?:face-down )?Defense|must attack)\b", re.I)   # HOSTILE_VERB: things you do TO a card, not FOR it
HELPFUL_WORD = re.compile(r"\b(?:(?:that|which) targets?|Special Summon|add|search|gain|gains|equip|cannot be destroyed|unaffected|protected|cannot be targeted|treated as|can be used as)\b", re.I)   # HELPFUL_WORD: a clause with one of these gives the cards something good, so it is not hostile

TRIGGER_START = re.compile(r"^\s*(?:when|if|while|each time|whenever|after|during|as long as)\b", re.I)   # TRIGGER_START: a condition that REACTS to something ("When a Fusion Monster is destroyed")
PASSIVE_HOSTILE = re.compile(r"\b(?:is|are|be|been) (?:negated|destroyed|banished|returned|shuffled)\b", re.I)   # PASSIVE_HOSTILE: the card is not the one doing it ("is destroyed")
PROTECT_ATK = re.compile(r"(?:cannot attack|can't attack|that attacks|which attacks|attacks) (?:a |an |any |all |your )?(?:face-up )?$", re.I)   # PROTECT_ATK: "monsters cannot attack X" / "a monster that attacks X": X is the one being protected

def clause_of(text, s, e):   # clause_of(): the piece of `text` around the matched word, between the nearest ; : . breaks (commas do NOT split: lists like "Fish, Sea Serpent, or Aqua" stay together)
    a = max(text.rfind(ch, 0, s) for ch in ";:.") + 1   # start of this clause
    cuts = [i for i in (text.find(ch, e) for ch in ";:.") if i != -1]   # the next break after the word
    return text[a:min(cuts) if cuts else len(text)], text[a:s]   # (whole clause, the part before the word)

EXCEPT_BEFORE = re.compile(r"\b(?:except|other than)\b[^;:.]*$", re.I)   # EXCEPT_BEFORE: "...lose 500 ATK, except Dinosaur monsters": the excepted trait is spared

def not_my_cards(text, s, e):   # not_my_cards(): True when the word at s:e describes cards that are NOT supported by this card
    clause, before = clause_of(text, s, e)   # look at the clause the word sits in
    bm = BATTLE_OPP.search(before)   # "this card battles / destroys ..." right before the word?
    if bm and not re.search(r"\byou control\b", clause, re.I) and not re.search(r"\b(?:while|has|have|with|using|material)\b", bm.group(0), re.I): return True   # "this card destroys / battles a LIGHT monster": the opponent's monster (checked first: "this card" would otherwise count as OWN)
    if OWN_MARK.search(clause): return False   # the clause names our own cards or this card: keep the word
    if PROTECT_ATK.search(before) or EXCEPT_BEFORE.search(before): return False   # protected (cannot attack X) or spared (except X): keep
    if HELPFUL_WORD.search(clause): return False   # the clause gives the cards something good
    pos = len(before)   # where the word sits inside the clause
    for hv in HOSTILE_VERB.finditer(clause):   # every hostile verb in the clause
        if hv.end() <= pos and re.search(r"\b(?:when|while|if|whenever|in response to|during|that|which)\b", clause[hv.end():pos], re.I): continue   # "...cannot activate cards when a Gishki monster is Summoned": the word sits in a new sub-clause, it is not the object of the verb
        passive = bool(PASSIVE_HOSTILE.fullmatch(hv.group(0)))   # "are negated" (done TO the subject) or an active verb ("destroys")
        if passive and re.search(r"\b(?:that|which|who)\s*$", clause[:hv.start()], re.I): continue   # "a Dinosaur monster THAT IS banished": only describes where the card is, nothing is done to it
        if passive and hv.start() > pos:   # "LIGHT monsters are negated": the word is the subject being hit
            if TRIGGER_START.match(clause): continue   # ...but "When X is destroyed" only REACTS to it
            return True   # the trait describes cards being hit: not our cards
        if not passive and hv.end() <= pos:   # "negate the effect of a LIGHT monster": the word is the object being hit
            return True   # the trait describes cards being hit: not our cards
    return False   # the word is the actor, not the target (or no hostile verb): keep it

# EXCLUDED: the word sits in a short "except ..." / "other than ..." list that runs up to it ("..., except a Pendulum Monster")
EXCLUDED = re.compile(r",?\s*\b(?:except|other than)\b(?:(?!\b(?:then|also|and if you do|if|when|while)\b|,\s*(?!(?:or|and|and/or)\b)[a-z])[^;:.)]){0,60}$")   # (the list ends at ")" or at ", including ..." / ", up to ...": only ", or" / ", and" lists go on)
EXCLUDE_ACTION = re.compile(r"\b(?:Special Summon|add|send|banish|discard|target|excavate|reveal|shuffle|place|choose|Set|equip|look at|declare)\b", re.I)   # something the card DOES to the cards before "except"
RESTRICTION = re.compile(r"\b(?:cannot|can't|can only|only|neither|nor|must|unless|be used|be (?:Normal |Special )?Summoned|treated|all|each|every)\b", re.I)   # words that make the "except" part of a lock or rule (those are handled as locks)

LIST_BEFORE = re.compile(r"(?:(?:[A-Z][\w-]*(?: [A-Z][\w-]*)*|\"[^\"]+\"),\s+(?:or\s+|and\s+|and/or\s+)?|(?:[A-Z][\w-]*(?: [A-Z][\w-]*)*|\"[^\"]+\")\s+(?:or|and|and/or)\s+)+$")   # the earlier words of a list, up to the current one: "Fusion, Synchro, or "

def skip_token(text, s, e):   # skip_token(): True when a matched word should be ignored
    if POSSESSIVE.match(text[e:]): return True   # a following "'s Level" and similar: ignore
    clause, before = clause_of(text, s, e)   # the clause the word sits in, and the part of it before the word
    ex = EXCLUDED.search(before)   # "Special Summon 1 "Performapal" monster, except a Pendulum Monster": left out, so not helped
    if ex and EXCLUDE_ACTION.search(before[:ex.start()]) and not RESTRICTION.search(before) and not HOSTILE_VERB.search(before[:ex.start()]): return True   # only after an action, never in a lock or rule ("Neither player can Special Summon monsters, except DARK monsters" still supports DARK), and never when something harmful SPARES the type ("all monsters lose 500 ATK, except Dinosaurs")
    if OPP_AFTER.match(text[e:]) or OPP_BEFORE.search(text[:s]): return True   # the word describes the opponent's cards (checked before and after): ignore
    head = LIST_BEFORE.sub("", text[:s])   # a later word of a list ("opponent's banished Fusion, Synchro, or Xyz"): look before the whole list
    if head != text[:s] and OPP_BEFORE.search(head): return True   # the whole list is the opponent's
    if not_my_cards(text, s, e): return True   # the word describes cards the effect is aimed AT (hostile / the battled monster)
    return False   # otherwise keep it

def tokens(text):   # tokens(): list every trait word found in the text as (kind, label, start, end)
    out = []   # out collects the results
    for m in TOKEN.finditer(text):   # check every match of the big TOKEN regex
        s, e = m.start(), m.end()   # where this match starts and ends in the text
        if skip_token(text, s, e): continue   # ignore matches that belong to the opponent or are properties
        if m.group("q"):   # a quoted name
            lab = find_label("Archetype", m.group("q"))   # look up the quoted name as an archetype label
            if lab: out.append(("archetype", lab, s, e))   # if the label exists, remember it
        elif m.group("nst"):   # "no Spell/Trap Cards"
            parts = [find_label("Trait", "Continuous Spell", "!="), find_label("Trait", "Field Spell", "!="),   # build one "!=" label for each forbidden Spell/Trap kind
                     find_label("Card Type", "Trap", "!="), find_label("Trait", "Continuous Trap", "!=")]   # (second half of that list)
            parts = [p for p in parts if p]   # drop the labels that do not exist
            if parts: out.append(("block", " && ".join(parts), s, e))   # join them with && into one "block" term
        elif m.group("attr"):   # an Attribute
            op = "!=" if m.group("n1") else "="   # "non-" in front means != , otherwise =
            lab = find_label("Attribute", m.group("attr"), op)   # look up the Attribute label
            if lab: out.append(("attribute" + op, lab, s, e))   # remember it (kind includes the operator, so = and != never merge)
        elif m.group("type"):   # a monster Type
            op = "!=" if m.group("n2") else "="   # "non-" in front means != , otherwise =
            lab = find_label("Type", m.group("type"), op)   # look up the Type label
            if lab: out.append(("type" + op, lab, s, e))   # remember it
        elif m.group("tr1") or m.group("tr2"):   # a monster trait or Tuner
            word = m.group("tr1") or m.group("tr2")   # take whichever of the two groups matched
            op = "!=" if (m.group("n3") or m.group("n4")) else "="   # "non-" in front means !=
            lab = find_label("Trait", word + " Monster", op)   # look up "<word> Monster" as a Trait label
            if lab: out.append(("trait" + op, lab, s, e))   # remember it
        elif m.group("st"):   # a Spell/Trap subtype
            labs = [find_label("Trait", m.group("st") + " " + m.group("sttype"))]   # label for the first Spell/Trap word
            if m.group("sttype2"):   # "Equip Spell/Trap" style text has a second type
                labs.append(find_label("Trait", m.group("st") + " " + m.group("sttype2")))   # add the label for the second type
            labs = [x for x in labs if x]   # drop labels that do not exist
            if labs: out.append(("trait=", labs[0] if len(labs) == 1 else "(" + " || ".join(labs) + ")", s, e))   # one label as is, two labels as "(A || B)"
        elif m.group("a1"):   # "with 1000 ATK" style
            l1 = find_label(m.group("s1").title() if m.group("s1") == "DEF" else "ATK", m.group("a1")) or find_label("DEF", m.group("a1"))   # label for the first stat value (ATK, or DEF when it says DEF)
            labs = [l1]   # start the list with it
            if m.group("a2"):   # a second stat ("and 500 DEF") exists
                labs.append(find_label("ATK" if m.group("s2") == "ATK" else "DEF", m.group("a2")) or find_label("DEF", m.group("a2")))   # add its label too
            labs = [x for x in labs if x]   # drop labels that do not exist
            if labs: out.append(("stat", " && ".join(labs), s, e))   # join them with && into one "stat" term
        elif m.group("s3") or m.group("s4"):   # "ATK of 1000 or lower" / "1000 or lower ATK" styles
            st = m.group("s3") or m.group("s4"); num = m.group("a3") or m.group("a4"); c = m.group("c3") or m.group("c4")   # read the stat name, the number and the comparison word
            lab = stat_label("ATK" if st == "ATK" else "DEF", num, OPS[c])   # look up the nearest ATK/DEF label for that comparison
            if lab: out.append(("stat", lab, s, e))   # remember it
        elif m.group("lv"):   # a Level
            lab = find_label("Level", m.group("lv"), OPS[m.group("lc")])   # look up "Level <op> n"
            lab2 = find_label("Level", m.group("lv2")) if m.group("lv2") else None   # "Level 3 or 4": look up the second Level too
            if lab and lab2: lab = "(" + lab + " || " + lab2 + ")"   # keep both Levels as one OR group
            if lab: out.append(("level", lab, s, e))   # remember it
        elif m.group("rk"):   # a Rank
            lab = find_label("Rank", m.group("rk"), OPS[m.group("rc")])   # look up "Rank <op> n"
            lv = find_label("Level", m.group("rk"), OPS[m.group("rc")]) if text[:s].endswith("Level/") else None   # "Level/Rank 3 or higher": the Level half too
            if lab and lv: lab = "(" + lv + " || " + lab + ")"   # one OR group, so "Level/Rank 4 Warrior" keeps Warrior on both halves
            if lab: out.append(("rank", lab, s, e))   # remember it
        elif m.group("lk"):   # a Link rating
            lab = find_label("Link Rating", m.group("lk"), OPS[m.group("kc")])   # look up "Link Rating <op> n"
            if lab: out.append(("link", lab, s, e))   # remember it
        else:   # everything left is a plain noun
            n = m.group("noun")   # the noun that matched
            if n == "Spell/Trap": labs = [find_label("Card Type", "Spell"), find_label("Card Type", "Trap")]   # "Spell/Trap" gives both Card Types
            elif n.lower().startswith("monster"): labs = [find_label("Card Type", "Monster")]   # "monster(s)" gives Card Type Monster
            elif n.startswith("Spell"): labs = [find_label("Card Type", "Spell")]   # Spell / Spell Cards gives Card Type Spell
            else: labs = [find_label("Card Type", "Trap")]   # anything else is a Trap
            out.append(("noun", labs, s, e))   # remember the noun; it is attached later to the trait words next to it
    return out   # return the list of findings

GAP_AND = re.compile(r"\s*(?:non-Token\s+)?")   # GAP_AND: text between two words that means "and" (only spaces)
GAP_OR = re.compile(r"[\s,]*(?:(?:or|and|and/or)\s+)?[\s,]*")   # GAP_OR: text between two words that means "or" (commas, "or", "and/or")

def extract_terms(text):   # extract_terms(): cached front door; the same text always gives the same answer, so work it out only once
    return list(_extract_terms(text))   # hand back a fresh list each time so callers can never change the cached copy

@functools.lru_cache(maxsize=None)   # remember the result for every text already seen
def _extract_terms(text):   # _extract_terms(): turn the trait words in a text into trait expressions (the real work)
    toks = tokens(text)   # find all the trait words
    groups, cur, prev = [], None, None   # groups holds finished groups, cur the group being built, prev the previous word
    for t in toks:   # walk through the words in order
        gap = text[prev[3]:t[2]] if prev else None   # the text between the previous word and this one
        if prev and GAP_AND.fullmatch(gap): cur.append(("and", t))   # only spaces between them: same group, joined by "and"
        elif prev and GAP_OR.fullmatch(gap) and gap.strip(): cur.append(("or", t))   # a comma/"or" between them: same group, joined by "or"
        else:   # otherwise this word starts a new group
            cur = [("start", t)]; groups.append(cur)   # start the new group and register it
        prev = t   # remember this word as the previous one
    terms = []   # terms will hold the finished expressions
    for g in groups:   # build one or more expressions per group
        alts, noun = [], None   # alts: the alternatives (separated by "or"); noun: the card-type noun of this group
        for link, (kind, lab, s, e) in g:   # walk through the words of the group with how each is linked
            if kind == "noun": noun = lab; continue   # a noun is not added to the list but remembered
            if link == "or" and alts and alts[-1] and alts[-1][-1][0] == kind and not kind.endswith("!=") and kind not in ("block", "stat"):   # "or" between two words of the same kind (e.g. two Attributes) means one slot with several values
                alts[-1][-1][1].append(lab)   # so add the value to the previous slot
            elif link == "or": alts.append([[kind, [lab]]])   # any other "or" starts a new alternative
            else:   # an "and" (or the first word) goes into the current alternative
                if not alts: alts.append([])   # make sure there is a current alternative
                alts[-1].append([kind, [lab]])   # add the word as a new slot
        if not alts: continue   # groups with no usable words give nothing
        for alt in alts:   # each alternative becomes one expression
            slots = ["(" + " || ".join(v) + ")" if len(v) > 1 else v[0] for _, v in alt]   # a slot with several values is written "(A || B)", with one value just "A"
            if noun: slots.append("(" + " || ".join(noun) + ")" if len(noun) > 1 else noun[0])   # add the noun as the last slot
            terms.append(" && ".join(slots))   # join the slots with &&
    return tuple(terms)   # return all expressions (a tuple, so the cached copy cannot be changed)

def combine(term_lists):   # combine(): merge several expression lists into one column string
    flat = list(dict.fromkeys(t for terms in term_lists for t in terms))   # flatten the lists and remove duplicates, keeping the order
    return " || ".join("(" + t + ")" if (" && " in t and len(flat) > 1) else t for t in flat)   # join with ||, putting brackets around && expressions when there is more than one

TARGET_RE = re.compile(r"\b(?:target(?:ing)?|select(?:ing)?)\b.*$", re.I)   # TARGET_RE: from the word "target" or "select" to the end of the text



# ================================================================
# PART 2: mechanic matchers (one block per group of mechanics)
# ================================================================


# ---- load the mechanic labels and which of the four columns each may go in ----
def _read(path):   # function that reads a CSV file into a list of row dicts
    raw = open(path, "rb").read()   # read the whole file as raw bytes so the encoding can be chosen below
    for enc in ("utf-8-sig", "cp1252"):   # try the encodings in this order (plain UTF-8 first, then Windows-1252)
        try: return list(csv.DictReader(io.StringIO(raw.decode(enc), newline="")))   # decode, parse the CSV and return the rows if the decoding worked
        except UnicodeDecodeError: pass   # wrong encoding -> ignore the error and try the next one
MECH = {}   # MECH will map a normalised label name -> (label text, which columns it may go in)
for r in _read(MECH_CSV):   # go through every row of the mechanics CSV
    lab = re.sub(r"\s+", " ", re.sub(r"\s*=\s*", " = ", r["Label"].strip()))   # tidy the label: single spaces, and exactly one space on each side of '='
    MECH[norm(lab)] = (lab, {k: r[k].strip().upper() == "TRUE" for k in   # store the label plus a dict of column flags (continues on the next line)
                             ("strictSupports", "generalSupports", "strictSupportedBy", "generalSupportedBy")})   # the four column names; a cell saying TRUE means that column is allowed

def mech_label(name, column):   # function that checks a label against the column flags
    # return the label text if it exists AND is allowed in this column, else None
    hit = MECH.get(norm(name))   # look up the label by its normalised name (no case, no spaces)
    return hit[0] if hit and hit[1][column] else None   # give back the label text only if it exists and this column is allowed, otherwise None

# ---- small helpers shared by all the matchers below ----
TRAIT_KEYS = ("Card Type", "Type =", "Attribute", "Archetype", "Trait", "Level", "ATK", "DEF")   # an expression holding one of these words is a real trait label
NOT_AN_ACTION = re.compile(r"(?:\bcannot|\bcan't|\bnor|\bnot|\bunless|\bif|\bwhen|\bafter|\bbefore|\bwas|\bwere|\bhave|\bhas|\bunable to|\bwould)\s+(?:you\s+|they\s+|the\s+|your opponent\s+)?(?:\w+\s+)?$", re.I)   # words right before a verb that make it a mention or a restriction ("cannot send", "if you banish"), not something the card does

def keep_allowed(labels, column):   # keep_allowed(): only the labels the Mechanics CSV allows in this column, each once, with the CSV's spelling
    out = []   # the labels that survive
    for l in dict.fromkeys(labels):   # each label once, in the order found
        base = l.split(" && ")[-1]   # the mechanic label is the last part ("Type = Fiend && Self = Tribute" -> "Self = Tribute")
        ok = mech_label(base, column)   # its CSV spelling if this column allows it, otherwise None
        if ok: out.append(l.replace(base, ok))   # allowed: keep it, with the CSV spelling swapped in
    return out   # the allowed labels

def cost_target(cost):   # cost_target(): the "target 1 ..." clause in a cost, or None when nothing (or only this card) is targeted
    t = TARGET_RE.search(cost)   # from the word "target" / "select" to the end of the cost
    if t and not re.match(r"(?:target|select)\s+(?:1\s+)?this card", t.group(0), re.I): return t.group(0)   # another card was targeted: a later "it" means that card
    return None   # nothing targeted, or only this card

def trait_terms_in(text):   # trait_terms_in(): the trait expressions found in a text, keeping only real trait labels (see TRAIT_KEYS)
    return [t for t in extract_terms(text) if any(k in t for k in TRAIT_KEYS)]   # e.g. ["Type = Fiend && Card Type = Monster"]

def with_label(terms, label):   # with_label(): join each trait expression to a mechanic label with &&
    return [("(" + t + ")" if " || " in t else t) + " && " + label for t in terms]   # an "or" expression gets brackets first: "(A || B) && label"

def need_exprs(obj, label):   # need_exprs(): the strictSupportedBy expressions for one mechanic: "traits && label", or the bare label when no traits are named
    return with_label(trait_terms_in(obj), label) or [label]   # "discard 1 WATER monster" -> ["Attribute = WATER && Card Type = Monster && Self = Discard"]

# ---- Special Summon ----
SS = re.compile(r"\bSpecial Summon\b(?!ed|ing)", re.I)               # "Special Summon", not "Special Summoned"
SKIP_BEFORE = re.compile(r"(?:\bthe|\bnot|\bno|\bnor)\s*$|(?:\bcannot|\bcan't|\bcan only|\bonly|\bneither player can|\b(?:that|the other) players? can|\bopponent (?:would|can|must|may))\b[^.;,]{0,40}$", re.I)   # words right before a match that mean it is a mention or a prohibition, not an action
LOC_WORD = r"(?:Extra Deck|Deck|hand|GY|Graveyard|banished|banishment|removed from play)"   # the place names that can follow 'from' / 'in' (Deck, hand, GY, banished ...)
LOC = re.compile(r"\b(?:from|in)\s+(?:your|either player's|either|the|your opponent's|their)\s+(?:side of the field,\s*)?(?P<locs>" + LOC_WORD +   # 'from/in <owner's> <place>': the named group 'locs' captures the place words
                 r"(?:(?:\s*,\s*(?:or\s+|and/or\s+)?|\s+(?:or|and/or|and)\s+)" + LOC_WORD + r")*)", re.I)   # ... plus any further places joined by commas / 'or' / 'and/or' / 'and'
TARGET_REF = re.compile(r"^\s*(?:it|them|that\b|those\b|the targeted|the selected|these|both\b|the other|all of (?:them|those))", re.I)   # object starts with 'it', 'them', 'that' ...: it points back at an earlier target
SELF_ONLY = re.compile(r"^\s*this (?:(?:face-up|equipped) )?card\b(?!\s*(?:\([^)]*\))?\s*(?:,\s*)?(?:and|also)\s+(?:\d|1|up to|that|it))", re.I)   # object starts with 'this card' (but not 'this card and 1 other ...'): self-only

def place_words(loc_text):   # place_words(): split a place phrase like "Deck, GY, or banishment" into single lower-case words
    parts = re.split(r"\s*,\s*|\s+or\s+|\s+and/or\s+|\s+and\s+|\s*,\s*or\s+", loc_text.lower())   # cut at commas / 'or' / 'and/or' / 'and'
    return [re.sub(r"^(?:or|and/or|and)\s+", "", w.strip()) for w in parts]   # drop an 'or' / 'and' left at the front of a word

def loc_labels(loc_text):   # turn a phrase like 'Deck or GY' into a list of Special Summon labels
    out = []   # collected labels
    for w in place_words(loc_text):   # each single place word ("deck", "gy", ...)
        name = {"hand": "Hand", "deck": "Deck", "extra deck": "Extra Deck", "gy": "GY", "graveyard": "GY",   # look the word up in the table of place names (table continues on the next line)
                "banished": "Banished", "banishment": "Banished", "removed from play": "Banished"}.get(w)   # (table continued) several spellings map to the same place name
        if name: out.append("Self = Special Summon (" + name + ")")   # if the word is a known place, add the matching label
    return out   # hand back the labels

TOKEN_WORD = re.compile(r"\bTokens?\b")   # matches the word Token or Tokens
BANISHED_WORD = re.compile(r"\b(?:banished|removed from play|banishment)\b", re.I)   # matches any spelling for 'banished'
LAST_TARGET = re.compile(r"\b(?:target|select)\b(?:(?!\bSpecial Summon\b).)*$", re.I)   # finds the last 'target' / 'select' in a text that has no 'Special Summon' after it

def special_summon_labels(text, target_source="", lead=""):   # function: all Special Summon labels in one piece of text (the cost's target text is the fallback)
    # all Special Summon labels found in one piece of text (may be several)
    found = []   # labels found so far
    for m in SS.finditer(text):   # look at every 'Special Summon' verb in the text
        if SKIP_BEFORE.search(text[max(0, m.start() - 40):m.start()]): continue       # prohibitions, nouns, opponent's summons
        obj = re.split(r"[.;]|\bSpecial Summon\b", text[m.end():m.end() + 160], maxsplit=1)[0]   # the clause after the verb
        if SELF_ONLY.match(obj): continue                                             # "Special Summon this card"
        if is_self_it(obj, lead + " " + text[:m.start()], target_source): continue                 # "Special Summon it" where "it" is this card itself: self-only, so it counts for nothing
        if TOKEN_WORD.search(obj):                                                     # a Token summon has its own label
            found.append("Self = Token"); continue   # Token summon: add its label and move on to the next match
        loc = LOC.search(obj)   # is a source place named in the clause?
        here = []   # labels for this one match
        if loc: here = loc_labels(loc.group("locs"))   # a place is named -> convert it to labels
        elif TARGET_REF.match(obj):                                                    # "Special Summon it": find the target's source
            tm = LAST_TARGET.search(text[:m.start()])                                  # a "target/select ..." earlier in this same text
            src = tm.group(0) if tm else target_source                                # otherwise the target clause in the cost
            lt = LOC.search(src) if src else None   # find a place inside the target text, if there is any target text
            here = loc_labels(lt.group("locs")) if lt else []   # turn that place into labels (nothing if none was found)
            if src and BANISHED_WORD.search(src) and "Self = Special Summon (Banished)" not in here: here.append("Self = Special Summon (Banished)")   # the target came from banishment but no label says so yet -> add the banished label
        if BANISHED_WORD.search(obj) and "Self = Special Summon (Banished)" not in here: here.append("Self = Special Summon (Banished)")   # the clause itself mentions banishment -> add the banished label once
        found += here or ["Self = Special Summon"]                                     # source not named -> generic
    return found   # hand back every label found

SELF_IT = re.compile(r"\b(?:add|send|banish|excavate|draw|target|select|reveal|discard|Tribute|return)\w*\b", re.I)   # a verb that handles ANOTHER card: after it, "it" may point at that card instead of this card
SELF_MENTION = re.compile(r"\bthis (?:(?:face-up|equipped) )?card\b|\bthis is\b", re.I)   # the words that name this card

def is_self_it(obj, before, target_source):   # is_self_it(): True when "Special Summon it" is about this card itself
    if not re.match(r"\s*it\b", obj, re.I): return False   # only the word "it" is handled here
    ms = list(SELF_MENTION.finditer(before))   # every mention of "this card" earlier in the same text
    if ms:   # "this card" is named before
        tail = before[ms[-1].end():]   # the words between that mention and "Special Summon it"
        if ms[-1].group(0).lower().startswith("this is"): return SELF_IT.search(tail) is None   # "If this is the only card in your hand, you can Special Summon it": the comma does not matter here
        return SELF_IT.search(tail) is None and not re.search(r"[;,.]", tail)   # self only when it is the same clause ("... add this card to your hand or Special Summon it"), with no other card handled in between
    return (not before.strip(" ,")) and bool(SELF_MENTION.search(target_source or "")) and not re.search(r"\b(?:target|select)\w*", target_source or "", re.I)   # first verb of the effect, and the cost is about this card ("reveal this card") without any target

def special_summon(pieces):   # function: Special Summon labels for all pieces of a card
    helps, needs = [], []   # labels for effects (helps) and for costs (needs)
    for cond, cost, effect in pieces:   # go through every (condition, cost, effect) piece
        t = TARGET_RE.search(cost)   # the target clause in the cost, if there is one
        lead = cond if re.match(r"\s*if this is\b", cond, re.I) else ""             # "If this is the only card in your hand, ..." names this card
        helps += special_summon_labels(effect, t.group(0) if t else cost, lead)       # effect side -> generalSupports
        needs += special_summon_labels(cost)                                           # cost side -> generalSupportedBy
    return [], keep_allowed(helps, "generalSupports"), [], keep_allowed(needs, "generalSupportedBy")   # the four columns (ss, gs, sb, gb): effect labels -> generalSupports, cost labels -> generalSupportedBy

# ---------- Add to hand (includes searching) ----------
ADD = re.compile(r"\badd\b(?!ed|ing)", re.I)                                            # the verb "add" (not "added")
TO_HAND = re.compile(r"\bto\s+(?:your|the|their|your opponent's)\s+hand\b", re.I)       # "... to your hand"
ADD_BACK_REF = re.compile(r"^\s*(?:to\s+(?:your|the|their)\s+hand|it|them|that\b|those\b|the targeted|the selected|these|(?:1|one|up to \d+) of (?:them|those|these)|both\b|the other)", re.I)   # object starts with 'to your hand', 'it', 'them', 'that' ... : the added card was named earlier
EXCAVATED = re.compile(r"\bexcavated\b|\b(?:top|bottom)\b[^.;]*\b(?:of|from) your Deck\b", re.I)  # digging cards off the top of the Deck

def add_labels_from(loc_text):   # function: turn a location phrase into 'add from ... to hand' labels
    out = []                                            # the labels found in a location phrase like "Deck, GY, or banishment"
    for w in place_words(loc_text):   # each single place word ("deck", "gy", ...)
        name = {"deck": "deck", "gy": "GY", "graveyard": "GY", "banished": "banished",   # table of place names (continues on the next line)
                "banishment": "banished", "removed from play": "banished"}.get(w)       # hand and Extra Deck have no add label
        if name: out.append("Self = add from " + name + " to hand")   # known place -> add the label for it
    return out   # hand back the labels

def add_to_hand_labels(text, target_source="", prev=""):   # function: all add-to-hand labels in one piece of text
    found = []   # labels found so far
    for m in ADD.finditer(text):                        # each "add" in the text
        if SKIP_BEFORE.search(text[max(0, m.start() - 40):m.start()]): continue      # "cannot add", "your opponent can add"
        obj = re.split(r"[.;]|\badd\b", text[m.end():m.end() + 200], maxsplit=1)[0]  # the clause after "add"
        if not TO_HAND.search(obj): continue            # "add 500 ATK", "add 1 counter": not adding to the hand
        if SELF_ONLY.match(obj): continue               # "add this card to your hand": self-only, ignored
        loc = LOC.search(re.split(r",\s*(?:then|also)\b", obj)[0])   # source named in the clause? (only before ", then ..." so a later "from your hand" is not mistaken for the source of "it")
        if loc:   # a source place is named in the clause
            found += add_labels_from(loc.group("locs"))   # turn the named place into labels
        else:   # no place named, so work it out from the context
            before = text[:m.start()]                   # everything earlier in this same text
            if EXCAVATED.search(obj) or EXCAVATED.search(before): # excavated cards come from the Deck
                found.append("Self = add from deck to hand")   # label: cards dug from the Deck
            elif BANISHED_WORD.search(obj):             # "add the monster banished by this effect"
                found.append("Self = add from banished to hand")   # label: cards added from banishment
            elif ADD_BACK_REF.match(obj):               # "add it / add 1 of them": the source is where the target was
                tm = LAST_TARGET.search(before)   # the last 'target/select' clause earlier in the text
                cands = [tm.group(0)] if tm else [before[-200:], target_source, prev]   # earlier text first, then the cost's target clause, then the previous sentence
                for src in cands:   # try each candidate text for a named place
                    if src and re.search(r"\b(?:banish|remove)\b(?!ed)", src, re.I) and not LAST_TARGET.search(src):   # "Banish 1 card from your Deck ... add that card": the card is now banished
                        found.append("Self = add from banished to hand"); break   # so it is added from banishment; stop looking
                    lt = LOC.search(src or "")   # search it for a place (empty text if the candidate is None)
                    if lt:                              # found a named source
                        found += add_labels_from(lt.group("locs")); break   # add the labels and stop looking at further candidates
                    if src and BANISHED_WORD.search(src) and "Self = add from banished to hand" not in found:   # the source was banished text and the label is not in the list yet
                        found.append("Self = add from banished to hand"); break   # add the banished label and stop
    return found   # hand back every label found

def add_to_hand(pieces):   # function: add-to-hand labels for all pieces of a card
    helps = []                                          # add-to-hand only ever appears as an effect (generalSupports)
    prev = ""   # effect text of the piece before (a one-sentence "Add it to your hand." points at the target named there)
    for cond, cost, effect in pieces:   # go through every (condition, cost, effect) piece
        t = TARGET_RE.search(cost)                      # a target clause in the cost, if any
        helps += add_to_hand_labels(effect, t.group(0) if t else cost, prev); prev = effect   # add the labels found in the effect (the cost's target is the fallback)
    return [], keep_allowed(helps, "generalSupports"), [], []   # the four columns (ss, gs, sb, gb): only generalSupports is ever filled


# ---------------- Normal Summon / Tribute Summon / Tribute ----------------
# Words right before the phrase that mean it is only being MENTIONED or forbidden, not done
NT_SKIP = re.compile(r"(?:\bcannot|\bcan't|\bcan not|\bnot|\bno|\bfor|\bthe|\bthis|\ban?|\byour|\bof|\bas|\bwhen|\bif|\bwas|\bis|\bbe|\bduring|\bfrom)\s+(?:\w+\s+)?$|\b(?:cannot|can't)\b[^.;]{0,50}$", re.I)   # words before the match that show it is only mentioned, forbidden or part of another sentence
NORMAL_SUMMON = re.compile(r"\bNormal Summon\b(?!ed|ing|/Set)(?!\s+(?:this card|it)\b)", re.I)      # the verb; "Normal Summon/Set" is a mention
TRIBUTE_SUMMON = re.compile(r"\bTribute Summon\b(?!ed|ing)", re.I)         # the verb "Tribute Summon"
TRIBUTE_COST = re.compile(r"(?<!\bTo )\bTribute\s+(?!Summon|Set\b|Burial|Tribute)(?!(?:(?!\bto\b|,\s*(?:then|and)\b)[^.;]){0,60}\b(?:your opponent controls|opponent's))", re.I)   # "Tribute this card", "Tribute 1 monster", "Tribute monsters you control ..." (not "Tribute Summon")

def nt_hits(regex, text):   # function: matches of a regex that are real actions
    # count matches of regex that are real actions (not mentions or restrictions)
    return [m for m in regex.finditer(text) if not NT_SKIP.search(text[:m.start()])]   # keep a match only if nothing in the text before it marks it as a mention

def normal_tribute(pieces):   # function: Normal Summon / Tribute Summon / Tribute labels for a card
    helps, needs = [], []                                           # labels found as effect (helps) / as cost (needs)
    for cond, cost, effect in pieces:   # go through every (condition, cost, effect) piece
        others = [m for m in TRIBUTE_COST.finditer(cost) if not THIS_ONLY.match(cost[m.end():])]   # Tributes of OTHER monsters ("Tribute this card" pays with itself: never a need)
        if others and not TRIBUTE_SUMMON.search(cost):   # a Tribute in the cost that is not a Tribute Summon
            needs.append("Self = Tribute")                          # Tribute paid as a cost -> the deck must be able to do it
        if nt_hits(NORMAL_SUMMON, effect):   # a real 'Normal Summon' in the effect
            helps.append("Self = Normal Summon")                    # effect that gives a Normal Summon
        elif re.search(r"\b(?:select|target)\b[^.;]*?\bmonster\b[^.;]*?\band Normal Summon (?:it|that monster)\b", effect, re.I):   # "Select 1 Gemini monster you control and Normal Summon it": "it" is the selected monster, not this card
            helps.append("Self = Normal Summon")                    # that is a Normal Summon given to another monster
        if nt_hits(TRIBUTE_SUMMON, effect):   # a real 'Tribute Summon' in the effect
            helps.append("Self = Tribute Summon")                   # effect that gives a Tribute Summon
    helps, needs = list(dict.fromkeys(helps)), list(dict.fromkeys(needs))   # remove duplicates but keep the order
    strict = [l for l in (mech_label(h, "strictSupports") for h in helps) if l and not mech_label(l, "generalSupports")]  # only if the label is NOT allowed in the general column
    general = keep_allowed(helps, "generalSupports")   # otherwise the general column
    need_general = keep_allowed(needs, "generalSupportedBy")   # the cost labels that are allowed in generalSupportedBy
    return strict, general, [], need_general   # the four columns (ss, gs, sb, gb); strictSupportedBy is filled by tribute_needs()


# ---------------- Discard ----------------

DISCARD = re.compile(r"\bdiscards?\b", re.I)                         # the verb: discard / discards (not "discarded")
OPP_WORDS = re.compile(r"\b(?:opponent's|their|both players|each player|they drew)\b", re.I)    # the opponent's cards are involved
BOTH_PLAYERS = re.compile(r"\b(?:both players|each player|either player)\b", re.I)   # both sides discard
LEAD_ONLY = re.compile(r"^\s*(?:[^,.;]*,\s*)?(?:you (?:can|may)\s*)?$", re.I)   # only lead words ("Once per turn, you can") before the verb
PURPOSE_AFTER = re.compile(r"\bdiscards? (?:this card|it)\b[^,;.]{0,30}\bto (?:target|select|activate|add|Special|negate|destroy)", re.I)   # "discard this card to select ..."
SELF_CARD = re.compile(r"^\s*(?:this card|it\b|itself)", re.I)       # the clause is only about this card itself

def discard_clauses(text):   # function: every real 'discard' with its object clause and the text before it
    out = []                                                         # (clause text, preceding text) for every real "discard"
    for m in DISCARD.finditer(text):   # look at every 'discard' / 'discards'
        before = text[:m.start()]                                    # everything before the verb
        if NOT_AN_ACTION.search(before):                              # mention / restriction -> not an action
            continue   # skip it
        clause = re.split(r"[;,.]|\bthen\b|\band if you do\b|\band (?:Tribute|banish|send|pay|detach|discard|shuffle|return|reveal|remove)\b|\bto (?:target|add|Special|activate|negate|destroy|banish|draw|return)\b", text[m.end():])[0]   # object of the verb, up to the next break
        out.append((clause, before))   # remember this clause and what came before it
    return out   # hand back all of them

def discard(pieces):   # function: Discard labels for a card (strict needs, general helps, general needs)
    need_strict, helps, need_general = [], [], []                    # strictSupportedBy exprs, generalSupports, generalSupportedBy
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        lead = cond if re.match(r"\s*(?:once per turn,?\s*)?you (?:can|may) discard", cond, re.I) else ""   # 'You can discard X: effect' keeps its cost in front of the colon
        for clause, before in discard_clauses(cost + " " + lead):  # ----- cost side: the deck must be able to discard
            if OPP_WORDS.search(clause) and not re.search(r"\byour hand\b", clause, re.I):   # the opponent discards in a cost, and it is not 'your hand'
                need_general.append("Opponent = Discard")            # a cost that makes the opponent discard (very rare)
                continue   # nothing more to do for this clause
            if SELF_CARD.search(clause): continue                    # "discard this card" as the cost: it pays with itself - never a need
            need_strict += need_exprs(clause, "Self = Discard")   # what kind of card is discarded ("WATER monster" ...) && Self = Discard, or the bare label for "discard 1 card"
        for clause, before in discard_clauses(effect):               # ----- effect side: the card makes someone discard
            both = BOTH_PLAYERS.search(before[-25:]) or BOTH_PLAYERS.search(clause)   # "Both players discard ..."
            if both:   # both players discard
                helps += ["Opponent = Discard", "Self = Discard (effect)"]  # both players discard -> counts for both labels
            elif OPP_WORDS.search(clause) or re.search(r"\byour opponent\s*$", before, re.I):   # the opponent discards (named, or 'your opponent' right before the verb)
                helps.append("Opponent = Discard")                   # "from their hand" / "your opponent discards"
            elif SELF_CARD.search(clause):   # the clause is about this card itself ("You can discard this card, then ...")
                pass                                                 # it pays with itself, or discards itself: never a need, never support
            else:   # anything else: we discard as part of the effect
                helps.append("Self = Discard (effect)")              # you discard as part of the effect
    return [], keep_allowed(helps, "generalSupports"), keep_allowed(need_strict, "strictSupportedBy"), keep_allowed(need_general, "generalSupportedBy")   # the four columns (ss, gs, sb, gb), each filtered by what it allows


# ---------------- Send to GY ----------------
# "send <object> to the GY": group "obj" is everything between the verb and the GY
SEND = re.compile(r"\bsends?\b(?P<obj>[^;.]{0,200}?)\bto (?:the|your|their|your opponent's|both|each) (?:GYs?|Graveyards?)\b", re.I)   # matches 'send <object> to the GY'; the group 'obj' is the text between the verb and the GY
OPP_OBJ = re.compile(r"\b(?:your opponent controls|your opponent's|opponent's|their|they control)\b", re.I)    # the opponent's cards are sent
TOP_DECK = re.compile(r"\btop\b[^.;]*\bDeck\b", re.I)                # "the top 3 cards of your Deck" -> Self-Mill
LOC_FIELD = re.compile(r"\b(?:field|Monster Zones?|Spell & Trap Zones?|Pendulum Zones?|you control|equipped|on the field)\b", re.I)  # the card is on the field
LOC_HAND = re.compile(r"\bhand\b", re.I)                             # from the hand
LOC_EXTRA = re.compile(r"\bExtra Deck\b", re.I)                      # from the Extra Deck
LOC_DECK = re.compile(r"\b(?<!Extra )Deck\b", re.I)                  # from the (main) Deck, not the Extra Deck
BACK_REF = re.compile(r"^\s*(?:it\b|them\b|that\b(?!\s+(?:opponent's|player's))|those\b|the (?:targeted|selected))", re.I)    # points back at an earlier target
SELF_OBJ = re.compile(r"^\s*(?:this (?:face-up )?card|it\b|them\b|that\b(?!\s+(?:opponent's|player's))|those\b|the (?:targeted|selected))", re.I)  # object is this card or a back-reference

def self_cost(obj, before):   # self_cost(): True when a cost only uses up this card itself ("send this card to the GY", "banish it" in a GY effect)
    if THIS_ONLY.match(obj): return True   # "this card" (but not "this card and 1 other monster")
    return bool(re.match(r"\s*it\b", obj, re.I)) and not TARGET_RE.search(before)   # a bare "it" with no "target / select" before it in the cost is this card

def send_places(obj):   # function: where the sent cards come from, as short keys
    # return the places the cards are sent from, as short keys
    if TOP_DECK.search(obj): return ["mill"]                         # milling the top of the Deck
    places = []   # keys found
    if LOC_HAND.search(obj): places.append("Hand")   # from the hand
    if LOC_EXTRA.search(obj): places.append("Extra Deck")   # from the Extra Deck
    if LOC_DECK.search(obj): places.append("Deck")   # from the Deck
    if LOC_FIELD.search(obj): places.append("field")   # from the field
    return places or [""]                                            # "" = location not named

def send_label(place, effect):   # function: place key -> label text
    # turn a place key into the label text, picking the "(Effect)" variant when it is an effect
    if place == "mill": return "Self = Self-Mill"   # Deck milling has its own label
    if place == "": return "Self = Send to GY"                       # generic label
    base = "Self = Send to GY (" + place + ")"   # the base label for this place
    return (base + " (Effect)") if effect and mech_label(base + " (Effect)", "generalSupports") else base   # use the '(Effect)' variant for effects when that label exists

def send_to_gy(pieces):   # function: Send to GY labels for a card
    need_strict, helps, need_general = [], [], []                    # strictSupportedBy exprs, generalSupports, generalSupportedBy
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        for side, text in (("cost", cost), ("effect", effect)):      # look at both halves of the piece
            for m in SEND.finditer(text):                            # every "send ... to the GY"
                if NOT_AN_ACTION.search(text[:m.start()]):               # mention / restriction -> not an action
                    continue   # skip it
                if re.match(r"\s*,?\s*instead\b", text[m.end():], re.I):   # "... to the GY instead" is a replacement effect
                    continue   # skip it
                obj = m.group("obj")                                 # what is being sent, and from where
                before = text[max(0, m.start() - 25):m.start()]      # a few words before the verb ("Both players")
                if side == "effect" and BACK_REF.match(obj):         # "send it / them to the GY" -> look at what the cost targeted
                    t = cost_target(cost)   # what the cost targeted (None: nothing, or only this card)
                    if t: obj = t   # use the target as the object
                    elif not re.search(r"\byou control\b|\bexcavate", obj + " " + text[:m.start()], re.I) \
                         and (opp_backref(cond, text[:m.start()]) or SUBJ_OPP.search(text[:m.start()])):   # not our own cards, and the opponent sends it / it is a battled or summoned opponent's monster
                        obj = "your opponent controls"   # "your opponent must send that monster to the GY"
                    else: continue                                   # nothing to point at -> ignore
                elif SELF_OBJ.match(obj) and side == "effect":       # self-only effects ("this card") don't count as support
                    continue   # skip it
                elif side == "cost" and self_cost(obj, text[:m.start()]):   # "send / banish this card as the cost": it pays with itself,
                    continue   # so it needs nothing from the rest of the deck
                if BOTH_PLAYERS.search(before): owners = ["opp", "self"]   # both players send
                elif OPP_OBJ.search(obj): owners = ["opp"]           # the opponent's cards
                else: owners = ["self"]                              # our own cards
                for owner in owners:   # one label per owner (opponent / us)
                    if owner == "opp":   # the opponent's cards
                        if TOP_DECK.search(obj): continue                    # milling the opponent is Opponent = Mill (batch C)
                        (helps if side == "effect" else need_general).append("Opponent = Send to GY")   # cost side goes to generalSupportedBy, effect side to generalSupports
                        continue   # next owner
                    for place in send_places(obj):                   # one label per place named
                        if side == "effect":   # effect side
                            helps.append(send_label(place, True))    # effect -> generalSupports
                        else:   # cost side
                            need_strict += need_exprs(obj, send_label(place, False))   # the sent card's traits && the label for this place (or the bare label for "send 1 card")
    return [], keep_allowed(helps, "generalSupports"), keep_allowed(need_strict, "strictSupportedBy"), keep_allowed(need_general, "generalSupportedBy")   # the four columns (ss, gs, sb, gb), each filtered by what it allows


# ---------------- Banish ----------------
BANISH = re.compile(r"\bbanish(?:es)?\b(?!ed|ing|ment)", re.I)       # the verb banish (not banished / banishing)
LOC_GY = re.compile(r"\b(?:GYs?|Graveyards?)\b", re.I)               # from the GY
OWN_PLACE = re.compile(r"\byour (?:GY|Graveyard|hand|Deck|field|Extra Deck)\b|\byou control\b|\bon your side of the field\b", re.I)   # our own cards are named too
FACE_DOWN = re.compile(r"\bface-down\b", re.I)                       # banished face-down

def banish_places(obj):   # function: where the banished cards come from, as short keys
    # return the places the cards are banished from, as short keys (matching the label names)
    places = []   # keys found
    if LOC_GY.search(obj): places.append("GY")   # from the GY
    if LOC_HAND.search(obj): places.append("Hand")   # from the hand
    if LOC_DECK.search(obj): places.append("Deck")   # from the Deck
    if LOC_FIELD.search(obj): places.append("Field")   # from the field
    return places or [""]                                            # "" = location not named

def banish_label(place, effect, face_down):   # function: build a Banish label
    # build the label text: base name + (place) + (Effect), choosing the face-down family when needed
    name = "Self = Banish Face-Down" if face_down else "Self = Banish"   # face-down banish has its own label family
    if place: name += " (" + place + ")"   # add the place in brackets
    if effect and mech_label(name + " (Effect)", "strictSupports"): name += " (Effect)"    # effect variant when it exists
    return name   # hand back the label

def summoned_target(pieces, this_piece):   # summoned_target(): True when an EARLIER piece with the same target (same cost text) already Special Summons something ("Special Summon it ... banish that monster later")
    cost = this_piece[1]   # the (carried) target text of this piece
    if not cost: return False   # no target text to compare
    earlier = pieces[:pieces.index(this_piece)] if this_piece in pieces else []   # only the pieces that come before this one
    return any(cost in k0 and re.search(r"\bSpecial Summon\b", e0, re.I) for c0, k0, e0 in earlier)   # same cost target, and that earlier piece Special Summons something

def banish(pieces):   # function: Banish labels for a card
    need_strict, helps_strict, helps_general, need_general = [], [], [], []   # strictSupportedBy exprs, strictSupports, generalSupports, generalSupportedBy
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        for side, text in (("cost", cost), ("effect", effect)):      # look at both halves
            for m in BANISH.finditer(text):                          # every real "banish"
                if NOT_AN_ACTION.search(text[:m.start()]): continue    # mention / restriction -> not an action
                # object of the verb: up to the next break or purpose phrase
                obj = re.split(r"[;.]|,(?!\s*(?:(?:or|and|and/or)\s+)?[A-Z])(?!\s*(?:or|and|and/or)\s+(?:face-up |face-down )?(?:field|GY|Graveyard|hand|Deck)\b)|\bthen\b|\band if you do\b|\band (?:Tribute|banish|send|pay|detach|discard|shuffle|return|reveal|remove)\b|\bto (?:target|add|Special|activate|negate|destroy|draw|return|send)\b", text[m.end():])[0]   # the object of the verb: cut at the next break or purpose phrase (a comma followed by a capitalised name, as in "Fish, Sea Serpent, or Aqua", does not cut)
                if re.search(r"\binstead\b", text[m.end():m.end() + 80].split(".")[0], re.I): continue   # "... instead" replacement effects
                before = text[max(0, m.start() - 25):m.start()]      # a few words before the verb ("Both players")
                if side == "effect" and BACK_REF.match(obj):         # "banish it / them" -> look at what the cost targeted
                    t = cost_target(cost)   # what the cost targeted (None: nothing, or only this card)
                    if t: obj = t   # use the target as the object
                    elif opp_backref(cond, text[:m.start()]) or re.search(r"\bdestroy\b[^.;]*\byour opponent controls\b", text[:m.start()], re.I): obj = "your opponent controls"   # "negate the activation, and if you do, banish that card" / "destroy ... your opponent controls, and banish them": the opponent's card
                    else: continue                                   # nothing to point at -> ignore
                    if OPP_OBJ.search(obj) and summoned_target(pieces, (cond, cost, effect)): continue   # "Special Summon it ... when this card leaves the field, banish that monster": the monster is now ours, so this is a tie, not support
                elif SELF_OBJ.match(obj) and side == "effect":       # self-only effects ("this card") don't count as support
                    continue   # skip it
                elif side == "cost" and self_cost(obj, text[:m.start()]):   # "send / banish this card as the cost": it pays with itself,
                    continue   # so it needs nothing from the rest of the deck
                if BOTH_PLAYERS.search(before): owners = ["opp", "self"]   # both players banish
                elif OPP_OBJ.search(obj): owners = ["opp", "self"] if OWN_PLACE.search(obj) else ["opp"]   # opponent's cards (maybe ours too)
                elif LOC_FIELD.search(obj) and not OWN_PLACE.search(obj) and not re.search(r"\bequipped\b", obj, re.I): owners = ["opp"]   # "banish 1 monster on the field" -> the opponent's
                else: owners = ["self"]                              # our own cards
                face_down = bool(FACE_DOWN.search(obj + " " + text[m.end():m.end() + 40].split(",")[0]))   # "face-down" right after the object counts too
                for owner in owners:                                 # one label per owner
                    if owner == "opp":   # the opponent's cards
                        (helps_general if side == "effect" else need_general).append("Opponent = Banish")   # effect -> generalSupports, cost -> generalSupportedBy
                        continue   # next owner
                    for place in banish_places(obj):                 # one label per place named
                        if side == "effect":   # effect side
                            helps_strict.append(banish_label(place, True, face_down))   # effect -> strictSupports (bare label)
                        else:   # cost side
                            need_strict += need_exprs(obj, banish_label(place, False, face_down))   # the banished card's traits && the cost label (or the bare label for "banish 1 card")
    # keep each label only if its flags allow that column (and use the CSV's exact spelling)
    need_strict = keep_allowed(need_strict, "strictSupportedBy")   # needs whose mechanic label is allowed in strictSupportedBy
    helps_strict = keep_allowed(helps_strict, "strictSupports")   # effect labels allowed in strictSupports
    helps_general = keep_allowed(helps_general, "generalSupports")   # effect labels allowed in generalSupports
    need_general = keep_allowed(need_general, "generalSupportedBy")   # cost labels allowed in generalSupportedBy
    return helps_strict, helps_general, need_strict, need_general   # the four columns (ss, gs, sb, gb)


# ---------------- Batch A part 1: Pay LP, Detach, Attach, Counters, Equip ----------------
OPP_NEAR = re.compile(r"\b(?:your opponent|opponent's|opponent)\b[^.;]{0,25}$", re.I)   # the verb belongs to the opponent
NEG_NEAR = re.compile(r"\b(?:cannot|can't|can not|do not|does not|not)\s+(?:\w+\s+)?$", re.I)   # "cannot pay", "do not remove" ...
PAY_LP = re.compile(r"\bpay\b\s+(?:\d[\d,]*|half|all|1000|your)?[^.;,]{0,25}?\b(?:LP|Life Points)\b", re.I)   # "pay 800 LP", "pay half your LP"
DETACH = re.compile(r"\bdetach\b(?!ed|ing)(?P<rest>[^.;,]{0,70})", re.I)   # "detach 1 material from this card"
OWN_DETACH = re.compile(r"\bfrom\s+(?:this card|it|itself|(?:an? |your |any )?[^.;,]{0,30}you control)\b|\bfrom\s+(?:your|an?)\s+[^.;,]{0,30}Xyz Monster\b(?!\s+(?:your )?opponent)|\bfrom\s+an?\s+(?:Xyz\s+)?(?:Monster|monster)\s+on\s+(?:either|the)\s+field\b|^(?:(?!\bfrom\b).)*$", re.I)   # only our own Xyz (or no "from" at all)
ATTACH = re.compile(r"\battach\b(?!ed|ing)", re.I)                    # "attach this card to it as material"
PLACE_CTR = re.compile(r"\b(?:place|put)\b[^.;]{0,40}?\bCounters?\b", re.I)   # "place 1 Spell Counter on it"
CTR_TARGET = re.compile(r"\b(?:that|which|those|with)\s+(?:you|they)\s+(?:can|could)\s*$", re.I)   # "... that you can place a counter on" is only a target description
CTR_ON_SELF = re.compile(r"^\s*on (?:this (?:face-up )?card|itself)\b", re.I)   # "... Counter on this card": the counters only go on this card (self-only)
REMOVE_CTR = re.compile(r"\bremove\b[^.;]{0,40}?\bCounters?\b", re.I)  # "remove 3 Fog Counters"
EQUIP_VERB = re.compile(r"\b[Ee]quip\b(?!ped|ping)(?!\s+(?:Spells?|Cards?|Traps?|only\b))")   # the verb (not the noun "Equip Spell")
EQUIP_ONLY = re.compile(r"\bEquip only to\b(?P<obj>[^.;]*)", re.I)    # "Equip only to a Warrior monster" -> a requirement

def resources(pieces):   # mechanics about resources: Pay LP, Detach, counters, Attach, Equip
    ss, gs, sb, gb = [], [], [], []                                  # strictSupports, generalSupports, strictSupportedBy, generalSupportedBy
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        both = cost + " ; " + effect                                 # for verbs that are needs wherever they appear
        for m in PAY_LP.finditer(both):                              # ---- Pay LP: always a need
            if not (NEG_NEAR.search(both[:m.start()]) or OPP_NEAR.search(both[:m.start()])): gb.append("Self = Pay LP")   # count it only if no 'cannot'-type word or opponent word sits before the verb (then the payer is us)
        for m in DETACH.finditer(both):                              # ---- Detach: always a need, but only from our own Xyz
            rest = m.group("rest")                                   # "1 material from this card", "1 material from a monster you control" ...
            if re.search(r"\bfrom\s+(?:this (?:face-up )?card|itself)\b", rest, re.I) or not re.search(r"\bfrom\b", rest, re.I): continue   # from this card itself (no "from" = this card too): it pays with itself - never a need
            if not OPP_NEAR.search(both[:m.start()]) and OWN_DETACH.search(rest): gb.append("Self = Detach Xyz Material")   # count it only if the opponent is not named before it and the rest names another of our own Xyz Monsters
        for m in REMOVE_CTR.finditer(both):                          # ---- Remove counters: a need (our own counters, on another card)
            if re.match(r"\s*from (?:this (?:face-up )?card|itself)\b", both[m.end():], re.I): continue   # "remove 2 counters from this card": it pays with itself - never a need
            pre = re.sub(r"\b(?:your and )?your opponent's (?:Standby|End|Main|Battle|Draw) Phases?\b", "", both[:m.start()], flags=re.I)   # ("During each of your and your opponent's Standby Phases" only names a time)
            if not (NEG_NEAR.search(pre) or OPP_NEAR.search(pre) or re.search(r"\bopponent'?s?\b", m.group(0), re.I)): gb.append("Self = Remove counters")   # skip negated, opponent-owned or 'opponent's counters' cases, otherwise it is a need
        for m in ATTACH.finditer(effect):                            # ---- Attach: effect gives strictSupports
            ss.append("Self = Attach Xyz Material")   # each Attach in the effect supports Xyz-material effects
        for m in PLACE_CTR.finditer(effect):                         # ---- Place counters in an effect
            if CTR_ON_SELF.match(effect[m.end():]): continue          # "place 1 Spell Counter on this card": only this card gets them - helps nothing else
            if not CTR_TARGET.search(effect[:m.start()]): ss.append("Self = Place counters")   # count it unless the counters go onto a targeted card (CTR_TARGET)
        for m in PLACE_CTR.finditer(cost):                           # ---- Place counters as a cost: a need
            if not CTR_TARGET.search(cost[:m.start()]): gb.append("Self = Place counters")   # same check, but as a cost the counters are a need
        label = "Self = Union Equip" if re.search(r"\bUnion\b", cond + cost + effect) else "Self = Equip"   # Union monsters have their own label
        not_itself = lambda t: [x for x in EQUIP_VERB.finditer(t) if not re.match(r"\s*this (?:face-up )?card\b", t[x.end():], re.I)]   # "equip this card to ..." = this card equips itself: not counted
        if not_itself(effect): ss.append(label)                      # ---- Equip in an effect: support
        if not_itself(cost): gb.append(label)                        # ---- Equip as a cost: a need
        # ("Equip only to X" is NOT an Equip need: the Spell equips itself. Its X is a plain trait need, added in trait_terms())
    return keep_allowed(ss, "strictSupports"), keep_allowed(gs, "generalSupports"), keep_allowed(sb, "strictSupportedBy"), keep_allowed(gb, "generalSupportedBy")   # return the four columns in the standard order (ss, gs, sb, gb), each filtered by what the column allows


# ---------------- Batch A part 2: Reveal, Excavate, Shuffle, Top/Bottom of Deck, Extra Deck, Set ----------------

def resolve_obj(side, obj, cost):   # turn the matched object text into the text to analyse, or None to ignore it
    # for effects, "it / them / that" points at the cost's target; "this card" is self-only. Returns the object text, or None to ignore.
    if side != "effect": return None if THIS_ONLY.match(obj) else obj   # costs count as written - except "this card" itself, which pays with itself (never a need)
    if BACK_REF.match(obj):                                          # "it", "them", "that monster" ...
        return cost_target(cost)   # what the cost targeted (None: nothing to point at, so it is ignored)
    return None if SELF_OBJ.match(obj) else obj                      # self-only effects don't count

REVEAL = re.compile(r"\breveal\b(?!ed|ing)(?P<obj>[^;.]{0,100})", re.I)          # "reveal 1 Ritual Spell in your hand"
EXCAVATE = re.compile(r"\bexcavate\b(?!d)", re.I)                                  # "excavate the top card of your Deck"
SHUFFLE = re.compile(r"\bshuffles?\b(?P<obj>[^;.]{0,120}?)\binto\b(?P<dest>[^;.]{0,20}?)\bDeck\b", re.I)   # "shuffle X into the Deck"
TOP_BOTTOM = re.compile(r"\b(?:place|return|put|add)\b(?P<obj>[^;.]{0,80}?)\b(?:on|at|to) (?:either )?(?:the )?(?P<w>top(?: or bottom)?|bottom) of (?:(?:your|the|their|either|that|each|its owner's)\s+)?(?:\w+\s+)?Deck\b", re.I)  # "place X on the bottom of the Deck"
RETURN_DECK = re.compile(r"\breturn\b(?P<obj>[^;.]{0,80}?)\bto (?:your |the |their |his/her |its owner's )?(?:\w+ )?Deck\b(?!\s*(?:and|or) the (?:top|bottom))(?<!Extra Deck)", re.I)   # RETURN_DECK: "return X to your Deck" (not the Extra Deck, not top/bottom)
RET_EXTRA = re.compile(r"\breturn\b(?P<obj>[^;.]{0,80}?)\bto the Extra Deck\b", re.I)    # "return it to the Extra Deck"
SET_VERB = re.compile(r"\b[Ss]et\b\s+(?P<obj>(?:up to\s+)?(?:\d+|a|an|it\b|them\b|that\b|this card)[^;.,]{0,70})(?!\s*(?:ATK|DEF|Level))")   # "Set 1 Spell/Trap from your Deck"
SET_NOT = re.compile(r"\b(?:ATK|DEF|Level|Rank|Attribute|Type|Link Rating)\b|\bwas Set\b|\bis Set\b|\bNormal Summon/Set\b|\bSet Monster\b|\bSet Cards?\b", re.I)   # not about Setting a card

def opp_backref(cond, before):   # True when "it / that card / that monster" most likely points at the OPPONENT's card (negated, battled, or summoned / activated by the opponent)
    if SS.search(re.sub(r"\bnegate the (?:\w+ )?Summon\b", "", before, flags=re.I)): return False   # it may be our own summoned card (but "negate the Special Summon" is not our summon)
    if NEGATED_BEFORE.search(before): return True   # "negate the activation, and if you do, <do something to> that card"
    if re.search(r"\btargets?\b[^:;]*\byou control\b", cond, re.I): return False   # it may be our own summoned / targeted card
    return bool(BATTLE_FOE.search(cond) or OPP_EVENT.search(cond))   # the triggering event is the opponent's

def resources2(pieces):   # find Reveal, Excavate, Shuffle, Top/Bottom of Deck, Return to Extra Deck and Set
    ss, gs, sb, gb = [], [], [], []                                  # strictSupports, generalSupports, strictSupportedBy, generalSupportedBy
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        for side, text in (("cost", cost), ("effect", effect)):      # look at both halves
            for m in REVEAL.finditer(text):                          # ---- Reveal
                obj = m.group("obj")   # the text after the verb 'reveal'
                if THIS_ONLY.match(obj): continue                    # "reveal this card": it pays with itself - never a need, never support
                if re.search(r"\bhand\b", obj, re.I) or side == "cost": sb += need_exprs(obj, "Self = Reveal")   # revealing from the hand / as a cost -> a need
                elif not OPP_OBJ.search(obj) and not SUBJ_OPP.search(text[:m.start()]): gs.append("Self = Reveal")   # unless "They / your opponent reveal" (then the opponent reveals):                                           # otherwise an effect
            if side == "effect" and EXCAVATE.search(text): gs.append("Self = Excavate")                           # ---- Excavate (effect only)
            for m in SHUFFLE.finditer(text):                         # ---- Shuffle into the Deck
                obj = resolve_obj(side, m.group("obj"), cost)   # decide what is being shuffled (None = self-only back-reference)
                if obj is None and side == "effect" and BACK_REF.match(m.group("obj")) and opp_backref(cond, text[:m.start()]): obj = "your opponent controls"   # "negate the Summon, and if you do, shuffle that monster into the Deck": it is the opponent's
                if obj is None or re.search(r"\bExtra\b", m.group("dest"), re.I): continue                       # self-only / Extra Deck -> ignore
                if SUBJ_OPP.search(text[:m.start()]) or re.search(r"\btheir\b", m.group("dest"), re.I):           # "They must shuffle 1 of those cards into their Deck": the opponent does it
                    (gs if side == "effect" else gb).append("Opponent = Shuffle into deck"); continue   # opponent label, then next match
                if OPP_OBJ.search(obj):                              # the opponent's cards
                    (gs if side == "effect" else gb).append("Opponent = Shuffle into deck"); continue   # in an effect it supports, in a cost it is a need; then go to the next match
                place = "GY" if re.search(r"\b(?:GY|Graveyard)\b", obj, re.I) else "Hand" if re.search(r"\bhand\b", obj, re.I) else "Field" if LOC_FIELD.search(obj) else ""   # work out where the shuffled cards come from: GY, hand, field, or unknown
                label = "Self = Shuffle into deck" + (" (From " + place + ")" if place else "")                  # the label for this place
                if side == "effect": (gs if place == "GY" else ss).append(label)                                 # GY -> generalSupports, others -> strictSupports
                else: sb += need_exprs(obj, label)                                                               # a cost -> a need
            for m in RETURN_DECK.finditer(text):                     # ---- "Return 2 cards from your hand to your Deck" (a shuffle in disguise)
                if NOT_AN_ACTION.search(text[:m.start()]): continue      # mention / restriction -> not an action
                obj = resolve_obj(side, m.group("obj"), cost)   # decide what is returned (None = self-only back-reference)
                if obj is None and side == "effect" and BACK_REF.match(m.group("obj")) and opp_backref(cond, text[:m.start()]): obj = "your opponent controls"   # the returned card is the opponent's
                if obj is None: continue                             # self-only -> ignore
                if OPP_OBJ.search(obj) or re.search(r"\bhis/her\b|\bopponent's\b", obj, re.I):   # the opponent's cards
                    (gs if side == "effect" else gb).append("Opponent = Shuffle into deck"); continue   # opponent label, then next match
                place = "GY" if re.search(r"\b(?:GY|Graveyard)\b", obj, re.I) else "Hand" if re.search(r"\bhand\b", obj, re.I) else "Field" if LOC_FIELD.search(obj) else ""   # where the cards come from
                if not place: continue                               # unknown source (e.g. "return it to the Deck"): do not guess
                label = "Self = Shuffle into deck (From " + place + ")"   # same label the shuffle rule uses
                if side == "effect": (gs if place == "GY" else ss).append(label)   # GY -> generalSupports, others -> strictSupports
                else: sb += need_exprs(obj, label)                   # a cost -> a need
            for m in TOP_BOTTOM.finditer(text):                      # ---- Place on top / bottom of the Deck
                obj = resolve_obj(side, m.group("obj"), cost)   # decide what is being placed (None = self-only back-reference)
                if obj is None or re.search(r"\bopponent", text[m.end():m.end() + 30] + text[max(0, m.start() - 15):m.start()], re.I): continue   # ignore self-only results and any placement that mentions the opponent right next to the match
                labels = ["Self = Place on " + w + " of Deck" for w in (("top", "bottom") if "or" in m.group("w").lower() else (m.group("w").lower(),))]   # one label for top or bottom, both labels for "either the top or bottom"
                for label in labels:   # handle each label
                    if side == "effect": ss.append(label)   # in an effect this is a strict support
                    else: sb += need_exprs(obj, label)   # in a cost it becomes a need combined with the object's traits
            for m in RET_EXTRA.finditer(text):                       # ---- Return to the Extra Deck
                obj = resolve_obj(side, m.group("obj"), cost)   # decide what is being returned (None = self-only)
                if obj is None or (OPP_OBJ.search(obj) and not re.search(r"\byour (?!opponent)", obj, re.I)): continue   # nothing usable, or ONLY the opponent's cards ("return it to the Extra Deck" after targeting their monster): skip ("1 of your banished ..." is ours)
                if side == "effect": gs.append("Self = Return to Extra Deck")   # an effect that returns cards to the Extra Deck supports others
                else: sb += need_exprs(obj, "Self = Return to Extra Deck")   # a cost that does it is a need, combined with the object's traits
            if side == "effect":                                     # ---- Set (effects only)
                for m in SET_VERB.finditer(text):   # look at every 'Set ...' match
                    obj = m.group("obj")   # the thing being Set
                    if SET_NOT.search(obj): continue                 # "Set ... ATK" etc. is not Setting a card
                    obj = resolve_obj("effect", obj, cost)   # resolve 'it / them' and drop self-only objects
                    if obj is None: continue                         # self-only / nothing to point at
                    if re.search(r"\bfrom your Deck\b", obj, re.I): gs.append("Self = Set from Deck")   # Setting straight from the Deck
                    elif re.search(r"\b(?:Spell|Trap)\b", obj): gs.append("Self = Set Spell/Trap")   # Setting a Spell or Trap
                    elif re.search(r"\bmonsters?\b", obj, re.I): gs.append("Self = Set Monster")   # Setting a monster
    return keep_allowed(ss, "strictSupports"), keep_allowed(gs, "generalSupports"), keep_allowed(sb, "strictSupportedBy"), keep_allowed(gb, "generalSupportedBy")   # return the four columns in the standard order, each filtered


# ---------------- Batch B: summon types, Pendulum Zone, Field Zone ----------------
SUMMON_TYPES = {                                                     # label -> the verb that performs it
    "Self = Fusion Summon":   re.compile(r"\bFusion Summon\b(?!ed|ing)", re.I),   # Fusion Summon as an action (not 'Fusion Summoned' / 'Fusion Summoning')
    "Self = Contact Fusion":  re.compile(r"\bContact Fusion\b", re.I),   # Contact Fusion
    "Self = Ritual Summon":   re.compile(r"\bRitual Summon\b(?!ed|ing)", re.I),   # Ritual Summon as an action
    "Self = Synchro Summon":  re.compile(r"\bSynchro Summon\b(?!ed|ing)", re.I),   # Synchro Summon as an action
    "Self = Xyz Summon":      re.compile(r"\bXyz Summon\b(?!ed|ing)", re.I),   # Xyz Summon as an action
    "Self = Link Summon":     re.compile(r"\bLink Summon\b(?!ed|ing)", re.I),   # Link Summon as an action
    "Self = Pendulum Summon": re.compile(r"\bPendulum Summon\b(?!ed|ing)", re.I),   # Pendulum Summon as an action
    "Self = Flip Summon":     re.compile(r"\bFlip Summon\b(?!ed|ing)", re.I),   # Flip Summon as an action
}   # end of the SUMMON_TYPES table
MAT_PERMIT = re.compile(r"\bcan (?:also )?(?:substitute|be used|be treated)|\bis treated as|\bsubstitute\b", re.I)   # "can be used as material", "can substitute"
MAT_FOR = re.compile(r"\bfor (?:an?|the) (?P<types>(?:(?:Fusion|Synchro|Xyz|Link|Ritual|Pendulum)(?:,\s*|,?\s+or\s+|\s+and\s+|/)?)+)\s*Summon", re.I)   # "for a Fusion, Synchro, Xyz, or Link Summon"
PEND_ZONE = re.compile(r"\b(?:place|put)\b[^.;]{0,60}?\b(?:in|into|to) (?:your|the|a) Pendulum Zones?\b", re.I)    # "place this card in your Pendulum Zone"
FIELD_ZONE = re.compile(r"\b(?:place|put)\b[^.;]{0,60}?\b(?:in|into|to) (?P<who>your|the|your opponent's|the other) Field Zone\b", re.I)   # "place 1 Field Spell in your Field Zone"

SUMMON_NO = re.compile(r"\b(?:cannot|can't|unable to)\b[^.;]*$|\bfor (?:an?|the) (?:[\w/]+,?\s+(?:or\s+|and\s+)?)*$", re.I)   # a "cannot" earlier in the sentence, or inside a "for a Fusion, Synchro, or ..." list

TREATED_AS = re.compile(r"\btreated as an? (Fusion|Synchro|Xyz|Link|Ritual|Pendulum) Summon\b", re.I)   # "this Special Summon is treated as an Xyz Summon"

def summon_types(pieces):   # find summon-type actions, material permissions, Pendulum Zone and Field Zone placement
    ss, gs, sb, gb = [], [], [], []                                  # strictSupports, generalSupports, strictSupportedBy, generalSupportedBy
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        for label, rx in SUMMON_TYPES.items():                       # ---- the summon verb used as an action in an effect
            if [h for h in nt_hits(rx, effect) if not SUMMON_NO.search(effect[:h.start()])]:   # real actions only (not "for a Synchro Summon", "cannot ...")
                (gs if label == "Self = Flip Summon" else ss).append(label)   # Flip Summon goes to generalSupports, all the other types to strictSupports
        if MAT_PERMIT.search(effect) and not re.search(r"\bcannot\b|\bcan't\b|\bexcept\b", effect, re.I):   # ---- "can be used as material for a Xyz Summon"
            for m in MAT_FOR.finditer(effect):   # each 'for a ... Summon' list found in the sentence
                for t in re.findall(r"Fusion|Synchro|Xyz|Link|Ritual|Pendulum", m.group("types"), re.I):   # pull out each named summon type from the list
                    ss.append("Self = " + t.capitalize() + " Summon")   # the permitted summon type
        if [x for x in PEND_ZONE.finditer(effect) if not re.match(r"(?:place|put)\s+this (?:face-up )?card\b", x.group(0), re.I)]: ss.append("Self = Place in Pendulum Scale")   # ---- Pendulum Zone (another card, not "place this card in your Pendulum Zone")
        for m in FIELD_ZONE.finditer(effect):                        # ---- Field Zone (our own only)
            if m.group("who").lower() in ("your", "the"): ss.append("Self = Place in Field Zone")   # only placing in our own Field Zone counts
    for m in TREATED_AS.finditer(CARD_TEXT[0]):                      # ---- "(This Special Summon is treated as an Xyz Summon.)" - the brackets are cut off the pieces, so the whole text is read
        summons = list(SS.finditer(CARD_TEXT[0][:m.start()]))        # the Special Summon this reminder belongs to
        if summons and re.match(r"\s*this (?:face-up )?card\b", CARD_TEXT[0][summons[-1].end():], re.I): continue   # the card Summons ITSELF that way: self-only
        ss.append("Self = " + m.group(1).capitalize() + " Summon")   # it performs that kind of Summon for another monster
    return keep_allowed(ss, "strictSupports"), keep_allowed(gs, "generalSupports"), sb, gb   # return the four columns: only the first two use the filter, the need columns are returned empty


# ---------------- Batch C part 1: Destroy and Bounce ----------------
DESTROY = re.compile(r"\bdestroy\b(?!ed|ing)", re.I)                 # the verb destroy (not destroyed / destroying)
BOUNCE = re.compile(r"\breturn\b(?P<obj>[^;.]{0,100}?)\bto (?:the|your|their|its owner's|the owner's|that player's)\s*(?:owner's\s+)?hand\b", re.I)   # "return X to the hand"
MY_FIELD = re.compile(r"\byou control\b|\byour (?:other )?(?:Pendulum Zones?|Spell & Trap Zones?|Monster Zones?|field)\b|\bequipped\b"
                      r"|\bon your side of the field\b|\byour (?!opponent)(?:[\w-]+ )?monsters?\b", re.I)   # our own field ("your other Pendulum Zone", "on your side of the field", "your Normal Monsters")
MY_DECK = re.compile(r"\b(?:in|from) your Deck\b", re.I)                # our own Deck
MY_HAND = re.compile(r"\b(?:your|the) hand\b|\bfrom your hand\b", re.I)          # our own hand
THIS_ONLY = re.compile(r"^\s*this (?:face-up )?card\b(?!\s*(?:\([^)]*\))?\s*(?:,\s*)?(?:and|also)\s+(?:\d|1|up to|that|it|other))", re.I)   # only this card (not "this card and 1 other ...")

def resolve_obj2(side, obj, cost):   # like resolve_obj, but treats 'this card and 1 other ...' as not self-only
    # like resolve_obj, but "this card and 1 other monster" is not self-only
    if side != "effect": return None if BACK_REF.match(obj) else obj   # costs count as written, but "it" in a cost points at nothing
    if BACK_REF.match(obj):                                          # "it", "them", "that monster" -> the cost's target
        return cost_target(cost)   # what the cost targeted (None: nothing to point at, so it is ignored)
    return None if THIS_ONLY.match(obj) else obj                     # self-only effects don't count

def owners_of(obj, before):   # work out whose cards are affected
    # who owns the cards: returns ["self"], ["opp"] or both
    if BOTH_PLAYERS.search(before[-25:] + " " + obj): return ["self", "opp"]   # both players are named, so both owners
    opp = bool(OPP_OBJ.search(obj))                                  # does the object name the opponent's cards?
    describes = lambda pre: re.search(r"\b(?:that|which|who)\b[^,;]*$|\bthe number of\b[^,;]*$", pre, re.I)   # "... THAT battled one of your monsters", "equal to THE NUMBER OF ... you control" (until a comma ends it)
    own = [m for rx in (MY_FIELD, MY_HAND, MY_DECK) for m in rx.finditer(obj) if not describes(obj[:m.start()])]   # "yours" words, except those that only describe or count
    mine = bool(own)                                                 # does the object name our own cards?   # does the object name the opponent's cards, and does it name ours?
    if opp and mine: return ["self", "opp"]                          # both named
    if opp: return ["opp"]   # only the opponent
    if mine or re.search(r"\bpoints? to\b", obj, re.I): return ["self"]   # only us (monsters a Link Monster points to are ours unless the opponent is named)
    return ["opp"]                                                   # "on the field" with no owner -> the opponent's

NEGATED_BEFORE = re.compile(r"\bnegate\b[^.;]*\b(?:Summon|activation|effects?|attack)\b", re.I)   # NEGATED_BEFORE: "negate the Summon / activation / effect" came earlier, so "it" is the card that was negated

BATTLE_FOE = re.compile(r"\bthis (?:face-up )?(?:card|monster)\b[^,:;]{0,30}\b(?:attacks?|attacked|battles?)\b[^,:;]{0,50}\bmonster\b", re.I)   # BATTLE_FOE: condition "if this card attacks / battles a ... monster" (the monster it fights belongs to the opponent)
OPP_EVENT = re.compile(r"\b(?:your )?opponent (?:Special Summons|Normal Summons|Tribute Summons|Flip Summons|Summons|activates|declares|attacks|Sets)\b|\bopponent's (?:monster|card)s? (?:declares|activates|attacks|battles|is Special Summoned|is Normal Summoned)|\bbattles an opponent's\b|\bthat your opponent (?:activated|controls)\b|\b(?:monster|card)s? your opponent controls\b[^:;]{0,30}\b(?:activates|declares|attacks)\b|\b(?:on|to) your opponent's (?:side of the )?field\b", re.I)   # OPP_EVENT: the triggering event is something the opponent does, so "that monster / that card" in the effect is theirs
SELECTED_OPP = re.compile(r"\b(?:select|target|choose|pick up|look at|see)\b[^.;]*?\b(?:your opponent controls|opponent's)\b[^.;]*$", re.I)   # SELECTED_OPP: earlier in the same effect an opponent's card was selected, so a later "it" points at it

def destroy_bounce(pieces):   # find Destroy and Bounce effects and costs
    ss, gs, sb, gb = [], [], [], []                                  # strictSupports, generalSupports, strictSupportedBy, generalSupportedBy
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        for side, text in (("cost", cost), ("effect", effect)):      # look at both halves
            for kind, rx in (("destroy", DESTROY), ("bounce", BOUNCE)):   # handle destroy and bounce with their own patterns
                for m in rx.finditer(text):   # every match of the verb
                    before = re.sub(r"(?:and\s+)?if you do,?\s*$", "", text[:m.start()], flags=re.I)   # "and if you do" is not a condition word here
                    if NOT_AN_ACTION.search(before): continue            # mention / restriction -> not an action
                    if kind == "destroy":   # for destroy, the object is the text after the verb up to a break word
                        after = re.sub(r"^,?\s*or (?:banish|send|return)\w*,?", "", text[m.end():], flags=re.I)   # "destroy, or banish, 1 card you control": read past the ", or banish,"
                        obj = re.split(r"[;.]|,(?!\s+(?:or|in)\b)|\bthen\b|\band if you do\b|\bto (?:target|add|Special|activate|negate|draw|return|send|banish)\b", after)[0]   # cut the object at the first break: punctuation, 'then', 'and if you do' or a following purpose verb
                    else:   # for bounce, the pattern already captured the object
                        obj = m.group("obj")   # the object matched by BOUNCE
                        if not obj.strip(): obj = re.split(r"[;.,]", text[m.end():])[0]   # "return to your hand 1 of your monsters ...": the cards are named after "hand"
                    if re.search(r"\binstead\b", text[m.end():m.end() + 80].split(".")[0], re.I): continue   # replacement effects
                    if kind == "bounce" and re.search(r"\b(?:banished|GY|Graveyard)\b", obj, re.I): continue  # that is "add to hand", not a bounce
                    raw_obj = obj   # keep the original object text
                    obj = resolve_obj2(side, obj, cost)   # resolve 'it / them' and drop self-only objects
                    if obj is None and side == "effect" and BACK_REF.match(raw_obj) and NEGATED_BEFORE.search(before):   # "negate the activation, and if you do, destroy it": "it" is the opponent's negated card
                        obj = "your opponent controls"   # so the destroyed card belongs to the opponent
                    if obj is None and side == "effect" and re.match(r"\s*the (?:targeted|selected)\b", raw_obj, re.I) and OPP_OBJ.search(raw_obj): obj = raw_obj   # "return the selected Set Spell/Trap cards your opponent controls": the object names the opponent itself
                    if obj is None and side == "effect" and BACK_REF.match(raw_obj):   # "destroy / return that monster / it" with nothing resolved yet (destroy and bounce alike)
                        battled = BATTLE_FOE.search(cond)   # "if this card attacks a ... monster: destroy that monster"
                        their_event = OPP_EVENT.search(cond) and not SS.search(before) and not re.search(r"\btargets?\b[^:;]*\byou control\b", cond, re.I)   # the opponent did something (and the effect did not Summon or target our own card first)
                        picked = SELECTED_OPP.search(before)   # "select 1 monster your opponent controls and destroy it"
                        if battled or their_event or picked: obj = "your opponent controls"   # in all three cases the target is the opponent's
                    if obj is None: continue                         # self-only / nothing to point at
                    from_our_gy = re.search(r"\byour (?:GY|Graveyard)\b", obj, re.I) and not OPP_OBJ.search(obj)   # "it" is a card from our own GY ...
                    summoned_foe = OPP_OBJ.search(obj) and summoned_target(pieces, (cond, cost, effect))   # ... or an opponent's card this card Summoned to our side earlier
                    if BACK_REF.match(raw_obj) and (from_our_gy or summoned_foe): continue   # "destroy / return it" about a monster this card just Summoned: tied to this card, not support
                    for owner in owners_of(obj, text[max(0, m.start() - 25):m.start()]):   # each owner (self / opp) of the affected cards gets its own label
                        if owner == "opp":   # opponent's cards
                            label = "Opponent = Destroy" if kind == "destroy" else "Opponent = Bounce"   # choose the label for destroy or bounce
                            (gs if side == "effect" else gb).append(label)   # effects go to generalSupports, costs to generalSupportedBy
                        else:   # our own cards
                            if kind == "bounce": label = "Self = self-bounce"   # bouncing our own card
                            elif MY_DECK.search(obj) and not MY_FIELD.search(obj) and not MY_HAND.search(obj): label = "Self = Destroy"   # destroying cards in the Deck (no place label for that)
                            elif MY_HAND.search(obj) and not MY_FIELD.search(obj): label = "Self = Destroy (Hand)"   # destroying from the hand when only the hand is named
                            else: label = "Self = Destroy (Field)"   # otherwise our own field
                            if side == "effect": gs.append(label)   # in an effect this goes to generalSupports
                            else: sb += need_exprs(obj, label)       # a cost -> a need with the traits of the card
    return keep_allowed(ss, "strictSupports"), keep_allowed(gs, "generalSupports"), keep_allowed(sb, "strictSupportedBy"), keep_allowed(gb, "generalSupportedBy")   # return the four columns in the standard order, each filtered


# ---------------- Batch C part 2: LP, Draw, Burn, Steal, Mill, Give, Move Columns ----------------
DRAW = re.compile(r"\bdraws?\b(?!n|ing)(?!\s*Phase)", re.I)          # the verb draw (not drawn / drawing / Draw Phase)
GAIN = re.compile(r"\b(?:gain|recover)s?\b(?![^.;]{0,15}\b(?:ATK|DEF)\b)[^.;]{0,30}\b(?:LP|Life Points)\b|\bincrease (?:your|the) (?:LP|Life Points)\b", re.I)   # "gain 500 LP"
INFLICT = re.compile(r"\binflicts?\b(?P<rest>[^.;]{0,80}?\bdamage\b[^.;]{0,40})", re.I)   # "inflict 500 damage to your opponent"
STEAL = re.compile(r"\b(?:take control|switch control|gain control of)\b", re.I)                        # "take control of it"
GIVE_CTRL = re.compile(r"\bgive control\b", re.I)                    # "give control of this card to your opponent"
MILL_OPP = re.compile(r"\bsend\b[^;.]{0,40}\btop\b[^;.]{0,40}(?:opponent's|their)\s+Deck", re.I)   # "send the top 2 cards of their Deck to the GY"
MOVE_COL = re.compile(r"\bmove\b[^.;]{0,50}\b(?:Main Monster Zone|Monster Zone|column)\b", re.I)   # "Move 1 monster to an adjacent Monster Zone"
CARD_TEXT = [""]   # the whole text of the card being filled (set in fill_card)
GAIN_COND = re.compile(r"\b(?:if|when|each time|whenever)\s+you\s+(?:gain|recover)\b[^.;:]{0,20}\b(?:LP|Life Points)\b(?![^.;:]{0,30}\binstead\b)", re.I)   # condition "if you gain LP"
SUBJ_OPP = re.compile(r"(?:\bopponent|\bthey|\bthat player|\bthe other player)\s*(?:\w+\s+)?$", re.I)   # the subject right before the verb is the opponent
SUBJ_BOTH = re.compile(r"\b(?:both players|each player|either player)\s*(?:\w+\s+)?$", re.I)           # both players
# gain turned into damage: "if a player would gain LP, they take damage instead" (Nurse Reficule / Simochi)
CONVERT = re.compile(r"\bwould\b[^.;]{0,60}\b(?:gain|recover)\b[^.;]{0,40}\b(?:LP|Life Points)\b[^.;]{0,100}\b(?:damage|lose)\b[^.;]{0,40}\binstead\b|\bwould\b[^.;]{0,60}\b(?:gain|recover)\b[^.;]{0,40}\b(?:LP|Life Points)\b[^.;]{0,40}\binstead\b[^.;]{0,30}\b(?:damage|lose)\b", re.I)   # matches 'would gain LP ... damage instead' in either word order (the two alternatives)

def lp_and_more(pieces):   # find Draw, Gain LP / Give Life, Burn, Steal, Mill, Give control and Move Columns
    ss, gs, sb, gb = [], [], [], []                                  # strictSupports, generalSupports, strictSupportedBy, generalSupportedBy
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        for side, text in (("cost", cost), ("effect", effect)):      # look at both halves
            def real(m):                                             # True when the verb is an action (not "if you ...", "cannot ...")
                return not NOT_AN_ACTION.search(re.sub(r"(?:and\s+)?if you do,?\s*$", "", text[:m.start()], flags=re.I))   # true when the text before the verb has no restriction word (after removing 'and if you do')
            for m in DRAW.finditer(text):                            # ---- Draw
                if not real(m): continue   # skip when the verb is not a real action (negated, in a condition)
                near = text[max(0, m.start() - 30):m.start()]        # the subject right before the verb
                who = ["opp"] if SUBJ_OPP.search(near) else ["self", "opp"] if SUBJ_BOTH.search(near) else ["self"]   # opponent if named right before, both if both players are named, otherwise just us
                for w in who:   # one label per player involved
                    if w == "opp": ss.append("Opponent = Draw")      # only good in mill -> strictSupports
                    else: (gs if side == "effect" else gb).append("Self = Draw")   # our own draw: support in an effect, need in a cost
            for m in GAIN.finditer(text):                            # ---- Gain LP / Give Life
                if not real(m) or re.search(r"\binstead\b", text[m.end():m.end() + 40], re.I): continue   # skip non-actions and 'gain ... instead' replacements
                near = text[max(0, m.start() - 30):m.start()]   # the text just before the verb names who gains
                who = ["opp"] if SUBJ_OPP.search(near) else ["self", "opp"] if SUBJ_BOTH.search(near) else ["self"]   # opponent / both players / just us
                for w in who:   # one label per player involved
                    if w == "opp": ss += ["Opponent = Give Life", "Opponent = Reverse Burn"]   # giving life is reverse burn too
                    elif side == "effect": gs.append("Self = Gain LP")   # our own LP gain only counts as an effect
            if side == "effect" and GAIN_COND.search(cond): sb.append("Self = Gain LP")   # "if you gain LP: ..." needs LP gain
            if side == "cost":   # old-style "Tribute 1 monster and inflict damage ..." is split into the cost, but the burn is still an effect
                for m in INFLICT.finditer(text):   # ---- Burn written inside the cost
                    if real(m) and not re.search(r"\bbattle damage\b|\bto (?:you|yourself)\b|\bboth players\b", m.group("rest"), re.I): gs.append("Opponent = Burn")   # same rules as in an effect
            if side == "effect":   # things only effects can do
                if CONVERT.search(text): ss.append("Opponent = Reverse Burn")                  # turns gained LP into damage
                for m in INFLICT.finditer(text):                     # ---- Burn
                    rest = m.group("rest")   # the text after 'inflict'
                    if not real(m) or re.search(r"\bbattle damage\b|\bto (?:you|yourself)\b|\bboth players\b", rest, re.I): continue   # skip non-actions, battle damage, self-damage and damage to both players
                    gs.append("Opponent = Burn")   # burn only counts when it hits the opponent
                if MILL_OPP.search(text): ss.append("Opponent = Mill")                           # ---- Mill the opponent
                if [x for x in MOVE_COL.finditer(text) if not re.match(r"move this (?:face-up )?card\b", x.group(0), re.I)]: gs.append("Self = Move Columns")   # ---- Move Columns (another card, not "move this card")
            for m in STEAL.finditer(text):                           # ---- Steal
                switch = m.group(0).lower().startswith("switch")   # "switch control" can also just swap monsters between your own cards ...
                card_words = cond + " " + cost + " " + effect + " " + re.sub(r"opponent's card effects", "", CARD_TEXT[0], flags=re.I)   # this piece plus the whole card text ("opponent's card effects" is only protection wording, so it is removed)
                swaps_own = switch and not re.search(r"opponent", card_words, re.I)   # ... so "switch" only counts as stealing when the opponent is named somewhere
                if real(m) and not swaps_own: (gs if side == "effect" else gb).append("Opponent = Steal")   # stealing is support in an effect, a need in a cost
            if side == "effect" and GIVE_CTRL.search(text): gs.append("Self = Give")            # ---- Give control to the opponent
    return keep_allowed(ss, "strictSupports"), keep_allowed(gs, "generalSupports"), sb, keep_allowed(gb, "generalSupportedBy")   # return the four columns: strictSupportedBy is returned as collected without the filter


# ---------------- Batch D: negation and battle ----------------
NEGATE = re.compile(r"\bnegates?\b(?!d)(?P<after>[^.;]{0,50})", re.I)    # "negate the attack", "negate its effects"
END_BATTLE = re.compile(r"\bend (?:the|your opponent's|your) Battle Phase\b|\bthe Battle Phase (?:for that turn |of that turn )?ends\b|\battack (?:is|was) negated\b", re.I)   # END_BATTLE: "end the Battle Phase", "the Battle Phase ends immediately", "your opponent's next attack is negated"
PASSIVE_NEG = re.compile(r"\b(?:has|have) (?:its|their) (?:activated )?effects? negated\b|(?:this card'?s? )?\beffect(?:s|\(s\))?\b(?:(?!\bcannot\b)[^.;]){0,40}?\b(?:is|are) negated\b", re.I)   # PASSIVE_NEG: "have their effects negated", "its effects are negated", "The effects of Effect Monsters with A-Counters are negated"
PREV_ACT = re.compile(r"\b(?:cannot|can't)\s+(?:activate|be activated)\b", re.I)         # "cannot activate cards or effects"
PREV_ATK = re.compile(r"\b(?:cannot|can't)\s+(?:change (?:its|their|the) battle positions?,? (?:or|nor) )?(?:declare an? attack|attack)\b(?!\s+directly)", re.I)   # "cannot attack" (not "cannot attack directly")
FORCE_ATK = re.compile(r"\bmust attack\b|\bmust be attacked\b|\bmust declare an attack\b", re.I)     # "must attack"
PASSIVE_POS = re.compile(r"(?P<obj>[^.;]{0,80}?)\b(?:are|is) changed to (?:face-up )?(?:Attack|Defense) Position\b", re.I)   # PASSIVE_POS: "<monsters> are changed to Attack Position" (a continuous effect on those monsters)
CHG_POS = re.compile(r"\bchange\b(?P<obj>[^.;]{0,60}?)\b(?:to|into)\b[^.;]{0,20}?\b(?:Attack|Defense) Position\b|\bchange (?:its|their|the|that monster's|that target's) battle positions?\b", re.I)   # "change it to Defense Position"
DIRECT = re.compile(r"\b(?:can|may)\s+(?:also\s+)?attack (?:your opponent )?directly\b", re.I)    # "can attack directly"
MULTI = re.compile(r"\bcan make (?:a )?(?:second|third)\b[^.;]{0,20}\battack|\bmake up to \d+ attacks?\b|\battack (?:all|twice)\b|\bcan attack all\b|\bmake (?:a )?second attack\b", re.I)   # "can make a second attack"
PIERCING = re.compile(r"\binflicts? piercing\b|\bpiercing battle damage\b", re.I)                       # "inflict piercing battle damage"
BATTLE_DMG = re.compile(r"\binflicts?\b(?P<rest>[^.;]{0,50}?\bbattle damage\b[^.;]{0,40})", re.I)       # "inflict battle damage to your opponent"
DEST_BATTLE = re.compile(r"\bdestroys?\b[^.;]{0,50}\bby battle\b", re.I)                                  # "destroys an opponent's monster by battle"
COND_WORD = re.compile(r"\b(?:when|if|each time|whenever|after|while)\b[^,:;]{0,50}$", re.I)             # the verb sits inside a condition

def for_another_monster(effect, start, cost, prev=""):   # for_another_monster(): does the attack ability at `start` go to ANOTHER monster (not this card)? (prev = the sentence before)
    before = re.split(r"[.:]", effect[:start])[-1]                   # the words before it in the same sentence
    ours = cost if re.search(r"\byou control\b|\bequipped\b|\bplayer controls\b|\bpoints? to\b", cost, re.I) and not OPP_OBJ.search(cost) else ""   # the cost targeted one of YOUR monsters (not an opponent's card, not "a monster on the field")
    if re.search(r"\bit\s+$", before, re.I) and re.search(r"\bequip this card to\b", CARD_TEXT[0], re.I): ours += " equipped"   # "... equip this card to it. It can make a second attack": the equipped monster
    if re.search(r"\bit\s+$", before, re.I) and re.search(r"\b(?:select|target)\s+(?:1|one|up to \d+)\b[^.]*\byou control\b", prev + " " + effect[:start], re.I): ours += " you control"   # "Select 1 monster you control. ... it can attack directly": the selected monster
    other = re.search(r"\bequipped\b|\b(?:that|those|the)(?: other)? monsters?\b|\bother monsters?\b|\byou control\b|\btarget|\bFusion Summon|\bequip this card\b", before + " " + ours, re.I) \
        or re.search(r"(?:\bmonsters\b|\"[^\"]+\")[^,;:]{0,40}$", before, re.I)   # another monster is named - or a group / a named card is the subject ("your Ghostrick monsters can ...", "\"SPYRAL Super Agent\" on the field can ...")
    itself = re.search(r"(?<!with )(?<!to )\bthis (?:[\w-]+ ){0,3}?(?:card|monster)\s+(?:can\s+)?(?:also\s+)?$", effect[:start], re.I)   # "this (Fusion Summoned) card can ..." (not "equipped with this card can")
    if CARD_IS_MONSTER[0] and (itself or not other): return False   # a monster that does it itself ("It can make a second attack", "This card can attack directly")
    return True                                                      # a Spell / Trap, or a monster giving it to another monster

def battle_negate(pieces):   # find Negate, Prevent activation / attacks, Force attack, Change position, Direct and Multi-attack, Piercing, battle damage
    ss, gs, sb, gb = [], [], [], []                                  # strictSupports, generalSupports, strictSupportedBy, generalSupportedBy
    prev_eff = ""                                                    # the effect of the piece before ("Select 1 monster you control." then "It can attack directly.")
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        for m in NEGATE.finditer(effect):                            # ---- Negate (effects only)
            pre = effect[:m.start()]   # the text before the verb
            if NOT_AN_ACTION.search(pre): continue                       # "cannot negate" etc.
            after = m.group("after").strip()   # the words right after 'negate'
            if re.match(r"(?:the|that|an|the next|that monster's)?\s*attack\b", after, re.I): gs.append("Opponent = Negate attack")   # negating an attack
            elif re.match(r"(?:the|that|a)\s+(?:Special |Normal |Flip |Tribute )?Summon", after, re.I): gs.append("Opponent = Negate Summon")   # negating a Summon
            elif re.match(r"this card'?s?\b", after, re.I): continue   # negating itself is a drawback, not support
            elif re.search(r"special summon\b[^.;]*(?:,|\bbut\b|\balso\b)\s*(?:but |also |and )?$", pre, re.I) and re.match(r"(?:its|their|that monster's|those monsters')\s+effects?", after, re.I): continue   # "Special Summon it, but negate its effects": our own summoned monster is the one negated (a drawback)
            else: gs.append("Opponent = Negation")   # anything else negated
        for m in PASSIVE_NEG.finditer(effect):                       # ---- passive negate: "that target has its effects negated", "its effects are negated"
            before = effect[:m.start()]                              # the text before it in this piece
            sentence = re.split(r"[.;:]", before)[-1]                # ... and in the same sentence
            prev_sent = re.split(r"(?<=[.;:])\s+", (prev_eff + " " + before[:len(before) - len(sentence)]).strip())[-1]   # the sentence before this one
            pronoun = re.match(r"\s*(?:its|their|it|they|that monster's|those monsters'|the effect\(s\) of that monster|the\s*$)(?!\w)", sentence, re.I)   # this sentence starts with "Its / Their / It ...": it is about the card named before
            if re.match(r"this card'?s?\b", m.group(0), re.I) or re.search(r"\bthis card'?s\s*$", sentence, re.I): continue   # "this card's effects are negated": a drawback on itself
            if re.search(r"\bcannot (?:have|be)\s*$|\bcannot have\b", sentence + m.group(0)[:20], re.I): continue   # "cannot have their activated effects negated": protection
            subject = re.sub(r"\bwhile you control this (?:face-up )?card\b", " ", sentence, flags=re.I)   # the negated card is named in this sentence ("while you control this card" is only a condition) ...
            if re.search(r"\b(?:its|their|that target's|those)\b", sentence + " " + m.group(0), re.I): subject += " " + (cost_target(cost) or (prev_sent if pronoun else ""))   # ... or, for "its / their", the card the cost targeted (no target: the card named in the sentence before)
            if re.search(r"\byou (?:currently )?control\b|\byour (?!opponent)|\bused this card as\b", subject, re.I) and not re.search(r"\bopponent", subject, re.I): continue   # YOUR monster ("Target 1 monster you control; its effects are negated"): a drawback or a cost, not support
            if re.search(r"\b(?:whose|following)\b", sentence + m.group(0), re.I): continue   # "cards whose effects are negated" (a description), "the following effects are negated" (its own coin result)
            if re.search(r"\b(?:Special Summon|take control|gain control|Summoned monster)\b", sentence, re.I): continue   # "Special Summon it, but its effects are negated" / "take control of it, but ...": a drawback on OUR new monster
            if pronoun and not cost_target(cost) and re.search(r"\b(?:Special Summon|take control|gain control)\b", prev_sent, re.I): continue   # "Special Summon 1 monster from your GY. Its effects are negated.": the sentence before summoned it
            if re.search(r"(?<!this card )\bgains? \d+ ATK\b", sentence, re.I) and not re.search(r"\btarget\b", CARD_TEXT[0], re.I): continue   # "it gains 800 ATK, also its effects are negated": a boost for YOUR monster with a drawback (a card that targets, like Forbidden Chalice, is kept)
            gs.append("Opponent = Negation")                         # anything else that has its effects negated
        for m in END_BATTLE.finditer(effect):                        # ---- "end the Battle Phase" / "its next attack is negated": stops the opponent's attacks
            if NOT_AN_ACTION.search(effect[:m.start()]) or re.search(r"\byour (?:own )?Battle Phase\b", m.group(0), re.I): continue   # a mention ("cannot end ...") or YOUR own Battle Phase
            gs.append("Opponent = Negate attack")                    # an opponent's attack is stopped
        for m in PREV_ACT.finditer(effect):                          # ---- Prevent activation (opponent only)
            pre = effect[max(0, m.start() - 60):m.start()]   # the text before the match
            if not re.search(r"\b(?:you|this card|that effect|it)\s*$", pre, re.I): gs.append("Opponent = Prevent activation")   # it is only about the opponent when the subject is not us / this card
        for m in PREV_ATK.finditer(effect):                          # ---- Prevent attacks (opponent only)
            pre = effect[max(0, m.start() - 80):m.start()]   # the text before the match
            if re.search(r"\bopponent\b|\bneither player\b|\ball monsters\b", pre, re.I): gs.append("Opponent = prevent attacks")   # only count it when the opponent (or all monsters) is named
            else:   # no owner named just before: "that monster / it cannot attack"
                subject_is_ours = re.search(r"(?:\bthis (?:face-up )?(?:card|monster)|\byou|\bmonsters? you control|\bthe turn)\s*(?:also\s+)?$", effect[max(0, m.start() - 25):m.start()], re.I)   # the subject right before is this card, you, or your own monsters
                our_monster = re.search(r"special summon(?!ed this way)|take control|\btoken\b|summoned this way", cond + " " + effect[:m.start()], re.I)   # the card Summons / steals / makes a Token first: "it" is our own monster
                opponent_named = BATTLE_FOE.search(cond) or re.search(r"\byour opponent(?:'s)?\b", cond + " " + cost + " " + effect[:m.start()], re.I)   # this card battled their monster, or the opponent was named earlier
                if not subject_is_ours and not our_monster and opponent_named: gs.append("Opponent = prevent attacks")   # so it is the opponent's monster that cannot attack
        for m in FORCE_ATK.finditer(effect):                         # ---- Force attack (opponent only)
            if re.search(r"\bopponent\b", effect[max(0, m.start() - 80):m.start()], re.I): gs.append("Opponent = Force attack")   # only count it when the opponent is named before the match
        for side, text in (("cost", cost), ("effect", effect)):      # ---- Change battle position
            for m in CHG_POS.finditer(text):   # every 'change ... position' match
                if re.search(r"face-down", text[m.start():m.end() + 25], re.I): continue   # face-down is batch F (Flip)
                obj = m.group("obj") or ""   # the object that changes position
                if not obj and re.match(r"change the battle position of\b", m.group(0), re.I) is None and re.match(r"\s*of\b", text[m.end():], re.I) and not NOT_AN_ACTION.search(text[:m.start()]):   # "change the battle position of 1 monster" (a real action, not "cannot change its battle position")
                    obj = re.sub(r"^\s*of\s+", "", text[m.end():m.end() + 60].split(";")[0])   # the words after "of" are the object
                if side == "cost":   # costs and effects are handled differently
                    # (a cost that changes THIS card's position pays with itself - never a need)
                    continue   # nothing else to do for costs
                if not obj.strip() and re.match(r"change (?:its|their)\b", m.group(0), re.I) and not NOT_AN_ACTION.search(text[:m.start()]) and not re.search(r"\b(?:or|nor|and)\s*$", text[:m.start()], re.I):   # "change its battle position": "its" is the target chosen in the cost
                    obj = cost_target(cost) or obj   # the target chosen in the cost is the object (if it was another card)
                obj = resolve_obj2("effect", obj.strip(), cost) if obj.strip() else None   # resolve 'it / them', dropping self-only objects
                if obj is None and re.match(r"change (?:its|their|it|them|that monster'?s?|those targets'?|the battle position of that monster)\b", m.group(0) + text[m.end():m.end() + 30], re.I) and not NOT_AN_ACTION.search(text[:m.start()]):   # "it / its" with no target in the cost ...
                    picked = re.findall(r"\b(?:select|target)s? (?:1|one|up to \d+|\d+)\b[^.;]*", (prev_eff + " " + text[:m.start()])[-200:], re.I)   # ... the monster selected just before ("Select 1 monster your opponent controls. Change it to Attack Position")
                    obj = picked[-1] if picked else ("your opponent controls" if opp_backref(cond, text[:m.start()]) else None)   # or the opponent's monster the condition is about ("When your opponent Summons a monster: change that monster to Defense Position")
                if obj is None: continue                             # self-only / nothing to point at
                for owner in owners_of(obj, effect[max(0, m.start() - 25):m.start()]):   # each owner of the changed cards gets a label
                    gs.append("Opponent = Change battle position" if owner == "opp" else "Self = Change battle position")   # opponent's monsters get the Opponent label, ours the Self label
        for m in PASSIVE_POS.finditer(effect):                       # ---- "All Insect monsters your opponent controls are changed to Attack Position"
            if OPP_OBJ.search(m.group("obj")) or BATTLED.search(m.group("obj")): gs.append("Opponent = Change battle position")   # only when the changed monsters are the opponent's (or the monster that battled this card)
        for m in DIRECT.finditer(effect):                            # ---- Direct attack
            if not NOT_AN_ACTION.search(effect[:m.start()]) and for_another_monster(effect, m.start(), cost, prev_eff): gs.append("Self = Direct attack")   # a real action, for ANOTHER monster ("This card can attack directly" is self-only)
        for m in MULTI.finditer(effect):                             # ---- Multi-attack (only extra attacks for OTHER monsters count)
            if for_another_monster(effect, m.start(), cost, prev_eff): gs.append("Self = Multi-attack")   # a Spell / Trap, or a monster giving the extra attacks to another monster
        prev_eff = effect                                            # remember this piece's effect for the next piece
        mp = PIERCING.search(effect)   # ---- Piercing
        if mp:   # the effect mentions piercing damage
            before_p = cond + " " + effect[:mp.start()]   # the condition and the words before it name who attacks
            self_p = re.search(r"\bthis (?:face-up )?(?:card|monster)\b", before_p, re.I) and not re.search(r"\b(?:you control|equipped|all|each|other)\b", before_p, re.I)   # "If this card attacks ..., inflict piercing damage" is about this card only
            if not EQUIP_CARD[0] and re.search(r"\bit attacks\b", before_p, re.I) and not re.search(r"\b(?:you control|equipped|all|each|other)\b", before_p, re.I): self_p = True   # on a monster, "if it attacks" means this card (on an Equip Spell it means the equipped monster)
            if not self_p: gs.append("Self = Piercing battle damage")   # only piercing given to other monsters counts
        for text, is_cond in ((cond, True), (effect, False)):        # ---- battle damage / destroy by battle
            for m in BATTLE_DMG.finditer(text):   # every battle-damage inflict match
                rest = m.group("rest")   # the words after 'inflict'
                if re.search(r"\bequal to\b[^.;]{0,30}$", rest[:re.search(r"\bbattle damage\b", rest).start()] if re.search(r"\bbattle damage\b", rest) else "", re.I): continue   # "inflict damage equal to that battle damage" is a Burn, not battle damage dealt
                if re.search(r"\bpiercing\b|\bto (?:you|yourself)\b|\bboth players\b|\bno battle damage\b", rest, re.I): continue   # skip piercing, self-damage, both players and 'no battle damage'
                if is_cond or COND_WORD.search(text[:m.start()]): gb.append("Opponent = inflicts battle damage to your opponent")   # a condition -> a need
                else: gs.append("Opponent = inflicts battle damage to your opponent")                                               # an effect -> support
            if DEST_BATTLE.search(text): gb.append("Opponent = Destroys monster by Battle")   # destroying by battle is always a need
    return keep_allowed(ss, "strictSupports"), keep_allowed(gs, "generalSupports"), sb, keep_allowed(gb, "generalSupportedBy")   # return the four columns: strictSupportedBy is returned as collected without the filter


# ---------------- Batch E: protection, stat changes, Level, Monster in S/T Zone ----------------
BREAKS = re.compile(r"[;,.:]|\bthen\b|\band\b|\balso\b|\bbut\b|\bif you do\b|\bwhile\b|\buntil\b", re.I)   # where a subject ends
SELF_SUBJ = re.compile(r"^(?:(?:the|that)\s+)?(?:this (?:(?:(?:face-up|face-down) (?:Attack|Defense) Position|linked|face-up|Link Summoned|Synchro Summoned|Xyz Summoned|Fusion Summoned|Ritual Summoned|Pendulum Summoned|Normal Summoned|Special Summoned|Tribute Summoned|Flip Summoned)\s+)?(?:card|monster)|it|itself)$|^$", re.I)   # "this card", "it", or no subject at all
GAIN_STAT = re.compile(r"\bgains?\b[^.;,]{0,30}?\b(ATK/DEF|ATK and DEF|ATK|DEF)\b", re.I)                    # "gains 300 ATK/DEF"
LOSE_STAT = re.compile(r"\b(?:loses?|halves?)\b[^.;,]{0,30}?\b(ATK/DEF|ATK and DEF|ATK|DEF)\b", re.I)         # "loses 500 ATK"
CANT_DESTROY = re.compile(r"\bcannot be destroyed\b(?P<how>\s+(?:by|as a result of)\s+[^.;,]{0,40})?", re.I)                     # "cannot be destroyed by battle or card effects"
CANT_TARGET = re.compile(r"\bcannot be targeted\b(?!\s+for attacks)", re.I)                                   # "cannot be targeted by card effects"
CANT_TARGET_ATK = re.compile(r"\bcannot be (?:targeted for attacks|selected as an attack target)\b", re.I)    # "cannot be targeted for attacks"
CANT_TGT_EFF = re.compile(r"\bcannot target\b(?P<obj>[^.;:]{0,220}?)\bwith (?:card )?effects\b", re.I)   # active wording: "your opponent cannot target X with card effects"
CANT_SEL_ATK = re.compile(r"\bcannot (?:target|select)\b(?P<obj>[^.;:]{0,80}?)\s*(?:\bfor attacks\b|\bas (?:an )?attack targets?\b)", re.I)   # active wording: "your opponent cannot target Insect-Type monsters for attacks"
UNAFFECTED = re.compile(r"\bunaffected by\b", re.I)                                                           # "is unaffected by other cards' effects"
LEVEL_CHG = re.compile(r"\b(?:increases?|decreases?|reduces?)\b[^.;,]{0,25}\bLevels?(?:/Rank)?\b|\bchange (?:the |its |their |this card's |that monster's |his |her )?(?:original )?Level\b|\bLevel\b[^.;]{0,15}\bbecomes?\b|\bbecomes?\b[^.;]{0,15}\bLevel \d|\bLevels?\b[^.;]{0,40}\b(?:is|are) (?:increased|decreased|reduced)\b", re.I)   # "increase its Level by 1"
MON_IN_ST = re.compile(r"\bplace\b[^.;]{0,80}\bSpell & Trap Zone\b[^.;]{0,60}\bContinuous (?:Spell|Trap)\b|\bTreat (?:it|this card|that card) as (?:a )?(?:Continuous )?(?:Spell|Trap)\b", re.I)   # a monster placed in the S/T Zone as a Continuous Spell

EQUIP_CARD = [False]   # EQUIP_CARD[0] is True while the card being processed is an Equip Spell ("Equip only to ...")
CARD_IS_MONSTER = [False]   # CARD_IS_MONSTER[0] is True while the card being processed is a monster (set in fill_card)

def subject_before(text, start, cost):                              # find who a verb at position `start` is about; returns (subject text, is_self_only)
    # the subject of the verb that starts at `start`, and whether it is self-only (this card / it / nothing)
    seg = BREAKS.split(text[:start])[-1].strip(' )("\'')           # text since the last break (without stray brackets / quotes)
    pre0 = text[:start].rstrip()   # everything before the verb, without trailing spaces
    if not seg and pre0.endswith(",") and re.match(r"\s*(?:all|each|other|monsters|your opponent)\b", pre0, re.I): return pre0, False   # "All monsters on the field, except X, lose 100 ATK": the subject is the group named at the start of the sentence
    while True:                                                      # drop trailing helper words ("it is", "monsters will")
        new = re.sub(r"\b(?:is|are|also|become|becomes|be|will|can|that|which|each|all)\s*$", "", seg, flags=re.I).strip()   # remove one helper word from the end of the subject
        if new == seg: break                                         # nothing was removed, so the subject is clean
        seg = new                                                    # keep the shorter subject and try to strip again
    if re.search(r"\b(?:make|have|let)\s+this (?:face-up )?(?:card|monster)$", seg, re.I): return seg, True   # "You can make this card gain 500 ATK": this card itself
    if re.search(r"^\d\+?\s.*?(?<!using )(?<!with )(?<!to )(?<!by )(?<!of )(?<!from )\bthis (?:[\w-]+ ){0,2}?(?:card|monster)$", seg, re.I) and " and " not in seg:   # "2+ Effect Monsters This card cannot ...":
        return seg, True                                             # a material line glued to the sentence hides the "This card" subject
    if SELF_SUBJ.match(seg):                                         # the subject looks like "this card" / "it" / empty
        # "Other monsters you control and this card cannot ..." -> the subject is a compound, so it is not self-only
        full = re.split(r"[;,.:]|\bthen\b|\balso\b|\bbut\b|\bif you do\b|\bwhile\b|\buntil\b", text[:start])[-1]   # like BREAKS, but "and" does not end the subject
        parts = [x.strip(' )("\'') for x in re.split(r"\band\b", full)]                                                 # the pieces joined by "and"
        # the next test (spread over two lines): the piece before "this card" exists, is not "this card" itself, and is not just a verb phrase
        if len(parts) > 1 and seg and parts[-2] and not SELF_SUBJ.match(parts[-2]) \
           and not re.search(r"\b(?:gains?|loses?|cannot|can|is|are|must|will|negate|destroy|Special Summon|add|return|inflict|draw|banish|send|take|activate|change|place|shuffle|equip|target|select|calculation|if|when)\b|\d+\s*(?:ATK|DEF)", parts[-2], re.I):   # verbs / stat numbers mean it is not a subject
            return full.strip(), False                                # the other half of the subject is not "this card" -> it counts
        if seg.lower() == "it" and (EQUIP_CARD[0] or re.search(r"\bequipped monster\b", text[:start], re.I)): return seg, False   # "While the equipped monster is attacking, it cannot be destroyed": "it" is the equipped monster, another card
        if seg.lower() == "it":                                      # "it" may point at the cost's target
            t = cost_target(cost)   # a \"target 1 ...\" clause in the cost about another card
            if t: return t, False   # the target is another card, so \"it\" is not self-only
        return seg, True                                             # otherwise the subject is only this card
    return seg, False                                                # the subject is some other group of cards

def buffs(pieces):                                                   # labels for ATK/DEF changes and protection effects
    ss, gs, sb, gb = [], [], [], []                                  # strictSupports, generalSupports, strictSupportedBy, generalSupportedBy
    prev_eff = ""                                                    # effect text of the piece before ("Select 1 monster your opponent controls." then "It loses 500 ATK.")
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        for m in GAIN_STAT.finditer(effect):                         # ---- Gain ATK / DEF
            subj, selfy = subject_before(effect, m.start(), cost)    # who gains the stat, and is it only this card
            if selfy: continue                                       # self-only buffs don't count
            st = m.group(1).upper()                                  # the matched stat word(s) in capitals ("ATK", "DEF", "ATK AND DEF")
            if "ATK" in st: gs.append("Self = Gain ATK")             # an ATK boost
            if "DEF" in st: gs.append("Self = Gain DEF")             # a DEF boost
        for m in LOSE_STAT.finditer(effect):                         # ---- Lose ATK / DEF (opponent's or unspecified only)
            subj, selfy = subject_before(effect, m.start(), cost)    # who loses the stat
            imperative = m.group(0).lower().startswith("halve ") and not subj and not re.search(r"this card|by this effect|you control|equipped", effect[m.start():m.start() + 100], re.I)   # "Halve the ATK of all monsters ..." has no subject but is not about this card
            if re.match(r"(?:the targeted|that|the selected)\b", subj, re.I):   # "the targeted monster loses 1000 ATK": the subject was chosen in the cost
                tc = TARGET_RE.search(cost)   # the target clause written in the cost
                if tc: subj = tc.group(0)   # use it as the subject so "you control" is seen
            points_back = re.match(r"\s*(?:it|that monster|those monsters|that card|they)\b", subj, re.I) and not re.search(r"\bthis card\b", subj, re.I)   # the subject is "it / that monster" (not this card)
            picked_foe = re.search(r"\b(?:select|target)\b[^;]*?\b(?:your opponent controls|opponent's)\b", prev_eff + " " + cost + " " + effect[:m.start()], re.I)   # an opponent's monster was selected / targeted just before
            if points_back and picked_foe: selfy = False; subj = "your opponent controls"   # "It loses 500 ATK" right after selecting an opponent's monster: the opponent's monster loses it
            if (selfy and not imperative) or (re.search(r"\byou control\b", subj, re.I) and not OPP_OBJ.search(subj)): continue    # our own drawbacks don't count
            if re.search(r"\blos(?:e|es)\s+(?:\d+\s+)?(?:LP|Life Points)\b", m.group(0), re.I): continue   # "you lose LP equal to its ATK": Life Points, not ATK
            earlier = ("" if cost.strip() else prev_eff + " ") + effect[:m.start()]   # this sentence (and the one before, when this one targets nothing itself: "Special Summon 1 monster. Its ATK becomes 0.")
            summons = list(SS.finditer(earlier))                     # every "Special Summon" before the stat change
            summoned_other = summons and not re.match(r"\s*this (?:face-up )?card\b", earlier[summons[-1].end():], re.I)   # the last one Summons ANOTHER monster (not this card)
            pronoun = re.search(r"\b(?:it|its|they|their|them)\s+(?:\w+\s+)?$", effect[:m.start()], re.I)   # "but its ATK...", "it loses 500 ATK": points back at that monster
            if summoned_other and pronoun and not re.search(r"\bopponent", subj + " " + effect[:m.start()], re.I): continue   # a drawback on OUR new monster ("their" here means the Summoned monsters, not the opponent's)
            st = m.group(1).upper()                                  # the matched stat word(s) in capitals
            if "ATK" in st: gs.append("Opponent = Lose ATK")         # an ATK drop on the opponent's side
            if "DEF" in st: gs.append("Opponent = Lose DEF")         # a DEF drop on the opponent's side
        prev_eff = effect                                            # remember this piece's effect for the next piece
        for m in CANT_DESTROY.finditer(effect):                      # ---- Cannot be destroyed
            subj, selfy = subject_before(effect, m.start(), cost)    # who cannot be destroyed
            if selfy: continue                                       # self-only protection does not count
            how = (m.group("how") or "").lower()                     # the "by battle" / "by card effects" part, if any
            if not how: gs.append("Self = Cannot be destroyed")      # no qualifier: protected from all destruction
            else:                                                    # there is a qualifier
                if "battle" in how: gs.append("Self = Cannot be destroyed by battle")               # protected from battle destruction
                if "effect" in how: gs.append("Self = Cannot be destroyed by Card effects")         # protected from effect destruction
        for rx, label in ((CANT_TARGET, "Self = Cannot be targeted"), (CANT_TARGET_ATK, "Self = Cannot be targeted for attacks"), (UNAFFECTED, "Self = Uneffected by Card Effects")):   # three protection patterns and their labels
            for m in rx.finditer(effect):                            # ---- targeting / unaffected protections
                subj, selfy = subject_before(effect, m.start(), cost)   # who is protected
                if not selfy: gs.append(label)                       # only protection given to other cards counts
        for m in CANT_TGT_EFF.finditer(effect):                      # ---- "your opponent cannot target X with card effects" (X is the protected card)
            obj = m.group("obj").strip()                             # the protected cards named in the sentence
            if obj and not SELF_OBJ.match(obj): gs.append("Self = Cannot be targeted")   # protecting other cards counts; "this card" alone does not
        for m in CANT_SEL_ATK.finditer(effect):                      # ---- "your opponent cannot target X for attacks" (X is the protected card)
            obj = m.group("obj").strip()                             # the protected cards named in the sentence
            if obj and not SELF_OBJ.match(obj): gs.append("Self = Cannot be targeted for attacks")   # protecting other cards counts; "this card" alone does not
        for m in re.finditer(r"cannot (?:select|target)[^.;]*?,?\s+or\s+target\s+(?:them|it|that monster)\s+with\s+card\s+effects", effect, re.I):   # "cannot select X as an attack target, or target them with card effects"
            obj = re.sub(r"^.*?cannot (?:select|target)\s+", "", m.group(0), flags=re.I)   # the protected cards: the words after "cannot select / target"
            if not SELF_OBJ.match(obj) and "another" in obj.lower(): gs.append("Self = Cannot be targeted")   # protects other cards
        for m in LEVEL_CHG.finditer(effect):                         # ---- Level changes (of another monster, not this card's own Level)
            if not re.search(r"\bthis (?:face-up )?card'?s?\b|\bLevel of this card\b", effect[max(0, m.start() - 20):m.end() + 15], re.I):
                gs.append("Self = Change Level"); break               # one is enough
        placed = lambda t: [x for x in MON_IN_ST.finditer(t) if not re.match(r"(?:place|treat)\s+this (?:face-up )?card\b", x.group(0), re.I)]   # not "place this card in your S/T Zone"
        if placed(effect): ss.append("Self = Monster in S/T Zone")   # ---- monsters placed in the S/T Zone
        if placed(cost): sb.append("Self = Monster in S/T Zone")     # (as a cost it is a need)
    return keep_allowed(ss, "strictSupports"), keep_allowed(gs, "generalSupports"), keep_allowed(sb, "strictSupportedBy"), gb   # filter three lists; gb is never filled here


# ---------------- Batch F: FLIP, Flip effects, Coin, Dice ----------------
FLIP_COND = re.compile(r"\bFLIP\b|\b(?:is|are|was|were) flipped face-(?:up|down)\b|\bflipped face-(?:up|down)\b")   # "FLIP:" or "When this card is flipped face-up"
FLIP_ACT = re.compile(r"\b(?:change|turn|flip)\b(?P<obj>[^.;]{0,60}?)\bto face-(?:up|down)\b|\bflip\b(?P<obj2>[^.;]{0,40}?)\bface-(?:up|down)\b", re.I)   # "change it to face-down Defense Position"
COIN = re.compile(r"\btoss(?:es)? (?:a )?coins?\b|\bcoin toss\b", re.I)                       # "toss a coin"
DICE = re.compile(r"\broll(?:s|ed)? (?:a )?(?:six-sided )?(?:die|dice)\b|\bdice roll\b|\bdie roll\b", re.I)   # "roll a six-sided die"
COIN_COND = re.compile(r"\bcoin toss\b|\btosses? a coin\b", re.I)                              # a condition that reacts to coin tosses
DICE_COND = re.compile(r"\brolls? (?:a )?(?:die|dice)\b|\bdice roll\b|\bdie roll\b|\brolls? \(?a die", re.I)   # a condition that reacts to dice rolls

def flip_coin_dice(pieces):                                          # labels for flipping cards face-up/down and for coin and dice effects
    ss, gs, sb, gb = [], [], [], []                                  # strictSupports, generalSupports, strictSupportedBy, generalSupportedBy
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        if FLIP_COND.search(cond): gb.append("Self = Flip")          # ---- FLIP autolabel: the card needs to be flipped
        for side, text in (("cost", cost), ("effect", effect)):      # ---- Flip actions
            for m in FLIP_ACT.finditer(text):                        # each "change ... to face-down" style action
                if NOT_AN_ACTION.search(text[:m.start()]): continue      # "cannot change ..." restrictions
                to_face_up = re.match(r"(?:change|turn)\b", m.group(0), re.I) and re.search(r"\bto face-up\b", m.group(0), re.I)   # "change / turn ... to face-up ..."
                if to_face_up and not re.search(r"face-down|\bSet\b", m.group(0) + text[m.end():m.end() + 45], re.I): continue   # "change it to face-up Defense Position" only moves a face-up monster, it does not flip anything
                obj = (m.group("obj") if m.group("obj") is not None else m.group("obj2")) or ""   # the words describing what is flipped (from whichever half of the regex matched)
                earlier = LAST_TARGET.search(text[:m.start()])       # "select / target 1 monster ..." earlier in this same text
                itself = THIS_ONLY.match(obj.strip()) or re.match(r"^\s*(?:this (?:Set )?card|it)\b", obj, re.I) and not TARGET_RE.search(cost) and not earlier   # the object is only this card (or an "it" with nothing selected before)
                if itself: continue                                  # this card flips itself: helps nothing else, needs nothing
                if side == "cost":                                   # the flip of another card is paid as a cost
                    gb.append("Self = Flip"); continue               # flipping as a cost is a need
                tgt = resolve_obj2("effect", obj.strip(), cost) if obj.strip() else None   # work out what "it / that monster" points at
                if tgt is None and earlier and BACK_REF.match(obj): tgt = earlier.group(0)   # "Select 1 face-up monster and change it to face-down": the selected monster
                if tgt is None and BACK_REF.match(obj) and (BATTLE_FOE.search(cond) or OPP_EVENT.search(cond)): tgt = "your opponent controls"   # "if this card battled an opponent's monster: change that monster to face-down": the battled / summoned monster is the opponent's
                if tgt is None: continue                             # nothing to point at
                for owner in owners_of(tgt, text[max(0, m.start() - 25):m.start()]):   # whose cards are flipped (looks at the words just before)
                    gs.append("Opponent = Flip" if owner == "opp" else "Self = Flip")   # opponent's cards or our own
        for side, text in (("cost", cost), ("effect", effect)):      # ---- Coin and Dice
            if COIN.search(text): (ss if side == "effect" else gb).append("Self = Coin Flip")   # coin in an effect supports; coin in a cost is a need
            if DICE.search(text): (ss if side == "effect" else gb).append("Self = Dice Roll")   # same for dice
        if COIN_COND.search(cond): ss.append("Self = Coin Flip")     # support cards that react to coin tosses
        if DICE_COND.search(cond): ss.append("Self = Dice Roll")     # support cards that react to dice rolls
    return keep_allowed(ss, "strictSupports"), keep_allowed(gs, "generalSupports"), sb, keep_allowed(gb, "generalSupportedBy")   # sb stays empty; the other three are filtered


# ---------------- Reactions: something happens TO this card, so the player must make it happen ----------------
# "Self" is the PLAYER. A card that reacts to being discarded / sent to the GY / banished / used as material needs the
# player to do that to it:  "If this card is discarded"  -> strictSupportedBy  Self = Discard  (it wants a discard outlet)
REACT_SUBJ = r"\bthis (?:face-up )?card(?: in your (?P<where>hand|field|GY|Graveyard|Deck|Extra Deck|Monster Zone|Spell & Trap Zone|Pendulum Zone))?"   # "this card", "this card in your hand" ...
AS_MATERIAL_NEED = re.compile(r"\bthis (?:face-up )?card is (?:sent to the (?:GY|Graveyard) |used |banished |detached )?as (?:a |an )?(?:(?P<k1>Fusion|Synchro|Xyz|Link|Ritual) Material\b|material for (?:an? |the )?(?:\"[^\"]+\" )?(?P<k2>Fusion|Synchro|Xyz|Link|Ritual) Summon)", re.I)   # "sent to the GY as Synchro Material"
REACT_SENT = re.compile(REACT_SUBJ + r" (?:is|was|has been) (?P<mid>[^:.;]{0,60}?)\bsent (?:from (?P<from>[^:.;,]{0,40}?) )?to (?:the|your) (?:GY|Graveyard)\b", re.I)   # "If this card is sent from the hand to the GY"
REACT_BANISHED = re.compile(REACT_SUBJ + r" (?:is|was|has been) (?P<mid>[^:.;]{0,60}?)\bbanished\b(?: from (?P<from>[^:.;,]{0,40}))?", re.I)   # "If this card is banished (from your GY)"
REACT_DISCARDED = re.compile(REACT_SUBJ + r" (?:is|was) (?P<mid>[^:.;]{0,60}?)\bdiscarded\b", re.I)   # "If this card is discarded"
OTHER_FIRST = re.compile(r"\b(?:destroyed|Tributed|banished|discarded|sent|returned|shuffled|detached)\b[^,]*?\band\s*$", re.I)   # "destroyed (by battle) and sent to the GY": that needs the OTHER thing ("banished, or sent" still counts)
EXTRA_ADDED = re.compile(r"\b(?:this (?:face-up )?card|an? (?:face-up )?(?:card|monster)(?:\(s\))?|cards?|monsters?)(?: (?:is|are))? added to your Extra Deck\b", re.I)   # "If this card / a card(s) is added to your Extra Deck"
REACT_ADDED = re.compile(REACT_SUBJ + r" is added (?:from (?P<from>your Deck|the Deck|the Main Deck|your GY|your Graveyard|the GY) )?to your hand(?P<rest>[^:.;]{0,40})", re.I)   # "If this card is added from your Deck to your hand (by a card effect)"
OTHER_NEEDS = (   # wording in a condition that needs the player to do something with OTHER cards: (pattern, label)
    (re.compile(r"\b(?:when|each time|if|whenever) you (?:Tribute Summon|Tribute Set)\b|\bTribut\w* for (?:a |the )?(?:successful )?Tribute Summon\b|\bTribute Summon a monster by Tributing this card\b", re.I), "Self = Tribute Summon"),   # Emperor Sem, Samsara Kaiser, Triple Star Trion
    (re.compile(r"\b(?:when|each time|if|whenever) you Ritual Summon\b|\bRitual Summoned using this card\b", re.I), "Self = Ritual Summon"),   # Djinn Releaser of Rituals
    (re.compile(r"\b(?:a|another) (?:face-up )?monster(?:\(s\))? you control(?:, except [^,]+,)? is destroyed by (?:a |your )?card effects?\b", re.I), "Self = Destroy (Field)"),   # Acrobatic Magician ("by battle or card effect" is not counted)
    (re.compile(r"\b(?:a|another) (?:monster|card)(?:\(s\))? is (?:sent|discarded) from (?:the|your) hand to (?:the|your) (?:GY|Graveyard)\b", re.I), "Self = Send to GY (Hand)"),   # Vanguard of the Underground Emperor
    (re.compile(r"\b(?:a|another) monster(?:\(s\))? is sent to your (?:GY|Graveyard)\b", re.I), "Self = Send to GY"),   # Agnimal Candle
)
EQUIPPED_TO_ME = re.compile(r"\bEquip Cards? equipped to (?:this card|it)\b", re.I)   # "gains 800 ATK for each Equip Card equipped to it": needs Equip Cards
REACT_DESTROYED = re.compile(REACT_SUBJ + r" (?:is|was|has been) destroyed by (?:a |your )?card effects?\b", re.I)   # "When this card is destroyed by a card effect (and sent to the GY)" ("by battle or card effect" is not counted)
BY_OPPONENT = re.compile(r"\bby (?:your )?opponent(?:'s)?\b|\bby an opponent's\b|\byour opponent (?:sends|banishes|discards)\b", re.I)   # only the opponent can do it: nothing the player can supply

def react_places(words, send):   # react_places(): "the hand or field" -> the matching label endings
    w = (words or "").lower()   # the place words ('' when the card names no place)
    out = []   # endings found
    if "hand" in w: out.append("Hand")   # from the hand
    if re.search(r"field|monster (?:card )?zone|spell (?:&|and) trap (?:card )?zone|pendulum zone", w): out.append("field" if send else "Field")   # from the field (the CSV spells it 'field' for Send to GY, 'Field' for Banish)
    if "extra deck" in w: out.append("Extra Deck")   # from the Extra Deck
    elif "deck" in w: out.append("Deck")   # from the main Deck
    if not send and re.search(r"\bgy\b|graveyard", w): out.append("GY")   # banished from the GY
    return out   # empty = no place named

def self_reactions(pieces):   # needs for what the player must do TO this card (only conditions are read)
    sb = []   # strictSupportedBy labels
    for cond, cost, effect in pieces:   # every (condition, cost, effect) piece
        if not cond: continue   # no condition: nothing reacts
        own_clause = lambda start: re.split(r",", cond[start:])[0]   # the clause a reaction sits in (up to the next comma)
        if EXTRA_ADDED.search(cond): sb.append("Self = Return to Extra Deck")   # "If a card(s) is added to your Extra Deck": needs cards going there
        mat = AS_MATERIAL_NEED.search(cond)   # "sent to the GY as Synchro Material": needs that Summon
        if mat and BY_OPPONENT.search(own_clause(mat.start())): mat = None   # (only the opponent can do it: nothing the player supplies)
        if mat: sb.append("Self = " + (mat.group("k1") or mat.group("k2")).capitalize() + " Summon")
        for m in REACT_ADDED.finditer(cond):   # "If this card is added from your Deck / GY to your hand": needs something that adds it
            where, rest = (m.group("from") or "").lower(), m.group("rest") or ""   # where it is added from, and the words after
            if BY_OPPONENT.search(rest): continue   # (only the opponent can do it)
            if "gy" in where or "graveyard" in where: sb.append("Self = add from GY to hand")   # from the GY
            elif where or re.search(r"\bexcept by drawing\b|\bby (?:a )?card effect\b", rest, re.I): sb.append("Self = add from deck to hand")   # from the Deck (or "except by drawing it")
        for rx, label in OTHER_NEEDS:   # Tribute / Ritual Summons, your monsters destroyed by card effects, cards sent from the hand ...
            for m in rx.finditer(cond):
                if not BY_OPPONENT.search(cond[m.start():m.end() + 40]): sb.append(label)   # (not when only the opponent does it)
        for m in REACT_DESTROYED.finditer(cond):   # "this card is destroyed by a card effect": needs the player to destroy it with a card effect
            if BY_OPPONENT.search(cond[m.start():m.end() + 40]): continue   # "... by your opponent's card effect": only the opponent can do it
            sb.append("Self = Destroy (Hand)" if (m.group("where") or "").lower() == "hand" else "Self = Destroy (Field)")   # destroyed in the hand, or (usually) on the field
        for rx, base, send in ((REACT_SENT, "Self = Send to GY", True), (REACT_BANISHED, "Self = Banish", False), (REACT_DISCARDED, "Self = Discard", None)):   # the three reactions
            for m in rx.finditer(cond):   # every "this card is ... sent / banished / discarded"
                if OTHER_FIRST.search(m.group("mid")): continue   # "destroyed and sent to the GY": needs the destruction, not this
                if BY_OPPONENT.search(own_clause(m.start())) or re.search(r"\bas a result\b", own_clause(m.start()), re.I): continue   # "sent to the GY by your opponent's card effect" / "... as a result" of something else: not the player's doing
                if mat and m.start() <= mat.start() <= m.end() + 40: continue   # the "as material" case is already covered by the Summon need
                if send is None:   # a discard has no place, but it may have to be done BY A CARD EFFECT (Dark World)
                    by_effect = re.search(r"\bby (?:a |your )?(?:card )?effect\b|\bby a card\b", cond[m.end():m.end() + 60], re.I)   # "discarded to the GY by a card effect"
                    sb.append(base + " (effect)" if by_effect else base); continue
                ends = react_places(m.groupdict().get("from") or m.group("where"), send)   # where it is sent / banished from
                by_effect = re.search(r"\bby (?:a |your )?(?:card )?effects?\b", own_clause(m.start()), re.I)   # "... sent from the hand to the GY by a card effect"
                labels = [base + " (" + e + ")" for e in ends] or [base]   # one label per place, or the bare label
                sb += [l + " (Effect)" if by_effect and mech_label(l + " (Effect)", "strictSupportedBy") else l for l in labels]   # the "(Effect)" version when the card says "by a card effect" and that label exists
    if EQUIPPED_TO_ME.search(CARD_TEXT[0]) and CARD_IS_MONSTER[0]: sb.append("Self = Equip")   # a monster that gets stronger with Equip Cards on it
    return [], [], keep_allowed(sb, "strictSupportedBy"), []   # the four columns (ss, gs, sb, gb): only strictSupportedBy


# ---------------- Tribute needs combined with the Tributed card's traits ----------------
def tribute_needs(pieces):                                           # "Tribute 1 Fiend monster" -> "Type = Fiend && Self = Tribute"
    sb = []                                                          # strictSupportedBy expressions
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        for mt in TRIBUTE_COST.finditer(cost):                       # "Tribute 1 Fiend monster", "Tribute 2 Level 4 or lower monsters"
            if TRIBUTE_SUMMON.search(cost): continue                 # "Tribute Summon" is not a Tribute cost
            obj = re.split(r"[;,.:]|\bthen\b|\bto (?:target|add|Special|activate|negate|destroy|draw|return|send|banish)\b", cost[mt.end():])[0]   # what is Tributed
            sb += with_label(trait_terms_in(obj), "Self = Tribute")   # only the combined forms (the bare label is already in generalSupportedBy)
    return [], [], keep_allowed(sb, "strictSupportedBy"), []   # the four columns (ss, gs, sb, gb): only strictSupportedBy, if "Self = Tribute" is allowed there


# A LOCK - "you cannot Special Summon monsters for the rest of this turn, except Machine monsters", "you cannot activate
# monster effects, except Plant monsters'", "you cannot activate non-EARTH monster effects" - pushes the deck towards X, so it
# SUPPORTS X: X goes to strictSupports wherever the lock is written (effect, cost or condition), and the lock is never a need.
LOCK_VERB = r"(?:(?:Normal (?:or|and/or|and) )?(?:Special |Pendulum |Fusion |Synchro |Xyz |Link |Ritual |Tribute )?Summon|activate)"   # what is locked: Summoning or activating
SUMMON_LOCK = re.compile(r"\bcannot\s+" + LOCK_VERB + r"\b(?P<mid>(?:(?!,\s*(?:also|then|and if you do)\b)[^.;])*?)\bexcept\s+"
                         r"(?P<x>[^.;]*?)(?=\s*,\s*(?:also|and|nor|or|then|the turn)\b|\s*[.;]|\s*$)", re.I)   # "cannot Special Summon / activate ..., except X" (stops at ", also ...")
NON_LOCK = re.compile(r"\bcannot\s+" + LOCK_VERB + r"\s+(?:the effects of\s+)?non-(?P<x>[A-Z][\w-]*)", re.I)   # "you cannot activate non-EARTH monster effects"
LOCKS = (SUMMON_LOCK, NON_LOCK)                                      # both kinds of lock

def lock_terms(x):   # lock_terms(): the trait expressions for the X of a lock
    x = re.sub(r"\bLevel/Rank/Link (\d+)", r"Level \1, Rank \1, or Link-\1", x)   # "Level/Rank/Link 2 monsters" -> Level 2, Rank 2 or Link-2
    terms = list(extract_terms(x))                                   # Types, Attributes, archetypes ... named in X
    for q in re.findall(r'"([^"]+?)s?"', x):                         # a quoted name with no archetype label may be a Series ("Adventurer Token")
        if not find_label("Archetype", q):
            lab = find_label("Series", q)
            if lab: terms.append(lab)
    return terms   # the expressions

MENTION_OWN = re.compile(r"\b(?:you control|in your (?:Main )?Monster Zones?|on your (?:side of the )?field)\b[^.;:]{0,20}?\b(?:that|which) mentions? (?P<x>\"[^\"]+\")"   # "1 monster in your Main Monster Zone that mentions "X""
                         r"|\b(?:that|which) mentions? (?P<y>\"[^\"]+\")[^.;:]{0,20}?\b(?:you control|in your (?:Main )?Monster Zones?)\b", re.I)   # "a monster that mentions "X" you control": the card needs that monster (it is not searched for)
FLOODGATE_TARGET = re.compile(r"\b(?:neither player can|(?:your opponent|each player|that player) cannot) (?:(?:Normal|Flip) (?:or|and/or|and) )?(?:Special |Normal |Tribute |Flip )?Summon\b(?:(?!,?\s*(?:except|also|and|then)\b)[^.;])*", re.I)   # FLOODGATE_TARGET: what a Summon floodgate stops ("Neither player can Special Summon Effect Monsters"), up to "except ..." / ", also" / the end of the sentence
AS_CONTINUOUS = re.compile(r"\b(?:treated )?as (?:a |an )?(?:face-up )?Continuous (?:Spells?|Traps?)(?: Cards?)?\b", re.I)   # "place it in your Spell & Trap Zone as a face-up Continuous Trap": no Continuous Trap is helped

# Clauses that PREVENT damage are not support for the monsters named in them.
# Example: Gatling Ogre says the opponent takes no damage from non-Fiend
# monsters' effects. That restriction must NOT produce Type != Fiend support.
NO_DAMAGE_FROM = re.compile(
    r"\b(?:your opponent|the opponent|opponent|you|(?:neither|both|each) players?)\s+"
    r"(?:take|takes|receive|receives|suffer|suffers)\s+no\s+"
    r"(?:battle\s+|effect\s+)?damage\s+from\b[^.;]*",
    re.I,
)

def effect_support_text(effect):
    """Remove harm-prevention clauses before harvesting supported traits.

    Preserve the original text for mechanic and other effect parsing.
    """
    return NO_DAMAGE_FROM.sub(" ", effect)


# Some harmful effects spare one Type, making that Type relatively better.
# "All non-Fairy monsters lose ATK" benefits Fairy monsters, not non-Fairies.
# Likewise, damage from non-Fiend monster effects being prevented favors Fiend
# effect-damage strategies. This is not generic support for every monster.
SPARED_TYPE_PENALTY = re.compile(
    r"\b(?:all|change all)\s+non[- ](?P<type1>[A-Za-z]+)(?:-Type)?\s+"
    r"(?:monsters?\b|(?:Attack Position )?monsters?\b)[^.;]*?"
    r"\b(?:lose|loses|change|changed|to Defense Position|cannot|can't)\b"
    r"|\b(?:takes?|receives?)\s+no\s+(?:effect\s+)?damage\s+from\s+"
    r"non[- ](?P<type2>[A-Za-z]+)(?:-Type)?\s+monsters?['’]?\s+effects?",
    re.I,
)

def spared_type_support(card_text):
    """Give positive support to Types spared by broad negative restrictions."""
    found = []
    for match in SPARED_TYPE_PENALTY.finditer(card_text):
        type_name = match.group('type1') or match.group('type2')
        label = find_label('Type', type_name)
        monster = find_label('Card Type', 'Monster')
        if label and monster:
            expr = f'{label} && {monster}'
            if expr not in found:
                found.append(expr)
    return found


def trait_terms(pieces):                                             # collect the trait expressions found in each piece
    # same logic as pipeline.card_traits, but returns the raw term lists so they can be merged with the mechanic expressions
    helps, needs = [], []                                            # term lists for "who is helped" and "what is needed"
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece
        for part in (cond, cost, effect):                            # a lock anywhere in the piece:
            for rx in LOCKS:
                for lock in rx.finditer(part):
                    if re.search(r"\bopponent\b[^.;,]{0,20}$", part[:lock.start()], re.I): continue   # (a lock on the OPPONENT supports nothing of ours)
                    helps.append(lock_terms(lock.group("x")))        # the X of the lock is supported
        cond, cost, effect = (NON_LOCK.sub(" ", SUMMON_LOCK.sub(" ", x)) for x in (cond, cost, effect))   # and the lock itself is neither help nor need
        cond, cost, effect = (AS_CONTINUOUS.sub(" ", x) for x in (cond, cost, effect))
        cond, cost, effect = (FLOODGATE_TARGET.sub(" ", x) for x in (cond, cost, effect))   # "Neither player can Special Summon Effect Monsters": those are the cards it STOPS (an "except X" after it stays: X is supported)   # "... as a Continuous Spell": the monster only becomes one (the "Monster in S/T Zone" matcher still sees these words)
        needs += [extract_terms(cond), extract_terms(cost)]          # conditions and costs are needs
        needs += [extract_terms(m.group(0)) for m in MENTION_OWN.finditer(effect)]   # a monster you already control that mentions X: a need (the whole phrase, so "a Fusion Monster that mentions ..." keeps "Fusion Monster") ...
        effect = MENTION_OWN.sub(" ", effect)                        # ... and not a support (removed before the effect's help terms below)
        needs += [extract_terms(m.group("obj")) for m in EQUIP_ONLY.finditer(effect)]   # "Equip only to a Warrior monster": needs Warriors (named cards are already in 'required cards')
        helps.append(extract_terms(effect_support_text(effect)))    # a restriction on damage is not trait support
        t = TARGET_RE.search(cost)                                   # a "target 1 ..." clause inside the cost
        if t: helps.append(extract_terms(t.group(0)))                # what is targeted is what gets helped
    return helps, needs                                              # both lists of term lists

GERUNDS = [(r"\bdiscarding\b", "discard"), (r"\bTributing\b", "Tribute"), (r"\bbanishing\b", "banish"), (r"\bsending\b", "send"),   # -ing word -> plain verb pairs, part 1
           (r"\bpaying\b", "pay"), (r"\bdetaching\b", "detach"), (r"\bshuffling\b", "shuffle"), (r"\brevealing\b", "reveal"),         # part 2
           (r"\breturning\b", "return"), (r"\bremoving\b", "remove")]            # "by discarding 1 card" -> "discard 1 card" (summon procedures)

PRONOUN_SUBJ = {"its": "it", "their": "they", "that target's": "that target", "that monster's": "that monster", "the attacking monster's": "the attacking monster",
                "that attacking monster's": "that attacking monster", "the targeted monster's": "the targeted monster", "that opponent's monster's": "that opponent's monster"}   # possessive -> subject
PRONOUN_RX = r"(its|their|that target's|that monster's|the attacking monster's|that attacking monster's|the targeted monster's|that opponent's monster's)"   # the possessives above (never "this card's")

def OLD_STAT_WORDS(t):   # rewrite old or unusual ATK/DEF wording into "X gains / loses N ATK", so the buff and drop matchers see it
    t = re.sub(r"\b[Ii]ncrease (?:the )?(ATK/DEF|ATK and DEF|ATK|DEF) of (.{1,80}?) by (\d+)(?: points)?", r"\2 gain \3 \1", t)   # "Increase the DEF of all monsters on your side of the field by 300 points"
    t = re.sub(r"\b[Ii]ncrease (\d+ (?:face-up )?monster)'s (ATK|DEF) by (\d+)(?: points)?", r"\1 gains \3 \2", t)   # "Increase 1 face-up monster's DEF by 700 points"
    if not re.search(r"\bequip this card to a monster on your side\b", CARD_TEXT[0], re.I):   # (Ring of Magnetism lowers YOUR equipped monster)
        t = re.sub(r"\b[Dd]ecrease (?:the )?(ATK/DEF|ATK and DEF|ATK|DEF) of (.{1,80}?) by (\d+)(?: points)?", r"\2 lose \3 \1", t)   # "Decrease the ATK of all monsters your opponent controls by 500"
    def sub(m):   # "its ATK is halved" -> "it loses half ATK" (left alone when the sentence is about THIS card or a monster we just Summoned)
        whole = CARD_TEXT[0] if m.group(0) in CARD_TEXT[0] else t   # look in the whole card text (a piece may be only this one sentence)
        at = whole.find(m.group(0)) if whole is not t else m.start()   # where the words sit in it
        start = max(whole.rfind(".", 0, at), whole.rfind(";", 0, at), whole.rfind(":", 0, at))   # where this sentence starts
        here = whole[start + 1:at]                                   # this sentence, up to the words
        before = whole[max(whole.rfind(".", 0, max(start, 0)), 0):start + 1] if whole[start:start + 1] == "." else ""   # the sentence before (only when this one starts a new sentence)
        if m.group(1).lower() in ("its", "their") and re.search(r"\bthis card\b|\bthis monster\b", here, re.I): return m.group(0)   # "If this card attacks, its ATK is halved": a drawback on this card, keep the words
        if not re.search(r"\b(?:target|select|opponent)", here, re.I) and re.search(r"\bthis card\b|\bSpecial Summon\b|\bNormal Summon this card\b", before, re.I): return m.group(0)   # "This card can attack directly. If it does, its ATK is halved." / "Special Summon 1 monster. That monster's ATK is halved."
        return PRONOUN_SUBJ[m.group(1).lower()] + " loses " + ("half " if "halved" in m.group(0).lower() else "all ") + m.group(2)
    t = re.sub(r"\b" + PRONOUN_RX + r" (?:original |current )?(ATK/DEF|ATK|DEF)(?: and DEF)? (?:is|are|becomes?) (?:halved|0)\b", sub, t, flags=re.I)   # "its ATK becomes 0", "that target's ATK is halved"
    t = re.sub(r"\bmake " + PRONOUN_RX + r" (ATK|DEF) 0\b", lambda m: PRONOUN_SUBJ[m.group(1).lower()] + " loses all " + m.group(2), t, flags=re.I)   # "make that target's ATK 0"
    t = re.sub(r"\bThe (ATK|DEF) of (that monster|the attacking monster|that attacking monster|the battling monster) becomes 0\b", r"\2 loses all \1", t, flags=re.I)   # "The ATK of that monster becomes 0"
    t = re.sub(r"\b[Ii]ncrease (your opponent's|your) Life Points by (\d+)(?: points)?", lambda m: ("your opponent gains " if "opponent" in m.group(1) else "you gain ") + m.group(2) + " LP", t)   # "increase your opponent's Life Points by 1000"
    return t   # the rewritten text

def norm_text(t, is_cost):                                           # tidy the wording so the matchers only see one form
    t = re.sub(r"\b(?:has|have) (its|their) (ATK/DEF|ATK and DEF|ATK|DEF) halved\b", r"loses half \1 \2", t, flags=re.I)   # "has its ATK halved" = "loses half its ATK" (so the lose-stat rule sees it)
    t = re.sub(r"\b(?:the )?(ATK/DEF|ATK|DEF)( and DEF)? (?:of (?:the|that|those)[^.;,]{0,30}? )?becomes? 0\b", r"loses all \1\2", t, flags=re.I) if not re.search(r"\bthis card|\boriginal\b", t, re.I) else t   # "the attacking monster's ATK becomes 0" = it loses all its ATK (skipped when this card / original ATK is involved)
    t = re.sub(r"\bremoved from play\b", "banished", t, flags=re.I)                  # old wording for banished
    t = OLD_STAT_WORDS(t)                                            # "Increase the DEF of X by 300", "its ATK is halved", "make that target's ATK 0" -> the gain / lose forms
    t = re.sub(r"\bremov(?:e|es|ing) from play\b", "banish", t, flags=re.I)         # "remove from play" = banish
    if is_cost:                                                      # the verb forms are only changed in costs
        for rx, rep in GERUNDS: t = re.sub(rx, rep, t, flags=re.I)                     # gerunds only inside costs
    return t                                                         # the tidied text

PROCEDURE = re.compile(r"^(?P<head>[^.;]*?\b(?:Special )?Summoned\b[^.;]*?)\s+by\s+(?P<tail>(?:discarding|Tributing|banishing|sending|paying|detaching|returning|removing|shuffling|revealing)\b.*)$", re.I)   # summon procedure written in the effect

AS_MATERIAL = re.compile(r"^(?P<cost>(?:you can\s+)?(?:banish|Tribute|discard|send|return|remove)\b[^.;,]*?)\s+(?P<rest>as\s+(?:1 of the |an? |the )?(?:monsters?|materials?)\b.*)$", re.I)   # AS_MATERIAL: "banish this card from your GY as 1 of the monsters required..." (the card is used up as material)

PROC_COND = re.compile(r"^(?P<head>[^.;]*?\bSpecial Summoned\b[^.;]*?)\s+by\s+(?P<tail>(?:having|controlling)\b.*)$", re.I)   # PROC_COND: "Must be Special Summoned (from your hand) by having 3 DARK monsters in your GY" (the "by having/controlling ..." part is a requirement)

def move_procedure_cost(cond, cost, effect):                         # turn "Summoned by <doing something>" into a real cost
    # "This card can only be Special Summoned by Tributing 2 monsters" -> effect "...Special Summoned", cost "Tribute 2 monsters"
    # ("Special Summoned by having / controlling X" was already moved into the condition by move_having_condition())
    ma = AS_MATERIAL.match(effect)                                   # "<pay something> as material" written in the effect
    if ma and not cost.strip(): return cond, ma.group("cost"), ma.group("rest")   # the paying part is a cost, the "as material" part stays the effect
    mt = PROCEDURE.match(effect)                                     # does the effect contain "...Summoned by <verb-ing> ..."?
    if mt and not cost.strip():                                      # only when the piece has no cost of its own
        return cond, norm_text(mt.group("tail"), True), mt.group("head")   # the "by ..." part becomes the cost, the rest stays the effect
    return cond, cost, effect                                        # otherwise leave the piece unchanged

def carry_targets(pieces):                                           # let "it / them" in a later piece point at an earlier target
    # a piece with no cost that says "it / them / that ..." gets the last target clause seen earlier in the same card
    out, last = [], ""                                               # the rebuilt pieces, and the most recent target clause
    for cond, cost, effect in pieces:                                # every (condition, cost, effect) piece, in order
        t = TARGET_RE.search(cost)                                   # does this piece's cost name a target?
        if t: last = t.group(0)                                      # remember the newest target clause
        elif last and not cost.strip() and re.search(r"\b(?:it|them|that (?:monster|card)|those)\b", effect, re.I):   # no cost, but the effect says "it / them / that monster"
            cost = last                                              # only the target text, so no cost verbs are added
        out.append((cond, cost, effect))                             # store the piece (possibly with the borrowed target)
    return out                                                       # the rebuilt list

STAT_BLOCK = re.compile(r"(?:\s+as (?:an? )?(?:Effect |Normal |Tuner )?Monsters?)?\s*\((?=[^()]*/)[^()]*\bATK\s+[\d?]+[^()]*\bDEF\s+[\d?]+[^()]*\)")   # "(Warrior/LIGHT/Level 4/ATK 1000/DEF 1000)" = stats of a Token / Trap Monster being summoned (needs real numbers, so "lose 200 ATK/DEF" reminders stay)

# SELF_ONLY_PHRASES: wording that only changes or limits THIS card, so it supports nothing (removed before any search)
SELF_ONLY_PHRASES = re.compile(
    r"\b(?:treat|treats|treated)\b[^.;:]{0,40}?\bas (?:a |an )?(?:non-)?Tuners?\b(?: monsters?)?"   # "treat this card as a Tuner": only changes a monster's type, Tuners gain nothing
    r"|\b(?:is|are) (?:also )?treated as (?:a |an )?(?:non-)?Tuners?\b(?: monsters?)?"   # "this card is treated as a Tuner"
    r"|\byou can only (?:Special |Normal |Link |Synchro |Xyz |Fusion |Ritual |Pendulum )?Summon \"[^\"]+\"(?:\(s\))? once per turn\b",   # "You can only Link Summon "Akashic Magician(s)" once per turn": a rule about this card only
    re.I)

def strip_stat_blocks(pieces):                                       # delete the stat brackets of summoned Tokens / Trap Monsters
    # the stats of the monster being created are not support for anything, so remove them before any matcher or trait search
    # (the same goes for SELF_ONLY_PHRASES: "treat this card as a Tuner", "You can only Link Summon "X" once per turn")
    return [tuple(SELF_ONLY_PHRASES.sub(" ", STAT_BLOCK.sub("", x)) for x in piece) for piece in pieces]   # clean each of the three texts of every piece

SELF_UNAFFECTED = re.compile(r"^\s*(?:this (?:[\w-]+ )*?card (?:is|are) )?unaffected by\b[^;]*$", re.I)   # SELF_UNAFFECTED: a whole effect that only says "(This card is) unaffected by ..." (protection for this card alone)

def drop_self_unaffected(pieces):   # empty the effect of every piece that only protects this card
    # self-only protection gives nothing to anyone, so remove it before the trait search too (otherwise "Unaffected by "Hole" Normal Traps" leaks Hole / Normal Trap)
    return [(c, co, "" if SELF_UNAFFECTED.match(e) else e) for c, co, e in pieces]   # keep condition and cost, empty a self-only "unaffected by ..." effect

def move_having_condition(pieces):   # move "Special Summoned by having / controlling X" into the condition
    # "Must be Special Summoned by having X in your GY": the "by having / controlling X" part is a requirement, so it moves into the condition BEFORE the trait search
    out = []   # the rebuilt pieces
    for c, co, e in pieces:   # every (condition, cost, effect) piece
        mc = PROC_COND.match(e)   # "...Special Summoned by having / controlling <something>"?
        if mc and not co.strip(): c, e = ((c + ", " if c.strip() else "") + mc.group("tail")), mc.group("head")   # the "having" part joins the condition, the rest stays the effect
        out.append((c, co, e))   # store the piece (changed or not)
    return out   # the rebuilt list

LEAD_TRIB = re.compile(r"^(?P<cost>\s*(?:you can |you may )?Tribute\s+(?!Summon|Set\b|this card to)[^.;,]*?),\s*(?P<eff>(?:and if you do|then)\b.*)$", re.I | re.S)   # an effect that starts "Tribute 1 monster, and if you do, ..." (no colon): the Tribute is really the cost

def lead_tribute_cost(c, co, e):   # move a leading "Tribute X, and if you do / then ..." out of the effect into the cost
    if co: return c, co, e   # a cost is already known
    m = LEAD_TRIB.match(e)   # does the effect start with a Tribute clause?
    if not m or re.search(r"opponent", m.group("cost"), re.I): return c, co, e   # no, or it Tributes the opponent's cards
    return c, m.group("cost").strip(), m.group("eff").strip()   # the Tribute clause is the cost

# ---------------- Floodgates: rules that stop BOTH players (or the opponent) from doing something ----------------
# A floodgate is something the card does, so its label only goes in generalSupports. Each label has its own patterns;
# they only match general subjects ("Neither player", "Monsters", "Any card", "Your opponent") - never "you cannot ..."
# (a lock on yourself is handled as a lock) and never "this card cannot ..." (only about this card).
FLOODGATES = {
    "Floodgate = Search": r"\b(?:neither player can|(?:your opponent|either player) cannot) add\b[^.]*\bDeck\b|\bcards cannot be added from either player's (?:Main )?Deck\b",
    "Floodgate = Special Summon": r"\b(?:neither player can|(?:your opponent|each player|that player|the other player) (?:cannot|can only))(?:\s+(?:Normal|Flip)\s+(?:or|and/or|and)\s+|\s+)Special Summon(?! monsters from the GYs?\b| monsters from (?:the|either) (?:GY|Graveyard)\b)|(?:^|[.;:]\s+)(?:(?!\+)[^.;:]){0,40}?\bmonsters cannot be Special Summoned\b(?! from the (?:GY|Graveyard)\b)|\bneither player can Summon more than\b|\bturn player can only Special Summon\b",
    "Floodgate = Normal Summon": r"\b(?:neither player|your opponent|that player|any player)\b[^.]{0,20}\bcannot Normal Summon|\bMonster Cards cannot be Set\b|\bneither player can Summon more than\b",
    "Floodgate = Activation": r"\bneither player can activate\b|\b(?:your opponent|each player) can only activate 1\b|\bno (?:Trap|Spell)(?: Cards?)? can be activated\b|\bcards with that name, and their effects, cannot be used\b|\bcannot be played as long as\b",
    "Floodgate = Attack": r"(?:^|[.:;]\s+)(?:Any monster of the declared Type|Monsters of the declared Type|Monsters(?! (?:you|your opponent) control)(?:(?! you control| your opponent controls)[^.;]){0,50}?)\s*cannot (?:declare an )?attack|\bcan attack with only 1\b|\bonly 1 monster can attack\b|\bneither player can conduct\b[^.]*\bBattle Phase\b",   # general "Monsters ... cannot attack" sentences ("your opponent's monsters cannot attack this card" is protection)
    "Floodgate = Position": r"\bany monster that is (?:Normal |Flip |Special )?Summoned\b[^.]*\bchanged to (?:face-up )?(?:Attack|Defense) Position\b|\ball face-up monsters on the field are changed to\b|\bif a monster declares an attack, it is changed to\b|\bbattle positions of face-up monsters\b[^.]*\bcannot be changed\b",
    "Floodgate = Zones": r"\b(?:the selected|that selected|that chosen|that) (?:Main Monster )?zones? cannot be used\b|\bneither player can use the selected zones\b|\bmaximum number of cards that each player can have\b|\bcards cannot be placed on the field if\b|\bMonster Card Zones cannot be used\b",
    "Floodgate = Graveyard": r"\bcards cannot be sent from the Deck to the (?:Graveyard|GY)\b|\bcards on the field cannot be returned to the Deck\b|\bwould be returned (?:to the Deck from the Graveyard|from the field to the hand) are (?:removed from play|banished) instead\b|\bneither player can Special Summon monsters from the GYs?\b|\bmonsters cannot be Special Summoned from the (?:GY|Graveyard)\b",
    "Floodgate = Tribute": r"\bneither player can Tribute (?:cards|monsters|Summon)\b|\bmonsters your opponent (?:currently )?controls cannot be Tributed\b",
    "Floodgate = Hand": r"\b(?:each player|your opponent)(?:'s)? hand size limit\b|\bhand size limit of each player\b|\bno limit to the number of cards in players' hands\b|\b(?:both players|your opponent|each player) must (?:keep|play with) their hands? revealed\b|\bshow their opponent their hand\b|\bopponent cannot draw cards except\b",   # (your OWN hand revealed / hand size is not a floodgate)
    "Floodgate = Burn": r"\b(?:each time|whenever) (?:a player activates|a Spell Card is activated|a Trap Card is activated)\b[^.]*\b(?:takes?|inflict)\b[^.]*\bdamage\b|\binflict damage to both players\b",
    "Floodgate = Rules": r"\ball face-up monsters(?: on the field)? become that (?:Type|Attribute)\b|\bmonsters in the GYs become that Type\b|\bany battle damage a player takes becomes\b|\bboth players skip\b|\bDecks upside down\b",
}
FLOODGATES = {label: re.compile(rx, re.I) for label, rx in FLOODGATES.items()}   # compile each label's patterns once
# FLOOD_VETO: words right after the match that make it NOT a floodgate - it only protects one effect ("in response to this
# effect") or hits only the cards this card chose ("that monster's effects", "Monsters this card points to cannot attack")
FLOOD_VETO = {
    "Floodgate = Activation": re.compile(r"^[^.;]{0,60}?\b(?:in response|when (?:that|this)|that |those |its |their |the equipped|targeted|the effects of (?:the equipped|that|those))", re.I),
    "Floodgate = Attack": re.compile(r"^\s*(?:face-down|\"|the turn you activate)|this card points to|other than (?:this card|the targeted)|equipped monster's name", re.I),
}

# REPLACE_INSTEAD: "Any card sent to the GY is banished instead" - the card makes cards go somewhere else, so it supports that
REPLACE_INSTEAD = (
    (re.compile(r"\b(?:any|all|monster|Spell)\b[^.]{0,40}\b(?:sent to the (?:Graveyard|GY)|activated|returned[^.]{0,30})\b[^.]{0,30}\b(?:is|are|will be) (?:banished|removed from play) instead\b", re.I), "Self = Banish (Effect)"),   # Banisher of the Light, Dimensional Fissure
    (re.compile(r"\b(?:any|all)\b[^.]{0,40}\b(?:is|are) shuffled into the Deck instead\b", re.I), "Self = Shuffle into deck"),   # Grave Protector
)

# ---------------- The monster this card battled, and odd burn / reverse burn wording ----------------
BATTLED = re.compile(r"\b(?:a|any) (?:non[ -][\w-]+ )?monster (?:that (?:attacks|battles(?: with)?|battled(?: with)?|is attacked by) this card|attacked by this card|that this card destroys|attacking \"[^\"]+\")|\bthe (?:attacking|attacked|battling) monster\b|\b(?:that|the) monster (?:it|this card) (?:battled|battles)\b", re.I)   # BATTLED: "a monster that attacks this card", "the attacking monster"
BATTLE_CTX = re.compile(r"\bthis card (?:battles|battled|attacks|attacked|is attacked|destroys|was attacked)\b|\b(?:attacks|battles(?: with)?|battled) this card\b|\battacked by this card\b|\bthis (?:face-up |Defense Position )?card battled\b", re.I)   # the text is about this card's battle
BATTLE_HITS = (   # (pattern, label): something done to the monster this card battled
    (re.compile(r"\bdestroy (?:that monster|the attacking monster|that attacking monster|both monsters)\b|\bwill be destroyed\b", re.I), "Opponent = Destroy"),   # Ryu Kokki, Steel Scorpion, Zone Eater
    (re.compile(r"\b(?:banish|remove) (?:that monster|the attacking monster)(?: from play)?\b|\bthis card destroys\b[^.;]{0,60}\b(?:is|are) (?:removed from play|banished)\b", re.I), "Opponent = Banish"),   # Ally of Justice Unknown Crusher, D.D. Crazy Beast
    (re.compile(r"\breturns? (?:to (?:its owner's|the|their owner's) hand|both monsters to their owners' hands)\b", re.I), "Opponent = Bounce"),   # Kelbek, Mist Valley Soldier, Gundari
    (re.compile(r"\b(?:return|place|add) (?:the card|that monster) (?:on|to) the top of (?:your opponent's|the|their) Deck\b", re.I), "Opponent = Shuffle into deck"),   # Mystical Knight of Jackal, Legendary Jujitsu Master
)
OPP_TO_DECK = re.compile(r"\byour opponent returns \d+ random cards? from their hand to their Deck\b|\b(?:select|target) \d+ cards? (?:in|from) your opponent's (?:Graveyard|GY)\b[^;]*?\b(?:return (?:it|them) to the Deck|add them to your opponent's Deck)\b", re.I)   # Legacy Hunter, Foolish Return, Keldo
ODD_BURN = re.compile(r"\bthe (?:attacking )?player(?: who [^.;]{0,50}?)? takes? \d+ (?:points of )?damage\b|\bthe controller of the [^.;]{0,30}? takes \d+ (?:points of )?damage\b|\bhalve your opponent's LP\b|\b(?:is|are) also inflicted to your opponent\b", re.I)   # Atomic Firefly, Marshmallon, Mask of Dispel, Michion
REVERSE = re.compile(r"\byour opponent takes (?:the|that|any) (?:effect )?damage\b(?![^.;]{0,40}\bbattle\b)[^.;]{0,60}?\binstead\b|\b(?:effect )?damage you would take from an opponent's card effect is inflicted to your opponent instead\b|\byour opponent takes the effect damage\b", re.I)   # Barrel Behind the Door, Spell of Pain, Morphtronic Lantron

def battle_extras(pieces):   # the battled monster is destroyed / banished / bounced / put on the Deck; odd burn and reverse-burn wording
    ss, gs = [], []                                                  # strictSupports, generalSupports
    text = CARD_TEXT[0]                                              # the whole text: the battle and what happens to the monster are often in different sentences
    if CARD_IS_MONSTER[0] and BATTLE_CTX.search(text):               # a monster that talks about its own battles
        for rx, label in BATTLE_HITS:
            for m in rx.finditer(text):
                around = text[max(0, m.start() - 160):m.end() + 40]  # the sentence(s) around it
                if (BATTLE_CTX.search(around) or BATTLED.search(around)) and not re.search(r"\b(?:destroy|banish) this card\b|\bthis card (?:is|will be) (?:destroyed|banished)\b", text[max(0, m.start() - 20):m.end()], re.I):   # about the battled monster, not "destroy this card"
                    gs.append(label); break                          # one match per label is enough
    if OPP_TO_DECK.search(text): gs.append("Opponent = Shuffle into deck")   # the opponent's cards go back into their Deck
    if ODD_BURN.search(text): gs.append("Opponent = Burn")             # "the player who destroyed it takes 1000 damage"
    if REVERSE.search(text): gs.append("Opponent = Burn")              # damage you would take goes to the opponent instead: plain burn (Reverse Burn is only for turning the opponent's LP gain into damage)
    return keep_allowed(ss, "strictSupports"), keep_allowed(gs, "generalSupports"), [], []   # the four columns (ss, gs, sb, gb)


def floodgates(pieces):   # floodgate labels (generalSupports) and the "... instead" supports (strictSupports), read from the whole card text
    text = CARD_TEXT[0]                                              # the whole text: a floodgate is usually a sentence with no condition
    gs = []                                                          # the floodgates the text has
    for label, rx in FLOODGATES.items():
        for m in rx.finditer(text):                                  # every match of this label's patterns
            veto = FLOOD_VETO.get(label)                             # words that cancel it, if this label has any
            if veto and (veto.search(text[m.end():m.end() + 80]) or veto.search(text[max(0, m.start() - 40):m.end()])): continue   # only one effect or the chosen cards: not a floodgate
            gs.append(label); break                                  # one real match is enough
    ss = [label for rx, label in REPLACE_INSTEAD if rx.search(text)]      # "Any card sent to the GY is banished instead": supports banishing
    return keep_allowed(ss, "strictSupports"), keep_allowed(gs, "generalSupports"), [], []   # the four columns (ss, gs, sb, gb)


MATCHERS = (special_summon, add_to_hand, normal_tribute, tribute_needs, discard, send_to_gy, banish,   # every mechanic matcher, in the order
            resources, resources2, summon_types, destroy_bounce, lp_and_more, battle_negate, buffs, flip_coin_dice, self_reactions, floodgates, battle_extras)   # fill_card() runs them (the order decides the label order)

def fill_card(r):   # compute the four support columns for one card row
    CARD_TEXT[0] = r["card text"] or ""   # keep the whole card text for matchers that need more than one piece
    EQUIP_CARD[0] = bool(re.search(r"\bEquip only to\b", r["card text"] or ""))   # remember whether "it" in this card means the equipped monster
    CARD_IS_MONSTER[0] = (r.get("primary card type") or "") == "Monster"   # remember whether this card is a monster (a Spell / Trap never attacks itself)
    pieces0 = move_having_condition(drop_self_unaffected(strip_stat_blocks(parse(r))))                                            # split the card text into (condition, cost, effect) pieces
    pieces = [(norm_text(c, False), norm_text(co, True), norm_text(e, False)) for c, co, e in pieces0]   # wording normalised for the mechanic matchers
    pieces = [move_procedure_cost(c, co, e) for c, co, e in pieces]                                       # "Special Summoned by Tributing X" -> the Tributing is a cost
    pieces = [lead_tribute_cost(*p) for p in pieces]                                                      # "Tribute 1 monster, and if you do, ..." -> the Tribute is a cost
    pieces = carry_targets(pieces)                                                                        # bullets like "Destroy it" inherit the earlier target
    help_terms, need_terms = trait_terms(pieces0)                     # trait expressions for who is helped / what is needed
    ss, gs, sb, gb = [], [], [], []                                  # collected labels for the four columns
    for matcher in MATCHERS:                                         # every mechanic matcher, in order
        a, b, c, d = matcher(pieces)                                 # each one gives back the same four lists: ss, gs, sb, gb
        ss += a; gs += b; sb += c; gb += d                           # add them to the card's four columns
    # Negative non-Type targets are often monsters HARMED by the effect, not
    # monsters supported by it. Do not turn those targets into strictSupports.
    # Preserve unrelated support clauses (e.g. Splendid VENUS protects Spells/Traps).
    card_text = (r.get("card text") or "").lower()
    if (re.search(r"\bnon[- ](?:[a-z-]+)(?:-type)?\b", card_text)
            and re.search(r"\b(?:lose|loses|lost|takes? no damage|no damage|change all non-)\b", card_text)):
        cleaned = []
        for group in help_terms:
            good = [term for term in group if not re.search(r"\bType\s*!=", term, re.I)]
            # The "Monster" remainder is not meaningful support on its own.
            if any(re.search(r"\bType\s*!=", term, re.I) for term in group):
                good = [term for term in good if term != "Card Type = Monster"]
            if good:
                cleaned.append(good)
        help_terms = cleaned
    # Protection that explicitly excludes Effect Monsters benefits NON-EFFECT
    # monsters. The ordinary trait scanner can invert the exception and label
    # Effect Monsters themselves as the beneficiaries (Non-Spellcasting Area).
    if re.search(r"\b(?:except for|except)\s+(?:all\s+)?Effect Monsters\b", card_text) and re.search(r"\bunaffected by\b", card_text):
        help_terms = [[term for term in group if term != "Trait = Effect Monster"]
                      for group in help_terms]
        help_terms = [group for group in help_terms if group]
        help_terms.append(["Trait != Effect Monster && Card Type = Monster"])
    # Append genuine benefits from the spared Type, not the penalized Type.
    # Unrelated support effects already extracted above remain untouched.
    ss += spared_type_support(r.get("card text") or "")
    strict_supports = combine(help_terms + [[x] for x in ss])                    # who is helped: traits OR bare strict labels
    flat = [t for terms in need_terms for t in terms]                              # the plain trait terms found in costs/conditions
    drop = {t for t in flat if any(e.startswith(t + " && ") or e.startswith("(" + t + ") && ") for e in sb)}   # a trait already inside "trait && mechanic" is not repeated
    need_terms = [[t for t in terms if t not in drop] for terms in need_terms]   # remove those repeated traits from the plain need terms
    strict_by = combine(need_terms + [[x] for x in sb])                          # what is needed: traits OR combined need expressions
    # Revealing is Heart of the Underdog's effect, not an alternate requirement.
    if (r.get("name") or "").casefold() == "heart of the underdog":
        strict_by = "Trait = Normal Monster"
    general_supports = " || ".join(dict.fromkeys(gs))                              # single mechanics, joined with ||
    general_by = " || ".join(dict.fromkeys(gb))                                    # same for the needed mechanics
    return strict_supports, general_supports, strict_by, general_by   # the four column values, in file order



# ================================================================
# PART 3: assembly and the actual run
# ================================================================
COLS = ("strictSupports", "generalSupports", "strictSupportedBy", "generalSupportedBy")   # the four columns to fill

def known_labels():                                                  # build one pattern that matches any legal label
    # one compiled pattern holding every legal label, longest first (so "Self = Banish (GY) (Effect)" wins over "Self = Banish (GY)")
    labs = sorted(set(LABELS.values()) | {v[0] for v in MECH.values()}, key=len, reverse=True)   # all trait labels plus all mechanic labels, longest first
    return re.compile("|".join(re.escape(l) for l in labs), re.I)   # one big "either label" pattern, case-insensitive

def bad_tokens(expr, known):                                         # list whatever in `expr` is not a legal label
    # remove every legal label from the expression; whatever is left (besides operators and brackets) is not a legal label
    expr = re.sub(r" (?:<=|>=|!=) ", " = ", expr)                    # "Level <= 4" / "Trait != Tuner" are built from the "= " label
    rest = known.sub("\x00", expr)                                  # replace each label by a marker, in one pass
    rest = re.sub(r"[\x00()&|\s]+", " ", rest).strip()             # operators / brackets / markers are fine
    return [rest] if rest else []                                    # anything left over is an unknown label, otherwise the list is empty

def main():                                                          # read the master, fill every card, write the result
    with open(CARD_CSV, encoding="utf-8-sig", newline="") as f:      # read the master (utf-8, with or without BOM)
        reader = csv.DictReader(f)                                   # rows become dictionaries keyed by column name
        fields = list(reader.fieldnames)                             # keep the original column order
        cards = list(reader)                                         # load every card row into memory
    for c in COLS:                                                   # make sure the four columns exist
        if c not in fields: fields.append(c)                         # add a missing column at the end
    known = known_labels()                                           # pattern of all legal labels
    filled, errors, unknown = collections.Counter(), [], []          # statistics
    for n, r in enumerate(cards, 1):                                 # every card
        try:                                                         # protect the run from a single bad card
            vals = fill_card(r)                                      # the four column values
        except Exception as e:                                       # one broken card must not stop the run
            errors.append((r.get("name", "?"), repr(e))); vals = ("", "", "", "")   # remember the error and leave the columns empty
        for col, v in zip(COLS, vals):                               # pair each column name with its new value
            r[col] = v                                               # write the value into the card row
            if v: filled[col] += 1                                   # count how many cards got something in this column
            for t in bad_tokens(v, known): unknown.append((r.get("name", "?"), col, t))   # record any text that is not a legal label
        if n % 2000 == 0: print(f"  ... {n} / {len(cards)} cards", flush=True)   # progress
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:  # write the filled copy (BOM so Excel shows the letters right)
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")   # writer using the original column order (plus any new columns)
        w.writeheader(); w.writerows(cards)                          # header line, then every card
    print(f"\nDone: {len(cards)} cards -> {OUT_CSV}")                # final summary: how many cards and where they went
    for col in COLS: print(f"  {col:20s} filled for {filled[col]:6d} cards ({100 * filled[col] / len(cards):.1f}%)")   # per-column fill statistics
    print("  cards with no label at all:", sum(1 for r in cards if not any(r[c] for c in COLS)))   # cards where all four columns are empty
    print("  errors:", len(errors), "| labels not found in the label CSVs:", len(unknown))   # how many cards crashed / had illegal labels
    for e in errors[:10]: print("   ERROR", e)                       # show up to ten errors
    for u in unknown[:10]: print("   UNKNOWN", u)                   # show up to ten unknown labels

if __name__ == "__main__":                                           # only run when the file is executed directly (not imported)
    main()                                                           # start the program
