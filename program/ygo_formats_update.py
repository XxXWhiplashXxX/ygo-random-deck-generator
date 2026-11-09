"""
ygo_formats_update.py  --  keeps the Forbidden Lists, staples and Genesys points up to date.

  refresh_banlists(cards, log)       -> True if the list file was rebuilt         ("Refresh Banlists": cards up to date)
  update_banlists(work, cards, log)  -> the same, inside a card update's work folder ("Update Card Database")
  refresh_staples(log)               -> True if format_staples.json was rebuilt  (the separate "Check Staples" button)

What it downloads:
  banlists     every time: today's TCG / OCG / WCS / Traditional lists (EDOPro), the Master Duel list (Master Duel
               Meta), the Genesys points (Yugioh Meta), and any Format Library format's banlist we don't have yet
               (old formats never change, so each is downloaded once).
  staples      only with "Check Staples": which cards each Format Library format's decks played, plus today's most
               popular cards (staples for today's formats). A format whose staples are finished goes on
               complete_formats.txt and is never asked about again. A format with 0 staples (Format Library sent
               no decks, or its page failed) is never complete: it is asked again every time, in case it was fixed.
Then two step scripts build the files the randomizer reads:
  ygo_make_banlists.py  -> forbidden lists/randomizer formats.lflist.conf
  ygo_make_staples.py   -> program/data/format_staples.json
Nothing here can stop a card update: a download that fails is logged, and the old files stay.
"""
import json             # every site answers in JSON
from typing import Optional  # 'a number or None' in function headers
import time             # pauses between requests, and the staples time limit
import urllib.parse     # puts 'Limited 1' safely into an address
from pathlib import Path

import ygo_io as IO     # download(), run_script(), read_json(), write_json_lines()
import ygo_paths as P   # where program/, data/ and 'forbidden lists' are
from ygo_banlist import fl_shown_name, load_banlists, current_formats, load_complete   # what we already have

# ---- where everything comes from ------------------------------------------------------------------
BANLIST_URLS = {                                                          # EDOPro's files for today's lists
    name: "https://raw.githubusercontent.com/ProjectIgnis/LFLists/master/" + name  # EDOPro's GitHub folder + the file name
    for name in ("0TCG.lflist.conf", "OCG.lflist.conf", "World.lflist.conf", "Traditional.lflist.conf")}  # TCG, OCG, World (WCS) and Traditional
FL_API = "https://formatlibrary.com/api"                                  # Format Library: every format + its banlist
MD_API = "https://www.masterduelmeta.com/api/v1/cards"                    # Master Duel Meta: the Master Duel list
YGM_API = "https://www.yugiohmeta.com/api/v1/cards"                     # Yugioh Meta: Genesys points, TCG/OCG popularity
MD_STATUSES = ("Forbidden", "Limited 1", "Limited 2", "Limited", "Semi-Limited")   # every status Master Duel has used

# ---- how hard to try --------------------------------------------------------------------------------
WORKERS = 1                                                               # downloads at the same time: one (small fan sites)
POLITE_PAUSE = 1.0                                                        # seconds between requests: about 1 a second
STAPLES_MINUTES = 15                                                      # a time limit _download_staples can be given (Check Staples uses none)

# ---- the files we make ------------------------------------------------------------------------------
BANLIST_FILE = "randomizer formats.lflist.conf"                           # in 'forbidden lists'
GENESYS_FILE = "genesys_points.json"                                      # in program/data
STAPLES_CACHE = P.DATA / "staples_cache"                                  # downloaded deck files, kept between runs
COMPLETE_FILE = P.DATA / "complete_formats.txt"                           # formats whose staples are finished for good
STAPLES_FILE = P.DATA / "format_staples.json"                             # every format's staples (made by ygo_make_staples.py)

