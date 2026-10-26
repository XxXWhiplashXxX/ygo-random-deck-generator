"""
ygo_update.py  --  "Check for new cards": keeps cards.csv and Stats.csv up to date with YGOPRODeck.

  check()                         -> (latest version, version we have, program changed?)    takes about a second
  run_update(cards, stats, log)   -> runs every step below, on COPIES, then swaps the files in
Folders come from ygo_paths.py: the step scripts are in program/, the data files in program/data/,
backups go to program/backups/, and the temporary work folder is program/update_work/.

Steps (each one is one of the scripts we made, run in order inside a work folder):
   1. download the full card list WITH page views         (cardinfo.php?misc=yes, ~24 MB)
   2. add the cards cards.csv doesn't have yet             ygo_add_new_cards.py
   3. add their archetypes to Stats                        ygo_add_new_archetypes.py
   4. join old + new cards                                 ygo_merge_cards.py
   5. traits from the API                                  ygo_fix_traits.py
   6. tidy Stats                                           ygo_fix_stats.py
   7. Yugipedia: card pages (re-read after 30 days),      ygo_wiki_series.py, ygo_wiki_gaps.py,
      series pages, cards found by passcode               ygo_wiki_passcode.py, ygo_add_passcode_matches.py
   8. archetype + series labels                            ygo_wiki_labels.py, ygo_series_pages.py
   9. eras from release dates (Format Library's eras)      ygo_set_eras.py
      + ratings from views (every card)                    ygo_rate_by_views.py
  10. release dates + alternate-art Ids                    ygo_add_dates.py
  11. support columns                                      ygo_fill_supports.py
      + Fusion substitutes tagged as Fusion support        ygo_fusion_substitutes.py
      + supports Yugipedia lists that the parser missed   ygo_wiki_supports.py
      + duplicate archetypes removed / merged             ygo_clean_labels.py   (Yugipedia first, YGOPRODeck last)
  12. swap: old cards.csv / Stats.csv go to program/backups/, the new ones take their place
  13. the Forbidden Lists, staples and Genesys points      ygo_formats_update.py (if this fails, the card update
                                                           still counts - see that file for what it downloads)
If ANY of steps 1-11 fails, nothing is swapped and your files stay exactly as they were.

Files this needs in program/data/:
   Mechanics.csv          the mechanic labels (the parser needs it)
   card_archseries.csv    what the wiki said about each card (kept between updates so known cards aren't re-read)

Run on its own:   python ygo_update.py          (checks, asks, updates)
"""
import hashlib          # the "program stamp": a short fingerprint of the files that build cards.csv
import json             # the API answers in JSON; the saved version file
import shutil           # copying files and removing the work folder
import time             # date-stamp for backups
from pathlib import Path

import ygo_io as IO     # download(), run_script()
import ygo_paths as P   # where program/, data/ and backups/ are
from ygo_formats_update import update_banlists   # step 13: the Forbidden Lists, staples and Genesys points

VERSION_URL = "https://db.ygoprodeck.com/api/v7/checkDBVer.php"           # tiny page: just the database version
CARDS_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php?misc=yes"      # every card, with page views
SERIES_CATEGORY = "Category:TCG and OCG series"                          # Yugipedia's list of series (about 225)
ARCHETYPE_CATEGORY = "Category:TCG and OCG archetypes"                    # Yugipedia's list of archetypes (about 610)
KEEP_BACKUPS = 3                                                          # backups of cards.csv / Stats.csv to keep
VERSION_FILE = "ygo_db_version.json"                                      # remembers the version we last updated to


# The files that decide what cards.csv contains: every step script, the helper they all use, and the parser's labels.
# If any of them changes, the cards are rebuilt even when YGOPRODeck has no new cards. (The GUI, the deck builder and
# the settings are not here: they don't change cards.csv.)
STAMP_SCRIPTS = ("ygo_add_new_cards.py", "ygo_add_new_archetypes.py", "ygo_merge_cards.py", "ygo_fix_traits.py",
                 "ygo_fix_stats.py", "ygo_wiki_series.py", "ygo_wiki_gaps.py", "ygo_wiki_passcode.py",
                 "ygo_add_passcode_matches.py", "ygo_wiki_labels.py", "ygo_series_pages.py", "ygo_set_eras.py",
                 "ygo_rate_by_views.py", "ygo_add_dates.py", "ygo_fill_supports.py", "ygo_fusion_substitutes.py",
                 "ygo_wiki_supports.py", "ygo_clean_labels.py", "ygo_io.py")                                             # (all in program/)
