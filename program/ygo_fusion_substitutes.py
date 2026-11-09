# ygo_fusion_substitutes.py
# Fusion substitutes (King of the Swamp, Goddess with the Third Eye, Fusion Parasite ...) count as FUSION SUPPORT:
# every card whose text says it can stand in for a Fusion Material gets  Trait = Fusion Monster  in its
# strictSupports column, so a Fusion-themed deck or a Fusion support pick can choose it.
#   - most substitutes work for any Fusion Monster          ->  Trait = Fusion Monster
#   - some only for one archetype ('if you Fusion Summon a "HERO" monster this turn, ... a substitute')
#                                                            ->  (Archetype = HERO && Trait = Fusion Monster)
#   - a card that already has 'Trait = Fusion Monster' in its strictSupports is left as it is
# Runs after the support-column parser (ygo_fill_supports.py) on every update. Rewrites the file in place.
#
#   python ygo_fusion_substitutes.py cards_filled.csv

import re    # finds the substitute wording in the card text
import sys   # the file name typed after the script name

from ygo_io import read_table, write_table   # shared file reading / writing

CARDS_FILE = sys.argv[1] if len(sys.argv) > 1 else "cards.csv"  # the card file to change (in place)

# The ways card text says "this can stand in for a Fusion Material":
#   old cards:   'You can substitute this card for any 1 Fusion Material Monster' (also 'Fusion-Material')
#   newer cards: 'can be used as a substitute for any 1 Fusion Material whose name is specifically listed'
#                'this face-up card can be used as a substitute for 1 material mentioned by name'
SUBSTITUTE = re.compile(r"substitute (this card )?for (any )?(1|one) Fusion[- ]Material"  # 'substitute this card for any 1 Fusion Material' ...
                        r"|as a substitute for (any )?(1|one) (Fusion )?material", re.I)  # ...or 'as a substitute for 1 material'
ONLY_FOR = re.compile(r'Fusion Summon an? "([^"]+)" monster', re.I)    # an archetype limit: 'Fusion Summon a "HERO" monster'
FUSION = "Trait = Fusion Monster"                                       # the label a Fusion support card points at

columns, rows = read_table(CARDS_FILE)                      # read the cards

changed = []                                                # 'Name: label' for each card changed
for r in rows:                                              # each card
    text = r.get("card text") or ""                         # its text
    if not SUBSTITUTE.search(text):
        continue                                                        # not a Fusion substitute
    strict = (r.get("strictSupports") or "").strip()        # its strictSupports so far
    if FUSION.lower() in strict.lower():                    # it already supports Fusions:
        continue                                                        # already Fusion support
    limit = ONLY_FOR.search(text)                                       # only for one archetype's Fusions?
    label = f"(Archetype = {limit.group(1)} && {FUSION})" if limit else FUSION  # Fusion support (only that archetype's, if limited)
    r["strictSupports"] = f"{strict} || {label}" if strict else label   # 'A || B' = supports A or B
    changed.append(f"{r['name']}: {label}")                 # note it

write_table(CARDS_FILE, columns, rows)                      # save the cards
print(f"{len(changed)} Fusion substitutes now count as Fusion support")  # the summary
for line in changed:                                        # each card changed
    print("   " + line)                                     # print it
