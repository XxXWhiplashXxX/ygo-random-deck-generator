"""Browser interface for the existing Yu-Gi-Oh! deck engine.

Run locally with:  python web/app.py   (then open http://127.0.0.1:8000)

This file only does three jobs:
  1. serve the web page (index.html, app.js, style.css)
  2. turn the page's settings into a deck with the SAME engine the desktop app uses
  3. download each card picture once, save it, and serve the saved copy afterwards
"""
import logging                                   # prints errors (with the full traceback) to the console
import os                                        # reads environment variables like PORT and IMAGE_CACHE_DIR
import random                                    # rolls the 'Randomize ...' tick boxes
import sys                                       # lets us add the program/ folder to Python's import path
import threading                                 # locks, so two visitors can't change shared data at once
import time                                      # measures the gap between image downloads
import urllib.request                            # downloads card pictures (standard library, nothing to install)
from contextlib import asynccontextmanager       # builds the 'run this at startup' hook for FastAPI
from pathlib import Path                         # file paths that work on Windows and Linux

from fastapi import FastAPI, HTTPException       # the web framework, and its 'send an error back' exception
from fastapi.responses import FileResponse       # sends a file from disk as the reply
from pydantic import BaseModel                   # describes the shape of the JSON the page sends us

ROOT = Path(__file__).resolve().parents[1]       # the project folder (one level above web/)
sys.path.insert(0, str(ROOT / 'program'))        # so 'import ygo_...' finds the engine files
import ygo_paths as P                            # where data/ and 'forbidden lists/' are
import ygo_settings as S                         # defaults, 'Random' rolls, settings -> Request
from ygo_banlist import load_banlists            # reads the .lflist.conf banlist files
from ygo_builder import Builder                  # Main Deck engine
from ygo_data import read_csv, load_tree         # loads cards.csv and the mechanics tree
from ygo_extra import ExtraBuilder               # Extra Deck engine
from ygo_final import build_deck                 # runs the whole pipeline: Main + Extra
from ygo_labels import load_labels, theme_labels # all category labels / only the ones a theme can be

log = logging.getLogger('ygo_web')               # our own logger; its messages show in the console / Render log

# ---------------------------------------------------------------------------
# Web-only defaults
# ---------------------------------------------------------------------------
# The desktop app's defaults (S.defaults()) are kept as they are, so old presets still load the same way.
# The website starts from slightly different values. They live HERE only, and the page reads them from
# /api/options, so there is exactly one place to change a website default.
WEB_DEFAULTS = {
    'main_size': 60, 'monster_target': 30, 'spell_target': 20, 'trap_target': 10,
    'min_rating': 2, 'max_rating': 5,
    'deck_type_overall_pct': 50, 'support_overall_pct': 30, 'generic_overall_pct': 20,
    'support_on': True, 'allow_normals': False, 'extra_match_main_ratio': True,
    'rand_extra_ratios': False,
    'extra_fusion_pct': 25, 'extra_synchro_pct': 25, 'extra_xyz_pct': 25, 'extra_link_pct': 25,
}

# The key groups we check and use more than once.
ALLOCATION_KEYS = ('deck_type_overall_pct', 'support_overall_pct', 'generic_overall_pct')   # must total 100
EXTRA_PCT_KEYS = ('extra_fusion_pct', 'extra_synchro_pct', 'extra_xyz_pct', 'extra_link_pct')   # must total 100
EXTRA_SWITCH_KEYS = ('allow_fusion', 'allow_synchro', 'allow_xyz', 'allow_link')   # same order as the line above
CATEGORY_KEYS = ('theme1', 'theme2', 'support1', 'support2')   # the four categories the result reports
LEVEL_KEYS = ('level_low_pct', 'level_mid_pct', 'level_high_pct')   # Level 1-4 / 5-6 / 7+ shares: total 100 (when used)


def web_defaults() -> dict:
    """The desktop defaults with the website's own starting values laid on top."""
    return {**S.defaults(), **WEB_DEFAULTS}


