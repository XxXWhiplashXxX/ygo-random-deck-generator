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
        # Remove only unrestricted Polymerization support branches; all other
        # actions and archetype-specific support expressions stay intact.
        branches = [part.strip() for part in (row.get("strictSupports") or "").split(" || ")]
        row["strictSupports"] = " || ".join(
            (f"({require}) && Archetype = Polymerization" if b.lower() == "archetype = polymerization" else b)
            for b in branches if b
        )
        touched.append(name)
    return touched

if __name__ == "__main__":
    columns, rows = read_table(CARDS_FILE)
    changed = apply(rows)
    write_table(CARDS_FILE, columns, rows)
    print(f"Constrained {len(changed)} archetype Fusion searchers: {', '.join(changed)}")
