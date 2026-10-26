"""Local-only website: the same page and engine as the public site, plus a card-database updater.

Run with:  python web/offline.py   (or double-click 'Yu-Gi-Oh Randomizer.pyw')
NEVER serve this module on a public host: the updater rewrites files on this computer.
"""
import logging                                   # prints errors to the console window
import socket                                    # finds a free port to run on
import sys                                       # lets us add program/ to the import path
import threading                                 # runs the updater in the background; locks shared state
import traceback                                 # turns an error into readable text for the updater log
import webbrowser                                # opens the page in the default browser
from contextlib import asynccontextmanager       # builds the 'run this at startup' hook
from pathlib import Path                         # file paths

from fastapi import FastAPI, HTTPException, Request   # web framework, error replies, and the incoming request
from fastapi.responses import FileResponse, HTMLResponse   # reply with a file / with HTML text

ROOT = Path(__file__).resolve().parents[1]       # the project folder
sys.path.insert(0, str(ROOT / 'program'))        # so 'import ygo_...' finds the engine files
import ygo_paths as P                            # where data/ is
import ygo_update as U                           # card database updater
import ygo_formats_update as F                   # banlist / staples updater
import app as main_web                           # the public website's app (web/app.py)

LOCAL_HOSTS = ('127.0.0.1', 'localhost')         # the only addresses the updater answers to


@asynccontextmanager
async def lifespan(_app):
    """Preload the deck engine at startup. (A mounted app's own startup hook doesn't run, so we do it here.)"""
    threading.Thread(target=main_web.warm_up, daemon=True).start()
    yield


offline_app = FastAPI(title='Yu-Gi-Oh! Randomizer — Offline', lifespan=lifespan)

# ---------------------------------------------------------------------------
# Updater state (read by the page every 1.2 seconds while an update runs)
# ---------------------------------------------------------------------------
state_lock = threading.Lock()                    # the updater thread and web requests both touch `state`
state = {'busy': False, 'phase': 'idle', 'logs': [], 'check': None, 'error': None}
MAX_LOG_LINES = 600                              # keep only the newest lines so memory can't grow forever


def log(message):
    """Add one line to the updater log shown on the page."""
    with state_lock:
        state['logs'].append(str(message))
        state['logs'] = state['logs'][-MAX_LOG_LINES:]


def finish(error=None):
    """Mark the current job as done (or failed with `error`)."""
    with state_lock:
        state['error'] = error
        state['busy'] = False
        state['phase'] = 'error' if error else 'done'


def run_check():
    """Background job: is there newer card data, or did the parser change?"""
    try:
        latest, have, changed = U.check()                         # newest database version, ours, parser changed?
        needs_cards = latest != have or changed
        with state_lock:
            state['check'] = {'latest': latest, 'installed': have,
                              'parser_changed': changed, 'needs_cards': needs_cards}
        log('Card data update available.' if needs_cards else 'Card data up to date. You can refresh banlists.')
        finish()
    except Exception:
        log(traceback.format_exc())                               # the full error goes into the log on the page
        finish('Could not check for updates. Check your connection and the log.')


def run_update(mode):
    """Background job: rebuild the card database ('cards') or the forbidden lists ('banlists')."""
    try:
        if mode == 'cards':
            log('Rebuilding the card database. Existing files are backed up by the updater.')
            ok = U.run_update(P.DATA / 'cards.csv', P.DATA / 'Stats.csv', log=log)
        else:
            log('Refreshing forbidden lists...')
            ok = F.refresh_banlists(P.DATA / 'cards.csv', log=log)
        if ok is False:
            finish('The updater reported a failure. See the log.')
            return
        main_web.reset_caches()                                   # the website forgets the old cards / lists / labels
        log('Update complete. Theme and banlist choices will refresh after you reload the page.')
        finish()
    except Exception:
        log(traceback.format_exc())
        finish('Update failed. See the log for details.')


# ---------------------------------------------------------------------------
# The page: the public index.html with the updater panel dropped into its placeholder
# ---------------------------------------------------------------------------
UPDATER_SLOT = '<div id="updaterSlot"></div>'    # an empty placeholder in index.html (invisible on the public site)

