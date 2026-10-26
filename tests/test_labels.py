"""Tests for the archetype / series labels: no duplicates of the kinds ygo_clean_labels.py removes."""
import subprocess                                        # runs ygo_clean_labels.py the way the updater does
import sys                                               # the Python that is running these tests
import unittest

import helpers as H                                      # paths (tests/helpers.py)
from ygo_io import read_json, read_table                 # shared file reading


def stats_labels() -> list:
    """Every Label in Stats.csv."""
    return [r["Label"] for r in read_table(H.P.DATA / "Stats.csv")[1]]


class LabelTests(unittest.TestCase):
    def test_cleanup_has_nothing_left_to_do(self):
        """Running the cleanup again must change nothing: the data is already clean."""
        result = subprocess.run([sys.executable, str(H.ROOT / "program" / "ygo_clean_labels.py"),
                                 "cards.csv", "Stats.csv", "--dry-run"],
                                cwd=H.P.DATA, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(" 0 changed", result.stdout, result.stdout)

    def test_no_archetype_is_a_type_or_attribute(self):
        """'Archetype = zombie' style duplicates of Types / Attributes must not exist."""
        labels = stats_labels()
        kinds = {l.split(" = ", 1)[1].lower() for l in labels if l.startswith(("Type = ", "Attribute = "))}
        archetypes = {l[12:].lower() for l in labels if l.startswith("Archetype = ")}
        wiki = {t["title"].lower() for t in read_json(H.P.DATA / "archetype_list.json")["query"]["categorymembers"]}
        # (an archetype Yugipedia itself lists, like 'Warrior (archetype)', is allowed)
        clash = {a for a in archetypes & kinds if a not in wiki and f"{a} (archetype)" not in wiki}
        self.assertEqual(clash, set())

    def test_no_label_is_listed_twice(self):
        """Each label appears in Stats.csv once (ignoring capitals)."""
        labels = [l.lower() for l in stats_labels()]
        self.assertEqual(len(labels), len(set(labels)))


if __name__ == "__main__":
    unittest.main()
