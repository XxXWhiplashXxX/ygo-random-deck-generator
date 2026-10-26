"""Tests for the deck engine: seeds, Random themes, theme lists, and Fusion Substitute = Polymerization."""
import unittest                                          # Python's built-in test tool

import helpers as H                                      # shared engine + paths (tests/helpers.py)
from ygo_final import counts_as, satisfied               # 'does this card count as that name' + requirement check
from ygo_builder import State, norm                      # an empty deck we can put cards into; name tidying
from ygo_labels import theme_labels                      # the Theme 1 / Theme 2 choices


class SeedTests(unittest.TestCase):
    def test_same_seed_same_deck(self):
        """The whole point of a seed: the same settings + seed always give the same deck."""
        a = H.make_deck(seed="12345")
        b = H.make_deck(seed="12345")
        self.assertEqual(H.names(a.main), H.names(b.main))
        self.assertEqual(H.names(a.extra), H.names(b.extra))

    def test_extra_size_zero_means_no_extra_deck(self):
        """Extra Deck size 0 must give an empty Extra Deck (wincon cards used to sneak in)."""
        deck = H.make_deck(seed="9", extra_size="0")
        self.assertEqual(len(deck.extra), 0)


class ThemeListTests(unittest.TestCase):
    def test_theme_list_has_only_theme_kinds(self):
        """Theme boxes offer what Random can pick: Archetype, Series, Type, Attribute, Trait, win conditions, Generic."""
        _, _, _, labels = H.engine()
        themes = theme_labels(labels)
        self.assertEqual(themes[0], "Random")                                    # Random first
        self.assertIn("Generic", themes)
        self.assertIn("Attribute = DARK", themes)                                # Attributes included
        self.assertFalse(any(t.startswith(("Self = ", "Opponent = ")) for t in themes))   # mechanics are support-only

    def test_random_theme_has_legal_cards_in_an_old_format(self):
        """In Goat Format a Random theme must have at least one card that format allows."""
        builder, _, _, _ = H.engine()
        goat = next(b for b in H.banlists() if b.lower().startswith("goat"))
        for seed in range(8):
            deck = H.make_deck(seed=str(seed), banlist=goat, date_pool=True)
            theme = deck.debug["themes"][0]
            req = H.S.make_request({**H.S.defaults(), "seed": str(seed), "banlist": goat}, list(H.banlists()))
            legal = {c.name for c in builder.allowed(req, rated=False)} | {c.name for c in builder.allowed(req, extra=True, rated=False)}
            self.assertTrue(builder.theme_has_cards(theme, legal), f"seed {seed}: {theme} has no legal cards")


class ThemeProtectionTests(unittest.TestCase):
    def test_theme_cards_are_not_swapped_for_outsiders_over_unmet_needs(self):
        """Step 4 ('needs not met') may only swap a theme card for another card of the SAME theme."""
        builder, _, _, _ = H.engine()
        for seed in range(12):
            deck = H.make_deck(seed=str(seed))
            theme = builder.theme_names                                        # this deck's theme cards
            for e in deck.main:
                if e.reason.startswith("swapped in for "):
                    gone = e.reason[len("swapped in for "):].split(" (")[0]
                    if gone in theme:
                        self.assertIn(e.info.name, theme, f"seed {seed}: {gone} -> {e.info.name}")

    def test_swaps_never_shrink_the_deck(self):
        """Protected theme swaps once left 7 of 300 test decks at 38-39 cards. A Main Deck is never under 40."""
        import playability_test                                              # its settings() rebuilds those exact decks
        builder, extra_builder, _, _ = H.engine()
        for seed in (11, 34, 135, 150, 197, 209, 289):
            deck = H.build_deck(builder, extra_builder, playability_test.settings(seed))
            self.assertGreaterEqual(len(deck.main), 40, f"seed {seed}")

    def test_charmers_keep_their_place(self):
        """The case that found the bug: a Goat Format Charmer deck used to lose all four Charmers."""
        goat = next(b for b in H.banlists() if b.lower().startswith("goat"))
        deck = H.make_deck(seed="13", banlist=goat, date_pool=True)
        if deck.debug["themes"] != ["Series = Cataclysmic Charmer"]:           # new card data can change what seed 13 rolls
            self.skipTest("seed 13 rolls a different theme with this card data")
        self.assertTrue(any("Charmer" in n for n in H.names(deck.main)))


class DeckSectionTests(unittest.TestCase):
    def test_aliases_never_cross_main_and_extra_deck(self):
        """NEX monsters (Marine Dolphin) are 'treated as' a Main Deck card (Aqua Dolphin) but live in the Extra Deck:
           no alias may link cards of different sections, or name-mixing could put an Extra Deck card in the Main Deck."""
        builder, _, _, _ = H.engine()
        for card in builder.infos:
            for name in card.aliases:
                other = builder.by_name[norm(name)]                            # every alias is a real card (KeyError if not)
                self.assertEqual(other.main_deck, card.main_deck, f"{card.name} -> {name}")

    def test_no_extra_deck_card_in_a_main_deck(self):
        """Neo-Spacian decks, where Marine Dolphin used to appear in the Main Deck."""
        for seed in range(4):
            deck = H.make_deck(seed=str(seed), deck1_label="Archetype = Neo-Spacian")
            self.assertEqual([n for n, e in zip(H.names(deck.main), deck.main) if not e.info.main_deck], [])


class FusionSubstituteTests(unittest.TestCase):
    def test_fusion_substitute_counts_as_polymerization(self):
        """Fusion Substitute's alias column says it is always treated as Polymerization."""
        builder, _, _, _ = H.engine()
        sub = builder.by_name["fusion substitute"]
        self.assertTrue(counts_as(sub, "Polymerization"))
        self.assertFalse(counts_as(builder.by_name["pot of greed"], "Polymerization"))

    def test_requirement_met_by_fusion_substitute(self):
        """A deck with only Fusion Substitute meets 'needs Polymerization'."""
        builder, _, _, _ = H.engine()
        deck = State()
        deck.add(builder.by_name["fusion substitute"], 1, "test", "test")
        self.assertTrue(satisfied(("name", "Polymerization"), deck))

    def test_both_cards_show_up_in_fusion_decks(self):
        """Over many Fusion-only decks, both Polymerization and Fusion Substitute get used."""
        seen = set()
        for seed in range(30):
            deck = H.make_deck(seed=str(seed), extra_fusion_pct=100, extra_synchro_pct=0, extra_xyz_pct=0,
                               extra_link_pct=0, allow_synchro=False, allow_xyz=False, allow_link=False)
            seen |= {n for n in H.names(deck.main) if n in ("Polymerization", "Fusion Substitute")}
        self.assertEqual(seen, {"Polymerization", "Fusion Substitute"})


if __name__ == "__main__":
    unittest.main()
