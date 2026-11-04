"""Tests for 'Base deck around a single card' (Advanced settings): Card 1 replaces Theme 1 and Card 2 replaces Theme 2.
A card is in at 3 copies (or the format's limit), its theme share is filled around it, the other theme still works,
'Random card' picks from the seed, and bad names give a clear error."""
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
    def test_card_1_is_in_at_three(self):
        """A Main Deck Card 1 is played at 3, and the deck keeps its size."""
        deck = H.make_deck(seed="5", anchor_card="Blackwing - Gale the Whirlwind", main_size="45")
        self.assertEqual(copies(deck.main, "Blackwing - Gale the Whirlwind"), 3)
        self.assertEqual(len(deck.main), 45)

    def test_its_archetype_fills_its_theme(self):
        """Card 1's theme slots come largely from its own archetype (here: Blackwing)."""
        deck = H.make_deck(seed="5", anchor_card="Blackwing - Gale the Whirlwind")
        blackwing = sum(1 for e in deck.main if "blackwing" in e.info.facts["archetype"])
        self.assertGreaterEqual(blackwing, 10)

    def test_card_1_with_a_normal_theme_2(self):
        """Card 1 + Theme 2: both the card and the Theme 2 archetype are in the deck."""
        deck = H.make_deck(seed="5", anchor_card="Dark Magician", deck1_pct=50, deck2_pct=50,
                           deck2_label="Archetype = blackwing")
        self.assertEqual(copies(deck.main, "Dark Magician"), 3)
        self.assertGreaterEqual(sum(1 for e in deck.main if "blackwing" in e.info.facts["archetype"]), 5)

    def test_card_2_with_no_share_is_still_in(self):
        """Card 2 with a 0% Theme 2 share: in at 3 copies, nothing built around it."""
        deck = H.make_deck(seed="5", deck1_label="Archetype = blackwing", anchor_card_2="Sangan", deck1_pct=100, deck2_pct=0)
        self.assertEqual(copies(deck.main, "Sangan"), 3)

    def test_two_cards(self):
        deck = H.make_deck(seed="5", anchor_card="Dark Magician", anchor_card_2="Sangan", deck1_pct=50, deck2_pct=50)
        self.assertEqual(copies(deck.main, "Dark Magician"), 3)
        self.assertEqual(copies(deck.main, "Sangan"), 3)

    def test_extra_deck_card_goes_in_the_extra_deck(self):
        """An Extra Deck card is in the Extra Deck at 3 copies, and the Extra Deck size still applies."""
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
        """The seed still rebuilds the same deck with cards."""
        a = H.make_deck(seed="77", anchor_card="Dark Magician")
        b = H.make_deck(seed="77", anchor_card="Dark Magician")
        self.assertEqual(H.names(a.main), H.names(b.main))
        self.assertEqual(H.names(a.extra), H.names(b.extra))

    def test_random_card_follows_the_seed(self):
        """'Random card': the same seed picks the same card, and the pick is in the deck."""
        a = H.make_deck(seed="31", rand_card_1=True)
        b = H.make_deck(seed="31", rand_card_1=True)
        name = a.debug["random_cards"]["anchor_card"]
        self.assertEqual(name, b.debug["random_cards"]["anchor_card"])
        self.assertGreater(copies(a.main, name) + copies(a.extra, name), 0)

    def test_two_random_cards_differ(self):
        deck = H.make_deck(seed="8", rand_card_1=True, rand_card_2=True, deck1_pct=50, deck2_pct=50)
        picks = deck.debug["random_cards"]
        self.assertNotEqual(picks["anchor_card"], picks["anchor_card_2"])

    def test_typed_in_any_capitals(self):
        """'dark magician' finds Dark Magician."""
        deck = H.make_deck(seed="5", anchor_card="dark magician")
        self.assertEqual(copies(deck.main, "Dark Magician"), 3)

    def test_unknown_card_is_a_clear_error(self):
        with self.assertRaises(AnchorError):
            H.make_deck(seed="5", anchor_card_2="Not A Real Card Name")

    def test_no_cards_clears_the_protection(self):
        """Blank card boxes are the normal build (the protected-card list is emptied)."""
        builder = H.engine()[0]
        H.make_deck(seed="5", anchor_card="Dark Magician")
        H.make_deck(seed="5")
        self.assertEqual(builder.anchor_names, set())


@unittest.skipUnless(HAS_FASTAPI, "FastAPI is not installed")
class AnchorWebTests(unittest.TestCase):
    def test_card_name_search(self):
        import app as A                                  # web/app.py
        names = A.card_names(q="Dark Magician")["names"]
        self.assertEqual(names[0], "Dark Magician")              # an exact match comes first
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