STAMP_DATA = ("Mechanics.csv",)                                           # (in program/data/)


def program_stamp() -> str:
    """A 12-letter fingerprint of the files in STAMP_SCRIPTS and STAMP_DATA: it changes whenever any of them changes."""
    h = hashlib.md5()                                                     # one running fingerprint for all the files
    for path in [P.PROGRAM / n for n in STAMP_SCRIPTS] + [P.DATA / n for n in STAMP_DATA]:
        h.update(path.name.encode())                                      # the file's name ...
        h.update(path.read_bytes() if path.is_file() else b"(missing)")   # ... and its contents (a missing file counts too)
    return h.hexdigest()[:12]                                             # e.g. 'a1b2c3d4e5f6'


def check() -> tuple:
    """(latest version on YGOPRODeck, the version we last updated to or None, True if the program changed since then)."""
    latest = json.loads(IO.download(VERSION_URL))[0]["database_version"]        # e.g. '153.95'
    saved_file = P.DATA / VERSION_FILE                                    # kept in the data folder
    saved = json.loads(saved_file.read_text()) if saved_file.is_file() else {}   # {"database_version": ..., "program": ...}
    have = saved.get("database_version")                                  # the card version we last updated to
    changed = saved.get("program") != program_stamp()                     # no stamp saved yet (an older file) also counts as changed
    return str(latest), (str(have) if have is not None else None), changed


def _make_writable_and_retry(func, path, _exc_info) -> None:
    """For shutil.rmtree on Windows: a read-only file (OneDrive often marks them) can't be deleted - make it writable, then try again."""
    import os, stat                                                        # (only needed here)
    os.chmod(path, stat.S_IWRITE)                                          # clear the read-only flag
    func(path)                                                             # delete it again (raises if it is truly locked)


