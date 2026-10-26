// offline.js — the card-database updater panel. Only the offline version (web/offline.py) loads this.
// The whole script is wrapped in (() => { ... })() so its names don't clash with app.js.
(() => {
  const $ = id => document.getElementById(id);
  let polling = null;                 // the repeating timer while a job runs (null when idle)
  let previousPhase = 'idle';         // lets us notice the moment a check finishes

  // What to show for each phase the server reports.
  const PHASE_TEXT = {
    checking: 'Checking for updates…',
    updating: 'Updating local files…',
    done: 'Ready.',
    idle: 'Not checked yet.',
  };

  // Ask the server how the updater is doing, and update the panel.
  async function refreshStatus() {
    const res = await fetch('/api/local-update/status');
    if (!res.ok) throw Error('Unable to read updater status');
    const s = await res.json();

    $('updateStatus').textContent = s.error || PHASE_TEXT[s.phase];
    $('updateLog').textContent = s.logs.join('\n');
    for (const id of ['updateCheck', 'updateCards', 'updateLists']) $(id).disabled = s.busy;   // no clicks mid-job

    if (s.check) {                                         // a check has finished at least once
      $('updateChoices').classList.remove('hidden');
      $('updateCards').hidden = !s.check.needs_cards;      // only offer a rebuild when there's something new
      $('updateLists').hidden = false;
      if (s.phase === 'done' && previousPhase === 'checking') {   // the check JUST finished: say what it found
        $('updateStatus').textContent = s.check.needs_cards
          ? `Update available: latest ${s.check.latest}, installed ${s.check.installed || 'unknown'}. Choose Update Card Database to rebuild.`
          : `Cards up to date (${s.check.latest}). You can still refresh forbidden lists.`;
      }
    }

    // Poll every 1.2 seconds while busy; stop when the job ends.
    if (s.busy && !polling) polling = setInterval(() => refreshStatus().catch(showError), 1200);
    else if (!s.busy && polling) { clearInterval(polling); polling = null; }
    previousPhase = s.phase;
  }

  function showError(e) {
    $('updateStatus').textContent = e.message;
  }

  // Start a job on the server ('check', 'run/cards' or 'run/banlists').
  async function start(path) {
    try {
      const res = await fetch('/api/local-update/' + path, { method: 'POST' });
      if (!res.ok) {
        const data = await res.json();
        throw Error(data.detail || 'Update request failed');
      }
      await refreshStatus();
    } catch (e) {
      showError(e);
    }
  }

  $('updateCheck').onclick = () => start('check');
  $('updateCards').onclick = () => {
    if (confirm('Rebuild cards.csv and Stats.csv now? This may take several minutes and requires internet access.')) {
      start('run/cards');
    }
  };
  $('updateLists').onclick = () => start('run/banlists');

  refreshStatus().catch(showError);                       // show the current state when the page opens
})();