# ---- staples for today's formats (popularity rankings) ----------------------------------------------
CURRENT_TOP = 200                                                         # cards per current-format ranking that count
# today's lists (by the start of their name) -> where their staples come from: (address, rank field)
CURRENT_SOURCES = {                                         # list kind -> (site, rank field)
    "TCG":         (YGM_API, "tcgPopRank"),       # Yugioh Meta: TCG popularity      -> TCG, WCS, TCG Traditional
    "OCG":         (YGM_API, "ocgPopRank"),       # Yugioh Meta: OCG popularity      -> OCG, OCG Traditional
    "Master Duel": (MD_API, "popRank"),           # Master Duel Meta: MD popularity -> Master Duel
}
CURRENT_LISTS = {"TCG (": "TCG", "WCS (": "TCG", "TCG Traditional (": "TCG",  # the start of a list's name -> which ranking it uses
                 "OCG (": "OCG", "OCG Traditional (": "OCG", "Master Duel": "Master Duel"}  # (OCG lists and Master Duel)


def _download_banlists(sources: Path, log) -> None:         # function: download every banlist source
    """Save every banlist source into `sources`. Anything that fails is only logged: the list builder keeps
       last time's copy of any list it can't make."""
    sources.mkdir(parents=True, exist_ok=True)              # the folder for the downloads
    log("--- Downloading today's lists from EDOPro ...")    # tell the user
    for name, url in BANLIST_URLS.items():                  # each EDOPro file
        try:
            (sources / name).write_bytes(IO.download(url))  # save it
        except Exception as e:                                            # one file failed: the others still count
            log(f"    couldn't download {name}: {e}")       # note it and carry on

    log("--- Downloading the formats from Format Library (about a minute) ...")  # tell the user
    try:
        formats = json.loads(IO.download(f"{FL_API}/formats"))                   # [{'name', 'date', 'banlist', 'category'}, ...]
        (sources / "fl_formats.json").write_text(json.dumps(formats), encoding="utf-8")  # save the format list (ygo_make_banlists.py reads it)
        have = set(load_banlists(P.FORBIDDEN))                            # lists already in 'forbidden lists'
        # dated TCG / OCG formats with a banlist...
        new = [f for f in formats if f.get("date") and f.get("banlist") and f.get("category") in ("TCG", "OCG")
               and fl_shown_name(f["name"], f["date"], f["category"]) not in have]   # old formats never change: skip them
        wanted = {(f["banlist"], f["category"]) for f in new}             # each banlist once (formats share them)
        for banlist, cat in sorted(wanted):                 # each one
            name = banlist.strip().lower().replace(" ", "-")              # 'September 2008' -> 'september-2008'
            url = f"{FL_API}/banlists/{name}" + ("?category=OCG" if cat == "OCG" else "")  # its address (OCG lists need a flag)
            try:
                (sources / f"fl_{name}_{cat}.json").write_bytes(IO.download(url))  # save it
            except Exception as e:                          # failed:
                log(f"    couldn't download {banlist} ({cat}): {e}")  # note it and carry on
            time.sleep(POLITE_PAUSE)                                               # be polite: a short pause between requests
        log(f"    {len(formats)} formats; {len(new)} new ones, {len(wanted)} banlists to download")  # the summary
        # (the staples are NOT downloaded here any more: the separate "Check Staples" button does that - refresh_staples())
    except Exception as e:                                  # Format Library didn't answer:
        log(f"    couldn't reach Format Library: {e}")      # note it and carry on

    log("--- Downloading the Master Duel list ...")         # tell the user
    try:
        cards = []                                          # every card on the list
        for status in MD_STATUSES:                                        # one status at a time, 50 cards a page
            for page in range(1, 20):                                     # (never more than 20 pages: a safety stop)
                url = f"{MD_API}?banStatus={urllib.parse.quote(status)}&limit=50&page={page}"  # this status, this page
                batch = json.loads(IO.download(url))        # download it
                cards += [{"name": c.get("name"), "banStatus": c.get("banStatus")} for c in batch]  # keep just the name and status
                if len(batch) < 50:                                       # a short page is the last page
                    break
        (sources / "md_cards.json").write_text(json.dumps(cards), encoding="utf-8")  # save them
        log(f"    {len(cards)} Master Duel cards on the list")  # the summary
    except Exception as e:                                  # failed:
        log(f"    couldn't reach Master Duel Meta: {e}")    # note it and carry on


