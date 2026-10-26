# Yu-Gi-Oh! Random Deck Generator

A fan-made deck-building challenge that generates randomized **Yu-Gi-Oh! Main and Extra Decks**. Choose themes and support categories, generate a deck, then refine the results into a deck you want to play.

**Play online:** *Public website link coming soon — replace this line with your actual Render URL when the site is live.*
**Download and run locally:** Use **Code → Download ZIP** on GitHub, or download a packaged version from **Releases** when one is published.

> This is an unofficial fan project, not affiliated with or endorsed by Konami.

## What it does

- Builds around **Theme 1** and **Theme 2**, such as an archetype, Type, Attribute, or mechanic.
- Selects additional **Support** cards, including automatically chosen support based on deck synergies.
- Fills remaining slots with **Generic** cards based on the chosen settings.
- Supports a wide range of banlists and historical formats, era limits, card ratings, and Extra Deck rules.
- Displays the generation seed and why each card was chosen (click or tap a card).
- Offers **PNG deck-image**, **YDK decklist**, **TXT**, and **copy decklist** exports.

## Play in your browser

1. Choose **Theme 1** and **Theme 2**, and how the theme cards are shared between them.
2. Choose the **Main Deck** and **Extra Deck** sizes.
3. Optionally **paste a seed** to reproduce a generation with the same settings and card data. Leave it blank for a new random result.
4. Click **Generate Deck**.
5. Click or tap any card to see why it was picked. Use **Export PNG**, **Export YDK**, **Export TXT**, or **Copy decklist**.
6. **Copy share link** gives a link that rebuilds this exact deck for anyone (with the same card database).

Open **Advanced Settings** for presets (saved in your browser), allocations, Support categories, era, rating limits, Monster/Spell/Trap targets, Level sliders, and other rules.

The Theme boxes list exactly what **Random** can pick: archetypes, series, Types, Attributes, traits, win conditions and Generic. Random only picks themes with at least one card the chosen format allows.

### Website defaults

These live in one place: `WEB_DEFAULTS` in `web/app.py`.

| Setting | Default |
|---|---:|
| Main Deck | 60 cards |
| Theme / Support / Generic | 50% / 30% / 20% |
| Monsters / Spells / Traps | 30 / 20 / 10 |
| Minimum / maximum rating | 2 / 5 |
| Auto select support categories | on |
| Extra Deck types | 25% each |

A generated pool is a starting point for a deck-building challenge, not a guarantee of a competitive or tournament-legal deck; review it before playing.

## Offline version (Windows)

The offline version opens the **same page as the website** on your own computer, with an extra **Check for New Cards** updater. No account or hosting needed.

1. Extract the **entire ZIP**, keeping `program/`, `program/data/`, `forbidden lists/` and `web/` together with the launcher.
2. Install **Python 3.10 or newer** (3.12 recommended).
3. Install the packages once, from the extracted folder in PowerShell:
   `py -3 -m pip install -r web/requirements.txt`
   (If you have several Pythons, install into the one the launcher uses: it prefers 3.12, then the newest 3.x.)
4. Double-click **`Yu-Gi-Oh Randomizer.pyw`**, or run `py -3 web/offline.py`.
5. Your browser opens a `http://127.0.0.1:...` address. Close the console window to stop it.

Generating decks works without internet once the card data is present. Card pictures already saved in `web/image_cache/` work offline; new pictures and the updater need internet.

**Updating cards:** click **Check for New Cards** under the settings. If new cards or parser changes are found, click **Update Card Database** and confirm; otherwise you can **Refresh Banlists**. Watch the **Updater log**, then reload the page to see new themes and banlists.

**The old desktop window** (Tkinter) is still included: run `py -3 program/ygo_gui.py`. See **[README.txt](README.txt)** for its controls and a detailed guide to how decks are built and how the updater works.

## Run the website locally (for development)

```bash
python -m pip install -r web/requirements.txt
python web/app.py
```

Then open **http://127.0.0.1:8000**. This is the public site without the updater.

## Tests

From the project folder:

```bash
python -m unittest discover -s tests -v
```

This checks seeds, themes, Fusion Substitute, the labels and the website's server code (about 2 minutes; the website tests are skipped if FastAPI isn't installed). For a big check of deck quality, run `python playability_test.py 300` from `program/` (about 10 minutes).

## Hosting on Render

Use these Web Service settings (or deploy with `render.yaml` as a Blueprint):

- **Build Command:** `pip install -r web/requirements.txt`
- **Start Command:** `python web/start.py`
- **Root Directory:** leave empty (repository root)

`web/start.py` uses Render's `PORT` variable (10000 if missing). The deck engine loads in the background when the server starts, so the first visitor doesn't wait for it.

**Card images:** the site downloads each small card picture **once** to `web/image_cache/` (or the folder in the `IMAGE_CACHE_DIR` variable) and serves the saved copy afterwards, with at least 0.5 seconds between new downloads. Render's free plan **wipes local files on restart**, so before substantial public traffic use a persistent disk (set `IMAGE_CACHE_DIR` to its mount path) or durable image storage. YGOPRODeck asks apps to download and re-host images rather than repeatedly hotlinking them: https://api.ygoprodeck.com/api-guide/

**Errors:** a failed deck shows the visitor a short message; the full details are printed to the Render log.

## Files at a glance

| Path | Purpose |
|---|---|
| `Yu-Gi-Oh Randomizer.pyw` | Starts the offline version on Windows |
| `web/app.py` | The website: page, deck API, card pictures |
| `web/offline.py` | The offline version: the website plus the card updater (never host this) |
| `web/start.py` | Render start file |
| `web/static/` | The page: `index.html`, `app.js`, `style.css`, `offline.js` |
| `program/` | Deck engine, card-data updater, support parser, old desktop window |
| `program/data/` | Card database and settings |
| `forbidden lists/` | Banlist data |
| `tests/` | Automated tests (`python -m unittest discover -s tests -v`) |
| `README.txt` | Detailed guide: settings, formats, how decks are built, the updater |

**Card names and images belong to their respective rights holders.**
