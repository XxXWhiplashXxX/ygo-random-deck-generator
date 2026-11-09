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
const form = document.getElementById('settings');           // the settings form
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
const clamp = (n, min, max) => Math.max(min, Math.min(max, Math.round(Number(n) || 0)));  // (rounded; NaN becomes 0)

// Add up the current values of a group of inputs.
const total = keys => keys.reduce((sum, key) => sum + Number(get(key).value), 0);  // (adds up their numbers)

// Every named input as {name: value}: tick boxes -> true/false, sliders -> numbers, the rest -> text.
function readSettings() {                                   // function: every setting on the page, as {name: value}
  const named = [...form.elements].filter(el => el.name);   // every input that has a name
  return Object.fromEntries(named.map(el => [               // make {name: value} pairs
    el.name,                                                // the name...
    el.type === 'checkbox' ? el.checked : el.type === 'range' ? Number(el.value) : el.value,  // ...and its value in the right form
  ]));
}

// Set one input by name (ignores names the page doesn't have, like deck2_pct).
function put(key, value) {                                  // function: set one input
  const el = get(key);                                      // the input
  if (!el) return;                                          // the page doesn't have it: do nothing
  if (el.type === 'checkbox') el.checked = !!value;         // tick box: on / off
  else el.value = value ?? '';                              // anything else: its value (blank if missing)
}

// ===================================================================
// Keeping linked numbers balanced
// ===================================================================
// After `changed` moves, share what's left of `target` among the other keys,
// keeping their sizes in proportion to each other (whole numbers only).
function balance(keys, changed, target) {                   // function: keep a group of inputs adding up to `target`
  const value = clamp(get(changed).value, 0, target);       // the moved input, kept inside 0..target
  get(changed).value = value;                               // (write the kept value back)
  const others = keys.filter(k => k !== changed);           // every other input in the group
  const remaining = target - value;                         // what's left for them
  const weights = others.map(k => Math.max(0, Number(get(k).value) || 0));  // their current sizes (no negatives)
  const weightSum = weights.reduce((a, b) => a + b, 0);     // their sizes added up
  // Exact (decimal) share for each; if all others are 0, split evenly.
  const exact = others.map((_, i) => weightSum ? remaining * weights[i] / weightSum : remaining / others.length);
  const amounts = exact.map(Math.floor);                                   // round everything down first ...
  const leftOver = remaining - amounts.reduce((a, b) => a + b, 0);  // how many 1s are still to hand out
  // ... then hand the leftover 1s to the values that lost the biggest fraction ('largest remainder').
  const order = exact.map((v, i) => ({ i, fraction: v - amounts[i] })).sort((a, b) => b.fraction - a.fraction);
  for (let n = 0; n < leftOver; n++) amounts[order[n].i]++;  // hand out the leftover 1s
  others.forEach((k, i) => { get(k).value = amounts[i]; });  // write the new values
}

// When the Main Deck size changes, rescale Monsters/Spells/Traps to the new size.
function rescaleCardMix(newSize) {                          // function: rescale Monsters/Spells/Traps to a new Main Deck size
  const size = clamp(newSize, 40, 60);                      // the new size, kept between 40 and 60
  const old = total(CARD_MIX);                              // their old total
  const scaled = CARD_MIX.map(k => (old ? Math.floor(Number(get(k).value) * size / old) : 0));  // each scaled to the new size (rounded down)
  const remaining = size - scaled.reduce((a, b) => a + b, 0);  // cards still to hand out
  for (let n = 0; n < remaining; n++) scaled[n % 3]++;          // spread the leftover cards one at a time
  CARD_MIX.forEach((k, i) => put(k, scaled[i]));            // write them back
}

// Show a running total, coloured red when it isn't 100.
function showTotal(id, sum, suffix) {                       // function: show a group's total in a label
  $(id).textContent = sum + suffix;                         // the text
  $(id).style.color = sum === 100 ? '#a8c6ff' : '#ff9b9b';  // blue at 100, red otherwise
}

// Copy every slider's value into its little number box, and refresh the totals.
function sync() {                                           // function: number boxes follow their sliders; totals refresh
  for (const box of document.querySelectorAll('.percent-number')) box.value = get(box.dataset.for).value;  // each number box shows its slider's value
  showTotal('extraTotal', total(EXTRA_MIX), '% total');     // Extra Deck total
  showTotal('total', total(ALLOCATION), '%');               // Theme / Support / Generic total
  showTotal('levelTotal', total(LEVEL_MIX), '% total');     // Level total
}