def run_update(cards_path: Path, stats_path: Path, log=print) -> bool:
    """Run every step on copies inside program/update_work, then swap the new files in.
       `log` is called with one line of text at a time (print, or the GUI's preview box).
       Returns True if the files were updated, False if a step failed (the old files are untouched)."""
    cards_path, stats_path = Path(cards_path).resolve(), Path(stats_path).resolve()   # full paths: the steps run inside the work folder
    work = P.PROGRAM / "update_work"                                      # everything happens in here
    log(f"--- Preparing the work folder ({work}) ...")
    if work.exists():
        shutil.rmtree(work, onerror=_make_writable_and_retry)             # start clean every time (read-only / synced files too)
    work.mkdir(exist_ok=True)

    def step(title: str, script: str, *args: str) -> None:
        """Run one script inside the work folder; stop everything if it fails."""
        IO.run_script(work, log, title, script, *args)

    try:
        # --- copy everything the steps need into the work folder ----------------
        for name in ("Mechanics.csv",):
            if not (P.DATA / name).is_file():
                raise RuntimeError(f"{name} is missing from {P.DATA}")
            shutil.copy(P.DATA / name, work / name)
        shutil.copy(cards_path, work / "cards_old.csv")                   # the current cards
        shutil.copy(stats_path, work / "Stats_old.csv")                   # the current Stats
        if (P.DATA / "card_archseries.csv").is_file():                    # the wiki notes from earlier updates
            shutil.copy(P.DATA / "card_archseries.csv", work / "card_archseries.csv")

        # --- 1. downloads ---------------------------------------------------------
        log("--- Downloading the card list from YGOPRODeck (about 24 MB) ...")
        (work / "api.json").write_bytes(IO.download(CARDS_URL, timeout=300))      # big file: allow up to 5 minutes
        log("--- Downloading the lists of series and archetypes from Yugipedia ...")
        for category, file in ((SERIES_CATEGORY, "series_list.json"), (ARCHETYPE_CATEGORY, "archetype_list.json")):
            titles = IO.wiki_category(category)                           # every title in the category (1-2 requests)
            if not titles:
                raise RuntimeError(f"Yugipedia sent an empty list for {category}")   # never build labels from an empty list
            (work / file).write_text(json.dumps({"query": {"categorymembers": [{"title": t} for t in titles]}}, ensure_ascii=False),
                                     encoding="utf-8")                    # (the same shape the wiki API uses, so the steps read it as before)

        # --- 2-6. new cards, Stats, traits ------------------------------------------
        step("Adding new cards", "ygo_add_new_cards.py", "api.json", "cards_old.csv", "new_cards.csv", "Stats_old.csv")
        step("Adding their archetypes to Stats", "ygo_add_new_archetypes.py", "new_cards.csv", "Stats_old.csv", "api.json", "Stats_new.csv")
        step("Joining old and new cards", "ygo_merge_cards.py", "cards_old.csv", "new_cards.csv", "cards_merged.csv")
        step("Traits from the API", "ygo_fix_traits.py", "cards_merged.csv", "api.json", "cards_new.csv")
        step("Tidying Stats", "ygo_fix_stats.py", "Stats_new.csv", "cards_new.csv")

        # --- 7-8. wiki archetypes and series -------------------------------------------
        step("Yugipedia: reading card pages (new ones, and any read over 30 days ago)", "ygo_wiki_series.py", "cards_new.csv")
        step("Yugipedia: reading the series pages", "ygo_wiki_gaps.py")
        step("Yugipedia: finding unmatched cards by passcode", "ygo_wiki_passcode.py")
        step("Yugipedia: adding confirmed matches", "ygo_add_passcode_matches.py")
        step("Rebuilding archetype and series labels", "ygo_wiki_labels.py")
        step("Adding series-page members", "ygo_series_pages.py")

        # --- 9-10. ratings and support columns -----------------------------------------
        step("Eras from release dates", "ygo_set_eras.py", "cards_new.csv", "api.json")
        step("Ratings from page views", "ygo_rate_by_views.py", "cards_new.csv", "api.json")
        step("Release dates", "ygo_add_dates.py", "cards_new.csv", "api.json")
        step("Support columns (your parser)", "ygo_fill_supports.py", "--cards", "cards_new.csv",
             "--stats", "Stats_new.csv", "--mechanics", "Mechanics.csv", "--out", "cards_filled.csv")
        step("Fusion substitutes count as Fusion support", "ygo_fusion_substitutes.py", "cards_filled.csv")
        step("Yugipedia: adding supports the parser missed", "ygo_wiki_supports.py", "cards_filled.csv")   # needs card_archseries.csv + Stats_new.csv
        step("Archetype-limited Fusion searchers", "ygo_fusion_searchers.py", "cards_filled.csv")
        # Last: merge or remove duplicate archetypes everywhere (the files above may still use old names)
        step("Removing duplicate archetypes", "ygo_clean_labels.py", "cards_filled.csv", "Stats_new.csv")
    except Exception as e:                                                # any failure: leave the real files alone
        log(f"!!! Update stopped: {e}")
        log("    Your cards.csv and Stats.csv were NOT changed.")
        return False

    # --- 12. swap: back up the old files, put the new ones in place --------------------
    stamp = time.strftime("%Y-%m-%d_%H%M")                                # e.g. 2026-10-21_1430
    backups = P.BACKUPS                                                   # program/backups
    shutil.copy(cards_path, backups / f"cards_{stamp}.csv")               # keep the old versions
    shutil.copy(stats_path, backups / f"Stats_{stamp}.csv")
    for kind in ("cards", "Stats"):                                       # only the newest KEEP_BACKUPS of each are kept
        old = sorted(backups.glob(f"{kind}_????-??-??_????.csv"))         # the date in the name sorts oldest first
        for path in old[:-KEEP_BACKUPS]:
            path.unlink()
    shutil.copy(work / "cards_filled.csv", cards_path)                    # the new versions
    shutil.copy(work / "Stats_new.csv", stats_path)
    shutil.copy(work / "card_archseries.csv", P.DATA / "card_archseries.csv")   # remember the wiki notes
    shutil.copy(work / "series_list.json", P.DATA / "series_list.json")
    shutil.copy(work / "archetype_list.json", P.DATA / "archetype_list.json")
    update_banlists(work, cards_path, log)                                # 13. (never stops the update)
    shutil.rmtree(work, ignore_errors=True)                               # the work folder is no longer needed
    try:                                                                  # remember which version we now have
        latest, _, _ = check()
        (P.DATA / VERSION_FILE).write_text(json.dumps({"database_version": latest, "program": program_stamp()}))   # the card version AND the program stamp
    except Exception:
        pass                                                              # not fatal: the next check just offers again
    log(f"=== Done. Old files saved in program/backups as cards_{stamp}.csv and Stats_{stamp}.csv")
    return True


if __name__ == "__main__":                                                # running the file directly
    latest, have, changed = check()
    print(f"YGOPRODeck database version {latest}; yours: {have or 'never updated'}")
    if latest == have and not changed:
        print("Already up to date.")
    elif latest == have:
        print("No new cards, but the program has changed since your last update, so cards.csv can be rebuilt.")
    elif input("Update now? This takes a few minutes. (y/n) ").strip().lower().startswith("y"):
        run_update(P.DATA / "cards.csv", P.DATA / "Stats.csv")
