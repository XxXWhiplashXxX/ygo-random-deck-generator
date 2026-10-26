"""Tests for the website's server code (web/app.py and web/offline.py), called directly - no browser needed.
Skipped when FastAPI isn't installed (install it with: python -m pip install -r web/requirements.txt)."""
import importlib.util                                    # checks whether a package is installed
import unittest

import helpers as H                                      # import paths (tests/helpers.py)

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None
if HAS_FASTAPI:
    import app as A                                      # web/app.py
    import offline as O                                  # web/offline.py
    from fastapi import HTTPException


def generate(**changes) -> dict:
    """Call /api/generate the way the page does: web defaults + changes, without the *2_pct boxes the page lacks."""
    settings = A.web_defaults()
    settings.pop("deck2_pct")
    settings.pop("support2_pct")
    settings["seed"] = ""
    settings.update(changes)
    return A.generate(A.GenerateInput(settings=settings))


def card_names(result: dict) -> list:
    return [c["name"] for c in result["main"]] + [c["name"] for c in result["extra"]]


@unittest.skipUnless(HAS_FASTAPI, "FastAPI is not installed")
class GenerateTests(unittest.TestCase):
    def test_theme_two_gets_its_share(self):
        """Theme 1 at 60% leaves 40% for Theme 2 (the page never sends deck2_pct)."""
        r = generate(deck1_label="Archetype = Blue-Eyes", deck2_label="Archetype = Dark Magician", deck1_pct=60, seed="77")
        self.assertEqual(r["settings"]["deck2_pct"], 40)
        self.assertEqual(r["categories"]["theme2"]["label"], "Archetype = Dark Magician")

    def test_blank_seed_stays_blank(self):
        """With random Extra Deck ratios, the Seed box must stay blank so the next click makes a NEW deck."""
        a = generate(rand_extra_ratios=True)
        b = generate(rand_extra_ratios=True)
        self.assertEqual(a["settings"]["seed"], "")
        self.assertNotEqual(a["seed"], b["seed"])

    def test_share_link_rebuilds_the_same_deck(self):
        """What the share link stores (rolled settings, every 'Randomize' off, the seed) rebuilds the same deck."""
        first = generate(rand_extra_ratios=True, rand_main_size=True, rand_theme_support=True, rand_deck12=True)
        shared = {k: (False if k.startswith("rand_") else v) for k, v in first["settings"].items()}
        shared["seed"] = str(first["seed"])
        again = A.generate(A.GenerateInput(settings=shared))
        self.assertEqual(card_names(first), card_names(again))

    def test_bad_input_gets_a_clear_error(self):
        cases = [({"deck_type_overall_pct": 90}, "total 100"),               # Theme + Support + Generic
                 ({"deck1_pct": "abc"}, "must be a number"),
                 ({"deck1_label": "Self = Fusion Summon"}, "chosen from the list"),   # not a theme label
                 ({"level_sliders": True, "level_low_pct": 90}, "Level")]
        for changes, words in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(HTTPException) as caught:
                    generate(**changes)
                self.assertEqual(caught.exception.status_code, 400)
                self.assertIn(words, caught.exception.detail)

    def test_options_lists(self):
        options = A.options()
        self.assertLess(len(options["theme_labels"]), len(options["labels"]))   # themes are a smaller list
        self.assertEqual(options["defaults"]["main_size"], 60)                  # the website's own default


@unittest.skipUnless(HAS_FASTAPI, "FastAPI is not installed")
class OfflineUpdaterTests(unittest.TestCase):
    class FakeRequest:
        """Just enough of a web request for require_local_request()."""
        def __init__(self, headers):
            self.headers = headers
            self.url = type("Url", (), {"scheme": "http"})()

    def test_only_this_computer_may_use_the_updater(self):
        allowed = [{"host": "127.0.0.1:5000"}, {"host": "localhost:5000", "origin": "http://localhost:5000"}]
        blocked = [{"host": "evil.com:5000", "origin": "http://evil.com:5000"},     # DNS rebinding
                   {"host": "127.0.0.1:5000", "origin": "http://evil.com"}]         # another website posting here
        for headers in allowed:
            O.require_local_request(self.FakeRequest(headers))                     # no error = allowed
        for headers in blocked:
            with self.subTest(headers=headers):
                with self.assertRaises(HTTPException):
                    O.require_local_request(self.FakeRequest(headers))

    def test_updater_panel_is_added_to_the_page(self):
        html = O.local_home().body
        html = html.decode() if isinstance(html, bytes) else html
        self.assertIn("localUpdater", html)
        self.assertIn("offline.js", html)


if __name__ == "__main__":
    unittest.main()
