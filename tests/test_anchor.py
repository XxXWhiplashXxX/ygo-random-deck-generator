"""Tests for 'Base deck around a single card' (Advanced settings): Card 1 replaces Theme 1 and Card 2 replaces Theme 2.
A card is in at 3 copies (or the format's limit), its theme share is filled around it, the other theme still works,
'Random card' picks from the seed, and bad names give a clear error."""
import importlib.util                                    # checks whether FastAPI is installed (for the web test)
import unittest                                          # Python's built-in test tool
from collections import Counter                          # counts copies of each card

import helpers as H                                      # shared engine + paths (tests/helpers.py)
from ygo_builder import AnchorError                      # the 'can't build around that card' error

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None  # True if FastAPI is installed


def copies(cards, name: str) -> int:                        # function: copies of one card in a deck list
    """How many copies of `name` a list of deck entries holds."""
    return Counter(e.info.name for e in cards)[name]        # count them


class AnchorTests(unittest.TestCase):                       # a group of single-card tests
    def test_card_1_is_in_at_three(self):                   # test: Card 1 is played at 3 copies
        """A Main Deck Card 1 is played at 3, and the deck keeps its size."""
        deck = H.make_deck(seed="5", anchor_card="Blackwing - Gale the Whirlwind", main_size="45")  # build a 45-card deck around Gale
        self.assertEqual(copies(deck.main, "Blackwing - Gale the Whirlwind"), 3)  # 3 copies of Gale
        self.assertEqual(len(deck.main), 45)                # and still 45 cards

    def test_its_archetype_fills_its_theme(self):           # test: its archetype fills its theme
        """Card 1's theme slots come largely from its own archetype (here: Blackwing)."""
        deck = H.make_deck(seed="5", anchor_card="Blackwing - Gale the Whirlwind")  # the same deck at the default size
        blackwing = sum(1 for e in deck.main if "blackwing" in e.info.facts["archetype"])  # Blackwing copies in it
        self.assertGreaterEqual(blackwing, 10)              # at least 10

    def test_card_1_with_a_normal_theme_2(self):            # test: Card 1 and a normal Theme 2
        """Card 1 + Theme 2: both the card and the Theme 2 archetype are in the deck."""
        deck = H.make_deck(seed="5", anchor_card="Dark Magician", deck1_pct=50, deck2_pct=50,  # Dark Magician + a Blackwing Theme 2...
                           deck2_label="Archetype = blackwing")  # (half and half)
        self.assertEqual(copies(deck.main, "Dark Magician"), 3)  # Dark Magician at 3
        self.assertGreaterEqual(sum(1 for e in deck.main if "blackwing" in e.info.facts["archetype"]), 5)  # and at least 5 Blackwing copies

    def test_card_2_with_no_share_is_still_in(self):        # test: a card in a 0% slot is still in
        """Card 2 with a 0% Theme 2 share: in at 3 copies, nothing built around it."""
        # Sangan as Card 2 with no Theme 2 share
        deck = H.make_deck(seed="5", deck1_label="Archetype = blackwing", anchor_card_2="Sangan", deck1_pct=100, deck2_pct=0)
        self.assertEqual(copies(deck.main, "Sangan"), 3)    # still 3 copies

    def test_two_cards(self):                               # test: two cards
        # Dark Magician and Sangan, half and half
        deck = H.make_deck(seed="5", anchor_card="Dark Magician", anchor_card_2="Sangan", deck1_pct=50, deck2_pct=50)
        self.assertEqual(copies(deck.main, "Dark Magician"), 3)  # both at 3
        self.assertEqual(copies(deck.main, "Sangan"), 3)    # (Sangan)

    def test_extra_deck_card_goes_in_the_extra_deck(self):  # test: an Extra Deck card goes in the Extra Deck
        """An Extra Deck card is in the Extra Deck at 3 copies, and the Extra Deck size still applies."""
        deck = H.make_deck(seed="5", anchor_card="Stardust Dragon", extra_size="10")  # Stardust Dragon as Card 1, Extra Deck size 10
        self.assertEqual(copies(deck.extra, "Stardust Dragon"), 3)  # 3 copies in the Extra Deck
        self.assertEqual(copies(deck.main, "Stardust Dragon"), 0)  # none in the Main Deck
        self.assertEqual(len(deck.extra), 10)               # the Extra Deck is still 10

    def test_highlander_lowers_it_to_one(self):             # test: Highlander lowers it to 1
        """Highlander (and Limited cards) lower the 3 copies to the format's limit."""
        highlander = next((b for b in H.banlists() if b.lower().startswith("highlander")), None)  # the Highlander list (or None)
        if highlander is None:                              # not there:
            self.skipTest("no Highlander list in 'forbidden lists'")  # (skip the test)
        deck = H.make_deck(seed="5", anchor_card="Raigeki", banlist=highlander)  # Raigeki in Highlander
        self.assertEqual(copies(deck.main, "Raigeki"), 1)   # just 1 copy

    def test_same_seed_same_deck(self):                     # test: same seed, same deck with a card
        """The seed still rebuilds the same deck with cards."""
        a = H.make_deck(seed="77", anchor_card="Dark Magician")  # one deck
        b = H.make_deck(seed="77", anchor_card="Dark Magician")  # the same settings again
        self.assertEqual(H.names(a.main), H.names(b.main))  # same Main Deck
        self.assertEqual(H.names(a.extra), H.names(b.extra))  # same Extra Deck

    def test_random_card_follows_the_seed(self):            # test: 'Random card' follows the seed
        """'Random card': the same seed picks the same card, and the pick is in the deck."""
        a = H.make_deck(seed="31", rand_card_1=True)        # one deck with a random Card 1
        b = H.make_deck(seed="31", rand_card_1=True)        # the same again
        name = a.debug["random_cards"]["anchor_card"]       # the card it picked
        self.assertEqual(name, b.debug["random_cards"]["anchor_card"])  # the same pick both times
        self.assertGreater(copies(a.main, name) + copies(a.extra, name), 0)  # and it is in the deck

    def test_two_random_cards_differ(self):                 # test: two random cards are different cards
        deck = H.make_deck(seed="8", rand_card_1=True, rand_card_2=True, deck1_pct=50, deck2_pct=50)  # both boxes random
        picks = deck.debug["random_cards"]                  # the two picks
        self.assertNotEqual(picks["anchor_card"], picks["anchor_card_2"])  # different

    def test_typed_in_any_capitals(self):                   # test: names work in any capitals
        """'dark magician' finds Dark Magician."""
        deck = H.make_deck(seed="5", anchor_card="dark magician")  # typed in lower case
        self.assertEqual(copies(deck.main, "Dark Magician"), 3)  # still found

    def test_unknown_card_is_a_clear_error(self):           # test: an unknown name is a clear error
        with self.assertRaises(AnchorError):                # it must raise AnchorError
            H.make_deck(seed="5", anchor_card_2="Not A Real Card Name")  # (a made-up name)

    def test_no_cards_clears_the_protection(self):          # test: blank boxes clear the protected list
        """Blank card boxes are the normal build (the protected-card list is emptied)."""
        builder = H.engine()[0]                             # the Main Deck engine
        H.make_deck(seed="5", anchor_card="Dark Magician")  # a deck with a card...
        H.make_deck(seed="5")                               # ...then one without
        self.assertEqual(builder.anchor_names, set())       # the protected list is empty again


