"""Tests for the support-column parser (program/ygo_fill_supports.py) as something other files can import."""
import csv                                               # reads the card file
import subprocess                                        # imports the parser in a fresh Python, like another file would
import sys                                                  # the Python running the tests
import unittest                                             # Python's built-in test tool

import helpers as H                                      # paths (tests/helpers.py)


class ParserImportTests(unittest.TestCase):                 # a group of parser tests
    def test_importing_runs_nothing(self):                  # test: importing the parser does nothing by itself
        """Importing the parser must not read the command line or load files (it used to start the whole parser)."""
        code = ("import sys; sys.argv = ['x', '--not-a-real-option']; import ygo_fill_supports as F; "  # a tiny script: fake a bad option, import the parser, print its table sizes
                "print(len(F.LABELS), len(F.MECH))")
        result = subprocess.run([sys.executable, "-c", code], cwd=H.ROOT / "program",  # run it in a fresh Python, inside program/
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)   # an unknown option would have crashed the old version
        self.assertEqual(result.stdout.split(), ["0", "0"])      # nothing loaded until setup() is called

    def test_setup_then_fill_a_card(self):                  # test: setup() then fill one card
        """After setup(), fill_card() works on its own and fills the same columns the update stores in cards.csv."""
        import ygo_fill_supports as F                       # the parser
        F.setup(H.P.DATA / "Stats.csv", H.P.DATA / "Mechanics.csv")  # load its label tables
        self.assertGreater(len(F.LABELS), 1000)             # they really loaded
        with open(H.P.DATA / "cards.csv", encoding="utf-8-sig", newline="") as f:  # open the card file
            card = next(r for r in csv.DictReader(f) if r["name"] == "Blackwing - Gale the Whirlwind")  # one support-heavy card
        values = F.fill_card(card)                          # fill its four support columns
        self.assertEqual(len(values), 4)                          # the four support columns
        self.assertTrue(any(values))                              # a support-heavy card gets something


if __name__ == "__main__":                                  # only when this file is run directly
    unittest.main()                                         # run the tests
