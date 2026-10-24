# Browser version

This web UI uses the **same Python generator and card database** as the desktop application.

## Run locally

Install Python 3.10+ and run the following from the project root:

```bash
python -m pip install -r web/requirements.txt
python web/app.py
```

Then open http://127.0.0.1:8000.

## Using it

Choose Theme 1, Theme 2, Main/Extra Deck size, and an optional seed on the main screen. The Generate Deck button is above Advanced Settings. Advanced Settings includes the Theme/Support/Generic percentages, support choices, format, monster/spell/trap targets, rating limits, and other options. The browser offers PNG, YDK, TXT, and copy-decklist exports.

Defaults: 60 Main Deck, 50% Theme / 30% Support / 20% Generic, 30 Monsters / 20 Spells / 10 Traps, rating 3–5, with automatic support selection on.

## Image cache and hosting

Card thumbnails are downloaded on demand from YGOPRODeck and cached to disk under `web/image_cache/` (or `IMAGE_CACHE_DIR`). The initial download needs internet access. Render free plans do not retain local cached files across restarts; configure persistent image storage before significant public traffic. See [hosting instructions](../WEB_HOSTING_README.md) and the [project README](../README.md).
