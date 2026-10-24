"""
ygo_formats_update.py  --  keeps the Forbidden Lists, staples and Genesys points up to date.
The "Check for New Cards" button runs refresh_banlists() every time (after a card update, or on its own when
the cards are already up to date).

  refresh_banlists(cards, log)       -> True if the list file was rebuilt         (the button, cards up to date)
  update_banlists(work, cards, log)  -> the same, inside a card update's work folder

What it downloads, and how often:
  every time   today's TCG / OCG / WCS / Traditional lists (EDOPro), the Master Duel list (Master Duel Meta),
               the Genesys points (Yugioh Meta), today's most popular cards (staples for today's formats)
  once         each Format Library format's banlist and its staples (which cards its decks played).
               A format whose staples are finished goes on complete_formats.txt and is never asked about again.
               A format with 0 staples (Format Library sent no decks, or its page failed) is never complete:
               it is asked again on every update, in case Format Library has fixed it.
Then two step scripts build the files the randomizer reads:
  ygo_make_banlists.py  -> forbidden lists/randomizer formats.lflist.conf
  ygo_make_staples.py   -> program/data/format_staples.json
Nothing here can stop a card update: a download that fails is logged, and the old files stay.
"""
import json             # every site answers in JSON
import shutil           # removes the work folder
import time             # pauses between requests, and the staples time limit
import urllib.parse     # puts 'Limited 1' safely into an address
from pathlib import Path

import ygo_io as IO     # download(), run_script(), read_json(), write_json_lines()
import ygo_paths as P   # where program/, data/ and 'forbidden lists' are
from ygo_banlist import fl_shown_name, load_banlists, current_formats, load_complete   # what we already have

# ---- where everything comes from ------------------------------------------------------------------
BANLIST_URLS = {                                                          # EDOPro's files for today's lists
    name: "https://raw.githubusercontent.com/ProjectIgnis/LFLists/master/" + name
    for name in ("0TCG.lflist.conf", "OCG.lflist.conf", "World.lflist.conf", "Traditional.lflist.conf")}
FL_API = "https://formatlibrary.com/api"                                  # Format Library: every format + its banlist
MD_API = "https://www.masterduelmeta.com/api/v1/cards"                    # Master Duel Meta: the Master Duel list
YGM_API = "https://www.yugiohmeta.com/api/v1/cards"                     # Yugioh Meta: Genesys points, TCG/OCG popularity
MD_STATUSES = ("Forbidden", "Limited 1", "Limited 2", "Limited", "Semi-Limited")   # every status Master Duel has used

# ---- how hard to try --------------------------------------------------------------------------------
WORKERS = 4                                                               # downloads at the same time (be gentle: 4 at most)
STAPLES_MINUTES = 15                                                      # most time the staples download may take

# ---- the files we make ------------------------------------------------------------------------------
BANLIST_FILE = "randomizer formats.lflist.conf"                           # in 'forbidden lists'
GENESYS_FILE = "genesys_points.json"                                      # in program/data
STAPLES_CACHE = P.DATA / "staples_cache"                                  # downloaded deck files, kept between runs
COMPLETE_FILE = P.DATA / "complete_formats.txt"                           # formats whose staples are finished for good
STAPLES_FILE = P.DATA / "format_staples.json"                             # every format's staples (made by ygo_make_staples.py)

# ---- staples for today's formats (popularity rankings) ----------------------------------------------
CURRENT_TOP = 200                                                         # cards per current-format ranking that count
# today's lists (by the start of their name) -> where their staples come from: (address, rank field)
CURRENT_SOURCES = {
    "TCG":         (YGM_API, "tcgPopRank"),       # Yugioh Meta: TCG popularity      -> TCG, WCS, TCG Traditional
    "OCG":         (YGM_API, "ocgPopRank"),       # Yugioh Meta: OCG popularity      -> OCG, OCG Traditional
    "Master Duel": (MD_API, "popRank"),           # Master Duel Meta: MD popularity -> Master Duel
}
CURRENT_LISTS = {"TCG (": "TCG", "WCS (": "TCG", "TCG Traditional (": "TCG",
                 "OCG (": "OCG", "OCG Traditional (": "OCG", "Master Duel": "Master Duel"}


