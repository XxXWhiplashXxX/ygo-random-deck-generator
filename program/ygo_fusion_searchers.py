# ygo_fusion_searchers.py
# Run AFTER the support parser and Yugipedia corrections during a database update.
# These cards can mention/search Polymerization, but are NOT general-purpose
# Polymerization searchers in an unrelated deck. Preserve their other support tags.
import sys                                                  # the file name typed after the script name
from ygo_io import read_table, write_table                  # shared CSV reading / writing

CARDS_FILE = sys.argv[1] if len(sys.argv) > 1 else "cards.csv"  # the card file to change (in place)
# Frightfur Patchwork searches Edge Imp + Polymerization; require the Fluffal/Frightfur engine.
# Other cards have better uses in their own archetype; the earlier parser was
# treating "supports Polymerization" as universal utility.
RESTRICTIONS = {                                            # card name -> the archetype it must have in the deck
    "Frightfur Patchwork": "(Archetype = Fluffal || Archetype = Frightfur)",  # (Fluffal / Frightfur)
    "Amazoness Spiritualist": "Archetype = Amazoness",      # (Amazoness)
    "Amazoness War Chief": "Archetype = Amazoness",         # (Amazoness)
    "Tamtam the Melodious Diva": "Archetype = Melodious",   # (Melodious)
    "Lunalight Black Sheep": "Archetype = Lunalight",       # (Lunalight)
    "Lunalight Kaleido Chick": "Archetype = Lunalight",     # (Lunalight)
    "Lunalight Masquerade": "Archetype = Lunalight",        # (Lunalight)
    "Lunalight Scarlet Tiger": "Archetype = Lunalight",     # (Lunalight)
}

def apply(rows):                                            # function: tie these cards' Polymerization support to their archetype
    touched = []                                            # names of the cards changed
    for row in rows:                                        # each card
        name = row.get("name", "")                          # its name
        if name not in RESTRICTIONS:                        # not one of these cards:
            continue
        require = RESTRICTIONS[name]                        # the archetype it needs
        # Preserve the originally detected needs as AND terms: an existing OR
        # must not bypass the new archetype requirement.
        prior = (row.get("strictSupportedBy") or "").strip()  # what it needed before
        if prior != require and not prior.startswith("(" + require + ") &&"):  # not already tied to the archetype:
            row["strictSupportedBy"] = f"({require}) && ({prior})" if prior else require  # the archetype AND its old needs
        # Searching Polymerization is a conditional effect, not a separate
        # unconditional way to support a deck. Couple this specific support
        # branch to the required archetype using AND (never OR).
        branches = [part.strip() for part in (row.get("strictSupports") or "").split(" || ")]  # its strictSupports branches
        poly = "Archetype = Polymerization"                 # the plain Polymerization branch
        if any(branch.lower() == poly.lower() for branch in branches):  # it has one:
            branches = [branch for branch in branches if branch.lower() != poly.lower()]  # take it out
            if name == "Tamtam the Melodious Diva":
                # Tamtam's Polymerization search requires a Melodious monster.
                # Combine the two branches, rather than leaving the Melodious
                # monster as an independent alternative to Polymerization.
                melodic = "(Archetype = Melodious && Card Type = Monster)"  # the Melodious monster branch
                branches = [branch for branch in branches if branch != melodic]  # take it out too
                branches.insert(0, f"({poly} && {melodic})")  # put back one joined branch first
            else:
                # Preserve unrelated effects, but never let the Poly search
                # count as support outside this card's archetype.
                branches.append(f"({poly} && {require})")   # add the Polymerization branch, tied to its archetype
            row["strictSupports"] = " || ".join(branches)   # write the branches back
        touched.append(name)                                # note the card
    return touched                                          # hand back the names changed

if __name__ == "__main__":                                  # only when this file is run directly
    columns, rows = read_table(CARDS_FILE)                  # read the cards
    changed = apply(rows)                                   # change them
    write_table(CARDS_FILE, columns, rows)                  # save the cards
    print(f"Constrained {len(changed)} archetype Fusion searchers: {', '.join(changed)}")  # the summary
