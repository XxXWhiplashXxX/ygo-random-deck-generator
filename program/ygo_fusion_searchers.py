# ygo_fusion_searchers.py
# Run AFTER the support parser and Yugipedia corrections during a database update.
# These cards can mention/search Polymerization, but are NOT general-purpose
# Polymerization searchers in an unrelated deck. Preserve their other support tags.
import sys
from ygo_io import read_table, write_table

CARDS_FILE = sys.argv[1] if len(sys.argv) > 1 else "cards.csv"
# Frightfur Patchwork searches Edge Imp + Polymerization; require the Fluffal/Frightfur engine.
# Other cards have better uses in their own archetype; the earlier parser was
# treating "supports Polymerization" as universal utility.
RESTRICTIONS = {
    "Frightfur Patchwork": "(Archetype = Fluffal || Archetype = Frightfur)",
    "Amazoness Spiritualist": "Archetype = Amazoness",
    "Amazoness War Chief": "Archetype = Amazoness",
    "Tamtam the Melodious Diva": "Archetype = Melodious",
    "Lunalight Black Sheep": "Archetype = Lunalight",
    "Lunalight Kaleido Chick": "Archetype = Lunalight",
    "Lunalight Masquerade": "Archetype = Lunalight",
    "Lunalight Scarlet Tiger": "Archetype = Lunalight",
}

def apply(rows):
    touched = []
    for row in rows:
        name = row.get("name", "")
        if name not in RESTRICTIONS:
            continue
        require = RESTRICTIONS[name]
        # Preserve the originally detected needs as AND terms: an existing OR
        # must not bypass the new archetype requirement.
        prior = (row.get("strictSupportedBy") or "").strip()
        if prior != require and not prior.startswith("(" + require + ") &&"):
            row["strictSupportedBy"] = f"({require}) && ({prior})" if prior else require
        # Searching Polymerization is a conditional effect, not a separate
        # unconditional way to support a deck. Couple this specific support
        # branch to the required archetype using AND (never OR).
        branches = [part.strip() for part in (row.get("strictSupports") or "").split(" || ")]
        poly = "Archetype = Polymerization"
        if any(branch.lower() == poly.lower() for branch in branches):
            branches = [branch for branch in branches if branch.lower() != poly.lower()]
            if name == "Tamtam the Melodious Diva":
                # Tamtam's Polymerization search requires a Melodious monster.
                # Combine the two branches, rather than leaving the Melodious
                # monster as an independent alternative to Polymerization.
                melodic = "(Archetype = Melodious && Card Type = Monster)"
                branches = [branch for branch in branches if branch != melodic]
                branches.insert(0, f"({poly} && {melodic})")
            else:
                # Preserve unrelated effects, but never let the Poly search
                # count as support outside this card's archetype.
                branches.append(f"({poly} && {require})")
            row["strictSupports"] = " || ".join(branches)
        touched.append(name)
    return touched

if __name__ == "__main__":
    columns, rows = read_table(CARDS_FILE)
    changed = apply(rows)
    write_table(CARDS_FILE, columns, rows)
    print(f"Constrained {len(changed)} archetype Fusion searchers: {', '.join(changed)}")