def _download_banlists(sources: Path, log) -> None:
    """Save every banlist source into `sources`. Anything that fails is only logged: the list builder keeps
       last time's copy of any list it can't make."""
    sources.mkdir(parents=True, exist_ok=True)
    log("--- Downloading today's lists from EDOPro ...")
    for name, url in BANLIST_URLS.items():
        try:
            (sources / name).write_bytes(IO.download(url))
        except Exception as e:                                            # one file failed: the others still count
            log(f"    couldn't download {name}: {e}")

    log("--- Downloading the formats from Format Library (about a minute) ...")
    try:
        formats = json.loads(IO.download(f"{FL_API}/formats"))                   # [{'name', 'date', 'banlist', 'category'}, ...]
        (sources / "fl_formats.json").write_text(json.dumps(formats), encoding="utf-8")
        have = set(load_banlists(P.FORBIDDEN))                            # lists already in 'forbidden lists'
        new = [f for f in formats if f.get("date") and f.get("banlist") and f.get("category") in ("TCG", "OCG")
               and fl_shown_name(f["name"], f["date"], f["category"]) not in have]   # old formats never change: skip them
        wanted = {(f["banlist"], f["category"]) for f in new}             # each banlist once (formats share them)
        for banlist, cat in sorted(wanted):
            name = banlist.strip().lower().replace(" ", "-")              # 'September 2008' -> 'september-2008'
            url = f"{FL_API}/banlists/{name}" + ("?category=OCG" if cat == "OCG" else "")
            try:
                (sources / f"fl_{name}_{cat}.json").write_bytes(IO.download(url))
            except Exception as e:
                log(f"    couldn't download {banlist} ({cat}): {e}")
            time.sleep(0.2)                                               # be polite: a short pause between requests
        log(f"    {len(formats)} formats; {len(new)} new ones, {len(wanted)} banlists to download")
        _download_staples(sources, formats, log)
    except Exception as e:
        log(f"    couldn't reach Format Library: {e}")

    log("--- Downloading the Master Duel list ...")
    try:
        cards = []
        for status in MD_STATUSES:                                        # one status at a time, 50 cards a page
            for page in range(1, 20):                                     # (never more than 20 pages: a safety stop)
                url = f"{MD_API}?banStatus={urllib.parse.quote(status)}&limit=50&page={page}"
                batch = json.loads(IO.download(url))
                cards += [{"name": c.get("name"), "banStatus": c.get("banStatus")} for c in batch]
                if len(batch) < 50:                                       # a short page is the last page
                    break
        (sources / "md_cards.json").write_text(json.dumps(cards), encoding="utf-8")
        log(f"    {len(cards)} Master Duel cards on the list")
    except Exception as e:
        log(f"    couldn't reach Master Duel Meta: {e}")


def _save(path: Path, data) -> None:
    """Save one downloaded file: written to a '.part' file first, then renamed, so closing the window
       mid-write can't leave a broken file behind (a broken file is just downloaded again)."""
    part = path.with_suffix(".part")
    part.write_text(json.dumps(data), encoding="utf-8")
    part.replace(path)


