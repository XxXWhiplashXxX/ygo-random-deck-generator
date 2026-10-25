"""Browser interface for the existing Yu-Gi-Oh! deck engine. Run: python web/app.py"""
import os
import time
import random
import sys
import threading
import urllib.request
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'program'))
import ygo_paths as P
import ygo_settings as S
from ygo_banlist import load_banlists
from ygo_builder import Builder
from ygo_data import read_csv, load_tree
from ygo_extra import ExtraBuilder
from ygo_final import build_deck
from ygo_labels import load_labels

app = FastAPI(title='Yu-Gi-Oh! Random Deck Generator')
# Set IMAGE_CACHE_DIR to a persistent disk mount on a public host.
IMAGE_CACHE_DIR = Path(os.environ.get('IMAGE_CACHE_DIR', str(ROOT / 'web/image_cache')))
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
image_fetch_lock = threading.Lock()
last_image_fetch = 0.0

lock = threading.Lock()  # The original Builder keeps per-format mutable caches.
engine = None

class GenerateInput(BaseModel):
    settings: dict

def get_engine():
    global engine
    if engine is None:
        labels = load_labels(P.DATA / 'Stats.csv', P.DATA / 'Mechanics.csv')
        builder = Builder(read_csv(P.DATA / 'cards.csv'), load_tree(P.DATA / 'game_mechanics_tree.csv'), labels=labels)
        engine = (builder, ExtraBuilder(builder))
    return engine

def card_json(entry):
    # The card passcode is the first column of cards.csv, as in ygo_final.export_deck.
    card_id = str(next(iter(entry.info.row.values()), '') or '').strip()
    return {'name': entry.info.name, 'id': card_id, 'origin': entry.origin, 'reason': entry.reason}

@app.get('/')
def home():
    return FileResponse(ROOT / 'web/static/index.html')

@app.get('/static/{filename}')
def static(filename: str):
    if filename not in ('app.js', 'style.css'):
        raise HTTPException(404)
    return FileResponse(ROOT / 'web/static' / filename)

@app.get('/api/options')
def options():
    return {'defaults': S.defaults(), 'labels': load_labels(P.DATA / 'Stats.csv', P.DATA / 'Mechanics.csv'),
            'banlists': [S.UNLIMITED, S.RANDOM] + list(load_banlists(P.FORBIDDEN)),
            'eras': S.ERAS, 'orders': S.BUILD_ORDERS}

@app.post('/api/generate')
def generate(data: GenerateInput):
    defaults = S.defaults()
    settings = {**defaults, **{k:v for k,v in data.settings.items() if k in defaults and k not in S.PATH_KEYS}}
    for key in ('deck_type_overall_pct','support_overall_pct','generic_overall_pct'):
        settings[key] = max(0, min(100, int(settings[key])))
    if sum(settings[k] for k in ('deck_type_overall_pct','support_overall_pct','generic_overall_pct')) != 100:
        raise HTTPException(400, 'Theme, Support and Generic must total 100%.')
    type_keys = ('extra_fusion_pct', 'extra_synchro_pct', 'extra_xyz_pct', 'extra_link_pct')
    try:
        percentages = [int(settings[k]) for k in type_keys]
    except (ValueError, TypeError):
        raise HTTPException(400, 'Extra Deck percentages must be numbers.')
    if any(x < 0 or x > 100 for x in percentages) or sum(percentages) != 100:
        raise HTTPException(400, 'Extra Deck percentages must total 100%.')
    for k, v in zip(('allow_fusion', 'allow_synchro', 'allow_xyz', 'allow_link'), percentages):
        settings[k] = v > 0
    settings['rand_extra_types'] = False   # Website sliders supersede the old random on/off switches.
    S.roll(settings, random.Random())
    try:
        req = S.make_request(settings, banlists=list(load_banlists(P.FORBIDDEN)))
        with lock:
            deck = build_deck(*get_engine(), req)
    except Exception as exc:
        raise HTTPException(400, f'Deck generation failed: {exc}') from exc
    selected = deck.debug.get('selected_categories', {})
    return {'main': [card_json(e) for e in deck.main], 'extra': [card_json(e) for e in deck.extra],
            'seed': req.seed, 'settings': settings, 'breakdown': S.breakdown_text(req),
            'categories': {k: {'label': selected.get(k, ''), 'detail': str(deck.debug.get(k, ''))}
                           for k in ('theme1','theme2','support1','support2')}}

@app.get('/api/card-image/{card_id}')
def card_image(card_id: str):
    if not card_id.isdigit() or len(card_id) > 12:
        raise HTTPException(400, 'Invalid card ID')
    try:
        return FileResponse(cached_image_path(card_id), media_type='image/jpeg', headers={'Cache-Control':'public, max-age=604800'})
    except Exception:
        raise HTTPException(404, 'Card image unavailable')

def cached_image_path(card_id: str) -> Path:
    """Fetch an image only once into a local cache, then serve the saved copy.

    IMPORTANT: On Render free hosting, local storage is ephemeral. Set IMAGE_CACHE_DIR
    to a persistent disk or deploy images from durable storage before going public.
    """
    global last_image_fetch
    path = IMAGE_CACHE_DIR / f'{card_id}.jpg'
    if path.is_file() and path.stat().st_size > 0:
        return path
    # Serialize downloads to avoid concurrent duplicate requests or bursts to YGOPRODeck.
    with image_fetch_lock:
        if path.is_file() and path.stat().st_size > 0:
            return path
        # Keep requests slow even if a deck has many uncached images.
        time.sleep(max(0.0, 0.5 - (time.monotonic() - last_image_fetch)))
        last_image_fetch = time.monotonic()
        req = urllib.request.Request(
            f'https://images.ygoprodeck.com/images/cards_small/{card_id}.jpg',
            headers={'User-Agent': 'YgoRandomizerWeb/1.0'})
        with urllib.request.urlopen(req, timeout=12) as response:
            if response.status != 200 or not response.headers.get('Content-Type', '').startswith('image/'):
                raise ValueError('Invalid image response')
            data = response.read(300_001)
            if len(data) > 300_000 or not data.startswith(b'\xff\xd8\xff'):
                raise ValueError('Invalid JPEG data')
        temp = path.with_suffix('.tmp')
        try:
            temp.write_bytes(data)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        return path

if __name__ == '__main__':
    import uvicorn
    # Works locally and on Render without relying on shell substitution of $PORT.
    uvicorn.run(app, host=os.environ.get('HOST', '127.0.0.1'),
                port=int(os.environ.get('PORT') or '8000'))
