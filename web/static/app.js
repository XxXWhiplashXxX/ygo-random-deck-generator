// app.js — everything the generator page does in the browser:
//   1. fill the controls from /api/options
//   2. keep linked percentages adding up to 100 (and Monster/Spell/Trap adding up to the Main Deck size)
//   3. searchable Theme / Support boxes
//   4. send the settings to /api/generate and draw the deck
//   5. exports: TXT, YDK, PNG and copy-to-clipboard

// ===================================================================
// Shared values and tiny helpers
// ===================================================================
let current = null;                        // the last generated deck (null until the first one)
const form = document.getElementById('settings');
const $ = id => document.getElementById(id);              // shorthand: $('png') = the element with id="png"
const get = key => form.elements.namedItem(key);           // the form input whose name="key"

// Groups of settings that must add up to a total.
const ALLOCATION = ['deck_type_overall_pct', 'support_overall_pct', 'generic_overall_pct'];   // total 100
const EXTRA_MIX = ['extra_fusion_pct', 'extra_synchro_pct', 'extra_xyz_pct', 'extra_link_pct']; // total 100
const CARD_MIX = ['monster_target', 'spell_target', 'trap_target'];                           // total = Main Deck size
const LEVEL_MIX = ['level_low_pct', 'level_mid_pct', 'level_high_pct'];                        // total 100
let defaults = {};                         // the server's starting values (a share link only stores what differs)

// The four categories a deck reports, with the names shown to the player. (One list, used everywhere.)
const CATEGORIES = [['theme1', 'Theme 1'], ['theme2', 'Theme 2'], ['support1', 'Support 1'], ['support2', 'Support 2']];

// A whole number kept between min and max ('abc' counts as 0).
const clamp = (n, min, max) => Math.max(min, Math.min(max, Math.round(Number(n) || 0)));

// Add up the current values of a group of inputs.
const total = keys => keys.reduce((sum, key) => sum + Number(get(key).value), 0);

// Every named input as {name: value}: tick boxes -> true/false, sliders -> numbers, the rest -> text.
function readSettings() {
  const named = [...form.elements].filter(el => el.name);
  return Object.fromEntries(named.map(el => [
    el.name,
    el.type === 'checkbox' ? el.checked : el.type === 'range' ? Number(el.value) : el.value,
  ]));
}

// Set one input by name (ignores names the page doesn't have, like deck2_pct).
function put(key, value) {
  const el = get(key);
  if (!el) return;
  if (el.type === 'checkbox') el.checked = !!value;
  else el.value = value ?? '';
}

// ===================================================================
// Keeping linked numbers balanced
// ===================================================================
// After `changed` moves, share what's left of `target` among the other keys,
// keeping their sizes in proportion to each other (whole numbers only).
function balance(keys, changed, target) {
  const value = clamp(get(changed).value, 0, target);
  get(changed).value = value;
  const others = keys.filter(k => k !== changed);
  const remaining = target - value;
  const weights = others.map(k => Math.max(0, Number(get(k).value) || 0));
  const weightSum = weights.reduce((a, b) => a + b, 0);
  // Exact (decimal) share for each; if all others are 0, split evenly.
  const exact = others.map((_, i) => weightSum ? remaining * weights[i] / weightSum : remaining / others.length);
  const amounts = exact.map(Math.floor);                                   // round everything down first ...
  const leftOver = remaining - amounts.reduce((a, b) => a + b, 0);
  // ... then hand the leftover 1s to the values that lost the biggest fraction ('largest remainder').
  const order = exact.map((v, i) => ({ i, fraction: v - amounts[i] })).sort((a, b) => b.fraction - a.fraction);
  for (let n = 0; n < leftOver; n++) amounts[order[n].i]++;
  others.forEach((k, i) => { get(k).value = amounts[i]; });
}

// When the Main Deck size changes, rescale Monsters/Spells/Traps to the new size.
function rescaleCardMix(newSize) {
  const size = clamp(newSize, 40, 60);
  const old = total(CARD_MIX);
  const scaled = CARD_MIX.map(k => (old ? Math.floor(Number(get(k).value) * size / old) : 0));
  const remaining = size - scaled.reduce((a, b) => a + b, 0);
  for (let n = 0; n < remaining; n++) scaled[n % 3]++;          // spread the leftover cards one at a time
  CARD_MIX.forEach((k, i) => put(k, scaled[i]));
}

