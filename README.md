# Yu-Gi-Oh! Random Deck Generator

A fan-made deck-building challenge that generates randomized **Yu-Gi-Oh! Main and Extra Decks**. Choose themes and support categories, generate a deck, then refine the results into a deck you want to play.

**Play online:** *Public website link coming soon — replace this line with your actual Render URL when the site is live.*  
**Download and run locally:** Use **Code → Download ZIP** on GitHub to get the project, or download a packaged version from **Releases** when one is published.

> This is an unofficial fan project, not affiliated with or endorsed by Konami.

## What it does

- Builds around **Theme 1** and **Theme 2**, such as an archetype, Type, Attribute, or mechanic.
- Selects additional **Support** cards, including automatically chosen support based on deck synergies.
- Fills remaining slots with **Generic** cards based on the chosen settings.
- Supports a wide range of banlists and historical formats, era limits, card ratings, and Extra Deck rules.
- Displays the generation seed and details about the chosen themes, support categories, and card selections.
- Offers **PNG deck-image**, **YDK decklist**, **TXT**, and **copy decklist** exports in the browser version.

## Play in your browser

Once the public website is live, open the link above. You won't need to install Python to use the hosted site.

1. Choose **Theme 1** and **Theme 2**.
2. Choose the **Main Deck** and **Extra Deck** sizes.
3. Optionally **paste a seed** to reproduce a generation with the same settings and card data. Leave it blank for a new random result.
4. Click **Generate Deck**.
5. Review the card grid and selection log; use **Export PNG**, **Export YDK**, **Export TXT**, or **Copy decklist**.

Open **Advanced Settings** for allocations, Support categories, banlists, rating limits, Monster/Spell/Trap targets, and other rules. Automatic support selection is enabled by default.

### Browser defaults

| Setting | Default |
|---|---:|
| Main Deck | 60 cards |
| Theme / Support / Generic | 50% / 30% / 20% |
| Monsters / Spells / Traps | 30 / 20 / 10 |
| Minimum / maximum rating | 2 / 5 |

A generated pool is a starting point for a deck-building challenge, not a guarantee of a competitive or tournament-legal deck; review it before playing.

## Download the offline desktop program

You can use the original **Windows desktop interface** separately from the public website.

1. Download the repository ZIP using **Code → Download ZIP**, or download an offline package from **Releases** when available.
2. Extract the **entire ZIP**, keeping the `program/`, `program/data/`, and `forbidden lists/` folders together with the launcher.
3. Install **Python 3.10 or newer** (Python 3.12 is a good choice) if it is not already installed.
4. On Windows, double-click **`Yu-Gi-Oh Randomizer.pyw`** to open the desktop application. If `.pyw` is not associated with Python, run `py -3.12 "Yu-Gi-Oh Randomizer.pyw"` in PowerShell from the extracted folder.

Once its card data and dependencies are present, the **desktop deck generator can run without an internet connection**. Updating card data, downloading new banlists, and retrieving external card images (for the web interface) require internet access. For detailed desktop controls and updating instructions, see **[README.txt](README.txt)**.

## Run the web interface locally

This is an alternative way to use the browser interface on **your own computer**; it is not the hosted public website.

You need Python 3.10+ and the complete project folder. In a terminal opened at the repository root:

```bash
python -m pip install -r web/requirements.txt
python web/app.py
```

Then open **http://127.0.0.1:8000**. On Windows, if `python` selects an older interpreter, use `py -3.12` instead of `python`.

Card data is included in `program/data/`. Card thumbnails are fetched as needed and cached under `web/image_cache/`; card artwork may require internet access the first time.

## Hosting and contributing

This repository contains both interfaces. The **website uses the existing Python deck-generation engine**; the `web/` folder provides the browser UI and API, while `program/` holds the core logic and desktop UI.

For Render deployment, see **[WEB_HOSTING_README.md](WEB_HOSTING_README.md)**. The recommended Start Command is `python web/start.py`. Free Render hosting has **ephemeral local storage**, so a disk image cache alone isn't durable across restarts. Consider persistent image storage before substantial public use, and follow the [YGOPRODeck API/image usage guidance](https://api.ygoprodeck.com/api-guide/).

## Files at a glance

| Path | Purpose |
|---|---|
| `Yu-Gi-Oh Randomizer.pyw` | Launch the Windows desktop interface |
| `program/` | Generator engine, desktop GUI, and data utilities |
| `program/data/` | Local card database and settings |
| `forbidden lists/` | Banlist data |
| `web/` | Web server and browser UI |
| `README.txt` | Detailed offline desktop guide |
| `WEB_HOSTING_README.md` | Instructions for public hosting |

**Card names and images belong to their respective rights holders.**

### Offline application (same interface as website)

Install the dependencies using `py -3.12 -m pip install -r web/requirements.txt`,
then double-click **Yu-Gi-Oh Randomizer.pyw** on Windows or run
`python web/offline.py`. This launches the exact same browser UI on your own
computer, with an extra **Check for New Cards** updater.

The old Tkinter window is still included as
`Yu-Gi-Oh Randomizer (Legacy Tkinter).pyw`. See `OFFLINE_WEB_README.md` for more.

