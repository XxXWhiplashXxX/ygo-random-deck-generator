"""Tests for the archetype / series labels: no duplicates of the kinds ygo_clean_labels.py removes."""
import subprocess                                        # runs ygo_clean_labels.py the way the updater does
import sys                                               # the Python that is running these tests
import unittest                                             # Python's built-in test tool

import helpers as H                                      # paths (tests/helpers.py)
from ygo_io import read_json, read_table                 # shared file reading


def stats_labels() -> list:                                 # function: every label in Stats.csv
    """Every Label in Stats.csv."""
    return [r["Label"] for r in read_table(H.P.DATA / "Stats.csv")[1]]  # the Label column


class LabelTests(unittest.TestCase):                        # a group of label tests
    def test_cleanup_has_nothing_left_to_do(self):          # test: the cleanup has nothing left to do
        """Running the cleanup again must change nothing: the data is already clean."""
        result = subprocess.run([sys.executable, str(H.ROOT / "program" / "ygo_clean_labels.py"),  # run the cleanup in report-only mode...
                                 "cards.csv", "Stats.csv", "--dry-run"],  # ...on the real data files
                                cwd=H.P.DATA, capture_output=True, text=True, timeout=300)  # (inside the data folder)
        self.assertEqual(result.returncode, 0, result.stderr)  # it ran without crashing
        self.assertIn(" 0 changed", result.stdout, result.stdout)  # and reported 0 changes

    def test_no_archetype_is_a_type_or_attribute(self):     # test: no archetype doubles as a Type / Attribute
        """'Archetype = zombie' style duplicates of Types / Attributes must not exist."""
        labels = stats_labels()                             # every label
        kinds = {l.split(" = ", 1)[1].lower() for l in labels if l.startswith(("Type = ", "Attribute = "))}  # every Type / Attribute name
        archetypes = {l[12:].lower() for l in labels if l.startswith("Archetype = ")}  # every archetype name
        # Yugipedia's archetype titles
        wiki = {t["title"].lower() for t in read_json(H.P.DATA / "archetype_list.json")["query"]["categorymembers"]}
        # (an archetype Yugipedia itself lists, like 'Warrior (archetype)', is allowed)
        clash = {a for a in archetypes & kinds if a not in wiki and f"{a} (archetype)" not in wiki}  # archetypes that are also a Type / Attribute (and not real ones)
        self.assertEqual(clash, set())                      # there must be none

    def test_no_label_is_listed_twice(self):                # test: no label appears twice
        """Each label appears in Stats.csv once (ignoring capitals)."""
        labels = [l.lower() for l in stats_labels()]        # every label, lower-case
        self.assertEqual(len(labels), len(set(labels)))     # no repeats


if __name__ == "__main__":                                  # only when this file is run directly
    unittest.main()                                         # run the tests