# ---------------------------------------------------------------------------
# Cached data
# ---------------------------------------------------------------------------
# None of this changes until the offline updater runs, so it is loaded ONCE and kept in memory.
# reset_caches() throws it away after an update so the next request loads the new files.
lock = threading.Lock()          # one deck build at a time: the Builder keeps per-format caches that can't be shared
engine = None                    # (Builder, ExtraBuilder) once loaded; about 5 seconds to build
_banlists = None                 # list of banlist names
_labels = None                   # list of Theme / Support category labels


def get_engine():
    """The deck engine, built the first time it is needed. Call this only while holding `lock`."""
    global engine
    if engine is None:                                                      # not built yet (or reset by an update)
        builder = Builder(read_csv(P.DATA / 'cards.csv'),                   # every card
                          load_tree(P.DATA / 'game_mechanics_tree.csv'),    # how mechanics relate to each other
                          labels=get_labels())                              # the category list
        engine = (builder, ExtraBuilder(builder))                           # Main and Extra Deck engines share the cards
    return engine


def get_banlists() -> list:
    """Every banlist name, read from disk only the first time."""
    global _banlists
    if _banlists is None:
        _banlists = list(load_banlists(P.FORBIDDEN))                        # the dict's keys are the list names
    return _banlists


def get_labels() -> list:
    """Every Theme / Support category label, read from disk only the first time."""
    global _labels
    if _labels is None:
        _labels = load_labels(P.DATA / 'Stats.csv', P.DATA / 'Mechanics.csv')
    return _labels


def reset_caches():
    """Forget everything loaded from disk (the offline updater calls this after a successful update)."""
    global engine, _banlists, _labels
    with lock:                                   # wait until any deck being built right now has finished
        engine = None
        _banlists = None
        _labels = None


def warm_up():
    """Build the engine now, so the first visitor doesn't wait ~5 seconds for it."""
    try:
        with lock:
            get_engine()
        log.info('Deck engine loaded.')
    except Exception:                            # a broken data file: log it; requests will show the error later
        log.exception('Could not preload the deck engine')


@asynccontextmanager
async def lifespan(_app):
    """FastAPI runs the code before 'yield' when the server starts."""
    threading.Thread(target=warm_up, daemon=True).start()   # in the background, so the server can still start right away
    yield                                                   # (code after 'yield' would run at shutdown; nothing needed)


app = FastAPI(title='Yu-Gi-Oh! Random Deck Generator', lifespan=lifespan)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
class GenerateInput(BaseModel):
    """The JSON body the page sends to /api/generate: {"settings": {...}}."""
    settings: dict


def whole_number(settings: dict, key: str, low: int = 0, high: int = 100) -> int:
    """settings[key] as a whole number between low and high, or a clear 400 error if it isn't a number."""
    try:
        return max(low, min(high, int(float(settings[key]))))   # float() first so '25.0' works too
    except (ValueError, TypeError) as err:
        raise HTTPException(400, f'{key} must be a number.') from err


def card_json(entry) -> dict:
    """One deck entry -> the small dict the page needs to draw a card."""
    row = entry.info.row                                              # the card's row from cards.csv
    card_id = str(row.get('Card Id') or '').strip()                   # the passcode, looked up by its column NAME
    return {'name': entry.info.name, 'id': card_id, 'origin': entry.origin, 'reason': entry.reason}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
STATIC_FILES = {'app.js', 'style.css'}           # the only files /static/ will hand out


@app.get('/')
def home():
    """The page itself."""
    return FileResponse(ROOT / 'web/static/index.html')


@app.get('/static/{filename}')
def static(filename: str):
    """app.js and style.css. Anything else is 404, so nobody can ask for other files on the server."""
    if filename not in STATIC_FILES:
        raise HTTPException(404)
    return FileResponse(ROOT / 'web/static' / filename)


@app.get('/api/options')
def options():
    """Everything the page needs to fill its controls: defaults and the dropdown choices."""
    return {'defaults': web_defaults(),
            'labels': get_labels(),                                   # Support boxes: every label
            'theme_labels': theme_labels(get_labels()),               # Theme boxes: only what Random can pick
            'banlists': [S.UNLIMITED, S.RANDOM] + get_banlists(),
            'eras': S.ERAS, 'orders': S.BUILD_ORDERS}