form.addEventListener('input', e => {                       // whenever any input changes as you type or drag:
  const target = e.target;                                  // the input that changed
  const key = target.dataset.for || target.name;        // a number box speaks for its slider (data-for)
  if (target.matches('.percent-number')) {                  // a number box:
    if (target.value === '') return;                     // let the player clear the box while typing
    put(key, clamp(target.value, 0, 100));               // copy the typed number onto the slider
  }
  if (ALLOCATION.includes(key)) balance(ALLOCATION, key, 100);  // rebalance whichever group it belongs to
  if (EXTRA_MIX.includes(key)) balance(EXTRA_MIX, key, 100);  // (Extra Deck mix)
  if (LEVEL_MIX.includes(key)) balance(LEVEL_MIX, key, 100);  // (Level mix)
  if (CARD_MIX.includes(key)) balance(CARD_MIX, key, clamp(get('main_size').value, 40, 60));  // (Monster/Spell/Trap, up to the Main Deck size)
  if (key === 'main_size') rescaleCardMix(target.value);    // the Main Deck size moved: rescale the card mix
  sync();                                                   // refresh the boxes and totals
});

// If a number box is left empty, put the slider's value back into it.
form.addEventListener('change', e => {                      // when an input is finished with:
  // an empty number box gets its slider's value back
  if (e.target.matches('.percent-number') && e.target.value === '') e.target.value = get(e.target.dataset.for).value;
});

// ===================================================================
// Searchable Theme / Support boxes
// ===================================================================
// Labels look like 'Archetype = Blue-Eyes'. Typing searches the part after '=',
// but the box always shows (and sends) the complete label.
// Each box searches its own list (its data-list attribute): Theme boxes get theme_labels, Support boxes get labels.
const labelLists = new Map();              // input element -> the list of labels it allows

function searchText(label) {                                // function: the part of a label the search looks at
  const afterEquals = label.includes('=') ? label.split(/=(.*)/s)[1] : label;   // split at the FIRST '=' only
  return afterEquals.trim().toLocaleLowerCase();            // trimmed, lower-case
}

function setupLabelSearch(lists) {                          // function: set up every Theme / Support search box
  for (const input of document.querySelectorAll('input.label-search')) {  // each search box
    const labels = lists[input.dataset.list] || [];     // this box's own list
    labelLists.set(input, labels);                          // remember it
    const popup = input.nextElementSibling;              // the suggestion list right after the input
    let selected = '';                                   // the last real label chosen in this box
    let active = -1;                                     // which suggestion the arrow keys are on (-1 = none)
    let visible = [];                                    // the suggestions currently shown

    // Link the box and its list for screen readers.
    popup.id = `suggestions-${input.name}`;                 // an id for the suggestion list
    input.setAttribute('aria-controls', popup.id);          // tie the box to it

    const remember = value => { selected = value; input.dataset.lastValid = value; };  // inner function: remember a chosen label

    function close() {                                      // inner function: hide the suggestions
      popup.hidden = true;                                  // hidden
      input.setAttribute('aria-expanded', 'false');         // (for screen readers)
      active = -1;                                          // nothing highlighted
    }

    function choose(value) {                                // inner function: pick one suggestion
      remember(value);                                      // remember it
      input.value = value;                                  // put it in the box
      close();                                              // hide the suggestions
      input.dispatchEvent(new Event('change', { bubbles: true }));  // tell the page the box changed
    }

    function highlightActive() {                            // inner function: highlight the arrow-key suggestion
      [...popup.children].forEach((node, i) => {            // each suggestion
        node.classList.toggle('active', i === active);      // highlighted if it's the active one
        node.setAttribute('aria-selected', String(i === active));  // (for screen readers)
      });
    }

    function showSuggestions() {                            // inner function: draw the suggestions for what's typed
      const typed = input.value.trim().toLocaleLowerCase();  // what's typed, lower-case
      // If the box already holds a full label, show everything instead of filtering by it.
      const query = labels.includes(input.value) ? '' : typed;  // the search text ('' = show all)
      visible = (query ? labels.filter(label => searchText(label).includes(query)) : labels).slice(0, 40);  // the matching labels (40 at most)
      popup.replaceChildren(...visible.map((label, i) => {  // one element per suggestion
        const item = document.createElement('div');         // a new element
        item.className = 'label-option';                    // styled as an option
        item.role = 'option';                               // (for screen readers)
        item.id = `${popup.id}-${i}`;                       // a unique id
        item.textContent = label;                           // the label text
        // mousedown (not click) fires before the input loses focus, so the choice isn't lost.
        item.addEventListener('mousedown', e => { e.preventDefault(); choose(label); });  // choose it on mouse-down
        return item;                                        // hand it back
      }));                                                  // (end of the list)
      active = -1;                                          // nothing highlighted
      popup.hidden = !visible.length;                       // hide the list if it's empty
      input.setAttribute('aria-expanded', String(!!visible.length));  // (for screen readers)
    }

    input.addEventListener('focus', () => {                 // when the box gets focus:
      if (labels.includes(input.value)) remember(input.value);   // put() may have set it directly
      showSuggestions();                                    // show the suggestions
    });

    input.addEventListener('input', () => {                 // as the player types:
      if (labels.includes(input.value)) remember(input.value);  // a full label typed: remember it
      showSuggestions();                                    // show the suggestions
    });

    input.addEventListener('keydown', e => {                // keyboard keys in the box:
      if (e.key === 'Escape') { close(); return; }          // Escape: hide the list
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {   // the arrow keys:
        e.preventDefault();                              // stop the cursor jumping in the text
        if (popup.hidden) showSuggestions();                // open the list if it's closed
        if (!visible.length) return;                        // nothing to move through
        const step = e.key === 'ArrowDown' ? 1 : -1;        // down = +1, up = -1
        active = (active + step + visible.length) % visible.length;   // wrap around at either end
        highlightActive();                                  // show it
        popup.children[active].scrollIntoView({ block: 'nearest' });  // keep it in view
      } else if (e.key === 'Enter' && !popup.hidden && visible.length) {  // Enter with the list open:
        e.preventDefault();                              // pick the suggestion instead of submitting the form
        choose(visible[Math.max(0, active)]);               // the highlighted one (or the first)
      }
    });

    // Leaving the box with half-typed text puts the last real label back.
    input.addEventListener('blur', () => {                  // when the box loses focus:
      setTimeout(() => {                                 // wait a moment so a click on a suggestion lands first
        // not a real label: put the last real one back
        if (!labels.includes(input.value)) input.value = selected || input.dataset.lastValid || labels[0] || '';
        close();                                            // hide the list
      }, 100);                                              // (after 100 ms)
    });
  }
}

