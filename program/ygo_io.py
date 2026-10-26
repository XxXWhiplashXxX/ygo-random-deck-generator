"""
ygo_io.py  --  reading and writing files, in one place.

Every update script reads and writes the same kinds of files. Instead of each script carrying its own copy
of that code, they all use these few functions:

    read_table(path)               -> (columns, rows)     a CSV file: cards.csv, Stats.csv ...
    write_table(path, columns, rows)                      ... and back
    read_api(path)                 -> [card, ...]        the YGOPRODeck download (api.json)
    make_traits(card)              -> 'Trait = ...'      a YGOPRODeck card's traits, our way
    read_json(path, default)       -> the data            any JSON file (default if missing / broken)
    write_json_lines(path, data)                          a {key: value} JSON file, one entry per line
    wiki_api(params)               -> the answer          one request to Yugipedia
    wiki_category(name)            -> [title, ...]        every page in a Yugipedia category
    wiki_pages(titles)             -> {title: page text}  up to 50 wiki pages at once
    field_values(page, field)      -> [value, ...]        one field of a card's wiki info box
    download(url)                  -> bytes              any web address (YGOPRODeck, Format Library ...)
    run_script(work, log, title, script, ...)            run one update step script, show its last lines
"""
import csv              # CSV files
import json             # JSON files and the wiki's answers
import re               # reads wiki info boxes
import subprocess       # runs the update step scripts
import sys              # the Python that is running us (it runs the step scripts too)
import time             # pauses between wiki requests
import urllib.parse     # builds wiki addresses
import urllib.request   # downloads from the wiki
from pathlib import Path


# ---------------------------------------------------------------- CSV tables
def read_table(path, encoding: str = "utf-8-sig") -> tuple:
    """A CSV file -> (list of column names, list of rows). Each row is a dict: {column name: text}.
       'utf-8-sig' also reads files that start with the hidden mark Excel adds (our card files do)."""
    with open(path, encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)                     # one dict per row, keyed by the header line
        return list(reader.fieldnames or []), list(reader)


def write_table(path, columns: list, rows: list, encoding: str = "utf-8-sig") -> None:
    """Write rows (dicts) to a CSV file with these columns, in this order. 'utf-8-sig' adds the hidden mark
       at the start so Excel shows letters like 'α' correctly. Keys a row has that aren't columns are an error."""
    with open(path, "w", encoding=encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()                           # the header line
        writer.writerows(rows)                         # then every row


# ---------------------------------------------------------------- JSON
def read_api(path) -> list:
    """Every card in a YGOPRODeck download (cardinfo.php?misc=yes). Works for the normal file, and for a
       hand-edited one where a Replace turned every '}]},{' between cards into a line break."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(text)["data"]                # the normal download
    except ValueError:
        return json.loads("]},{".join(text.splitlines()))["data"]   # put back the removed ']},{' and try again


def read_json(path, default=None):
    """A JSON file's data, or `default` if the file is missing or broken (e.g. a download that was cut off)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json_lines(path, data: dict) -> None:
    """Save {key: value} as JSON with one entry per line, so the file is easy to read and search:
           {
           "Maxx \\"C\\"": 50,
           "Abyss Dweller": 100
           }"""
    lines = [f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}" for k, v in data.items()]
    Path(path).write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")


def make_traits(card: dict) -> str:
    """A YGOPRODeck card -> our traits text, e.g. 'Trait = Pendulum || Trait = Synchro'.
       Spells / Traps: their kind ('Trait = Quick-Play Spell'). Monsters: every typeline word except the Type
       (Dragon ...), alphabetical; then 'Normal' for Normal Monsters, 'Effect' when that is all there is, and
       'Non-effect' last for monsters without an effect."""
    if card["frameType"] in ("spell", "trap"):                # Spells and Traps
        if card["race"]:                                      # race usually holds the kind, e.g. 'Quick-Play'
            kind = card["race"] + (" Spell" if card["frameType"] == "spell" else " Trap")  # -> 'Quick-Play Spell'
        else:                                                 # a few API entries leave race blank...
            kind = card["humanReadableCardType"]              # ...so use the full name instead, e.g. 'Normal Spell'
        return f"Trait = {kind}"
    typeline = card.get("typeline", [])                       # monsters: e.g. ['Dragon', 'Synchro', 'Tuner', 'Effect']
    parts = [t for t in typeline if t != card["race"]]        # drop the monster Type (Dragon, Machine...)
    has_effect = "Effect" in parts                            # does the card have an effect?
    parts = [t for t in parts if t not in ("Effect", "Normal")]  # take out Effect and Normal; handled below
    traits = sorted(parts)                                    # the rest in alphabetical order
    if not traits and has_effect:                             # a plain Effect Monster has nothing else...
        traits = ["Effect"]                                   # ...so it is just 'Trait = Effect'
    if "Normal" in typeline:                                  # Normal monsters get 'Normal' after the others
        traits.append("Normal")
    if not has_effect:                                        # no effect at all (Normal, or a non-effect Fusion ...)
        traits.append("Non-effect")                           # 'Non-effect' always goes last
    return " || ".join(f"Trait = {t}" for t in traits)        # joined with ' || '


# ---------------------------------------------------------------- Yugipedia (yugipedia.com)
# Yugipedia's rules (yugipedia.com/wiki/Yugipedia:API): a User-Agent that says who we are and how to reach us,
# at most ONE request per second, many pages per request (titles joined with |), and keep what we read for
# about 30 days instead of asking again. Agents that break the rules may be blocked without warning.
WIKI_API = "https://yugipedia.com/api.php"                         # Yugipedia's API address
WIKI_HEADERS = {"User-Agent": "YGO-deck-randomizer/1.0 (personal project; joe023937@gmail.com)"}   # who we are + contact (same as CONTACT below)
WIKI_WAIT = 1.0                                                     # seconds between requests (Yugipedia: at most 1 per second)
WIKI_BATCH = 50                                                     # the API gives at most 50 pages per request
WIKI_KEEP_DAYS = 30                                                 # a card page read less than this many days ago is not read again


def wiki_api(params: dict) -> dict:
    """Send one request to the wiki API and return the answer as Python data, then pause WIKI_WAIT seconds."""
    url = WIKI_API + "?" + urllib.parse.urlencode(dict(params, format="json"))   # always ask for JSON
    req = urllib.request.Request(url, headers=WIKI_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:          # give up after 60 seconds
        data = json.load(resp)
    time.sleep(WIKI_WAIT)
    return data


def wiki_category(category: str) -> list:
    """Every page title in a wiki category, e.g. 'Category:TCG and OCG series' -> ['"C"', '-Eyes Dragon', ...].
       500 titles per request; a longer category takes more requests (the answer says where to carry on)."""
    titles, more = [], {}
    while True:
        data = wiki_api({"action": "query", "list": "categorymembers", "cmtitle": category, "cmlimit": "500", **more})
        titles += [m["title"] for m in data.get("query", {}).get("categorymembers", [])]   # this request's titles
        if "continue" not in data:                                  # nothing left
            return titles
        more = data["continue"]                                     # where the next request carries on


def wiki_pages(titles: list, keep_missing: bool = True) -> dict:
    """Up to 50 page titles -> {asked-for title: page text}. A title with no page gets None
       (or is left out, with keep_missing=False). The wiki may tidy a title or redirect an old name to a new
       page; the answer is still keyed by the title that was asked for."""
    data = wiki_api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                     "titles": "|".join(titles), "redirects": "1"})   # '|' separates the titles
    q = data.get("query", {})
    rename = {s["from"]: s["to"] for s in q.get("normalized", []) + q.get("redirects", [])}   # tidy-ups and redirects
    texts = {p["title"]: p["revisions"][0]["slots"]["main"]["*"]   # final title -> page text
             for p in q.get("pages", {}).values() if "revisions" in p}   # (pages that exist)
    out = {}
    for t in titles:
        final = t
        while final in rename:                                      # follow every tidy-up / redirect step
            final = rename[final]
        if final in texts or keep_missing:
            out[t] = texts.get(final)                               # None = no such page
    return out