# ---------------------------------------------------------------------------
# Deck generation
# ---------------------------------------------------------------------------
@app.post('/api/generate')
def generate(data: GenerateInput):
    defaults = web_defaults()
    # Start from the defaults and keep only the keys we know. File paths are never taken from a visitor.
    settings = {**defaults, **{k: v for k, v in data.settings.items() if k in defaults and k not in S.PATH_KEYS}}

    # Apply every ticked 'Randomize ...' box first, so the checks below see the final numbers.
    S.roll(settings, random.Random())

    # Theme / Support / Generic must total 100%.
    for key in ALLOCATION_KEYS:
        settings[key] = whole_number(settings, key)
    if sum(settings[k] for k in ALLOCATION_KEYS) != 100:
        raise HTTPException(400, 'Theme, Support and Generic must total 100%.')

    # Level sliders: only checked when they are switched on (otherwise the engine ignores them).
    for key in LEVEL_KEYS:
        settings[key] = whole_number(settings, key)
    if settings.get('level_sliders') and sum(settings[k] for k in LEVEL_KEYS) != 100:
        raise HTTPException(400, 'Level 1-4, 5-6 and 7+ must total 100%.')

    # Themes must be labels a theme can be (the same list Random picks from).
    allowed_themes = set(theme_labels(get_labels()))
    for key, name in (('deck1_label', 'Theme 1'), ('deck2_label', 'Theme 2')):
        if settings[key] not in allowed_themes:
            raise HTTPException(400, f'{name} must be chosen from the list.')

    # The page has ONE slider per pair ('Theme 1 share'); the second gets whatever is left.
    # (Before this fix the page never sent deck2_pct, so it stayed 0 and Theme 2 was silently skipped.)
    for first, second in (('deck1_pct', 'deck2_pct'), ('support1_pct', 'support2_pct')):
        settings[first] = whole_number(settings, first)
        settings[second] = 100 - settings[first]

    # The web page uses Extra Deck percentages, not the desktop app's on/off switches.
    settings['rand_extra_types'] = False
    percentages = [whole_number(settings, k) for k in EXTRA_PCT_KEYS]
    randomize_ratios = bool(settings.get('rand_extra_ratios', False))
    if not randomize_ratios and sum(percentages) != 100:          # (random ratios replace them, so no check then)
        raise HTTPException(400, 'Extra Deck percentages must total 100%.')

    banlists = get_banlists()
    # make_request turns a blank seed into a random number and a text seed into a number.
    # We do that ONCE here and reuse the number, so everything below uses the same seed.
    seed = S.make_request(settings, banlists=banlists).seed

    if randomize_ratios:
        # The ratios come from the seed, so pasting this seed back in rebuilds the same ratios and deck.
        rng = random.Random(f'{seed}-extra-ratios')
        cuts = sorted([0, 100] + [rng.randint(0, 100) for _ in range(3)])   # 3 random cut points on 0..100
        percentages = [cuts[i + 1] - cuts[i] for i in range(4)]             # the 4 gaps between cuts total 100
        for key, value in zip(EXTRA_PCT_KEYS, percentages):
            settings[key] = value                                           # sent back so the sliders show them

    # Turn each type on only if it has a share. This must happen BEFORE the final request is made
    # (it used to happen after, so randomized ratios were built with the old switches).
    for key, value in zip(EXTRA_SWITCH_KEYS, percentages):
        settings[key] = value > 0

    # The final request, with the seed pinned. We do NOT write the seed into settings['seed']:
    # that value goes back into the Seed box, and a filled box would repeat the same deck on every click.
    req = S.make_request({**settings, 'seed': str(seed)}, banlists=banlists)

    try:
        with lock:                                       # one build at a time (see the note on `lock`)
            deck = build_deck(*get_engine(), req)
    except Exception as exc:
        log.exception('Deck generation failed')          # the full details go to the console / Render log ...
        raise HTTPException(500, 'Deck generation failed. Try different settings or another seed.') from exc   # ... the visitor gets a clean message

    selected = deck.debug.get('selected_categories', {})          # which real category each slot ended up as
    return {
        'main': [card_json(e) for e in deck.main],
        'extra': [card_json(e) for e in deck.extra],
        'seed': req.seed,                                         # shown in the title; paste it back to rebuild this deck
        'settings': settings,                                     # the rolled values, so the controls can show them
        'breakdown': S.breakdown_text(req),                       # the 'how many cards go where' text
        'categories': {k: {'label': selected.get(k, ''), 'detail': str(deck.debug.get(k, ''))}
                       for k in CATEGORY_KEYS},
    }