// Show a running total, coloured red when it isn't 100.
function showTotal(id, sum, suffix) {
  $(id).textContent = sum + suffix;
  $(id).style.color = sum === 100 ? '#a8c6ff' : '#ff9b9b';
}

// Copy every slider's value into its little number box, and refresh the totals.
function sync() {
  for (const box of document.querySelectorAll('.percent-number')) box.value = get(box.dataset.for).value;
  showTotal('extraTotal', total(EXTRA_MIX), '% total');
  showTotal('total', total(ALLOCATION), '%');
  showTotal('levelTotal', total(LEVEL_MIX), '% total');
}

form.addEventListener('input', e => {
  const target = e.target;
  const key = target.dataset.for || target.name;        // a number box speaks for its slider (data-for)
  if (target.matches('.percent-number')) {
    if (target.value === '') return;                     // let the player clear the box while typing
    put(key, clamp(target.value, 0, 100));               // copy the typed number onto the slider
  }
  if (ALLOCATION.includes(key)) balance(ALLOCATION, key, 100);
  if (EXTRA_MIX.includes(key)) balance(EXTRA_MIX, key, 100);
  if (LEVEL_MIX.includes(key)) balance(LEVEL_MIX, key, 100);
  if (CARD_MIX.includes(key)) balance(CARD_MIX, key, clamp(get('main_size').value, 40, 60));
  if (key === 'main_size') rescaleCardMix(target.value);
  sync();
});

// If a number box is left empty, put the slider's value back into it.
form.addEventListener('change', e => {
  if (e.target.matches('.percent-number') && e.target.value === '') e.target.value = get(e.target.dataset.for).value;
});

// ===================================================================
// Searchable Theme / Support boxes
// ===================================================================
// Labels look like 'Archetype = Blue-Eyes'. Typing searches the part after '=',
// but the box always shows (and sends) the complete label.
// Each box searches its own list (its data-list attribute): Theme boxes get theme_labels, Support boxes get labels.
const labelLists = new Map();              // input element -> the list of labels it allows

function searchText(label) {
  const afterEquals = label.includes('=') ? label.split(/=(.*)/s)[1] : label;   // split at the FIRST '=' only
  return afterEquals.trim().toLocaleLowerCase();
}

function setupLabelSearch(lists) {
  for (const input of document.querySelectorAll('input.label-search')) {
    const labels = lists[input.dataset.list] || [];     // this box's own list
    labelLists.set(input, labels);
    const popup = input.nextElementSibling;              // the suggestion list right after the input
    let selected = '';                                   // the last real label chosen in this box
    let active = -1;                                     // which suggestion the arrow keys are on (-1 = none)
    let visible = [];                                    // the suggestions currently shown

    // Link the box and its list for screen readers.
    popup.id = `suggestions-${input.name}`;
    input.setAttribute('aria-controls', popup.id);

    const remember = value => { selected = value; input.dataset.lastValid = value; };

    function close() {
      popup.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      active = -1;
    }

    function choose(value) {
      remember(value);
      input.value = value;
      close();
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function highlightActive() {
      [...popup.children].forEach((node, i) => {
        node.classList.toggle('active', i === active);
        node.setAttribute('aria-selected', String(i === active));
      });
    }

    function showSuggestions() {
      const typed = input.value.trim().toLocaleLowerCase();
      // If the box already holds a full label, show everything instead of filtering by it.
      const query = labels.includes(input.value) ? '' : typed;
      visible = (query ? labels.filter(label => searchText(label).includes(query)) : labels).slice(0, 40);
      popup.replaceChildren(...visible.map((label, i) => {
        const item = document.createElement('div');
        item.className = 'label-option';
        item.role = 'option';
        item.id = `${popup.id}-${i}`;
        item.textContent = label;
        // mousedown (not click) fires before the input loses focus, so the choice isn't lost.
        item.addEventListener('mousedown', e => { e.preventDefault(); choose(label); });
        return item;
      }));
      active = -1;
      popup.hidden = !visible.length;
      input.setAttribute('aria-expanded', String(!!visible.length));
    }

    input.addEventListener('focus', () => {
      if (labels.includes(input.value)) remember(input.value);   // put() may have set it directly
      showSuggestions();
    });

    input.addEventListener('input', () => {
      if (labels.includes(input.value)) remember(input.value);
      showSuggestions();
    });

    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') { close(); return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();                              // stop the cursor jumping in the text
        if (popup.hidden) showSuggestions();
        if (!visible.length) return;
        const step = e.key === 'ArrowDown' ? 1 : -1;
        active = (active + step + visible.length) % visible.length;   // wrap around at either end
        highlightActive();
        popup.children[active].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter' && !popup.hidden && visible.length) {
        e.preventDefault();                              // pick the suggestion instead of submitting the form
        choose(visible[Math.max(0, active)]);
      }
    });

    // Leaving the box with half-typed text puts the last real label back.
    input.addEventListener('blur', () => {
      setTimeout(() => {                                 // wait a moment so a click on a suggestion lands first
        if (!labels.includes(input.value)) input.value = selected || input.dataset.lastValid || labels[0] || '';
        close();
      }, 100);
    });
  }
}