// ===================================================================
// 'Base deck around a single card' boxes (Card 1 and Card 2)
// ===================================================================
// Like the Theme boxes, but there are ~14,000 cards, too many to send to the page at once. So as you type,
// the page asks the server for up to 40 matching names (/api/card-names). Unlike the Theme boxes, blank is allowed:
// a blank box means that theme works as usual.
function setupCardSearch(input, popup) {                    // function: set up one card box (Card 1 or Card 2)
  let visible = [];                                      // the names currently shown
  let active = -1;                                       // which one the arrow keys are on (-1 = none)
  let asked = 0;                                         // numbers each request, so a slow old answer can't replace a newer one
  input.setAttribute('aria-controls', popup.id);            // (for screen readers)

  function close() {                                        // inner function: hide the suggestions
    popup.hidden = true;                                    // hidden
    input.setAttribute('aria-expanded', 'false');           // (for screen readers)
    active = -1;                                            // nothing highlighted
  }

  function choose(name) {                                   // inner function: pick one name
    input.value = name;                                     // put it in the box
    close();                                                // hide the suggestions
    markOverridden();                                    // grey out the Theme box this card replaces
  }

  function draw() {                                         // inner function: draw the suggestions
    popup.replaceChildren(...visible.map((name, i) => {     // one element per name
      const item = document.createElement('div');           // a new element
      item.className = 'label-option';                      // styled as an option
      item.role = 'option';                                 // (for screen readers)
      item.id = `${popup.id}-${i}`;                         // a unique id
      item.textContent = name;                           // textContent, never innerHTML: names can't inject HTML
      item.classList.toggle('active', i === active);        // highlighted if it's the active one
      // mousedown (not click) fires before the box loses focus, so the choice isn't lost
      item.addEventListener('mousedown', e => { e.preventDefault(); choose(name); });  // choose it on mouse-down
      return item;                                          // hand it back
    }));                                                    // (end of the list)
    popup.hidden = !visible.length;                         // hide the list if it's empty
    input.setAttribute('aria-expanded', String(!!visible.length));  // (for screen readers)
  }

  async function search() {                                 // inner function: ask the server for matching names
    const typed = input.value.trim();                       // what's typed
    const mine = ++asked;                                // this request's number
    if (typed.length < 2) { visible = []; close(); return; }     // the server waits for 2 letters too
    try {                                                   // (ask the server)
      const res = await fetch('/api/card-names?q=' + encodeURIComponent(typed));  // send what's typed
      const data = await res.json();                        // read the JSON answer
      if (mine !== asked) return;                        // a newer search has started since: ignore this answer
      visible = data.names || [];                           // the names (none if missing)
      active = -1;                                          // nothing highlighted
      draw();                                               // show them
    } catch { close(); }                                 // no answer: just show no suggestions
  }

  input.addEventListener('input', () => { search(); markOverridden(); });  // as the player types: search, and grey the Theme box
  input.addEventListener('focus', search);                  // search when the box gets focus
  input.addEventListener('keydown', e => {                  // keyboard keys in the box:
    if (e.key === 'Escape') { close(); return; }            // Escape: hide the list
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && visible.length) {  // the arrow keys, with names showing:
      e.preventDefault();                                // stop the cursor jumping in the text
      active = (active + (e.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length;   // wrap around
      draw();                                               // show it
      popup.children[active].scrollIntoView({ block: 'nearest' });  // keep it in view
    } else if (e.key === 'Enter' && !popup.hidden && visible.length) {  // Enter with the list open:
      e.preventDefault();                                // pick the name instead of submitting the form
      choose(visible[Math.max(0, active)]);                 // the highlighted one (or the first)
    }
  });
  input.addEventListener('blur', () => setTimeout(close, 100));   // (a moment, so a click on a name lands first)
}

// Both card boxes use the same search; each has its own suggestion list.
function setupAnchorSearch() {                              // function: set up both card boxes
  setupCardSearch($('anchorCard1'), $('anchorSuggestions1'));  // Card 1
  setupCardSearch($('anchorCard2'), $('anchorSuggestions2'));  // Card 2
  for (const key of ['rand_card_1', 'rand_card_2']) get(key).addEventListener('change', markOverridden);  // the Random card boxes grey the Theme box too
}

// Grey out a Theme box while its card box is in use (a name typed in, or Random card ticked).
// The Theme box still works and is still saved; the server just ignores it while the card is used.
function markOverridden() {                                 // function: grey out a Theme box while its card box is used
  const card1 = get('anchor_card').value.trim() !== '' || get('rand_card_1').checked;  // Card 1 in use?
  const card2 = get('anchor_card_2').value.trim() !== '' || get('rand_card_2').checked;  // Card 2 in use?
  get('deck1_label').closest('label').classList.toggle('anchor-overridden', card1);  // grey Theme 1 if so
  get('deck2_label').closest('label').classList.toggle('anchor-overridden', card2);  // grey Theme 2 if so
}

// ===================================================================
// Start-up: fill the controls
// ===================================================================
async function init() {                                     // function: start the page
  const res = await fetch('/api/options');                  // ask the server for the options
  if (!res.ok) throw Error('Could not load settings');      // failed:
  const data = await res.json();                            // read the JSON answer
  // Each dropdown names its list in data-options (banlists / eras / orders).
  for (const select of document.querySelectorAll('select[data-options]')) {  // each dropdown
    const values = data[select.dataset.options];            // its list from the server
    select.replaceChildren(...values.map(v => new Option(v, v)));  // one <option> per value
  }
  setupLabelSearch(data);                                // each search box picks its own list from data
  setupAnchorSearch();                                   // the 'Base deck around a single card' box
  // The server's defaults are the only defaults (web/app.py, WEB_DEFAULTS).
  defaults = data.defaults;                                 // keep the defaults
  for (const [key, value] of Object.entries(defaults)) put(key, value);  // put each one in the form
  sync();                                                   // refresh the boxes and totals
  refreshPresetList();                                      // fill the presets list
  // Opened from a share link? Put its settings in and build the same deck straight away.
  const shared = readShareLink();                           // settings from a share link (or null)
  if (shared) {                                             // opened from a share link:
    applySettings(shared);                                  // put them in the form
    $('status').textContent = 'Loaded a shared deck. Building it…';  // say what's happening
    form.requestSubmit();                                 // same as pressing Generate Deck
  }
}

// Put a whole settings object into the form (unknown keys are ignored by put()).
function applySettings(values) {                            // function: put a whole settings object into the form
  for (const [key, value] of Object.entries(values)) put(key, value);  // each setting
  for (const input of document.querySelectorAll('input.label-search')) input.dataset.lastValid = input.value;  // each search box remembers its value as the last real one
  markOverridden();                                      // a preset / share link may have a single card in it
  sync();                                                   // refresh the boxes and totals
}

// ===================================================================
// Showing a deck
// ===================================================================
// One card: picture, name, where it came from. Clicking/tapping it shows why it was picked.
function cardElement(card) {                                // function: build one card's element
  const div = document.createElement('div');                // a new box
  div.className = 'card';                                   // styled as a card
  div.tabIndex = 0;                                       // reachable with the Tab key
  div.title = card.reason;                                // hover tooltip on desktop

  const img = document.createElement('img');                // its picture
  img.loading = 'lazy';                                   // only download pictures as they scroll into view
  img.alt = card.name;                                      // the text shown if the picture can't load
  img.src = '/api/card-image/' + encodeURIComponent(card.id);  // the picture's address on our server
  img.onerror = () => { img.style.visibility = 'hidden'; };   // missing picture: keep the space, hide the broken icon

  const name = document.createElement('strong');            // its name, in bold
  name.textContent = card.name;                             // (the text)
  const origin = document.createElement('span');            // where it came from (theme1, support1 ...)
  origin.textContent = card.origin;                         // (the text)
  const reason = document.createElement('small');           // why it was picked (hidden until clicked)
  reason.className = 'reason';                              // styled as the reason
  reason.textContent = card.reason || 'No reason recorded.';  // (the text)

  const toggle = () => div.classList.toggle('show-reason');   // CSS shows .reason only with this class
  div.addEventListener('click', toggle);                    // click / tap shows the reason
  // Enter or Space does the same from the keyboard
  div.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });

  div.append(img, name, origin, reason);                    // put the pieces in the box
  return div;                                               // hand it back
}

