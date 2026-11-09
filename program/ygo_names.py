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
from __future__ import annotations                          # lets type hints use the newer 'str | None' form

import re                                                   # regular expressions (text patterns)

from ygo_data import norm                                   # lower-case and tidy spaces

# old / incorrect name  ->  current name  (from the old randomizer's NAME_REMAP, plus the cases found in the CSV)
RENAMED = {                                                 # old / wrong name -> current name
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


def repair_text(s: str) -> str:                             # function: undo double-encoded text
    """Undo 'double-encoded' text (UTF-8 bytes that were read as cp1252/latin-1), up to three layers deep."""
    for _ in range(3):                                      # up to three layers
        for enc in ("cp1252", "latin-1"):                   # try both old encodings
            try:
                t = s.encode(enc).decode("utf-8")           # back to the real letters
                break
            except Exception:                               # couldn't:
                t = s                                       # keep it as it was
        if t == s:                                          # nothing changed:
            break
        s = t                                               # keep the repaired text and try another layer
    return s                                                # hand back the repaired text


class NameResolver:                                         # class: finds the real card for a name
    def __init__(self, names: list[str]):                   # set up from every card name
        self.exact = {norm(n): n for n in names}                         # normalised name -> exact name
        self.names = names                                  # every exact name, as a list
        self.renamed = {norm(k): v for k, v in RENAMED.items()}          # normalised old name -> current name (built once)

    def find(self, text: str) -> str | None:                # method: the exact card name for some text
        """Return the exact card name for `text`, or None."""
        t = text.strip()                                    # without spaces around it
        if norm(t) in self.exact:                                         # 1. exact
            return self.exact[norm(t)]                      # found it
        renamed = self.renamed.get(norm(t))                              # 2. renamed
        if renamed and norm(renamed) in self.exact:         # an old name of a real card
            return self.exact[norm(renamed)]                # found it
        fixed = repair_text(t)                                            # 3. garbled characters
        if fixed != t and norm(fixed) in self.exact:        # the repaired text is a real card
            return self.exact[norm(fixed)]                  # found it
        if "?" in t:                                                      # 4. '?' stands for one unknown character
            rx = re.compile("^" + ".".join(re.escape(p) for p in norm(t).split("?")) + "$")  # '?' -> 'any one character'
            hits = [n for k, n in self.exact.items() if rx.match(k)]   # every name it matches
            if len(hits) == 1:                                            # only when it is unambiguous
                return hits[0]                              # found it
        return None                                         # nothing matched