// ===================================================================
// Start-up: fill the controls
// ===================================================================
async function init() {
  const res = await fetch('/api/options');
  if (!res.ok) throw Error('Could not load settings');
  const data = await res.json();
  // Each dropdown names its list in data-options (banlists / eras / orders).
  for (const select of document.querySelectorAll('select[data-options]')) {
    const values = data[select.dataset.options];
    select.replaceChildren(...values.map(v => new Option(v, v)));
  }
  setupLabelSearch(data);                                // each search box picks its own list from data
  // The server's defaults are the only defaults (web/app.py, WEB_DEFAULTS).
  defaults = data.defaults;
  for (const [key, value] of Object.entries(defaults)) put(key, value);
  sync();
  refreshPresetList();
  // Opened from a share link? Put its settings in and build the same deck straight away.
  const shared = readShareLink();
  if (shared) {
    applySettings(shared);
    $('status').textContent = 'Loaded a shared deck. Building it…';
    form.requestSubmit();                                 // same as pressing Generate Deck
  }
}

// Put a whole settings object into the form (unknown keys are ignored by put()).
function applySettings(values) {
  for (const [key, value] of Object.entries(values)) put(key, value);
  for (const input of document.querySelectorAll('input.label-search')) input.dataset.lastValid = input.value;
  sync();
}

// ===================================================================
// Showing a deck
// ===================================================================
// One card: picture, name, where it came from. Clicking/tapping it shows why it was picked.
function cardElement(card) {
  const div = document.createElement('div');
  div.className = 'card';
  div.tabIndex = 0;                                       // reachable with the Tab key
  div.title = card.reason;                                // hover tooltip on desktop

  const img = document.createElement('img');
  img.loading = 'lazy';                                   // only download pictures as they scroll into view
  img.alt = card.name;
  img.src = '/api/card-image/' + encodeURIComponent(card.id);
  img.onerror = () => { img.style.visibility = 'hidden'; };   // missing picture: keep the space, hide the broken icon

  const name = document.createElement('strong');
  name.textContent = card.name;
  const origin = document.createElement('span');
  origin.textContent = card.origin;
  const reason = document.createElement('small');
  reason.className = 'reason';
  reason.textContent = card.reason || 'No reason recorded.';

  const toggle = () => div.classList.toggle('show-reason');   // CSS shows .reason only with this class
  div.addEventListener('click', toggle);
  div.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });

  div.append(img, name, origin, reason);
  return div;
}

function drawGrid(id, cards) {
  $(id).replaceChildren(...cards.map(cardElement));
}

// 'Theme 1: Archetype = Blue-Eyes — details' for each category.
function categoryLines(categories) {
  return CATEGORIES.map(([key, name]) => {
    const c = categories[key] || {};
    return `${name}: ${c.label || 'Automatic'}${c.detail ? ' — ' + c.detail : ''}`;
  });
}

