"""Tests for 'Base deck around a single card' (Advanced settings): the card is in at 3 copies (or the format's
limit), its archetype / supporters fill the deck, deck sizes still apply, and bad names give a clear error."""
import importlib.util                                    # checks whether FastAPI is installed (for the web test)
import unittest                                          # Python's built-in test tool
from collections import Counter                          # counts copies of each card

import helpers as H                                      # shared engine + paths (tests/helpers.py)
from ygo_builder import AnchorError                      # the 'can't build around that card' error

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None


def copies(cards, name: str) -> int:
    """How many copies of `name` a list of deck entries holds."""
    return Counter(e.info.name for e in cards)[name]


class AnchorTests(unittest.TestCase):
    def test_main_deck_card_is_in_at_three(self):
        """A Main Deck anchor is played at 3, and the deck keeps its size."""
        deck = H.make_deck(seed="5", anchor_card="Blackwing - Gale the Whirlwind", main_size="45")
        self.assertEqual(copies(deck.main, "Blackwing - Gale the Whirlwind"), 3)
        self.assertEqual(len(deck.main), 45)

    def test_its_archetype_fills_the_deck(self):
        """Plenty of the deck comes from the anchor's own archetype (here: Blackwing)."""
        deck = H.make_deck(seed="5", anchor_card="Blackwing - Gale the Whirlwind")
        blackwing = sum(1 for e in deck.main if "blackwing" in e.info.facts["archetype"])
        self.assertGreaterEqual(blackwing, 12)

    def test_extra_deck_card_goes_in_the_extra_deck(self):
        """An Extra Deck anchor is in the Extra Deck at 3 copies, and the Extra Deck size still applies."""
        deck = H.make_deck(seed="5", anchor_card="Stardust Dragon", extra_size="10")
        self.assertEqual(copies(deck.extra, "Stardust Dragon"), 3)
        self.assertEqual(copies(deck.main, "Stardust Dragon"), 0)
        self.assertEqual(len(deck.extra), 10)

    def test_highlander_lowers_it_to_one(self):
        """Highlander (and Limited cards) lower the 3 copies to the format's limit."""
        highlander = next((b for b in H.banlists() if b.lower().startswith("highlander")), None)
        if highlander is None:
            self.skipTest("no Highlander list in 'forbidden lists'")
        deck = H.make_deck(seed="5", anchor_card="Raigeki", banlist=highlander)
        self.assertEqual(copies(deck.main, "Raigeki"), 1)

    def test_same_seed_same_deck(self):
        """The seed still rebuilds the same deck in this mode."""
        a = H.make_deck(seed="77", anchor_card="Dark Magician")
        b = H.make_deck(seed="77", anchor_card="Dark Magician")
        self.assertEqual(H.names(a.main), H.names(b.main))
        self.assertEqual(H.names(a.extra), H.names(b.extra))

    def test_typed_in_any_capitals(self):
        """'dark magician' finds Dark Magician."""
        deck = H.make_deck(seed="5", anchor_card="dark magician")
        self.assertEqual(copies(deck.main, "Dark Magician"), 3)

    def test_unknown_card_is_a_clear_error(self):
        with self.assertRaises(AnchorError):
            H.make_deck(seed="5", anchor_card="Not A Real Card Name")

    def test_mode_off_is_unchanged(self):
        """A blank box is the normal build (the card protection list is cleared)."""
        builder = H.engine()[0]
        H.make_deck(seed="5", anchor_card="Dark Magician")
        H.make_deck(seed="5", anchor_card="")
        self.assertEqual(builder.anchor_names, set())


@unittest.skipUnless(HAS_FASTAPI, "FastAPI is not installed")
class AnchorWebTests(unittest.TestCase):
    def test_card_name_search(self):
        import app as A                                  # web/app.py
        names = A.card_names(q="dark magi")["names"]
        self.assertIn("Dark Magician", names)
        self.assertEqual(A.card_names(q="d")["names"], [])     # 1 letter: wait for more

    def test_bad_name_is_a_400(self):
        import app as A
        from fastapi import HTTPException
        settings = {**A.web_defaults(), "anchor_card": "Not A Real Card Name"}
        settings.pop("deck2_pct"); settings.pop("support2_pct")
        with self.assertRaises(HTTPException) as caught:
            A.generate(A.GenerateInput(settings=settings))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("No card named", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
