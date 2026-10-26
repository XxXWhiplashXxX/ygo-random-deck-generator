YU-GI-OH! RANDOM DECK GENERATOR — QUICK START
===========================================

TWO WAYS TO USE THE GENERATOR
-----------------------------
ONLINE WEBSITE
  When the public site is available, visit its URL (listed in README.md).
  Pick Theme 1 and Theme 2, Main/Extra Deck sizes, and optionally paste a seed.
  Press Generate Deck. Open Advanced Settings for support, allocation, banlist,
  ratings and other options. Click or tap a card to see why it was picked.
  The website supports PNG, YDK and TXT exports. No Python needed.

OFFLINE VERSION (WINDOWS)
  Extract the whole project and install Python 3.10+ (3.12 recommended).
  Once, in PowerShell from the extracted folder:
    py -3 -m pip install -r web/requirements.txt
  Then double-click "Yu-Gi-Oh Randomizer.pyw". It opens the same page as the
  website in your browser, plus a "Check for New Cards" updater.
  Keep program/data, forbidden lists and web with the launcher. Generating
  decks does not need internet; updating cards and banlists does.

OLD DESKTOP WINDOW (TKINTER)
  Still included:  py -3 program/ygo_gui.py
  The reference below describes this window. The website has the same
  settings, except the Level sliders and presets.

WEB VERSION DEFAULTS  (set in web/app.py, WEB_DEFAULTS)
  Main Deck: 60       Theme: 50%     Support: 30%     Generic: 20%
  Monsters: 30        Spells: 20     Traps: 10
  Rating: 2 to 5      Auto select support categories: checked
  Seed: leave blank for random, or paste a known seed to recreate a deck
  using the same settings and card database.

For the GitHub front page, local web development and Render hosting, see README.md.

--------------------------------------------------------------------------

DETAILED DESKTOP REFERENCE
==========================

YU-GI-OH! RANDOM DECK GENERATOR
================================
The old desktop window: run  py -3 program/ygo_gui.py  from the project folder.
(Double-clicking "Yu-Gi-Oh Randomizer.pyw" opens the newer browser version instead.)


1. FOLDERS
----------
  Yu-Gi-Oh Randomizer.pyw     starts the offline browser version
  web/                        the browser page and its small web server
  decks/                      every deck you export goes here (.ydk for EDOPro, plus .txt and .csv)
  forbidden lists/            the banlists the Forbidden List dropdown reads (randomizer formats.lflist.conf)
                              (any other .lflist.conf you drop in here shows up in the dropdown too)
  program/                    the Python scripts - you don't need to open these
  program/data/               the card data and everything the updater downloads (see section 5)
  program/backups/            copies of cards.csv and Stats.csv from before each card update