// The heading used by the TXT export, the copy button and the log.
function headerText() {
  if (!current) return '';
  return 'Yu-Gi-Oh! Random Deck Generator\n'
    + `Seed: ${current.seed}\n`
    + `${current.breakdown}\n`
    + 'Selected categories:\n'
    + categoryLines(current.categories).map(line => '  ' + line).join('\n') + '\n';
}

// The full decklist as text.
function deckText() {
  const section = (title, cards) => `${title} (${cards.length}):\n`
    + cards.map(c => `- ${c.name} [${c.origin}]${c.reason ? ' :: ' + c.reason : ''}`).join('\n');
  return headerText() + '\n' + section('Main Deck', current.main) + '\n\n' + section('Extra Deck', current.extra) + '\n';
}

function showDeck(deck) {
  current = deck;
  $('resultTitle').textContent = 'Generated deck · seed ' + deck.seed;
  $('status').textContent = 'Click or tap a card to see why it was selected. Export or copy your result above.';

  // Summary panel, built with textContent (never innerHTML), so card text can't inject HTML.
  const summary = $('summary');
  const heading = document.createElement('strong');
  heading.textContent = 'Selected categories';
  const lines = categoryLines(deck.categories).map(line => {
    const div = document.createElement('div');
    div.textContent = line;
    return div;
  });
  const breakdown = document.createElement('pre');
  breakdown.textContent = deck.breakdown;
  summary.replaceChildren(heading, ...lines, document.createElement('hr'), breakdown);
  summary.classList.remove('hidden');

  $('deck').classList.remove('hidden');
  $('mainCount').textContent = deck.main.length + ' cards';
  $('extraCount').textContent = deck.extra.length + ' cards';
  drawGrid('mainCards', deck.main);
  drawGrid('extraCards', deck.extra);
  $('log').textContent = deckText();
  for (const id of ['png', 'ydk', 'txt', 'copy', 'share']) $(id).disabled = false;

  // Show any values the server rolled (random sizes, ratios ...). The seed box is never filled by the
  // server for a blank seed, so the next click still makes a new deck.
  for (const [key, value] of Object.entries(deck.settings)) put(key, value);
  sync();
}

// ===================================================================
// Generate button
// ===================================================================
form.addEventListener('submit', async e => {
  e.preventDefault();                                     // stay on this page (no normal form submit)

  // Make sure every category box holds a real label before sending.
  for (const input of document.querySelectorAll('input.label-search')) {
    const labels = labelLists.get(input) || [];
    if (document.activeElement === input) input.blur();
    if (!labels.includes(input.value)) input.value = input.dataset.lastValid || labels[0] || '';
  }

  // Quick checks here too, so the player gets the message instantly (the server checks again).
  const settings = readSettings();
  if (total(ALLOCATION) !== 100) { $('status').textContent = 'Theme + Support + Generic must equal 100%.'; return; }
  if (!settings.rand_extra_ratios && total(EXTRA_MIX) !== 100) {
    $('status').textContent = 'Extra Deck types must total 100%.';
    return;
  }
  if (settings.level_sliders && !settings.rand_levels && total(LEVEL_MIX) !== 100) {
    $('status').textContent = 'Level 1-4, 5-6 and 7+ must total 100%.';
    return;
  }

  const button = $('generate');
  button.disabled = true;
  button.textContent = 'Generating…';
  $('status').textContent = 'Generating deck. This may take a moment.';
  try {
    const res = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings }),
    });
    const data = await res.json();
    if (!res.ok) throw Error(data.detail || 'Generation failed');   // the server's error message
    showDeck(data);
  } catch (err) {
    $('status').textContent = err.message;
  } finally {
    button.disabled = false;                              // runs whether it worked or not
    button.textContent = 'Generate Deck';
  }
});

// ===================================================================
// Exports
// ===================================================================
// Save a Blob as a file by clicking a temporary download link.
function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);       // free the memory once the download has started
}

$('txt').onclick = () => download(new Blob([deckText()], { type: 'text/plain;charset=utf-8' }), `ygo-deck-${current.seed}.txt`);