function drawGrid(id, cards) {                              // function: draw a list of cards into one grid
  $(id).replaceChildren(...cards.map(cardElement));         // replace the grid's contents
}

// 'Theme 1: Archetype = Blue-Eyes — details' for each category.
function categoryLines(categories) {                        // function: one text line per category
  return CATEGORIES.map(([key, name]) => {                  // for each of the four
    const c = categories[key] || {};                        // what the deck reported for it
    return `${name}: ${c.label || 'Automatic'}${c.detail ? ' — ' + c.detail : ''}`;  // 'Name: label — details'
  });
}

// The heading used by the TXT export, the copy button and the log.
function headerText() {                                     // function: the heading text for exports
  if (!current) return '';                                  // no deck yet:
  return 'Yu-Gi-Oh! Random Deck Generator\n'                // the title...
    + `Seed: ${current.seed}\n`                             // ...the seed...
    + `${current.breakdown}\n`                              // ...the card counts...
    + 'Selected categories:\n'                              // ...and the categories
    + categoryLines(current.categories).map(line => '  ' + line).join('\n') + '\n';  // (one per line)
}

// The full decklist as text.
function deckText() {                                       // function: the whole decklist as text
  const section = (title, cards) => `${title} (${cards.length}):\n`  // one section: its title and count...
    + cards.map(c => `- ${c.name} [${c.origin}]${c.reason ? ' :: ' + c.reason : ''}`).join('\n');  // ...then '- Name [origin] :: reason' per card
  // the heading, the Main Deck, then the Extra Deck
  return headerText() + '\n' + section('Main Deck', current.main) + '\n\n' + section('Extra Deck', current.extra) + '\n';
}

