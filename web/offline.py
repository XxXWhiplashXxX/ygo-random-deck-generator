"""Local-only website: the same UI/engine as Render, with a local card updater.
Run with: python web/offline.py. NEVER serve this module on a public host.
"""
import socket
import sys
import threading
import traceback
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'program'))
import ygo_paths as P
import ygo_update as U
import ygo_formats_update as F
import app as main_web

offline_app = FastAPI(title='Yu-Gi-Oh! Randomizer — Offline')
state_lock = threading.Lock()
state = {'busy': False, 'phase': 'idle', 'logs': [], 'check': None, 'error': None}

def log(message):
    with state_lock:
        state['logs'].append(str(message))
        state['logs'] = state['logs'][-600:]

def finish(error=None):
    with state_lock:
        state['error'] = error
        state['busy'] = False
        state['phase'] = 'error' if error else 'done'

def run_check():
    try:
        latest, have, changed = U.check()
        needs_cards = latest != have or changed
        with state_lock:
            state['check'] = {'latest': latest, 'installed': have,
                              'parser_changed': changed, 'needs_cards': needs_cards}
        log('Card data update available.' if needs_cards else 'Card data up to date. You can refresh banlists.')
        finish()
    except Exception:
        log(traceback.format_exc())
        finish('Could not check for updates. Check your connection and the log.')

def run_update(mode):
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
        # The web engine caches the old CSV; invalidate it only after a successful update.
        with main_web.lock:
            main_web.engine = None
        log('Update complete. Theme and banlist choices will refresh after you reload the page.')
        finish()
    except Exception:
        log(traceback.format_exc())
        finish('Update failed. See the log for details.')

# Insert only the updater into the *same* HTML file used by the public site.
# Any future website layout/search/slider changes automatically appear offline.
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
    html = (ROOT / 'web/static/index.html').read_text(encoding='utf-8')
    html = html.replace('</form></aside>', '</form>' + UPDATER_HTML + '</aside>', 1)
    html = html.replace('</body>', '<script src="/static/offline.js"></script></body>', 1)
    return HTMLResponse(html)

def require_local_origin(request: Request):
    """Block cross-site requests to the privileged updater on localhost."""
    origin = request.headers.get('origin')
    if origin and origin != f'{request.url.scheme}://{request.headers.get("host")}':
        raise HTTPException(403, 'Local updater only')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Cross-site requests are not allowed')

@offline_app.get('/static/offline.js')
def local_js():
    return FileResponse(ROOT / 'web/static/offline.js', media_type='application/javascript')

@offline_app.get('/api/local-update/status')
def status():
    with state_lock:
        return {**state, 'logs': list(state['logs'])}

@offline_app.post('/api/local-update/check')
def check(request: Request):
    require_local_origin(request)
    with state_lock:
        if state['busy']:
            raise HTTPException(409, 'Updater is already running.')
        state.update(busy=True, phase='checking', logs=[], check=None, error=None)
    threading.Thread(target=run_check, daemon=True).start()
    return {'started': True}

@offline_app.post('/api/local-update/run/{mode}')
def update(mode: str, request: Request):
    require_local_origin(request)
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

# All deck generation, export data, card pictures, settings and autocomplete are
# served by the exact same FastAPI app as the public website.
offline_app.mount('/', main_web.app)

def serve(open_browser=True):
    import uvicorn
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f'http://127.0.0.1:{port}/')).start()
    print(f'Yu-Gi-Oh! Randomizer running locally at http://127.0.0.1:{port}/')
    print('Close this terminal to stop the offline website.')
    uvicorn.run(offline_app, host='127.0.0.1', port=port)

if __name__ == '__main__':
    serve()