2. USING THE WINDOW
-------------------
  Deck Theme 1 / 2      what the deck is about: an archetype, series, Type, Attribute, trait or win condition,
                        "Generic", or "Random" (the boxes list exactly what Random can pick - including
                        Extra-Deck-only themes like CXyz, on purpose: they make funny decks)
  Support 1 / 2         extra cards that help the theme (or leave Support off: the builder picks cards
                        that help at least 1/3 of the deck, plus staples)
  Sizes and targets     main deck 40-60, Extra Deck 0-15, how many monsters / spells / traps
  Era                   newest era allowed (Format Library's eras, set from each card's release date on
                        every update)
  Rating                lowest and highest card rating (0-5). Generic filler always stays inside it.
                        Theme and support cards PREFER it: inside the range first, then the closest
                        ratings below it, and above it only when nothing inside or below is left. So a
                        weak theme (Sphinx ...) still gets a deck, using its best cards first.
  Main Deck Monster Levels
                        "Use Level sliders" off: the usual mix (low Levels are favoured 4 / 2 / 1).
                        On: three sliders, Level 1-4 / 5-6 / 7+, that always add up to 100% of the Main
                        Deck's monsters (moving one shares the rest between the other two). They are soft
                        targets: the deck leans that way, theme cards follow them more gently (a Blue-Eyes
                        deck keeps its Level 8s), and the "enough low-Level monsters" rule asks for no more
                        than the Level 1-4 slider allows. The Extra Deck is not affected.
  Normal Monsters       unticked = no Normal (vanilla) Monsters, except ones that ARE the theme
                        (Dark Magician in a Dark Magician deck, the Exodia pieces in an Exodia deck)
  Extra Deck types      Fusion / Synchro / Xyz / Link on or off
  Build order           main deck first, Extra Deck first, or random
  Seed                  the same seed + the same settings = the same deck (blank = a new one each time)
  "Random" tick boxes   roll that setting fresh every time you press Generate
  Presets               save / load your settings (kept in program/data/randomizer settings.txt).
                        Presets made before the Level sliders open with the sliders off and Normal
                        Monsters on - exactly as they built decks before.
  Export                saves the deck in the decks folder

  FORBIDDEN LIST dropdown - which format the deck is built for:
    Random                a different list every deck (follows the seed)
    Unlimited             no banlist, 3 of anything
    Highlander            every card, 1 copy each
    Genesys (100/50/0/200 points)
                          Konami's points format: TCG cards only, no Link or Pendulum Monsters, and the
                          whole deck (main + Extra) must stay under the point cap. The preview shows
                          "Genesys points: 85 / 100".
    TCG / OCG / WCS / TCG Traditional / OCG Traditional
                          today's official lists (Traditional = nothing Forbidden, only Limited)
    Master Duel           Master Duel's list, and only cards that are in Master Duel
    Goat, Edison, TeleDAD, HAT ... (125 formats)
                          every dated TCG and OCG format from Format Library, each with its own banlist

  "Only cards released by the list's date"
                          with a dated list (Edison 2010.04.24 ...), cards that came out later are left out.
                          TCG lists use TCG release dates, OCG lists use OCG dates.


3. THE "CHECK FOR NEW CARDS" BUTTON
-----------------------------------
  Archetypes and series come from Yugipedia. A YGOPRODeck archetype is used only when Yugipedia says
  nothing about it and it doesn't duplicate anything (ygo_clean_labels.py, the update's last step: a name
  Yugipedia calls a series becomes a series, 'zombie' becomes Type = Zombie, 'fossil fusion' -> Fossil ...).

  It always checks YGOPRODeck first. If there are new cards it offers a full update (5-10 minutes):
  new cards, their archetypes and series, wiki labels, ratings from page views, release dates and the
  support columns. It offers the same full update when the PROGRAM has changed since your last update
  (new step scripts or a new Mechanics.csv - for example new parser rules), even with no new cards, so
  the changes reach your cards.csv. Your old cards.csv and Stats.csv are copied to program/backups first, and if any
  step fails nothing is changed.

  Then (even when the cards are already up to date) it refreshes the Forbidden Lists:
    every time      today's TCG / OCG / WCS / Traditional lists (EDOPro), the Master Duel list,
                    the Genesys points, and the staples for today's formats (popularity rankings)
    once, then never again
                    each Format Library format's banlist and staples. A format whose staples are
                    finished goes on program/data/complete_formats.txt and is skipped from then on.
                    (The newest Format Library format, while it's under a year old, is refreshed
                    every time instead.)

  Progress shows one line per download with the time left. It is safe to close the window at any
  time: finished downloads are kept (program/data/staples_cache) and the next run carries on.
  A format with 0 staples (Format Library sent no decks, or its page is broken) is never complete: it
  is checked again on every update, in case Format Library fixes it. It still works as a format meanwhile.

  To start something over from zero (close the program first):
    staples         delete program/data/format_staples.json, program/data/complete_formats.txt
                    and the program/data/staples_cache folder
    one format      delete its line from program/data/complete_formats.txt
    banlists        delete forbidden lists/randomizer formats.lflist.conf (every list is downloaded again)


4. HOW A DECK IS BUILT
----------------------
  1. Theme       cards from Deck Theme 1 and 2 (an archetype's own cards, then its related cards)
  2. Support     cards from Support 1 and 2, or - with Support off - cards that help the deck
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

  Every card in the preview shows WHY it was picked, e.g. [staple] :: format staple (in 88% of this
  format's decks), or "last check: replaced X (strict need)". The Debug lines at the bottom list every
  swap (last_check), every repair (last_check_repairs) and anything still useless (still_useless).
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
    randomizer settings.txt   your presets (delete = presets are gone)

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
