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


@asynccontextmanager                                        # turns the function below into a startup/shutdown hook
async def lifespan(_app):                                   # function: what runs when the server starts
    """Preload the deck engine at startup. (A mounted app's own startup hook doesn't run, so we do it here.)"""
    threading.Thread(target=main_web.warm_up, daemon=True).start()  # load the engine in the background
    yield                                                   # (nothing to do at shutdown)


offline_app = FastAPI(title='Yu-Gi-Oh! Randomizer — Offline', lifespan=lifespan)  # the local app (the updater's addresses + the public site)

# ---------------------------------------------------------------------------
# Updater state (read by the page every 1.2 seconds while an update runs)
# ---------------------------------------------------------------------------
state_lock = threading.Lock()                    # the updater thread and web requests both touch `state`
state = {'busy': False, 'phase': 'idle', 'logs': [], 'check': None, 'error': None}  # busy? which step? the log lines, the last check's answer, an error
MAX_LOG_LINES = 600                              # keep only the newest lines so memory can't grow forever


def log(message):                                           # function: add one line to the updater log
    """Add one line to the updater log shown on the page."""
    with state_lock:                                        # (one thread at a time)
        state['logs'].append(str(message))                  # add it
        state['logs'] = state['logs'][-MAX_LOG_LINES:]      # keep only the newest lines


def finish(error=None):                                     # function: mark the current job finished
    """Mark the current job as done (or failed with `error`)."""
    with state_lock:                                        # (one thread at a time)
        state['error'] = error                              # the error text (None = it worked)
        state['busy'] = False                               # not busy any more
        state['phase'] = 'error' if error else 'done'       # 'error' or 'done'


def run_check():                                            # function: background job - check for an update
    """Background job: is there newer card data, or did the parser change?"""
    try:
        latest, have, changed = U.check()                         # newest database version, ours, parser changed?
        needs_cards = latest != have or changed             # new cards, or a step script changed?
        with state_lock:                                    # (one thread at a time)
            state['check'] = {'latest': latest, 'installed': have,  # save the answer for the page
                              'parser_changed': changed, 'needs_cards': needs_cards}  # (and whether a card update is needed)
        # say what was found
        log('Card data update available.' if needs_cards else 'Card data up to date. You can refresh banlists.')
        finish()                                            # done
    except Exception:                                       # it failed:
        log(traceback.format_exc())                               # the full error goes into the log on the page
        finish('Could not check for updates. Check your connection and the log.')  # done, with an error


def run_update(mode):                                       # function: background job - run one kind of update
    """Background job: rebuild the card database ('cards'), the forbidden lists ('banlists') or the staples ('staples')."""
    try:
        if mode == 'cards':                                 # the card database:
            log('Rebuilding the card database. Existing files are backed up by the updater.')  # say what's happening
            ok = U.run_update(P.DATA / 'cards.csv', P.DATA / 'Stats.csv', log=log)  # run the full card update
        elif mode == 'staples':                             # the staples:
            log('Checking staples for every format (the first time this can take an hour or more)...')  # say what's happening
            ok = F.refresh_staples(log=log)                 # run Check Staples
        else:
            log('Refreshing forbidden lists...')            # say what's happening
            ok = F.refresh_banlists(P.DATA / 'cards.csv', log=log)  # refresh the banlists
        if ok is False:                                     # it failed:
            finish('The updater reported a failure. See the log.')  # done, with an error
            return
        main_web.reset_caches()                                   # the website forgets the old cards / lists / labels
        log('Update complete. Theme and banlist choices will refresh after you reload the page.')  # say it worked
        finish()                                            # done
    except Exception:                                       # it crashed:
        log(traceback.format_exc())                         # the full error in the log
        finish('Update failed. See the log for details.')   # done, with an error


# ---------------------------------------------------------------------------
# The page: the public index.html with the updater panel dropped into its placeholder
# ---------------------------------------------------------------------------
UPDATER_SLOT = '<div id="updaterSlot"></div>'    # an empty placeholder in index.html (invisible on the public site)

# The updater panel's HTML: two buttons, the choices shown after a check, a status line and the log.
UPDATER_HTML = """<section class="offline-updater panel" id="localUpdater">
  <h3>Offline card database updater</h3>
  <p>Check for new cards or parser changes, and update the local database and forbidden lists.</p>
  <button type="button" id="updateCheck">Check for New Cards</button>
  <button type="button" id="updateStaples">Check Staples</button>
  <div id="updateChoices" class="hidden">
    <button type="button" id="updateCards">Update Card Database</button>
    <button type="button" id="updateLists">Refresh Banlists</button>
  </div>
  <p id="updateStatus" role="status"></p>
  <details><summary>Updater log</summary><pre id="updateLog"></pre></details>
</section>"""


