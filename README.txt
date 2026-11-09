YU-GI-OH! RANDOM DECK GENERATOR — DETAILED GUIDE
================================================
README.md covers getting started: the website, the offline version (Windows), running the site
locally and hosting it on Render. This file explains every setting, the updater buttons, how a deck
is built, and the data files.


1. FOLDERS
----------
  Yu-Gi-Oh Randomizer.pyw     starts the offline version (the website on your own computer, plus the updater)
  web/                        the page (web/static) and its small web server (app.py; offline.py adds the updater)
  forbidden lists/            the banlists the Banlist dropdown reads (randomizer formats.lflist.conf)
                              (any other .lflist.conf you drop in here shows up in the dropdown too)
  program/                    the deck engine, the card-data updater and the support-column parser
  program/data/               the card data and everything the updater downloads (see section 5)
  program/backups/            copies of cards.csv and Stats.csv from before each card update
  tests/                      automated tests (python -m unittest discover -s tests -v)


2. THE SETTINGS
---------------
  On the main page:
  Theme 1 / 2           what the deck is about: an archetype, series, Type, Attribute, trait or win condition,
                        "Generic", or "Random" (the boxes list exactly what Random can pick - including
                        Extra-Deck-only themes like CXyz, on purpose: they make funny decks)
  Theme 1 share         how the theme cards are split between Theme 1 and Theme 2
  Main / Extra Deck     Main Deck 40-60 cards, Extra Deck 0-15
  Fusion / Synchro / Xyz / Link
                        how the Extra Deck is split between the four types (they add up to 100%)
  Banlist               which format the deck is built for (see the list below)
  Seed                  the same seed + the same settings = the same deck (blank = a new one each time)

  In Advanced Settings:
  Presets               save / load your settings (kept in your browser)
  Build order           main deck first, Extra Deck first, or random
  Maximum era           newest era allowed (Format Library's eras, set from each card's release date on
                        every update)
  Restrict to format date
                        with a dated banlist (Edison 2010.04.24 ...), cards that came out later are left out.
                        TCG lists use TCG release dates, OCG lists use OCG dates.
  Base deck around a single card
                        Card 1 replaces Theme 1 and Card 2 replaces Theme 2 (a blank box = that theme works
                        as usual). The card is played at 3 copies (or the banlist's limit), and its theme's
                        share is filled with: what it needs, cards that support it, its archetype, its
                        Type / Attribute, then cards it supports. Slots one theme can't fill go to the other
                        theme first, then to staples and filler. "Random card" lets the seed pick the card.
  Allocation            how the Main Deck is split between Theme, Support and Generic (adds up to 100%)
  Support 1 / 2         categories of extra cards that help the theme. With "Auto select support
                        categories" unticked, the boxes are ignored and the builder picks support itself:
                        about two-thirds cards that support the theme (its own members included), and
                        one-third other cards that help the deck, plus staples
  Monsters / Spells / Traps
                        how many of each the Main Deck aims for
  Min / Max rating      lowest and highest card rating (0-5). Generic filler always stays inside it.
                        Theme and support cards PREFER it: inside the range first, then the closest
                        ratings below it, and above it only when nothing inside or below is left. So a
                        weak theme (Sphinx ...) still gets a deck, using its best cards first.
  Allow Normal Monsters ticked = anything goes. Unticked = no Normal (vanilla) Monsters, except the theme's
                        own (Dark Magician in a Dark Magician deck) and ones a card in the deck asks for
                        (the Normal Dinosaurs for Transcendosaurus)
  Use Level sliders     off: the usual mix (low Levels are favoured 4 / 2 / 1).
                        On: three sliders, Level 1-4 / 5-6 / 7+, that always add up to 100% of the Main
                        Deck's monsters (moving one shares the rest between the other two). They are soft
                        targets: the deck leans that way, theme cards follow them more gently (a Blue-Eyes
                        deck keeps its Level 8s), and the "enough low-Level monsters" rule asks for no more
                        than the Level 1-4 slider allows. The Extra Deck is not affected.
  Extra Deck theme/generic ratios match Main Deck
                        the Extra Deck uses the same Theme / Support / Generic split as the Main Deck
  "Randomize ..." boxes roll that setting fresh every time you press Generate Deck

  BANLIST dropdown - which format the deck is built for:
    Random                a different list every deck (follows the seed)
    Unlimited             no banlist, 3 of anything
    Highlander            every card, 1 copy each
    Genesys (100/50/0/200 points)
                          Konami's points format: TCG cards only, no Link or Pendulum Monsters, and the
                          whole deck (main + Extra) must stay under the point cap. The deck shows
                          "Genesys points: 85 / 100".
    TCG / OCG / WCS / TCG Traditional / OCG Traditional
                          today's official lists (Traditional = nothing Forbidden, only Limited), named
                          after their month, e.g. TCG (2026.10). They update themselves: when Konami
                          releases a new list, the next update picks it up. Older TCG and OCG lists stay
                          in the dropdown as past formats; WCS and Traditional only show the newest.
    Master Duel           Master Duel's list, and only cards that are in Master Duel
    Goat, Edison, TeleDAD, HAT ... (125 formats)
                          every dated TCG and OCG format from Format Library, each with its own banlist

  Exports: Export PNG (a picture of the deck), Export YDK (for EDOPro), Export TXT, Copy decklist,
  and Copy share link (a link that rebuilds this exact deck).


3. THE UPDATER BUTTONS (OFFLINE VERSION ONLY)
---------------------------------------------
  CHECK FOR NEW CARDS
  Archetypes and series come from Yugipedia. A YGOPRODeck archetype is used only when Yugipedia says
  nothing about it and it doesn't duplicate anything (ygo_clean_labels.py, the update's last step: a name
  Yugipedia calls a series becomes a series, 'zombie' becomes Type = Zombie, 'fossil fusion' -> Fossil ...).

  It always checks YGOPRODeck first. If there are new cards it offers a full update (5-10 minutes):
  new cards, their archetypes and series, wiki labels, ratings from page views, release dates and the
  support columns. It offers the same full update when the PROGRAM has changed since your last update
  (new step scripts or a new Mechanics.csv - for example new parser rules), even with no new cards, so
  the changes reach your cards.csv. Your old cards.csv and Stats.csv are copied to program/backups first, and if any
  step fails nothing is changed.

  If the cards are already up to date, "Refresh Banlists" downloads today's TCG / OCG / WCS / Traditional
  lists (EDOPro), the Master Duel list, the Genesys points and every Format Library format's banlist.

  CHECK STAPLES (its own button, because the first run can take an hour or more)
  Downloads which cards each format's decks played, for every Format Library format whose staples aren't
  finished (no time limit), and refreshes today's most popular cards (TCG, OCG, Master Duel).
    A format whose staples are finished goes on program/data/complete_formats.txt and is skipped from
    then on. (The newest Format Library format, while it's under a year old, is refreshed every time.)
    A format with 0 staples (Format Library sent no decks, or its page is broken) is never complete: it
    is checked again every time, in case Format Library fixes it. It still works as a format meanwhile.
  Progress shows one line per download with the time left. It is safe to stop at any time (close the
  console window): finished downloads are kept (program/data/staples_cache) and the next run carries on.
  Watch the Updater log, then reload the page to see new themes and banlists.

  To start something over from zero (close the program first):
    staples         delete program/data/format_staples.json, program/data/complete_formats.txt
                    and the program/data/staples_cache folder
    one format      delete its line from program/data/complete_formats.txt
    banlists        delete forbidden lists/randomizer formats.lflist.conf (every list is downloaded again)


4. HOW A DECK IS BUILT
----------------------
  1. Theme       cards from Theme 1 and 2 (an archetype's own cards, then its related cards)
  2. Support     cards from Support 1 and 2, or - with auto select off - cards that help the deck
  3. Staples     for a format with deck data, the cards that format's decks really played
                 (e.g. Mystical Space Typhoon in TeleDAD), at the copy count people ran - up to 1/5
                 of the deck
     A card that really belongs to the theme is protected: if its needs aren't met, it can only be swapped
     for another card of the SAME theme that works (or it stays). Only a card that truly can't work
     (step 8) may be replaced by an outside card, and only when no card of its own theme works there.
  4. Filler      cards that help many of the deck's cards, cards linked to them, then plain generic cards
  5. Required    cards the deck's cards need by name (Polymerization, a Ritual Spell, Fusion materials);
                 an Extra Deck card that is needed goes in the Extra Deck
  6. Rules       Ritual Monsters get a Ritual Spell (the one they name, if allowed), Pendulums come in
                 pairs, enough low-Level monsters
  7. Extra Deck  monsters the main deck can actually summon. Ones it can't are dropped. If fewer than 5
                 are left, a few filler cards are swapped for "enablers" (Tuners for a Synchro,
                 Polymerization + materials for a Fusion) so some Extra Deck monsters work.
  8. Last check  every card in the finished deck must WORK. A card is useless when:
                   - what it needs (its strict support) isn't in the deck,
                   - a card it requires is missing,
                   - it is an Extra Deck monster the main deck can't summon,
                   - it is a Ritual Monster with no Ritual Spell, or a Ritual Spell with no Ritual Monster,
                   - it is a floodgate that stops what a third of its own deck does.
                 A card missing only its required card is REPAIRED first (that card is added in place of
                 a filler card, up to 4 copies per deck). Anything else useless is swapped for a card of
                 the same kind that works here (cards that only worked because of it go too). The
                 earlier steps already check this before adding a card, so few swaps are needed.
  9. Checks      copy limits (3, the banlist, Highlander), the Genesys point cap, the release dates

  Every card shows WHY it was picked (click or tap it), e.g. format staple (in 88% of this format's
  decks), or "last check: replaced X (strict need)". For testing, the engine also records every swap
  (last_check), every repair (last_check_repairs) and anything still useless (still_useless) in the
  deck's debug notes; program/playability_test.py reports them.
  Cards that are "always treated as" another card count as that card: Fusion Substitute counts as
  Polymerization (a deck that needs Polymerization gets either one, equally likely, 3 copies between them),
  and Harpie Lady 1 counts as Harpie Lady.
  Fusion substitutes (King of the Swamp, Goddess with the Third Eye ...) count as Fusion support. The
  supports a card's Yugipedia page lists (e.g. Traptrix -> Trap Hole) are added too when the parser
  missed them, but only if the card's own text names that archetype, Type or Attribute.

  TESTING (program/playability_test.py, run from the program folder)
    python playability_test.py 300
        builds 300 decks (Random theme and support) and reports how many have a useless card, how many
        swaps were needed, the theme share, and the Level mix. Optional extras:
          levels=40/40/20   Level sliders on with these shares
          nonormals         Normal Monsters off
          rating=4-5        these rating limits
    The same seeds give the same decks, so a change can be tested before / after. It takes about 10
    minutes for 300 decks.


5. THE DATA FILES (program/data)
--------------------------------
  Needed - don't delete:
    cards.csv                 every card: stats, labels, support columns, era, rating, release dates
    Stats.csv                 every archetype and series
    Mechanics.csv             the mechanic labels the support parser uses
    game_mechanics_tree.csv   how mechanics relate (which ones count as which)
    card_archseries.csv       what Yugipedia said about each card, and when it was read (a card is read
                              again only after 30 days, as Yugipedia asks)

  Made by the button - safe to delete (they are downloaded again, see section 3):
    format_staples.json       how often each card was played in each format
    complete_formats.txt      formats whose staples are finished
    staples_cache/            downloaded deck files still in progress
    genesys_points.json       each card's Genesys points
    series_list.json          Yugipedia's list of series
    archetype_list.json       Yugipedia's list of archetypes
    ygo_db_version.json       the YGOPRODeck version you last updated to, and a fingerprint of the
                              program that built cards.csv (delete = the button offers a full card
                              update again)

  program/update_work/ is a temporary folder - it is wiped every run.


6. WHERE THE DATA COMES FROM
----------------------------
  YGOPRODeck (db.ygoprodeck.com)       every card, page views, release dates, alternate-art Ids
  Yugipedia (yugipedia.com)            archetypes, series and related cards (one request per second,
                                       with a User-Agent that gives a contact address, as Yugipedia asks)
  EDOPro (github.com/ProjectIgnis)     today's TCG / OCG / WCS / Traditional banlists
  Format Library (formatlibrary.com)   the old formats: dates, banlists, and what their decks played
  Yugioh Meta (yugiohmeta.com)         Genesys points; TCG and OCG card popularity
  Master Duel Meta (masterduelmeta.com) the Master Duel list and card popularity


7. CREDITS
----------
  Made by -whiplash- - the idea, the card database, the hand ratings, the support-column parser and
  every decision about how it should work.
  Built with help from Claude (Anthropic's AI assistant), which wrote much of the code alongside him.
