"""Shared setup for the tests: import paths, and ONE deck engine reused by every test (it takes ~5 seconds to load).

Run every test from the project folder with:
    python -m unittest discover -s tests -v
"""
import sys                                               # lets us add folders to Python's import path
from functools import lru_cache                          # remembers a function's answer, so it only runs once
from pathlib import Path                                 # file paths

ROOT = Path(__file__).resolve().parents[1]               # the project folder (one above tests/)
for folder in (ROOT / "program", ROOT / "web"):          # so 'import ygo_builder' and 'import app' work
    if str(folder) not in sys.path:                         # not already there:
        sys.path.insert(0, str(folder))                     # put it first

import ygo_paths as P                                    # where data/ and 'forbidden lists/' are   (after the path fix)
import ygo_settings as S                                 # defaults + settings -> Request
from ygo_banlist import load_banlists                    # the banlist names
from ygo_builder import Builder                          # Main Deck engine
from ygo_data import read_csv, load_tree                 # card file + mechanics tree
from ygo_extra import ExtraBuilder                       # Extra Deck engine
from ygo_final import Rules, build_deck                  # requirement rules + the whole pipeline
from ygo_labels import load_labels                       # the category list


@lru_cache(maxsize=None)                                 # the first call builds it; every later call gets the same one
def engine():
    """(builder, extra_builder, rules, labels), built once for the whole test run."""
    labels = load_labels(P.DATA / "Stats.csv", P.DATA / "Mechanics.csv")  # the category list
    # load every card
    builder = Builder(read_csv(P.DATA / "cards.csv"), load_tree(P.DATA / "game_mechanics_tree.csv"), labels=labels)
    return builder, ExtraBuilder(builder), Rules(builder), labels  # (Main engine, Extra engine, rule reader, labels)


@lru_cache(maxsize=None)                                    # the first call reads the files; later calls reuse it
def banlists() -> tuple:                                    # function: every banlist name
    """Every banlist name (a tuple, so lru_cache can keep it)."""
    return tuple(load_banlists(P.FORBIDDEN))                # the names, as a tuple


def make_deck(**changes):                                   # function: build one deck with some settings changed
    """Build one deck from the engine defaults (ygo_settings.defaults()) plus `changes` (any settings key), and return it."""
    builder, extra_builder, _, _ = engine()                 # the shared engines
    values = S.defaults()                                   # every default
    values.update(changes)                                  # lay the changes over them
    return build_deck(builder, extra_builder, S.make_request(values, list(banlists())))  # build the deck


def names(cards) -> list:                                   # function: a list of card names
    """Deck entries -> their card names, in order (for comparing two decks)."""
    return [e.info.name for e in cards]                     # one name per copy
