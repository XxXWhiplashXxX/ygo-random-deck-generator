"""Tests for the website's server code (web/app.py and web/offline.py), called directly - no browser needed.
Skipped when FastAPI isn't installed (install it with: python -m pip install -r web/requirements.txt)."""
import importlib.util                                    # checks whether a package is installed
import unittest                                             # Python's built-in test tool

import helpers as H  # noqa: F401                        # not used by name: importing it adds program/ and web/ to the import path (tests/helpers.py)

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None  # True if FastAPI is installed
if HAS_FASTAPI:                                             # installed:
    import app as A                                      # web/app.py
    import offline as O                                  # web/offline.py
    from fastapi import HTTPException                       # the web framework's error


def generate(**changes) -> dict:                            # function: ask for a deck the way the page does
    """Call /api/generate the way the page does: web defaults + changes, without the *2_pct boxes the page lacks."""
    settings = A.web_defaults()                             # the website defaults
    settings.pop("deck2_pct")                               # the page has no Theme 2 share box
    settings.pop("support2_pct")                            # nor a Support 2 share box
    settings["seed"] = ""                                   # a blank seed
    settings.update(changes)                                # the changes
    return A.generate(A.GenerateInput(settings=settings))   # ask for the deck


def card_names(result: dict) -> list:                       # function: every card name in a reply
    return [c["name"] for c in result["main"]] + [c["name"] for c in result["extra"]]  # Main Deck names, then Extra Deck names


@unittest.skipUnless(HAS_FASTAPI, "FastAPI is not installed")  # only when FastAPI is installed
class GenerateTests(unittest.TestCase):                     # a group of /api/generate tests
    def test_theme_two_gets_its_share(self):                # test: Theme 2 gets what Theme 1 leaves
        """Theme 1 at 60% leaves 40% for Theme 2 (the page never sends deck2_pct)."""
        # 60% Blue-Eyes, the rest Dark Magician
        r = generate(deck1_label="Archetype = Blue-Eyes", deck2_label="Archetype = Dark Magician", deck1_pct=60, seed="77")
        self.assertEqual(r["settings"]["deck2_pct"], 40)    # Theme 2 got 40%
        self.assertEqual(r["categories"]["theme2"]["label"], "Archetype = Dark Magician")  # and was used

    def test_blank_seed_stays_blank(self):                  # test: a blank seed stays blank
        """With random Extra Deck ratios, the Seed box must stay blank so the next click makes a NEW deck."""
        a = generate(rand_extra_ratios=True)                # one deck
        b = generate(rand_extra_ratios=True)                # another
        self.assertEqual(a["settings"]["seed"], "")         # the Seed box stays blank
        self.assertNotEqual(a["seed"], b["seed"])           # so the two decks have different seeds

    def test_share_link_rebuilds_the_same_deck(self):       # test: a share link rebuilds the same deck
        """What the share link stores (rolled settings, every 'Randomize' off, the seed) rebuilds the same deck."""
        # a deck with lots of Random boxes ticked
        first = generate(rand_extra_ratios=True, rand_main_size=True, rand_theme_support=True, rand_deck12=True)
        shared = {k: (False if k.startswith("rand_") else v) for k, v in first["settings"].items()}  # what a share link saves: the rolled settings with every Random box off
        shared["seed"] = str(first["seed"])                 # and the seed
        again = A.generate(A.GenerateInput(settings=shared))  # build it again
        self.assertEqual(card_names(first), card_names(again))  # the same cards

    def test_bad_input_gets_a_clear_error(self):            # test: bad settings give clear errors
        cases = [({"deck_type_overall_pct": 90}, "total 100"),               # Theme + Support + Generic
                 ({"deck1_pct": "abc"}, "must be a number"),  # not a number
                 ({"deck1_label": "Self = Fusion Summon"}, "chosen from the list"),   # not a theme label
                 ({"level_sliders": True, "level_low_pct": 90}, "Level")]
        for changes, words in cases:                        # each bad case
            with self.subTest(changes=changes):             # (reported one by one)
                with self.assertRaises(HTTPException) as caught:  # it must raise an HTTP error
                    generate(**changes)                     # ask for the deck
                self.assertEqual(caught.exception.status_code, 400)  # 400 = 'your request was wrong'
                self.assertIn(words, caught.exception.detail)  # with those words

    def test_options_lists(self):                           # test: the option lists
        options = A.options()                               # what the page asks for first
        self.assertLess(len(options["theme_labels"]), len(options["labels"]))   # themes are a smaller list
        self.assertEqual(options["defaults"]["main_size"], 60)                  # the website's own default


@unittest.skipUnless(HAS_FASTAPI, "FastAPI is not installed")  # only when FastAPI is installed
class OfflineUpdaterTests(unittest.TestCase):               # a group of offline-updater tests
    class FakeRequest:                                      # class: a pretend web request
        """Just enough of a web request for require_local_request()."""
        def __init__(self, headers):                        # set up from some headers
            self.headers = headers                          # (the headers)
            self.url = type("Url", (), {"scheme": "http"})()  # (and an address scheme)

    def test_only_this_computer_may_use_the_updater(self):  # test: only this computer may use the updater
        allowed = [{"host": "127.0.0.1:5000"}, {"host": "localhost:5000", "origin": "http://localhost:5000"}]  # requests that must be allowed
        blocked = [{"host": "evil.com:5000", "origin": "http://evil.com:5000"},     # DNS rebinding
                   {"host": "127.0.0.1:5000", "origin": "http://evil.com"}]         # another website posting here
        for headers in allowed:                             # each allowed one
            O.require_local_request(self.FakeRequest(headers))                     # no error = allowed
        for headers in blocked:                             # each refused one
            with self.subTest(headers=headers):             # (reported one by one)
                with self.assertRaises(HTTPException):      # it must raise an HTTP error
                    O.require_local_request(self.FakeRequest(headers))  # (check it)

    def test_updater_panel_is_added_to_the_page(self):      # test: the offline page has the updater panel
        html = O.local_home().body                          # the offline page's HTML
        html = html.decode() if isinstance(html, bytes) else html  # as text
        self.assertIn("localUpdater", html)                 # the panel is there
        self.assertIn("offline.js", html)                   # and its script


if __name__ == "__main__":                                  # only when this file is run directly
    unittest.main()                                         # run the tests