# ---------------------------------------------------------------------------
# Card pictures
# ---------------------------------------------------------------------------
# Set IMAGE_CACHE_DIR to a persistent disk mount on a public host (Render free disks are wiped on restart).
IMAGE_CACHE_DIR = Path(os.environ.get('IMAGE_CACHE_DIR', str(ROOT / 'web/image_cache')))
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)               # make the folder if it isn't there
IMAGE_URL = 'https://images.ygoprodeck.com/images/cards_small/{}.jpg'
MAX_IMAGE_BYTES = 300_000                                        # a small card picture is ~30-60 KB; refuse anything huge
MIN_SECONDS_BETWEEN_DOWNLOADS = 0.5                              # be polite to YGOPRODeck
image_fetch_lock = threading.Lock()                              # one download at a time
last_image_fetch = 0.0                                           # when the last download started


@app.get('/api/card-image/{card_id}')
def card_image(card_id: str):
    """One card picture, from the cache (downloading it the first time)."""
    if not card_id.isdigit() or len(card_id) > 12:                # passcodes are digits only: blocks '../' tricks
        raise HTTPException(400, 'Invalid card ID')
    try:
        path = cached_image_path(card_id)
    except Exception as exc:
        raise HTTPException(404, 'Card image unavailable') from exc
    # Browsers may keep the picture for a week (604800 seconds) without asking again.
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'public, max-age=604800'})


def cached_image_path(card_id: str) -> Path:
    """Download a picture only once into the cache folder, then always serve the saved copy.

    Downloads go one at a time with a short gap. The trade-off: while one slow download runs,
    other NEW pictures wait behind it (already-saved pictures are never held up).
    """
    global last_image_fetch
    path = IMAGE_CACHE_DIR / f'{card_id}.jpg'
    if path.is_file() and path.stat().st_size > 0:               # already saved: no lock, no waiting
        return path
    with image_fetch_lock:
        if path.is_file() and path.stat().st_size > 0:           # another request saved it while we waited for the lock
            return path
        wait = MIN_SECONDS_BETWEEN_DOWNLOADS - (time.monotonic() - last_image_fetch)
        time.sleep(max(0.0, wait))                               # keep the gap between downloads
        last_image_fetch = time.monotonic()
        req = urllib.request.Request(IMAGE_URL.format(card_id), headers={'User-Agent': 'YgoRandomizerWeb/1.0'})
        with urllib.request.urlopen(req, timeout=12) as response:
            if response.status != 200 or not response.headers.get('Content-Type', '').startswith('image/'):
                raise ValueError('Invalid image response')
            data = response.read(MAX_IMAGE_BYTES + 1)            # read one byte extra to notice an oversized file
            if len(data) > MAX_IMAGE_BYTES or not data.startswith(b'\xff\xd8\xff'):   # every JPEG starts with FF D8 FF
                raise ValueError('Invalid JPEG data')
        temp = path.with_suffix('.tmp')                          # write to a temp file first ...
        try:
            temp.write_bytes(data)
            temp.replace(path)                                   # ... then swap it in, so nobody sees a half-written file
        finally:
            temp.unlink(missing_ok=True)                         # tidy up if the write failed
        return path


if __name__ == '__main__':
    import uvicorn                                               # the web server program
    logging.basicConfig(level=logging.INFO)                      # show our log messages in the console
    # Works locally and on Render without relying on shell substitution of $PORT.
    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT') or '8000'))