UPDATER_HTML = """<section class="offline-updater panel" id="localUpdater">
  <h3>Offline card database updater</h3>
  <p>Check for new cards or parser changes, and update the local database and forbidden lists.</p>
  <button type="button" id="updateCheck">Check for New Cards</button>
  <div id="updateChoices" class="hidden">
    <button type="button" id="updateCards">Update Card Database</button>
    <button type="button" id="updateLists">Refresh Banlists</button>
  </div>
  <p id="updateStatus" role="status"></p>
  <details><summary>Updater log</summary><pre id="updateLog"></pre></details>
</section>"""


@offline_app.get('/')
def local_home():
    """The same page as the public site, so every layout change shows up offline automatically."""
    html = (ROOT / 'web/static/index.html').read_text(encoding='utf-8')
    if UPDATER_SLOT not in html:                                  # fail loudly instead of silently losing the updater
        raise HTTPException(500, 'index.html is missing the updater placeholder: ' + UPDATER_SLOT)
    html = html.replace(UPDATER_SLOT, UPDATER_HTML, 1)
    html = html.replace('</body>', '<script src="/static/offline.js"></script></body>', 1)
    return HTMLResponse(html)


def require_local_request(request: Request):
    """Only answer requests made by our own page on this computer.

    - Host must be localhost: blocks 'DNS rebinding', where a website points its own name at 127.0.0.1.
      (In that trick the Origin and Host match each other, so comparing those two alone isn't enough.)
    - Origin, when sent, must be this same address: blocks other websites posting to the updater.
    """
    host = request.headers.get('host', '')
    hostname = host.rsplit(':', 1)[0]                             # '127.0.0.1:5123' -> '127.0.0.1'
    if hostname not in LOCAL_HOSTS:
        raise HTTPException(403, 'Local updater only')
    origin = request.headers.get('origin')
    if origin and origin != f'{request.url.scheme}://{host}':
        raise HTTPException(403, 'Local updater only')
    if request.headers.get('sec-fetch-site') == 'cross-site':     # modern browsers label cross-site requests
        raise HTTPException(403, 'Cross-site requests are not allowed')


@offline_app.get('/static/offline.js')
def local_js():
    """The updater panel's script (only exists in the offline version)."""
    return FileResponse(ROOT / 'web/static/offline.js', media_type='application/javascript')


@offline_app.get('/api/local-update/status')
def status(request: Request):
    """The updater's current state and log, for the page to display."""
    require_local_request(request)
    with state_lock:
        return {**state, 'logs': list(state['logs'])}             # a copy, so it can't change while being sent


@offline_app.post('/api/local-update/check')
def check(request: Request):
    """Start a background 'is there an update?' check."""
    require_local_request(request)
    with state_lock:
        if state['busy']:
            raise HTTPException(409, 'Updater is already running.')
        state.update(busy=True, phase='checking', logs=[], check=None, error=None)
    threading.Thread(target=run_check, daemon=True).start()
    return {'started': True}


@offline_app.post('/api/local-update/run/{mode}')
def update(mode: str, request: Request):
    """Start a background update: mode is 'cards' or 'banlists'."""
    require_local_request(request)
    if mode not in ('cards', 'banlists'):
        raise HTTPException(400, 'Unknown update mode')
    with state_lock:
        if state['busy']:
            raise HTTPException(409, 'Updater is already running.')
        if mode == 'cards' and (not state['check'] or not state['check']['needs_cards']):
            raise HTTPException(400, 'Check for updates first.')
        state.update(busy=True, phase='updating', logs=[], error=None)
    threading.Thread(target=run_update, args=(mode,), daemon=True).start()
    return {'started': True}


# Everything else (deck generation, card pictures, options, app.js, style.css) is answered
# by the exact same app as the public website. Routes above are checked first.
offline_app.mount('/', main_web.app)


def serve(open_browser=True):
    """Start the local server on a free port and open the browser."""
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    with socket.socket() as sock:                                 # ask the system for any free port ...
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]                              # ... remember it, then let it go for uvicorn
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f'http://127.0.0.1:{port}/')).start()   # give the server a moment
    print(f'Yu-Gi-Oh! Randomizer running locally at http://127.0.0.1:{port}/')
    print('Close this window to stop the offline website.')
    uvicorn.run(offline_app, host='127.0.0.1', port=port)        # 127.0.0.1 = only this computer can connect


if __name__ == '__main__':
    serve()
