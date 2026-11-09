// offline.js — the card-database updater panel. Only the offline version (web/offline.py) loads this.
// The whole script is wrapped in (() => { ... })() so its names don't clash with app.js.
(() => {                                                    // run everything inside its own function
  const $ = id => document.getElementById(id);              // shorthand: $('updateLog') = the element with that id
  let polling = null;                 // the repeating timer while a job runs (null when idle)
  let previousPhase = 'idle';         // lets us notice the moment a check finishes

  // What to show for each phase the server reports.
  const PHASE_TEXT = {                                      // phase -> text
    checking: 'Checking for updates…',                      // (a check is running)
    updating: 'Updating local files…',                      // (an update is running)
    done: 'Ready.',                                         // (finished)
    idle: 'Not checked yet.',                               // (nothing run yet)
  };

  // Ask the server how the updater is doing, and update the panel.
  async function refreshStatus() {                          // function: read the updater's state and update the panel
    const res = await fetch('/api/local-update/status');    // ask the server
    if (!res.ok) throw Error('Unable to read updater status');  // failed:
    const s = await res.json();                             // read the JSON answer

    $('updateStatus').textContent = s.error || PHASE_TEXT[s.phase];  // the error, or the phase's text
    $('updateLog').textContent = s.logs.join('\n');         // the log, one line each
    for (const id of ['updateCheck', 'updateCards', 'updateLists', 'updateStaples']) $(id).disabled = s.busy;   // no clicks mid-job

    if (s.check) {                                         // a check has finished at least once
      $('updateChoices').classList.remove('hidden');        // show the update choices
      $('updateCards').hidden = !s.check.needs_cards;      // only offer a rebuild when there's something new
      $('updateLists').hidden = false;                      // Refresh Banlists is always offered
      if (s.phase === 'done' && previousPhase === 'checking') {   // the check JUST finished: say what it found
        $('updateStatus').textContent = s.check.needs_cards  // new cards / a changed step script:
          // offer the card update
          ? `Update available: latest ${s.check.latest}, installed ${s.check.installed || 'unknown'}. Choose Update Card Database to rebuild.`
          : `Cards up to date (${s.check.latest}). You can still refresh forbidden lists.`;  // otherwise say it's up to date
      }
    }

    // Poll every 1.2 seconds while busy; stop when the job ends.
    if (s.busy && !polling) polling = setInterval(() => refreshStatus().catch(showError), 1200);  // busy and not polling yet: ask again every 1.2 seconds
    else if (!s.busy && polling) { clearInterval(polling); polling = null; }  // finished: stop asking
    previousPhase = s.phase;                                // remember the phase for next time
  }

  function showError(e) {                                   // function: show an error in the status line
    $('updateStatus').textContent = e.message;              // its message
  }

  // Start a job on the server ('check', 'run/cards', 'run/banlists' or 'run/staples').
  async function start(path) {                              // function: start one updater job
    try {                                                   // (try it)
      const res = await fetch('/api/local-update/' + path, { method: 'POST' });  // ask the server to start it
      if (!res.ok) {                                        // refused:
        const data = await res.json();                      // its message
        throw Error(data.detail || 'Update request failed');  // (show it)
      }
      await refreshStatus();                                // show the new state at once
    } catch (e) {                                           // something went wrong:
      showError(e);                                         // show it
    }
  }

  $('updateCheck').onclick = () => start('check');          // Check for New Cards
  $('updateCards').onclick = () => {                        // Update Card Database:
    // ask first
    if (confirm('Rebuild cards.csv and Stats.csv now? This may take several minutes and requires internet access.')) {
      start('run/cards');                                   // then start it
    }
  };
  $('updateLists').onclick = () => start('run/banlists');   // Refresh Banlists
  $('updateStaples').onclick = () => {                     // its own job: every format's deck data, however long it takes
    // ask first
    if (confirm('Download the deck data for every format whose staples are not finished? The first time this can take an hour or more. Progress is saved, so you can stop and carry on later.')) {
      start('run/staples');                                 // then start it
    }
  };

  refreshStatus().catch(showError);                       // show the current state when the page opens
})();                                                       // (run the wrapper function now)