function showDeck(deck) {                                   // function: show a new deck on the page
  current = deck;                                           // remember it
  $('resultTitle').textContent = 'Generated deck · seed ' + deck.seed;  // the title with its seed
  // a hint
  $('status').textContent = 'Click or tap a card to see why it was selected. Export or copy your result above.';

  // Summary panel, built with textContent (never innerHTML), so card text can't inject HTML.
  const summary = $('summary');                             // the summary panel
  const heading = document.createElement('strong');         // its heading
  heading.textContent = 'Selected categories';              // (the text)
  const lines = categoryLines(deck.categories).map(line => {  // one line per category
    const div = document.createElement('div');              // a new line
    div.textContent = line;                                 // (the text)
    return div;                                             // hand it back
  });
  const breakdown = document.createElement('pre');          // the card counts
  breakdown.textContent = deck.breakdown;                   // (the text)
  summary.replaceChildren(heading, ...lines, document.createElement('hr'), breakdown);  // put it all in the panel
  summary.classList.remove('hidden');                       // show the panel

  $('deck').classList.remove('hidden');                     // show the deck area
  $('mainCount').textContent = deck.main.length + ' cards';  // Main Deck count
  $('extraCount').textContent = deck.extra.length + ' cards';  // Extra Deck count
  drawGrid('mainCards', deck.main);                         // draw the Main Deck
  drawGrid('extraCards', deck.extra);                       // draw the Extra Deck
  $('log').textContent = deckText();                        // the text log
  for (const id of ['png', 'ydk', 'txt', 'copy', 'share']) $(id).disabled = false;  // switch the export buttons on

  // Show any values the server rolled (random sizes, ratios ...). The seed box is never filled by the
  // server for a blank seed, so the next click still makes a new deck.
  for (const [key, value] of Object.entries(deck.settings)) put(key, value);  // put each value back in the form
  sync();                                                   // refresh the boxes and totals
}

