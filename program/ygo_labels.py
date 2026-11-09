"""Builds the label lists for the page's Theme / Support boxes from the CSV files (nothing hard-coded)."""
import csv                                              # standard CSV reader
import io                                               # lets csv read text we already decoded
from pathlib import Path                                    # file paths


def _read_labels(path: Path) -> list[str]:
    """Every value in the 'Label' column of one CSV.
    Stats.csv is UTF-8 (so names like Live☆Twin survive); Mechanics.csv is the older cp1252.
    So try UTF-8 first ('utf-8-sig' also drops the invisible BOM mark), and fall back to cp1252."""
    raw = Path(path).read_bytes()                       # the file as raw bytes
    for enc in ("utf-8-sig", "cp1252"):                 # try each encoding in turn
        try:
            text = raw.decode(enc)                      # works only if the bytes fit this encoding
            break                                           # it worked: stop trying
        except UnicodeDecodeError:                      # not this one: try the next
            continue
    rows = list(csv.DictReader(io.StringIO(text, newline="")))   # read the decoded text as a CSV
    return [r["Label"].strip() for r in rows if (r.get("Label") or "").strip()]  # every non-empty Label value


def load_labels(label_csv: Path, mechanics_csv: Path) -> list[str]:  # function: every label for the Theme / Support boxes
    """['Random', 'Generic', then every theme label and mechanic label, A-Z].
    'Opponent = ...' labels get ' (opponent)' added for display; strip_display() removes it again."""
    found: dict = {}                                        # shown label -> True (a dict keeps the first-seen order and drops repeats)
    for p in (label_csv, mechanics_csv):                # both lists go in both dropdowns
        if p.is_file():                                     # the file exists:
            for lab in _read_labels(p):                     # each label in it
                shown = lab + " (opponent)" if lab.lower().startswith("opponent =") else lab  # opponent labels get ' (opponent)' on screen
                found[shown] = True                         # keep it
    alpha = sorted((l for l in found if l not in ("Random", "Generic")), key=str.lower)  # every other label, A-Z (ignoring capitals)
    return ["Random", "Generic"] + alpha                    # Random and Generic first


# The kinds of label a deck THEME can be. 'Random' picks from exactly these (plus 'Generic'), and the Theme 1 / Theme 2
# boxes offer exactly these, so the two can never disagree. (Support boxes still offer every label.)
THEME_PREFIXES = ("Archetype = ", "Series = ", "Alternative Win Condition = ", "Attribute = ", "Type = ", "Trait = ")


def is_theme_label(label: str) -> bool:                     # function: is this a label a theme can be?
    """True for 'Generic' and every label of a THEME_PREFIXES kind."""
    return label == "Generic" or label.startswith(THEME_PREFIXES)  # Generic, or one of the theme kinds


def theme_labels(labels: list[str]) -> list[str]:           # function: the Theme 1 / Theme 2 choices
    """The Theme 1 / Theme 2 choices: 'Random' first, then every theme label in the same (A-Z) order as `labels`."""
    return ["Random"] + [lab for lab in labels if is_theme_label(lab)]  # Random, then the theme labels


def strip_display(label: str) -> str | None:                # function: remove the on-screen ' (opponent)'
    """Undo the display-only ' (opponent)' suffix; blank -> None."""
    s = (label or "").strip()                               # the label, without spaces around it
    if s.endswith(" (opponent)"):                           # it has the suffix:
        s = s[: -len(" (opponent)")]                        # cut it off
    return s or None                                        # blank -> None


if __name__ == "__main__":                              # quick self-test
    here = Path(__file__).resolve().parent / "data"         # the data folder (program/data)
    L = load_labels(here / "Stats.csv", here / "Mechanics.csv")  # read both label files
    print(len(L), L[:4], strip_display("Opponent = Negation (opponent)"))  # how many, the first four, and a strip test
