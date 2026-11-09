"""Tests for the deck engine: seeds, Random themes, theme lists, and Fusion Substitute = Polymerization."""
import unittest                                          # Python's built-in test tool

import helpers as H                                      # shared engine + paths (tests/helpers.py)
from ygo_final import counts_as, satisfied               # 'does this card count as that name' + requirement check
from ygo_builder import State, norm                      # an empty deck we can put cards into; name tidying
from ygo_labels import theme_labels                      # the Theme 1 / Theme 2 choices


class SeedTests(unittest.TestCase):                         # a group of seed tests
    def test_same_seed_same_deck(self):                     # test: same seed, same deck
        """The whole point of a seed: the same settings + seed always give the same deck."""
        a = H.make_deck(seed="12345")                       # one deck
        b = H.make_deck(seed="12345")                       # the same settings again
        self.assertEqual(H.names(a.main), H.names(b.main))  # same Main Deck
        self.assertEqual(H.names(a.extra), H.names(b.extra))  # same Extra Deck

    def test_extra_size_zero_means_no_extra_deck(self):     # test: Extra Deck size 0 means an empty Extra Deck
        """Extra Deck size 0 must give an empty Extra Deck (wincon cards used to sneak in)."""
        deck = H.make_deck(seed="9", extra_size="0")        # build one
        self.assertEqual(len(deck.extra), 0)                # no Extra Deck cards


class ThemeListTests(unittest.TestCase):                    # a group of theme-list tests
    def test_theme_list_has_only_theme_kinds(self):         # test: the Theme boxes only list theme kinds
        """Theme boxes offer what Random can pick: Archetype, Series, Type, Attribute, Trait, win conditions, Generic."""
        _, _, _, labels = H.engine()                        # the labels
        themes = theme_labels(labels)                       # the Theme choices
        self.assertEqual(themes[0], "Random")                                    # Random first
        self.assertIn("Generic", themes)                    # Generic is offered
        self.assertIn("Attribute = DARK", themes)                                # Attributes included
        self.assertFalse(any(t.startswith(("Self = ", "Opponent = ")) for t in themes))   # mechanics are support-only

    def test_random_theme_has_legal_cards_in_an_old_format(self):  # test: Random themes in Goat Format have legal cards
        """In Goat Format a Random theme must have at least one card that format allows."""  # (docstring)
        builder, _, _, _ = H.engine()                       # the Main Deck engine
        goat = next(b for b in H.banlists() if b.lower().startswith("goat"))  # the Goat Format banlist
        for seed in range(8):                               # eight seeds
            deck = H.make_deck(seed=str(seed), banlist=goat, date_pool=True)  # a Goat deck
            theme = deck.debug["themes"][0]                 # the theme Random picked
            req = H.S.make_request({**H.S.defaults(), "seed": str(seed), "banlist": goat}, list(H.banlists()))  # the same request again
            # every card Goat allows (Main and Extra Deck)
            legal = {c.name for c in builder.allowed(req, rated=False)} | {c.name for c in builder.allowed(req, extra=True, rated=False)}
            self.assertTrue(builder.theme_has_cards(theme, legal), f"seed {seed}: {theme} has no legal cards")  # the theme must have one of them


class ThemeProtectionTests(unittest.TestCase):              # a group of theme-protection tests
    def test_theme_cards_are_not_swapped_for_outsiders_over_unmet_needs(self):  # test: theme cards are only swapped for theme cards
        """Step 4 ('needs not met') may only swap a theme card for another card of the SAME theme."""
        builder, _, _, _ = H.engine()                       # the Main Deck engine
        for seed in range(12):                              # twelve seeds
            deck = H.make_deck(seed=str(seed))              # one deck
            theme = builder.theme_names                                        # this deck's theme cards
            for e in deck.main:                             # each card copy
                if e.reason.startswith("swapped in for "):  # it replaced another card:
                    gone = e.reason[len("swapped in for "):].split(" (")[0]  # the card it replaced
                    if gone in theme:                       # that was a theme card:
                        self.assertIn(e.info.name, theme, f"seed {seed}: {gone} -> {e.info.name}")  # so this one must be a theme card too

    def test_swaps_never_shrink_the_deck(self):             # test: swaps never leave a deck under 40 cards
        """Protected theme swaps once left 7 of 300 test decks at 38-39 cards. A Main Deck is never under 40."""
        import playability_test                                              # its settings() rebuilds those exact decks
        builder, extra_builder, _, _ = H.engine()           # the shared engines
        for seed in (11, 34, 135, 150, 197, 209, 289):      # the seeds that used to come up short
            deck = H.build_deck(builder, extra_builder, playability_test.settings(seed))  # rebuild that deck
            self.assertGreaterEqual(len(deck.main), 40, f"seed {seed}")  # at least 40 cards

    def test_charmers_keep_their_place(self):               # test: the Charmer deck keeps its Charmers
        """The case that found the bug: a Goat Format Charmer deck used to lose all four Charmers."""
        goat = next(b for b in H.banlists() if b.lower().startswith("goat"))  # the Goat Format banlist
        deck = H.make_deck(seed="13", banlist=goat, date_pool=True)  # the deck that found the bug
        if deck.debug["themes"] != ["Series = Cataclysmic Charmer"]:           # new card data can change what seed 13 rolls
            self.skipTest("seed 13 rolls a different theme with this card data")  # (skip the test)
        self.assertTrue(any("Charmer" in n for n in H.names(deck.main)))  # some Charmer must be in it