def _save(path: Path, data) -> None:                        # function: save a downloaded file safely
    """Save one downloaded file: written to a '.part' file first, then renamed, so closing the window
       mid-write can't leave a broken file behind (a broken file is just downloaded again)."""
    part = path.with_suffix(".part")                        # the temporary name
    part.write_text(json.dumps(data), encoding="utf-8")     # write it
    part.replace(path)                                      # then rename it over the real name in one step


def _download_staples(sources: Path, formats: list, log, minutes: Optional[float] = STAPLES_MINUTES) -> None:  # function: download the deck data behind the staples
    """For every Format Library format we don't have staples for yet, in two parts:
         1. each format's list of popular deck types (quick, about 125 requests)
         2. each deck type's card list (how many copies of each card those decks ran) - the long part,
            WORKERS downloads at a time, with a real 'X of Y' count
       Every file is kept in program/data/staples_cache, so a run that stops partway (time limit, closed
       window) carries on next time with only what is still missing. ygo_make_staples.py makes the scores.
       minutes = the most time it may take; None = no limit (the "Check Staples" button: every format, however long)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed   # a few downloads at the same time
    folder = STAPLES_CACHE                                  # the cache folder
    folder.mkdir(parents=True, exist_ok=True)               # make it if missing
    empty = {name for name, cards in (IO.read_json(STAPLES_FILE) or {}).items() if not cards}   # formats with 0 staples so far
    complete = load_complete(COMPLETE_FILE) - empty                      # finished formats: never downloaded again (0 staples is never finished)
    current = current_formats(formats)                                    # the format still going on: always refreshed
    todo = [f for f in formats if f.get("date") and f.get("category") in ("TCG", "OCG")  # dated TCG / OCG formats...
            and fl_shown_name(f["name"], f["date"], f["category"]) not in complete]  # ...that aren't finished
    for old in folder.glob("*.failures"):                                 # failure counters from older versions: not used any more
        old.unlink()                                        # delete it
    log(f"--- {len(empty)} formats have 0 staples so far: checking them again")  # tell the user
    for f in todo:                                                        # the current format: start its files fresh
        if fl_shown_name(f["name"], f["date"], f["category"]) in current:  # the current format:
            fmt = f["name"].strip().lower().replace(" ", "-")  # its address name
            for path in [folder / f"{fmt}_popular.json"] + list(folder.glob(f"{fmt}__*.json")):  # its saved files
                path.unlink(missing_ok=True)                # delete each one
    if not todo:                                            # nothing to do:
        log("--- Every format's staples are complete")      # say so
        return
    stop_at = time.time() + minutes * 60 if minutes else float("inf")     # a time limit, or none at all

    def fetch(job: tuple) -> str:                           # inner function: download one job (runs in a worker)
        """Download one address into one file (runs in a worker). Returns 'ok', 'failed' or 'out of time'.
           Nothing is saved for a failed download, so it is simply tried again on the next update."""
        url, target, label = job                            # (address, file to save, name for the log)
        if time.time() > stop_at:                           # past the time limit:
            return "out of time"                                          # left for next run
        try:
            _save(target, json.loads(IO.download(url, timeout=60)))              # some of these pages are slow: up to a minute
            return "ok"                                     # it worked
        except Exception as e:                                            # a slow or broken page
            return f"failed ({type(e).__name__}) - tried again next update"  # say why
        finally:                                            # either way:
            time.sleep(POLITE_PAUSE)                                      # be polite: about one request a second

    def run(jobs: list, what: str) -> int:                  # inner function: download a list of jobs with a progress line each
        """Download every job, WORKERS at a time (1: these are small fan sites), with one line per finished download. Returns how many worked."""
        began, good = time.time(), 0                        # start time / downloads that worked
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:  # WORKERS downloads at a time
            futures = {pool.submit(fetch, job): job for job in jobs}  # start them all
            for done, future in enumerate(as_completed(futures), 1):      # in the order they FINISH (a slow one can't hide the rest)
                result = future.result()                    # 'ok' / 'failed ...' / 'out of time'
                good += result == "ok"                      # count the ones that worked
                left = (time.time() - began) / done * (len(jobs) - done)  # average time so far x downloads still to go
                log(f"    {what} {done} of {len(jobs)}: {futures[future][2]} - {result}"  # one line per download...
                    f"  (about {int(left // 60)} min {int(left % 60):02d} s left)")  # ...with the time left
        return good                                         # hand back how many worked

    # ---- part 1: every format's list of deck types ----------------------------------------------------
    slugs = {f["name"]: f["name"].strip().lower().replace(" ", "-") for f in todo}   # 'Virtual World' -> 'virtual-world'
    jobs = [(f"{FL_API}/decks/popular/{fmt}", folder / f"{fmt}_popular.json", name)  # (address, file, name) for each format...
            for name, fmt in slugs.items() if IO.read_json(folder / f"{fmt}_popular.json") is None]  # ...whose deck-type list isn't saved yet
    # tell the user
    log(f"--- Part 1 of 2: the deck types of {len(todo)} formats ({len(todo) - len(jobs)} saved from earlier runs) ...")
    run(jobs, "format")                                     # download them

    # ---- part 2: every deck type's card list ---------------------------------------------------------
    jobs, reused = [], 0                                    # jobs / files already saved
    for name, fmt in slugs.items():                         # each format
        popular = IO.read_json(folder / f"{fmt}_popular.json")  # its deck-type list
        for deck in popular if isinstance(popular, list) else []:        # (a format with no deck data: nothing to do)
            kind = (deck.get("cleanName") or deck.get("name") or "").strip().lower().replace(" ", "-")  # the deck type's address name
            target = folder / f"{fmt}__{kind}.json"         # its file
            if IO.read_json(target) is not None:            # already saved:
                reused += 1                                               # already downloaded on an earlier run
            else:
                # its address...
                jobs.append((f"{FL_API}/decktypes/summary?id={urllib.parse.quote(kind)}&format={urllib.parse.quote(fmt)}",
                             target, f"{name} / {deck.get('name') or kind}"))  # ...file and log name
    # tell the user
    log(f"--- Part 2 of 2: {len(jobs)} deck types to download ({reused} saved from earlier runs), {WORKERS} at a time ...")
    got = run(jobs, "deck type")                            # download them
    if time.time() > stop_at:                               # the time limit was hit:
        log(f"    stopped after {minutes} minutes; the next run carries on from here")  # say so
    log(f"    {got} deck types downloaded, {reused} kept from earlier runs")  # the summary


def _download_genesys(log) -> None:                         # function: download the Genesys points
    """Save {card name: Genesys points} for every card that costs points into program/data/genesys_points.json.
       First asks Yugioh Meta for only the cards with points; if that answer looks wrong (the filter was
       ignored), reads every card instead and keeps the ones with points. On failure the old file stays."""
    log("--- Downloading the Genesys points ...")           # tell the user

    def pages(query: str, size: int) -> list:               # inner function: every card a query returns, page by page
        """Every card a query returns, `size` cards a page (stops at a short page, or after 100 pages)."""
        cards = []                                          # the cards we collect
        for page in range(1, 101):                          # pages 1 to 100
            batch = json.loads(IO.download(f"{YGM_API}?{query}limit={size}&page={page}"))  # one page
            cards += batch                                  # keep its cards
            if len(batch) < size:                           # a short page:
                break
            time.sleep(POLITE_PAUSE)                        # be polite
        return cards                                        # hand back every card

    try:
        cards = pages("genesysPoints%5B%24gt%5D=0&", 200)                 # genesysPoints[$gt]=0 : only cards with points
        # card name -> points (only cards with points)
        points = {c["name"]: int(c["genesysPoints"]) for c in cards if c.get("name") and (c.get("genesysPoints") or 0) > 0}
        if len(points) < 20 or len(points) < len(cards) // 2:             # too few, or mostly 0-point cards: filter ignored
            log("    (the quick way didn't work: reading every card instead, about a minute)")  # tell the user
            cards = pages("", 1000)                         # every card, 1000 a page
            # card name -> points again
            points = {c["name"]: int(c["genesysPoints"]) for c in cards if c.get("name") and (c.get("genesysPoints") or 0) > 0}
        if len(points) < 20:                                # still too few:
            raise ValueError(f"only {len(points)} cards with points")     # something is off: keep the old file
        points = dict(sorted(points.items(), key=lambda kv: (-kv[1], kv[0])))   # most points first, then A-Z
        IO.write_json_lines(P.DATA / GENESYS_FILE, points)                   # one card per line
        top = next(iter(points))                            # the card with the most points
        # the summary
        log(f"    {len(points)} cards cost points (highest: {json.dumps(top, ensure_ascii=False)}: {points[top]})")
    except Exception as e:                                  # failed:
        log(f"    couldn't get the Genesys points ({e}); the old ones are still there")  # note it (the old file stays)


def _current_staples(log) -> None:                          # function: today's most popular cards, as staples
    """Staples for today's formats, refreshed every update: each site's most popular cards (no copy counts,
       so copies = 0 = 'pick 1-3 as usual'). Saved into format_staples.json under today's list names.
       A ranking that can't be downloaded, or doesn't come back sorted, is skipped (the log says so)."""
    log("--- Downloading today's most popular cards (TCG, OCG, Master Duel) ...")  # tell the user
    rankings = {}                                           # game -> {card name: [score, copies]}
    for game, (api, field) in CURRENT_SOURCES.items():      # each game's ranking
        try:
            # Ways of asking, tried in order until one gives a sorted answer. (When a site sorts by rank, cards
            # with NO rank can come first, so the first two ask for ranked cards only.)
            asks = [f"{field}%5B%24gte%5D=1&sort={field}",                # tcgPopRank[$gte]=1 : ranked cards only
                    f"{field}%5B%24ne%5D=null&sort={field}",              # tcgPopRank[$ne]=null : the same, other wording
                    f"sort={field}",                                      # plain sort (works for Master Duel Meta)
                    f"{field}%5B%24lte%5D={CURRENT_TOP}"]                 # tcgPopRank[$lte]=200, NOT sorted: we sort it
            cards, why = [], "no answer"                    # the cards found / why it failed
            for ask in asks:                                # each way of asking
                got = []                                    # this way's cards
                try:
                    for page in (1, 2):                                   # 2 pages of 100 = the top 200
                        got += json.loads(IO.download(f"{api}?{ask}&limit=100&page={page}"))
                except Exception as e:                                    # the site refused this way of asking:
                    why = f"{type(e).__name__}: {e}"                      # try the next one
                    continue
                got = sorted((c for c in got if isinstance(c.get(field), int)), key=lambda c: c[field])   # ranked only, #1 first
                ranks = [c[field] for c in got]             # their ranks
                if len(ranks) >= 50 and ranks[0] <= 5 and ranks[min(49, len(ranks) - 1)] <= CURRENT_TOP:   # really the top cards
                    cards = got                                           # this way of asking worked
                    break
                # why it failed (for the log)
                why = f"not sorted by popularity ({len(ranks)} ranked cards, first rank {ranks[0] if ranks else None})"
            if not cards:                                   # no way worked:
                raise ValueError(why)                       # (skip this game)
            scores = {}                                     # card name -> [score, copies]
            for c in cards:                                 # each card
                if c[field] <= CURRENT_TOP and c.get("name"):  # inside the top 200, with a name:
                    scores.setdefault(c["name"], [round(1 - (c[field] - 1) / CURRENT_TOP, 3), 0])   # alt arts: first wins
            rankings[game] = scores                         # save this game's scores
            log(f"    {game}: {len(scores)} cards (most popular: {next(iter(scores))})")  # the summary
        except Exception as e:                              # failed:
            log(f"    {game}: skipped ({e})")               # note it and carry on
    if not rankings:                                        # no game worked:
        return
    path = STAPLES_FILE                                     # the staples file
    data = IO.read_json(path, {})                                            # the staples we already have
    for name in load_banlists(P.FORBIDDEN):                               # today's lists, whatever their date is now
        game = next((g for start, g in CURRENT_LISTS.items() if name.startswith(start)), None)  # which game's ranking it uses
        if game in rankings:                                # one we have:
            data[name] = rankings[game]                     # save it under the list's name
    IO.write_json_lines(path, data)                                          # one format per line


def update_banlists(work: Path, cards_path: Path, log) -> bool:  # function: download every banlist source and rebuild the list file
    """Download every banlist source and rebuild our list file. Never stops an update: on a failure the
       old list file stays. Returns True if the list file was rebuilt."""
    sources = work / "banlist_sources"                      # the downloads folder inside the work folder
    _download_banlists(sources, log)                        # download the banlists
    _download_genesys(log)                                  # download the Genesys points
    try:
        IO.run_script(work, log, "Building the Forbidden Lists", "ygo_make_banlists.py",  # build the list file...
              str(sources), str(P.FORBIDDEN / BANLIST_FILE), str(cards_path))  # ...from the downloads, into 'forbidden lists'
        return True                                                       # (staples: the separate refresh_staples() below)
    except Exception as e:                                  # failed:
        log(f"    banlists not updated ({e}); the old ones are still there")  # note it (the old file stays)
        return False                                        # (not rebuilt)


def refresh_banlists(cards_path: Path, log=print) -> bool:  # function: "Refresh Banlists" (no card update)
    """Only the Forbidden Lists (used when the cards are already up to date)."""
    work = P.PROGRAM / "update_work"                        # its own work folder
    IO.fresh_folder(work)                                   # start clean (OneDrive-safe; a locked leftover is reused)
    done = update_banlists(work, Path(cards_path).resolve(), log)  # download and rebuild
    IO.remove_folder(work)                                  # remove the work folder
    log("=== Forbidden Lists updated." if done else "=== Forbidden Lists not updated.")  # the summary
    return done                                             # hand back whether it worked


def refresh_staples(log=print) -> bool:                     # function: "Check Staples"
    """The "Check Staples" button, separate from the card / Forbidden List update:
         1. ask Format Library for its list of formats
         2. download the deck data of EVERY format whose staples aren't finished - no time limit, so the
            first run can take an hour or more (it pauses between requests to be polite to the site).
            Progress is saved as it goes: closing the window partway is fine, the next run carries on.
         3. work out each format's staples (ygo_make_staples.py -> format_staples.json)
         4. refresh today's most popular cards (TCG, OCG, Master Duel)
       Returns True if format_staples.json was rebuilt."""
    work = P.PROGRAM / "staples_work"                                     # its own folder: can't clash with a card update
    IO.fresh_folder(work)                                   # start clean (OneDrive-safe; a locked leftover is reused)
    sources = IO.fresh_folder(work / "banlist_sources")     # the downloads folder, empty
    done = False                                            # not done yet
    try:
        log("--- Downloading the formats from Format Library ...")  # tell the user
        formats = json.loads(IO.download(f"{FL_API}/formats"))           # [{'name', 'date', 'banlist', 'category'}, ...]
        (sources / "fl_formats.json").write_text(json.dumps(formats), encoding="utf-8")   # ygo_make_staples.py reads this
        _download_staples(sources, formats, log, minutes=None)            # every format, however long it takes
        IO.run_script(work, log, "Working out each format's staples", "ygo_make_staples.py",  # work out the staples...
                      str(sources), str(STAPLES_FILE), str(STAPLES_CACHE), str(COMPLETE_FILE))  # ...from the downloads and the cache
        _current_staples(log)                                             # today's formats
        done = True                                         # it worked
    except Exception as e:                                  # failed:
        log(f"    staples not updated ({e}); the old ones are still there")  # note it (the old file stays)
    IO.remove_folder(work)                                  # remove the work folder
    log("=== Staples updated." if done else "=== Staples not updated.")  # the summary
    return done                                             # hand back whether it worked