def field_values(text: str, field: str) -> list:
    """The values of one field of a card's wiki info box, e.g. 'archseries' -> ['Railway', 'Night Train'].
       Handles both '| field = X' and bullet lists ('* X' on the following lines); drops hidden comments."""
    m = re.search(r"^\|\s*" + field + r"\s*=(.*?)(?=^\||^\}\})", text, re.M | re.S)   # from '| field =' to the next '|' line
    if not m:
        return []                                                   # the card has no such field
    values = []
    for line in m.group(1).splitlines():                            # one value per line
        line = re.sub(r"<!--.*?-->", "", line.strip().lstrip("*")).strip()   # drop the '*' bullet and hidden comments
        if line:
            values.append(line)
    return values


# ---------------------------------------------------------------- downloads and update steps
CONTACT = "joe023937@gmail.com"                                          # so site owners can reach us instead of blocking
HEADERS = {"User-Agent": f"YGO-deck-randomizer/1.0 (personal project; {CONTACT})"}   # say who we are


def download(url: str, timeout: int = 30) -> bytes:
    """Download one address and return the raw bytes. Gives up after `timeout` seconds of no answer
       (30 for the small lists; the big card file passes a longer wait)."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _python() -> tuple:
    """(the python.exe to run the step scripts with, the 'no console window' flag).
       The window is started with pythonw.exe (no console). Run the steps with the normal python.exe next to it,
       so their progress text can be read, and tell Windows not to open a console window for them."""
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe" and python.with_name("python.exe").is_file():
        python = python.with_name("python.exe")
    return python, getattr(subprocess, "CREATE_NO_WINDOW", 0)            # the flag is Windows only; 0 elsewhere


def run_script(work: Path, log, title: str, script: str, *args: str) -> None:
    """Run one step script (from the program folder) inside the work folder, and log its last lines.
       Raises an error if the script crashed, which stops the update."""
    import ygo_paths as P                                                 # (here, so reading files never needs it)
    python, no_window = _python()
    log(f"--- {title}")
    result = subprocess.run([str(python), str(P.PROGRAM / script), *args], cwd=work,
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            creationflags=no_window)
    for line in (result.stdout or "").splitlines()[-6:]:                  # the script's last few lines of output
        log("    " + line)
    if result.returncode != 0:                                            # the script crashed
        for line in (result.stderr or "").splitlines()[-8:]:
            log("    ! " + line)
        raise RuntimeError(f"step failed: {title}")