// ===================================================================
// Generate button
// ===================================================================
form.addEventListener('submit', async e => {                // when the form is submitted (Generate Deck):
  e.preventDefault();                                     // stay on this page (no normal form submit)

  // Make sure every category box holds a real label before sending.
  for (const input of document.querySelectorAll('input.label-search')) {  // each search box
    const labels = labelLists.get(input) || [];             // the labels it allows
    if (document.activeElement === input) input.blur();     // finish any typing in it
    if (!labels.includes(input.value)) input.value = input.dataset.lastValid || labels[0] || '';  // not a real label: put the last real one back
  }

  // Quick checks here too, so the player gets the message instantly (the server checks again).
  const settings = readSettings();                          // every setting on the page
  // the three shares must total 100
  if (total(ALLOCATION) !== 100) { $('status').textContent = 'Theme + Support + Generic must equal 100%.'; return; }
  if (!settings.rand_extra_ratios && total(EXTRA_MIX) !== 100) {  // Extra Deck shares must total 100 (unless random)
    $('status').textContent = 'Extra Deck types must total 100%.';  // say so
    return;                                                 // (stop)
  }
  if (settings.level_sliders && !settings.rand_levels && total(LEVEL_MIX) !== 100) {  // Level shares must total 100 (when used, unless random)
    $('status').textContent = 'Level 1-4, 5-6 and 7+ must total 100%.';  // say so
    return;                                                 // (stop)
  }

  const button = $('generate');                             // the Generate button
  button.disabled = true;                                   // switch it off while we wait
  button.textContent = 'Generating…';                       // (and say so)
  $('status').textContent = 'Generating deck. This may take a moment.';  // tell the player
  try {                                                     // (try it)
    const res = await fetch('/api/generate', {              // send the settings to the server...
      method: 'POST',                                       // ...as a POST...
      headers: { 'Content-Type': 'application/json' },      // ...of JSON...
      body: JSON.stringify({ settings }),                   // ...holding {settings}
    });
    const data = await res.json();                          // read the answer
    if (!res.ok) throw Error(data.detail || 'Generation failed');   // the server's error message
    showDeck(data);                                         // draw it
  } catch (err) {                                           // something went wrong:
    $('status').textContent = err.message;                  // show the message
  } finally {                                               // always:
    button.disabled = false;                              // runs whether it worked or not
    button.textContent = 'Generate Deck';                   // put the button text back
  }
});

// ===================================================================
// Exports
// ===================================================================
// Save a Blob as a file by clicking a temporary download link.
function download(blob, filename) {                         // function: save some data as a file
  const url = URL.createObjectURL(blob);                    // a temporary address for the data
  const a = document.createElement('a');                    // a temporary link
  a.href = url;                                             // pointing at it
  a.download = filename;                                    // with the file name
  a.click();                                                // click it
  setTimeout(() => URL.revokeObjectURL(url), 5000);       // free the memory once the download has started
}

// TXT export: the decklist as a text file
$('txt').onclick = () => download(new Blob([deckText()], { type: 'text/plain;charset=utf-8' }), `ygo-deck-${current.seed}.txt`);

// YDK = the EDOPro / YGOPRO deck format: one passcode per line under #main, #extra and !side.
$('ydk').onclick = () => {                                  // YDK export:
  const ids = cards => cards.map(c => c.id).filter(id => /^\d+$/.test(id)).join('\n');   // digits only
  // the file's text
  const ydk = `#created by ygo_randomizer\n#main\n${ids(current.main)}\n#extra\n${ids(current.extra)}\n!side\n`;
  download(new Blob([ydk], { type: 'text/plain' }), `ygo-deck-${current.seed}.ydk`);  // save it
};                                                          // (end)

$('copy').onclick = async () => {                           // Copy decklist:
  try {                                                     // (try it)
    await navigator.clipboard.writeText(deckText());        // copy the text
    $('status').textContent = 'Decklist copied.';           // say it worked
  } catch {                                                 // the browser refused:
    $('status').textContent = 'Clipboard unavailable; use Export TXT.';   // e.g. older browsers, non-https pages
  }
};                                                          // (end)

// ---------- PNG export: draw the whole deck onto a canvas ----------
const PNG = {                                               // the PNG picture's layout numbers
  width: 1500, margin: 35, cols: 10,                      // image width, outer margin, cards per row
  cardW: 124, cardH: 181, gap: 13,                        // card picture size and gap between cards
  titleH: 200,                                            // space for the title and categories
};                                                          // (end)
PNG.rowH = PNG.cardH + 37;                                // one row = picture + room for the name under it

// Load each picture once, even if the deck has 3 copies (null if it fails to load).
function loadImage(cache, id) {                             // function: load one card picture (once per deck export)
  if (!cache.has(id)) {                                     // not asked for yet:
    cache.set(id, new Promise(resolve => {                  // remember a promise that finishes when it loads
      const img = new Image();                              // a new picture
      img.onload = () => resolve(img);                      // loaded: hand it back
      img.onerror = () => resolve(null);                    // failed: hand back null
      img.src = '/api/card-image/' + encodeURIComponent(id);   // same site, so the canvas can still be saved
    }));                                                    // (end of the promise)
  }
  return cache.get(id);                                     // the (possibly still loading) picture
}

