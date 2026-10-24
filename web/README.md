# Browser version (local)

The original desktop application remains unchanged. This web version calls the **same Python deck-building engine** and uses the existing card database and forbidden lists.

## Start

1. Install Python 3.10+.
2. From the project folder, run `python -m pip install -r web/requirements.txt`.
3. Run `python web/app.py`.
4. Open http://127.0.0.1:8000 in a browser.

The first generation can take longer while the card database is loaded. Card images require an internet connection. PNG exports include the actual card thumbnails that load successfully; missing images show placeholders. Export YDK and TXT work offline. No public hosting is configured.

## Notes

- Card images are proxied from YGOPRODeck and cached in memory. Before public deployment, check the current YGOPRODeck image usage/caching rules and implement persistent caching, rate limiting and appropriate deployment security.
- This is an initial web interface with the primary deck settings. Some advanced desktop-only controls (e.g. level distribution sliders and presets) are not exposed yet; their engine defaults still apply.
- The backend uses a lock because the original Builder maintains mutable format caches.