class DeckSectionTests(unittest.TestCase):                  # a group of Main / Extra Deck tests
    def test_aliases_never_cross_main_and_extra_deck(self):  # test: aliases stay inside one deck section
        """NEX monsters (Marine Dolphin) are 'treated as' a Main Deck card (Aqua Dolphin) but live in the Extra Deck:
           no alias may link cards of different sections, or name-mixing could put an Extra Deck card in the Main Deck."""
        builder, _, _, _ = H.engine()                       # the Main Deck engine
        for card in builder.infos:                          # every card
            for name in card.aliases:                       # each of its aliases
                other = builder.by_name[norm(name)]                            # every alias is a real card (KeyError if not)
                self.assertEqual(other.main_deck, card.main_deck, f"{card.name} -> {name}")  # same section

    def test_no_extra_deck_card_in_a_main_deck(self):       # test: no Extra Deck card ends up in a Main Deck
        """Neo-Spacian decks, where Marine Dolphin used to appear in the Main Deck."""
        for seed in range(4):                               # four seeds
            deck = H.make_deck(seed=str(seed), deck1_label="Archetype = Neo-Spacian")  # a Neo-Spacian deck
            self.assertEqual([n for n, e in zip(H.names(deck.main), deck.main) if not e.info.main_deck], [])  # no Main Deck copy may be an Extra Deck card


class FusionSubstituteTests(unittest.TestCase):             # a group of Fusion Substitute tests
    def test_fusion_substitute_counts_as_polymerization(self):  # test: Fusion Substitute counts as Polymerization
        """Fusion Substitute's alias column says it is always treated as Polymerization."""
        builder, _, _, _ = H.engine()                       # the Main Deck engine
        sub = builder.by_name["fusion substitute"]          # the card
        self.assertTrue(counts_as(sub, "Polymerization"))   # it counts as Polymerization
        self.assertFalse(counts_as(builder.by_name["pot of greed"], "Polymerization"))  # Pot of Greed doesn't

    def test_requirement_met_by_fusion_substitute(self):    # test: Fusion Substitute meets 'needs Polymerization'
        """A deck with only Fusion Substitute meets 'needs Polymerization'."""
        builder, _, _, _ = H.engine()                       # the Main Deck engine
        deck = State()                                      # an empty deck
        deck.add(builder.by_name["fusion substitute"], 1, "test", "test")  # with one Fusion Substitute
        self.assertTrue(satisfied(("name", "Polymerization"), deck))  # the requirement is met

    def test_both_cards_show_up_in_fusion_decks(self):      # test: both cards get used over many Fusion decks
        """Over many Fusion-only decks, both Polymerization and Fusion Substitute get used."""
        seen = set()                                        # which of the two were seen
        for seed in range(30):                              # thirty seeds
            deck = H.make_deck(seed=str(seed), extra_fusion_pct=100, extra_synchro_pct=0, extra_xyz_pct=0,  # a Fusion-only Extra Deck...
                               extra_link_pct=0, allow_synchro=False, allow_xyz=False, allow_link=False)  # ...(other kinds switched off)
            seen |= {n for n in H.names(deck.main) if n in ("Polymerization", "Fusion Substitute")}  # note which of the two it played
        self.assertEqual(seen, {"Polymerization", "Fusion Substitute"})  # both must have appeared


if __name__ == "__main__":                                  # only when this file is run directly
    unittest.main()                                         # run the tests