def _download_staples(sources: Path, formats: list, log) -> None:
    """For every Format Library format we don't have staples for yet, in two parts:
         1. each format's list of popular deck types (quick, about 125 requests)
         2. each deck type's card list (how many copies of each card those decks ran) - the long part,
            WORKERS downloads at a time, with a real 'X of Y' count
       Every file is kept in program/data/staples_cache, so a run that stops partway (time limit, closed
       window) carries on next time with only what is still missing. ygo_make_staples.py makes the scores."""
    from concurrent.futures import ThreadPoolExecutor, as_completed   # a few downloads at the same time
    folder = STAPLES_CACHE
    folder.mkdir(parents=True, exist_ok=True)
    empty = {name for name, cards in (IO.read_json(STAPLES_FILE) or {}).items() if not cards}   # formats with 0 staples so far
    complete = load_complete(COMPLETE_FILE) - empty                      # finished formats: never downloaded again (0 staples is never finished)
    current = current_formats(formats)                                    # the format still going on: always refreshed
    todo = [f for f in formats if f.get("date") and f.get("category") in ("TCG", "OCG")
            and fl_shown_name(f["name"], f["date"], f["category"]) not in complete]
    for old in folder.glob("*.failures"):                                 # failure counters from older versions: not used any more
        old.unlink()
    log(f"--- {len(empty)} formats have 0 staples so far: checking them again")
    for f in todo:                                                        # the current format: start its files fresh
        if fl_shown_name(f["name"], f["date"], f["category"]) in current:
            fmt = f["name"].strip().lower().replace(" ", "-")
            for path in [folder / f"{fmt}_popular.json"] + list(folder.glob(f"{fmt}__*.json")):
                path.unlink(missing_ok=True)
    if not todo:
        log("--- Every format's staples are complete")
        return
    stop_at = time.time() + STAPLES_MINUTES * 60                          # a slow site can't hold up the update forever

    def fetch(job: tuple) -> str:
        """Download one address into one file (runs in a worker). Returns 'ok', 'failed' or 'out of time'.
           Nothing is saved for a failed download, so it is simply tried again on the next update."""
        url, target, label = job
        if time.time() > stop_at:
            return "out of time"                                          # left for next run
        try:
            _save(target, json.loads(IO.download(url, timeout=60)))              # some of these pages are slow: up to a minute
            return "ok"
        except Exception as e:                                            # a slow or broken page
            return f"failed ({type(e).__name__}) - tried again next update"
        finally:
            time.sleep(0.2)                                               # be polite: a short pause per worker

    def run(jobs: list, what: str) -> int:
        """Download every job, WORKERS at a time, with one line per finished download. Returns how many worked."""
        began, good = time.time(), 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(fetch, job): job for job in jobs}
            for done, future in enumerate(as_completed(futures), 1):      # in the order they FINISH (a slow one can't hide the rest)
                result = future.result()
                good += result == "ok"
                left = (time.time() - began) / done * (len(jobs) - done)  # average time so far x downloads still to go
                log(f"    {what} {done} of {len(jobs)}: {futures[future][2]} - {result}"
                    f"  (about {int(left // 60)} min {int(left % 60):02d} s left)")
        return good

    # ---- part 1: every format's list of deck types ----------------------------------------------------
    slugs = {f["name"]: f["name"].strip().lower().replace(" ", "-") for f in todo}   # 'Virtual World' -> 'virtual-world'
    jobs = [(f"{FL_API}/decks/popular/{fmt}", folder / f"{fmt}_popular.json", name)
            for name, fmt in slugs.items() if IO.read_json(folder / f"{fmt}_popular.json") is None]
    log(f"--- Part 1 of 2: the deck types of {len(todo)} formats ({len(todo) - len(jobs)} saved from earlier runs) ...")
    run(jobs, "format")

    # ---- part 2: every deck type's card list ---------------------------------------------------------
    jobs, reused = [], 0
    for name, fmt in slugs.items():
        popular = IO.read_json(folder / f"{fmt}_popular.json")
        for deck in popular if isinstance(popular, list) else []:        # (a format with no deck data: nothing to do)
            kind = (deck.get("cleanName") or deck.get("name") or "").strip().lower().replace(" ", "-")
            target = folder / f"{fmt}__{kind}.json"
            if IO.read_json(target) is not None:
                reused += 1                                               # already downloaded on an earlier run
            else:
                jobs.append((f"{FL_API}/decktypes/summary?id={urllib.parse.quote(kind)}&format={urllib.parse.quote(fmt)}",
                             target, f"{name} / {deck.get('name') or kind}"))
    log(f"--- Part 2 of 2: {len(jobs)} deck types to download ({reused} saved from earlier runs), {WORKERS} at a time ...")
    got = run(jobs, "deck type")
    if time.time() > stop_at:
        log(f"    stopped after {STAPLES_MINUTES} minutes; the next update carries on from here")
    log(f"    {got} deck types downloaded, {reused} kept from earlier runs")


def _download_genesys(log) -> None:
    """Save {card name: Genesys points} for every card that costs points into program/data/genesys_points.json.
       First asks Yugioh Meta for only the cards with points; if that answer looks wrong (the filter was
       ignored), reads every card instead and keeps the ones with points. On failure the old file stays."""
    log("--- Downloading the Genesys points ...")

    def pages(query: str, size: int) -> list:
        """Every card a query returns, `size` cards a page (stops at a short page, or after 100 pages)."""
        cards = []
        for page in range(1, 101):
            batch = json.loads(IO.download(f"{YGM_API}?{query}limit={size}&page={page}"))
            cards += batch
            if len(batch) < size:
                break
            time.sleep(0.2)
        return cards

    try:
        cards = pages("genesysPoints%5B%24gt%5D=0&", 200)                 # genesysPoints[$gt]=0 : only cards with points
        points = {c["name"]: int(c["genesysPoints"]) for c in cards if c.get("name") and (c.get("genesysPoints") or 0) > 0}
        if len(points) < 20 or len(points) < len(cards) // 2:             # too few, or mostly 0-point cards: filter ignored
            log("    (the quick way didn't work: reading every card instead, about a minute)")
            cards = pages("", 1000)
            points = {c["name"]: int(c["genesysPoints"]) for c in cards if c.get("name") and (c.get("genesysPoints") or 0) > 0}
        if len(points) < 20:
            raise ValueError(f"only {len(points)} cards with points")     # something is off: keep the old file
        points = dict(sorted(points.items(), key=lambda kv: (-kv[1], kv[0])))   # most points first, then A-Z
        IO.write_json_lines(P.DATA / GENESYS_FILE, points)                   # one card per line
        top = next(iter(points))
        log(f"    {len(points)} cards cost points (highest: {json.dumps(top, ensure_ascii=False)}: {points[top]})")
    except Exception as e:
        log(f"    couldn't get the Genesys points ({e}); the old ones are still there")