// YDK = the EDOPro / YGOPRO deck format: one passcode per line under #main, #extra and !side.
$('ydk').onclick = () => {
  const ids = cards => cards.map(c => c.id).filter(id => /^\d+$/.test(id)).join('\n');   // digits only
  const ydk = `#created by ygo_randomizer\n#main\n${ids(current.main)}\n#extra\n${ids(current.extra)}\n!side\n`;
  download(new Blob([ydk], { type: 'text/plain' }), `ygo-deck-${current.seed}.ydk`);
};

$('copy').onclick = async () => {
  try {
    await navigator.clipboard.writeText(deckText());
    $('status').textContent = 'Decklist copied.';
  } catch {
    $('status').textContent = 'Clipboard unavailable; use Export TXT.';   // e.g. older browsers, non-https pages
  }
};

// ---------- PNG export: draw the whole deck onto a canvas ----------
const PNG = {
  width: 1500, margin: 35, cols: 10,                      // image width, outer margin, cards per row
  cardW: 124, cardH: 181, gap: 13,                        // card picture size and gap between cards
  titleH: 200,                                            // space for the title and categories
};
PNG.rowH = PNG.cardH + 37;                                // one row = picture + room for the name under it

// Load each picture once, even if the deck has 3 copies (null if it fails to load).
function loadImage(cache, id) {
  if (!cache.has(id)) {
    cache.set(id, new Promise(resolve => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = '/api/card-image/' + encodeURIComponent(id);   // same site, so the canvas can still be saved
    }));
  }
  return cache.get(id);
}

// Draw one titled block of cards starting at height y.
async function drawSection(ctx, cache, cards, y, title) {
  ctx.fillStyle = '#f2f5ff';
  ctx.font = 'bold 27px Arial';
  ctx.fillText(`${title} (${cards.length})`, PNG.margin, y - 17);
  await Promise.all(cards.map(async (card, i) => {
    const x = PNG.margin + (i % PNG.cols) * (PNG.cardW + PNG.gap);   // column
    const top = y + Math.floor(i / PNG.cols) * PNG.rowH;             // row
    const img = await loadImage(cache, card.id);
    if (img) ctx.drawImage(img, x, top, PNG.cardW, PNG.cardH);
    else { ctx.fillStyle = '#34445c'; ctx.fillRect(x, top, PNG.cardW, PNG.cardH); }   // grey box if no picture
    ctx.fillStyle = '#dbe6fb';
    ctx.font = '12px Arial';
    const name = card.name.length > 19 ? card.name.slice(0, 18) + '…' : card.name;   // short enough to fit
    ctx.fillText(name, x, top + PNG.cardH + 17);
  }));
}

$('png').onclick = async () => {
  const button = $('png');
  button.disabled = true;
  button.textContent = 'Rendering…';
  try {
    const rows = n => Math.ceil(n / PNG.cols);
    const mainY = PNG.titleH + 50;
    const extraY = mainY + rows(current.main.length) * PNG.rowH + 80;
    const height = extraY + rows(current.extra.length) * PNG.rowH + 75;

    const canvas = document.createElement('canvas');
    canvas.width = PNG.width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#111827';                            // background
    ctx.fillRect(0, 0, PNG.width, height);

    // Title, seed and categories.
    ctx.fillStyle = '#f3f6ff';
    ctx.font = 'bold 35px Arial';
    ctx.fillText('YU-GI-OH! RANDOM DECK', PNG.margin, 53);
    ctx.font = '20px Arial';
    ctx.fillStyle = '#aac2e9';
    ctx.fillText('Seed: ' + current.seed, PNG.margin, 89);
    ctx.font = '17px Arial';
    CATEGORIES.forEach(([key, name], i) => {
      const text = name + ': ' + (current.categories[key]?.label || 'Automatic');
      ctx.fillText(text.slice(0, 85), PNG.margin, 120 + i * 23);
    });

    const cache = new Map();
    await drawSection(ctx, cache, current.main, mainY, 'MAIN DECK');
    await drawSection(ctx, cache, current.extra, extraY, 'EXTRA DECK');
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) throw Error('PNG export failed');
    download(blob, `ygo-deck-${current.seed}.png`);
  } catch (e) {
    $('status').textContent = 'PNG export failed: ' + e.message;
  } finally {
    button.disabled = false;
    button.textContent = 'Export PNG';
  }
};