@unittest.skipUnless(HAS_FASTAPI, "FastAPI is not installed")  # only when FastAPI is installed
class AnchorWebTests(unittest.TestCase):                    # a group of website tests for the card boxes
    def test_card_name_search(self):                        # test: the card-name search
        import app as A                                  # web/app.py
        names = A.card_names(q="Dark Magician")["names"]    # suggestions for 'Dark Magician'
        self.assertEqual(names[0], "Dark Magician")              # an exact match comes first
        self.assertEqual(A.card_names(q="d")["names"], [])     # 1 letter: wait for more

    def test_bad_name_is_a_400(self):                       # test: a bad name gives a 400
        import app as A                                     # web/app.py
        from fastapi import HTTPException                   # the web framework's error
        settings = {**A.web_defaults(), "anchor_card": "Not A Real Card Name"}  # the website defaults, with a made-up card name
        settings.pop("deck2_pct"); settings.pop("support2_pct")  # the page doesn't send these two
        with self.assertRaises(HTTPException) as caught:    # it must raise an HTTP error
            A.generate(A.GenerateInput(settings=settings))  # (ask for a deck)
        self.assertEqual(caught.exception.status_code, 400)  # 400 = 'your request was wrong'
        self.assertIn("No card named", caught.exception.detail)  # with a clear message


if __name__ == "__main__":                                  # only when this file is run directly
    unittest.main()                                         # run the tests