// Draw one titled block of cards starting at height y.
async function drawSection(ctx, cache, cards, y, title) {   // function: draw one section (Main or Extra Deck) on the canvas
  ctx.fillStyle = '#f2f5ff';                                // heading colour
  ctx.font = 'bold 27px Arial';                             // heading font
  ctx.fillText(`${title} (${cards.length})`, PNG.margin, y - 17);  // 'MAIN DECK (60)'
  await Promise.all(cards.map(async (card, i) => {          // draw every card at the same time
    const x = PNG.margin + (i % PNG.cols) * (PNG.cardW + PNG.gap);   // column
    const top = y + Math.floor(i / PNG.cols) * PNG.rowH;             // row
    const img = await loadImage(cache, card.id);            // its picture
    if (img) ctx.drawImage(img, x, top, PNG.cardW, PNG.cardH);  // got it: draw it
    else { ctx.fillStyle = '#34445c'; ctx.fillRect(x, top, PNG.cardW, PNG.cardH); }   // grey box if no picture
    ctx.fillStyle = '#dbe6fb';                              // name colour
    ctx.font = '12px Arial';                                // name font
    const name = card.name.length > 19 ? card.name.slice(0, 18) + '…' : card.name;   // short enough to fit
    ctx.fillText(name, x, top + PNG.cardH + 17);            // the name under the picture
  }));                                                      // (wait for them all)
}

$('png').onclick = async () => {                            // Export PNG:
  const button = $('png');                                  // the button
  button.disabled = true;                                   // switch it off while drawing
  button.textContent = 'Rendering…';                        // (and say so)
  try {                                                     // (try it)
    const rows = n => Math.ceil(n / PNG.cols);              // rows needed for n cards
    const mainY = PNG.titleH + 50;                          // where the Main Deck starts
    const extraY = mainY + rows(current.main.length) * PNG.rowH + 80;  // where the Extra Deck starts
    const height = extraY + rows(current.extra.length) * PNG.rowH + 75;  // the picture's full height

    const canvas = document.createElement('canvas');        // a drawing surface
    canvas.width = PNG.width;                               // its width
    canvas.height = height;                                 // its height
    const ctx = canvas.getContext('2d');                    // its drawing tools
    ctx.fillStyle = '#111827';                            // background
    ctx.fillRect(0, 0, PNG.width, height);                  // fill it

    // Title, seed and categories.
    ctx.fillStyle = '#f3f6ff';                              // title colour
    ctx.font = 'bold 35px Arial';                           // title font
    ctx.fillText('YU-GI-OH! RANDOM DECK', PNG.margin, 53);  // the title
    ctx.font = '20px Arial';                                // seed font
    ctx.fillStyle = '#aac2e9';                              // seed colour
    ctx.fillText('Seed: ' + current.seed, PNG.margin, 89);  // the seed
    ctx.font = '17px Arial';                                // category font
    CATEGORIES.forEach(([key, name], i) => {                // each category on its own line
      const text = name + ': ' + (current.categories[key]?.label || 'Automatic');  // 'Theme 1: Archetype = ...'
      ctx.fillText(text.slice(0, 85), PNG.margin, 120 + i * 23);  // (85 letters at most)
    });

    const cache = new Map();                                // pictures loaded so far
    await drawSection(ctx, cache, current.main, mainY, 'MAIN DECK');  // draw the Main Deck
    await drawSection(ctx, cache, current.extra, extraY, 'EXTRA DECK');  // draw the Extra Deck
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));  // turn the canvas into PNG data
    if (!blob) throw Error('PNG export failed');            // (failed)
    download(blob, `ygo-deck-${current.seed}.png`);         // save it
  } catch (e) {                                             // something went wrong:
    $('status').textContent = 'PNG export failed: ' + e.message;  // say so
  } finally {                                               // always:
    button.disabled = false;                                // switch the button back on
    button.textContent = 'Export PNG';                      // (and its text)
  }
};                                                          // (end)

// ===================================================================
// Share link: the settings + seed of this deck, packed into the address after '#'
// ===================================================================
// The part after '#' never reaches the server, and the link only stores settings that differ from the
// defaults, so it stays short. Same settings + same seed + same card database = the same deck.

// Text -> URL-safe base64 (handles any characters, like ☆ in card names).
function toBase64Url(text) {                                // function: text -> URL-safe base64
  const bytes = new TextEncoder().encode(text);             // the text as UTF-8 bytes
  let binary = '';                                          // the bytes as a 'binary string'
  for (const b of bytes) binary += String.fromCharCode(b);  // (one character per byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');  // base64, with - and _ instead of + and /, no = padding
}

// URL-safe base64 -> text (the reverse of toBase64Url).
function fromBase64Url(code) {                              // function: URL-safe base64 -> text
  const binary = atob(code.replace(/-/g, '+').replace(/_/g, '/'));  // back to normal base64, then to bytes
  return new TextDecoder().decode(Uint8Array.from(binary, ch => ch.charCodeAt(0)));  // the bytes as UTF-8 text
}