// ===================================================================
// Share link: the settings + seed of this deck, packed into the address after '#'
// ===================================================================
// The part after '#' never reaches the server, and the link only stores settings that differ from the
// defaults, so it stays short. Same settings + same seed + same card database = the same deck.

// Text -> URL-safe base64 (handles any characters, like ☆ in card names).
function toBase64Url(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// URL-safe base64 -> text (the reverse of toBase64Url).
function fromBase64Url(code) {
  const binary = atob(code.replace(/-/g, '+').replace(/_/g, '/'));
  return new TextDecoder().decode(Uint8Array.from(binary, ch => ch.charCodeAt(0)));
}

function shareLink() {
  const page = new Set(Object.keys(readSettings()));      // only settings this page has
  const values = {};
  for (const [key, value] of Object.entries(current.settings)) {
    if (!page.has(key)) continue;
    // Every 'Randomize ...' box is turned OFF: the rolled values are stored instead, so nothing re-rolls.
    const fixed = key.startsWith('rand_') ? false : value;
    if (String(fixed) !== String(defaults[key])) values[key] = fixed;   // skip anything already at its default
  }
  values.seed = String(current.seed);                     // the seed that made this deck
  return location.origin + location.pathname + '#s=' + toBase64Url(JSON.stringify(values));
}

// The settings stored in the address (or null if there are none, or the link is broken).
function readShareLink() {
  if (!location.hash.startsWith('#s=')) return null;
  try {
    return JSON.parse(fromBase64Url(location.hash.slice(3)));
  } catch {
    $('status').textContent = 'That share link is broken; starting from the defaults.';
    return null;
  }
}

$('share').onclick = async () => {
  const link = shareLink();
  try {
    await navigator.clipboard.writeText(link);
    $('status').textContent = 'Share link copied. Anyone who opens it gets this exact deck.';
  } catch {
    prompt('Copy this link:', link);                       // no clipboard access: show it to copy by hand
  }
};

// ===================================================================
// Presets: named settings saved in THIS browser (localStorage)
// ===================================================================
// localStorage can be missing or blocked (private windows, some phones), so every use is wrapped in
// try/catch and the page works without it.
const PRESET_KEY = 'ygo-randomizer-presets';

function loadPresets() {
  try {
    return JSON.parse(localStorage.getItem(PRESET_KEY) || '{}');
  } catch {
    return {};
  }
}

function savePresets(presets) {
  try {
    localStorage.setItem(PRESET_KEY, JSON.stringify(presets));
    return true;
  } catch {
    $('status').textContent = 'This browser won\'t let the page save presets.';
    return false;
  }
}

// Rebuild the dropdown from what is saved.
function refreshPresetList(selected = '') {
  const names = Object.keys(loadPresets()).sort((a, b) => a.localeCompare(b));
  $('presetList').replaceChildren(new Option('(choose a preset)', ''), ...names.map(n => new Option(n, n)));
  $('presetList').value = selected;
}

$('presetSave').onclick = () => {
  const name = (prompt('Name for this preset:') || '').trim();
  if (!name) return;                                      // cancelled
  const presets = loadPresets();
  const values = readSettings();
  delete values.seed;                                     // a preset is settings, not one particular deck
  presets[name] = values;
  if (savePresets(presets)) {
    refreshPresetList(name);
    $('status').textContent = `Preset "${name}" saved.`;
  }
};

$('presetList').onchange = () => {                        // choosing a preset loads it
  const values = loadPresets()[$('presetList').value];
  if (!values) return;
  applySettings(values);
  $('status').textContent = `Preset "${$('presetList').value}" loaded.`;
};

$('presetDelete').onclick = () => {
  const name = $('presetList').value;
  if (!name || !confirm(`Delete the preset "${name}"?`)) return;
  const presets = loadPresets();
  delete presets[name];
  if (savePresets(presets)) {
    refreshPresetList();
    $('status').textContent = `Preset "${name}" deleted.`;
  }
};

// Go!
init().catch(e => { $('status').textContent = e.message; });
