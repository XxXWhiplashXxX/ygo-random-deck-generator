"""
ygo_names.py  --  finds the REAL card for a name that may be old, misspelled or garbled.

Some name references in the data (the `required cards` column, `name = "..."` in `materials`,
alias lists) do not match the card names exactly. `NameResolver.find(text)` tries, in order:
  1. an exact match (ignoring case and spacing)
  2. the RENAMED table below (old / wrong names -> current names; copied from the old engine)
  3. repairing garbled characters (double-encoded text -> the real letters, e.g. 'Kuwagata α')
  4. treating '?' as "any one character" ('Yummy?Snatchy' -> 'Yummy☆Snatchy')
and returns the card's exact name, or None if nothing matches.
"""
from __future__ import annotations

import re

from ygo_data import norm

# old / incorrect name  ->  current name  (from the old randomizer's NAME_REMAP, plus the cases found in the CSV)
RENAMED = {
    "Defender of the Sea": "Sentinel of the Seas",
    "After Genocide": "After the Struggle", "Amazon Archer": "Amazoness Archer",
    "Armityle the Chaos Phantom": "Armityle the Chaos Phantasm", "Big Core": "B.E.S. Big Core",
    "Cliff the Trap Remover": "Dark Scorpion - Cliff the Trap Remover", "Dark Assassin": "Dark Assailant",
    "Dark Trap Hole": "Darkfall", "Forbidden Graveyard": "Silent Graveyard", "Frog the Jam": "Slime Toad",
    "Harpie's Brother": "Sky Scout", "HERO Flash!!": "Hero Flash!!", "Hero's Bond": "HERO's Bond",
    "Hidden Book of Spell": "Hidden Spellbook", "Judgment of the Pharaoh": "Judgment of Pharaoh",
    "Kinetic Soldier": "Cipher Soldier", "Luster Dragon": "Luster Dragon #2", "Marie the Fallen One": "Darklord Marie",
    "Metaphysical Regeneration": "Supernatural Regeneration", "Null and Void": "Muko",
    "Nurse Reficule the Fallen One": "Darklord Nurse Reficule", "Oscillo Hero #2": "Wattkid",
    "Pigeonholing Books of Spell": "Spellbook Organization", "Red-Eyes B. Chick": "Black Dragon's Chick",
    "Red-Eyes B. Dragon": "Red-Eyes Black Dragon", "Red-Moon Baby": "Vampire Baby", "Trial of Hell": "Trial of Nightmare",
    "Big Shield Guardna": "Big Shield Gardna", "Cemetery Bomb": "Cemetary Bomb", "Contract with the Aybss": "Contract with the Abyss",
    "Gradius's Option": "Gradius' Option", "Hundred-Eyes Dragon": "Hundred Eyes Dragon",
    "Necrolancer the Timelord": "Necrolancer the Time-lord", "Sephylon, the Ultimate Time Lord": "Sephylon, the Ultimate Timelord",
    "Winged Dragon,Guardian of the Fortress #1": "Winged Dragon, Guardian of the Fortress #1",
    "B. Skull Dragon": "Black Skull Dragon", "Kuwagata Alpha": "Kuwagata α",          # old names used in the GOAT banlist
}


def repair_text(s: str) -> str:
    """Undo 'double-encoded' text (UTF-8 bytes that were read as cp1252/latin-1), up to three layers deep."""
    for _ in range(3):
        for enc in ("cp1252", "latin-1"):
            try:
                t = s.encode(enc).decode("utf-8")
                break
            except Exception:
                t = s
        if t == s:
            break
        s = t
    return s


class NameResolver:
    def __init__(self, names: list[str]):
        self.exact = {norm(n): n for n in names}                         # normalised name -> exact name
        self.names = names

    def find(self, text: str) -> str | None:
        """Return the exact card name for `text`, or None."""
        t = text.strip()
        if norm(t) in self.exact:                                         # 1. exact
            return self.exact[norm(t)]
        renamed = {norm(k): v for k, v in RENAMED.items()}.get(norm(t))   # 2. renamed
        if renamed and norm(renamed) in self.exact:
            return self.exact[norm(renamed)]
        fixed = repair_text(t)                                            # 3. garbled characters
        if fixed != t and norm(fixed) in self.exact:
            return self.exact[norm(fixed)]
        if "?" in t:                                                      # 4. '?' stands for one unknown character
            rx = re.compile("^" + ".".join(re.escape(p) for p in norm(t).split("?")) + "$")
            hits = [n for k, n in self.exact.items() if rx.match(k)]
            if len(hits) == 1:                                            # only when it is unambiguous
                return hits[0]
        return None