@offline_app.get('/')                                       # the address '/'
def local_home():                                           # function: the page, with the updater panel added
    """The same page as the public site, so every layout change shows up offline automatically."""
    html = (ROOT / 'web/static/index.html').read_text(encoding='utf-8')  # the public page's HTML
    if UPDATER_SLOT not in html:                                  # fail loudly instead of silently losing the updater
        raise HTTPException(500, 'index.html is missing the updater placeholder: ' + UPDATER_SLOT)
    html = html.replace(UPDATER_SLOT, UPDATER_HTML, 1)      # put the updater panel in the placeholder
    html = html.replace('</body>', '<script src="/static/offline.js"></script></body>', 1)  # and add the updater's script
    return HTMLResponse(html)                               # send the page


def require_local_request(request: Request):                # function: refuse anything not from our own page on this computer
    """Only answer requests made by our own page on this computer.

    - Host must be localhost: blocks 'DNS rebinding', where a website points its own name at 127.0.0.1.
      (In that trick the Origin and Host match each other, so comparing those two alone isn't enough.)
    - Origin, when sent, must be this same address: blocks other websites posting to the updater.
    """
    host = request.headers.get('host', '')                  # the address the browser used
    hostname = host.rsplit(':', 1)[0]                             # '127.0.0.1:5123' -> '127.0.0.1'
    if hostname not in LOCAL_HOSTS:                         # not localhost:
        raise HTTPException(403, 'Local updater only')      # 403 = not allowed
    origin = request.headers.get('origin')                  # the page the request came from (if the browser says)
    if origin and origin != f'{request.url.scheme}://{host}':  # a different address:
        raise HTTPException(403, 'Local updater only')      # 403 = not allowed
    if request.headers.get('sec-fetch-site') == 'cross-site':     # modern browsers label cross-site requests
        raise HTTPException(403, 'Cross-site requests are not allowed')  # 403 = not allowed


@offline_app.get('/static/offline.js')                      # the address '/static/offline.js'
def local_js():                                             # function: send the updater panel's script
    """The updater panel's script (only exists in the offline version)."""
    return FileResponse(ROOT / 'web/static/offline.js', media_type='application/javascript')  # (as JavaScript)


@offline_app.get('/api/local-update/status')                # the address '/api/local-update/status'
def status(request: Request):                               # function: the updater's state, for the page
    """The updater's current state and log, for the page to display."""
    require_local_request(request)                          # only our own page may ask
    with state_lock:                                        # (one thread at a time)
        return {**state, 'logs': list(state['logs'])}             # a copy, so it can't change while being sent


@offline_app.post('/api/local-update/check')                # the address '/api/local-update/check'
def check(request: Request):                                # function: start a background check
    """Start a background 'is there an update?' check."""
    require_local_request(request)                          # only our own page may ask
    with state_lock:                                        # (one thread at a time)
        if state['busy']:                                   # something is already running:
            raise HTTPException(409, 'Updater is already running.')  # 409 = busy
        state.update(busy=True, phase='checking', logs=[], check=None, error=None)  # a fresh state for this job
    threading.Thread(target=run_check, daemon=True).start()  # run the check in the background
    return {'started': True}                                # tell the page it started


@offline_app.post('/api/local-update/run/{mode}')           # the address '/api/local-update/run/<mode>'
def update(mode: str, request: Request):                    # function: start a background update
    """Start a background update: mode is 'cards', 'banlists' or 'staples'."""
    require_local_request(request)                          # only our own page may ask
    if mode not in ('cards', 'banlists', 'staples'):        # an unknown kind:
        raise HTTPException(400, 'Unknown update mode')     # 400 = 'your request was wrong'
    with state_lock:                                        # (one thread at a time)
        if state['busy']:                                   # something is already running:
            raise HTTPException(409, 'Updater is already running.')  # 409 = busy
        if mode == 'cards' and (not state['check'] or not state['check']['needs_cards']):  # a card update needs a check first that said one is needed
            raise HTTPException(400, 'Check for updates first.')  # 400 = 'your request was wrong'
        state.update(busy=True, phase='updating', logs=[], error=None)  # a fresh state for this job
    threading.Thread(target=run_update, args=(mode,), daemon=True).start()  # run it in the background
    return {'started': True}                                # tell the page it started


# Everything else (deck generation, card pictures, options, app.js, style.css) is answered
# by the exact same app as the public website. Routes above are checked first.
offline_app.mount('/', main_web.app)                        # the public website answers every other address


def serve(open_browser=True):                               # function: start the offline website
    """Start the local server on a free port and open the browser."""
    import uvicorn                                          # the web server program
    logging.basicConfig(level=logging.INFO)                 # show log messages in the console
    with socket.socket() as sock:                                 # ask the system for any free port ...
        sock.bind(('127.0.0.1', 0))                         # any free port on this computer
        port = sock.getsockname()[1]                              # ... remember it, then let it go for uvicorn
    if open_browser:                                        # open the browser?
        threading.Timer(1.2, lambda: webbrowser.open(f'http://127.0.0.1:{port}/')).start()   # give the server a moment
    print(f'Yu-Gi-Oh! Randomizer running locally at http://127.0.0.1:{port}/')  # tell the user where it is
    print('Close this window to stop the offline website.')  # and how to stop it
    uvicorn.run(offline_app, host='127.0.0.1', port=port)        # 127.0.0.1 = only this computer can connect


if __name__ == '__main__':                                  # only when this file is run directly
    serve()                                                 # start it