def _current_staples(log) -> None:
    """Staples for today's formats, refreshed every update: each site's most popular cards (no copy counts,
       so copies = 0 = 'pick 1-3 as usual'). Saved into format_staples.json under today's list names.
       A ranking that can't be downloaded, or doesn't come back sorted, is skipped (the log says so)."""
    log("--- Downloading today's most popular cards (TCG, OCG, Master Duel) ...")
    rankings = {}
    for game, (api, field) in CURRENT_SOURCES.items():
        try:
            # Ways of asking, tried in order until one gives a sorted answer. (When a site sorts by rank, cards
            # with NO rank can come first, so the first two ask for ranked cards only.)
            asks = [f"{field}%5B%24gte%5D=1&sort={field}",                # tcgPopRank[$gte]=1 : ranked cards only
                    f"{field}%5B%24ne%5D=null&sort={field}",              # tcgPopRank[$ne]=null : the same, other wording
                    f"sort={field}",                                      # plain sort (works for Master Duel Meta)
                    f"{field}%5B%24lte%5D={CURRENT_TOP}"]                 # tcgPopRank[$lte]=200, NOT sorted: we sort it
            cards, why = [], "no answer"
            for ask in asks:
                got = []
                try:
                    for page in (1, 2):                                   # 2 pages of 100 = the top 200
                        got += json.loads(IO.download(f"{api}?{ask}&limit=100&page={page}"))
                except Exception as e:                                    # the site refused this way of asking:
                    why = f"{type(e).__name__}: {e}"                      # try the next one
                    continue
                got = sorted((c for c in got if isinstance(c.get(field), int)), key=lambda c: c[field])   # ranked only, #1 first
                ranks = [c[field] for c in got]
                if len(ranks) >= 50 and ranks[0] <= 5 and ranks[min(49, len(ranks) - 1)] <= CURRENT_TOP:   # really the top cards
                    cards = got                                           # this way of asking worked
                    break
                why = f"not sorted by popularity ({len(ranks)} ranked cards, first rank {ranks[0] if ranks else None})"
            if not cards:
                raise ValueError(why)
            scores = {}
            for c in cards:
                if c[field] <= CURRENT_TOP and c.get("name"):
                    scores.setdefault(c["name"], [round(1 - (c[field] - 1) / CURRENT_TOP, 3), 0])   # alt arts: first wins
            rankings[game] = scores
            log(f"    {game}: {len(scores)} cards (most popular: {next(iter(scores))})")
        except Exception as e:
            log(f"    {game}: skipped ({e})")
    if not rankings:
        return
    path = STAPLES_FILE
    data = IO.read_json(path, {})                                            # the staples we already have
    for name in load_banlists(P.FORBIDDEN):                               # today's lists, whatever their date is now
        game = next((g for start, g in CURRENT_LISTS.items() if name.startswith(start)), None)
        if game in rankings:
            data[name] = rankings[game]
    IO.write_json_lines(path, data)                                          # one format per line


def update_banlists(work: Path, cards_path: Path, log) -> bool:
    """Download every banlist source and rebuild our list file. Never stops an update: on a failure the
       old list file stays. Returns True if the list file was rebuilt."""
    sources = work / "banlist_sources"
    _download_banlists(sources, log)
    _download_genesys(log)
    try:
        IO.run_script(work, log, "Building the Forbidden Lists", "ygo_make_banlists.py",
              str(sources), str(P.FORBIDDEN / BANLIST_FILE), str(cards_path))
        IO.run_script(work, log, "Working out each format's staples", "ygo_make_staples.py",
              str(sources), str(STAPLES_FILE), str(STAPLES_CACHE), str(COMPLETE_FILE))
        _current_staples(log)                                             # today's formats: refreshed every time
        return True
    except Exception as e:
        log(f"    banlists not updated ({e}); the old ones are still there")
        return False


def refresh_banlists(cards_path: Path, log=print) -> bool:
    """Only the Forbidden Lists (used when the cards are already up to date)."""
    work = P.PROGRAM / "update_work"
    shutil.rmtree(work, ignore_errors=True)                               # start clean
    work.mkdir()
    done = update_banlists(work, Path(cards_path).resolve(), log)
    shutil.rmtree(work, ignore_errors=True)
    log("=== Forbidden Lists updated." if done else "=== Forbidden Lists not updated.")
    return done