function shareLink() {                                      // function: the share link for the current deck
  const page = new Set(Object.keys(readSettings()));      // only settings this page has
  const values = {};                                        // the settings to store
  for (const [key, value] of Object.entries(current.settings)) {  // every setting the deck was built with
    if (!page.has(key)) continue;                           // not on this page: skip it
    // Every 'Randomize ...' box is turned OFF: the rolled values are stored instead, so nothing re-rolls.
    const fixed = key.startsWith('rand_') ? false : value;
    if (String(fixed) !== String(defaults[key])) values[key] = fixed;   // skip anything already at its default
  }
  values.seed = String(current.seed);                     // the seed that made this deck
  return location.origin + location.pathname + '#s=' + toBase64Url(JSON.stringify(values));  // this page's address + '#s=' + the packed settings
}

// The settings stored in the address (or null if there are none, or the link is broken).
function readShareLink() {                                  // function: the settings in a share link (or null)
  if (!location.hash.startsWith('#s=')) return null;        // not a share link:
  try {                                                     // (try to read it)
    return JSON.parse(fromBase64Url(location.hash.slice(3)));  // unpack it
  } catch {                                                 // broken:
    $('status').textContent = 'That share link is broken; starting from the defaults.';  // say so
    return null;                                            // (no settings)
  }
}

$('share').onclick = async () => {                          // Copy share link:
  const link = shareLink();                                 // make the link
  try {                                                     // (try it)
    await navigator.clipboard.writeText(link);              // copy it
    $('status').textContent = 'Share link copied. Anyone who opens it gets this exact deck.';  // say it worked
  } catch {                                                 // the browser refused:
    prompt('Copy this link:', link);                       // no clipboard access: show it to copy by hand
  }
};                                                          // (end)

// ===================================================================
// Presets: named settings saved in THIS browser (localStorage)
// ===================================================================
// localStorage can be missing or blocked (private windows, some phones), so every use is wrapped in
// try/catch and the page works without it.
const PRESET_KEY = 'ygo-randomizer-presets';                // the name presets are saved under in the browser

function loadPresets() {                                    // function: every saved preset
  try {                                                     // (try it)
    return JSON.parse(localStorage.getItem(PRESET_KEY) || '{}');  // read them ({} if none)
  } catch {                                                 // blocked:
    return {};                                              // (none)
  }
}

function savePresets(presets) {                             // function: save every preset
  try {                                                     // (try it)
    localStorage.setItem(PRESET_KEY, JSON.stringify(presets));  // save them
    return true;                                            // it worked
  } catch {                                                 // blocked:
    $('status').textContent = 'This browser won\'t let the page save presets.';  // say so
    return false;                                           // it didn't work
  }
}

// Rebuild the dropdown from what is saved.
function refreshPresetList(selected = '') {                 // function: rebuild the presets dropdown
  const names = Object.keys(loadPresets()).sort((a, b) => a.localeCompare(b));  // the names, A-Z
  $('presetList').replaceChildren(new Option('(choose a preset)', ''), ...names.map(n => new Option(n, n)));  // a blank first choice, then each name
  $('presetList').value = selected;                         // select the given one
}

$('presetSave').onclick = () => {                           // Save current...:
  const name = (prompt('Name for this preset:') || '').trim();  // ask for a name
  if (!name) return;                                      // cancelled
  const presets = loadPresets();                            // every saved preset
  const values = readSettings();                            // the current settings
  delete values.seed;                                     // a preset is settings, not one particular deck
  presets[name] = values;                                   // add (or replace) it
  if (savePresets(presets)) {                               // saved:
    refreshPresetList(name);                                // refresh the dropdown, with it selected
    $('status').textContent = `Preset "${name}" saved.`;    // say so
  }
};                                                          // (end)

$('presetList').onchange = () => {                        // choosing a preset loads it
  const values = loadPresets()[$('presetList').value];      // the chosen preset's settings
  if (!values) return;                                      // none chosen:
  applySettings(values);                                    // put them in the form
  $('status').textContent = `Preset "${$('presetList').value}" loaded.`;  // say so
};                                                          // (end)

$('presetDelete').onclick = () => {                         // Delete:
  const name = $('presetList').value;                       // the chosen preset
  if (!name || !confirm(`Delete the preset "${name}"?`)) return;  // nothing chosen, or the player said no:
  const presets = loadPresets();                            // every saved preset
  delete presets[name];                                     // remove this one
  if (savePresets(presets)) {                               // saved:
    refreshPresetList();                                    // refresh the dropdown
    $('status').textContent = `Preset "${name}" deleted.`;  // say so
  }
};                                                          // (end)

// Go!
init().catch(e => { $('status').textContent = e.message; });  // start the page; show any error in the status line
