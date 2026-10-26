# ygo_rate_by_views.py
# Rates EVERY card (0-5) from its YGOPRODeck page views (the "misc=yes" API file). Nothing is rated by hand.
# Older cards have had more years to collect views, so each card is only compared with cards from
# the same era AND the same release year. Each era has its own spread of ratings (ERA_SPREAD below):
# Duel Monsters cards stay lower on average than GO RUSH!! ones. Cards released in the last year are never below 2.
# Rewrites cards_new.csv in place (only the 'card rating' column changes).
#
#   python ygo_rate_by_views.py cards_new.csv card_viewcounts.txt

import sys                                     # file names typed after the script name
from collections import Counter, defaultdict  # counting, and grouping cards
from datetime import date, timedelta           # "released in the last year"

from ygo_io import read_api, read_table, write_table   # shared file reading / writing

CARDS_FILE = sys.argv[1] if len(sys.argv) > 1 else "cards_new.csv"      # the file to re-rate
API_FILE = sys.argv[2] if len(sys.argv) > 2 else "card_viewcounts.txt"  # the misc=yes API file
NEW_CARD_LOWEST = 2                                                      # new cards never go below this
NEW_CARD_DAYS = 365                                                      # "new" = released within this many days

# How many cards of each era get each rating, in percent:   0     1     2     3     4     5
# (copied from the hand ratings in the original cards.csv, so the eras keep the feel they had)
ERA_SPREAD = {
    "Duel Monsters": [9.6, 27.7, 39.2, 15.3,  5.9, 2.3],
    "GX":            [0.9, 30.9, 41.0, 18.4,  6.5, 2.3],
    "5DS":           [0.1, 34.7, 44.0, 14.8,  4.7, 1.7],
    "ZEXAL":         [0.6, 22.6, 44.7, 18.9, 10.7, 2.6],
    "ARC-V":         [0.2,  7.8, 36.6, 34.4, 15.5, 5.6],
    "VRAINS":        [0.0,  6.2, 29.1, 31.9, 24.0, 8.9],
    "SEVENS":        [0.1,  9.2, 24.8, 31.5, 26.4, 8.0],    # (SEVENS and GO RUSH!! were one era, "Modern", in the hand ratings:
    "GO RUSH!!":     [0.1,  9.2, 24.8, 31.5, 26.4, 8.0],    #  both start from Modern's spread)
}


def bands(percents):
    """[9.6, 27.7, ...] -> [(0.096, 0), (0.373, 1), ...]: the bottom 9.6% get a 0, the bottom 37.3% a 1 or less, ..."""
    total, running, out = sum(percents), 0.0, []
    for rating, share in enumerate(percents):   # 0, 1, 2, ...
        if share == 0:
            continue                             # nobody in this era gets this rating
        running += share / total                 # (dividing by the total keeps rounding from adding up past 100%)
        out.append((running, rating))
    return out


CUTOFFS = {era: bands(p) for era, p in ERA_SPREAD.items()}   # era -> [(top share, rating), ...] lowest first


def rating_for(era, position):
    """position = 0.0 (fewest views in its group) .. 1.0 (most) -> a rating, using the era's spread."""
    cut = CUTOFFS.get(era) or CUTOFFS["GO RUSH!!"]   # an era we don't know yet: use the newest era's spread
    for top, rating in cut:
        if position <= top + 1e-9:               # (tiny margin for rounding)
            return rating
    return cut[-1][1]                            # the very top: highest rating


# --- Views and release date for every card, by Card Id ----------------------
api_cards = read_api(API_FILE)                                # every card YGOPRODeck has
views, released = {}, {}
views_by_name, released_by_name = {}, {}                      # fallback: Konami sometimes gives a card a new Id
for c in api_cards:
    misc = (c.get("misc_info") or [{}])[0]                    # the extra info block
    day = misc.get("tcg_date") or misc.get("ocg_date") or ""  # first release (TCG, else OCG), e.g. '2023-04-27'
    views[str(c["id"])] = views_by_name[c["name"].lower()] = misc.get("views", 0)   # total page views
    released[str(c["id"])] = released_by_name[c["name"].lower()] = day

# --- Read the cards and group them by era + release year ---------------------
columns, rows = read_table(CARDS_FILE)
id_col = columns[0]                                           # 'Card Id'

for r in rows:                                                # a card whose Id changed: find it by name instead
    if r[id_col] not in views and r["name"].lower() in views_by_name:
        views[r[id_col]] = views_by_name[r["name"].lower()]
        released[r[id_col]] = released_by_name[r["name"].lower()]

groups = defaultdict(list)                                    # (era, year) -> the cards in it
for r in rows:
    if r[id_col] in views:                                    # cards the API has views for
        groups[(r["era"], released[r[id_col]][:4] or "unknown")].append(r)

new_since = (date.today() - timedelta(days=NEW_CARD_DAYS)).isoformat()   # e.g. '2025-10-21'
changed = 0
for (era, _yr), cards in groups.items():                       # each era + year group on its own
    cards.sort(key=lambda r: views[r[id_col]])                # fewest views first
    for i, r in enumerate(cards):
        position = (i + 0.5) / len(cards)                     # where it sits in its group, 0 to 1
        new = rating_for(era, position)
        if released[r[id_col]] >= new_since:                  # released in the last year: apply the floor
            new = max(new, NEW_CARD_LOWEST)                   # (no date at all = '' = not new)
        if str(new) != r["card rating"]:
            changed += 1
        r["card rating"] = str(new)

# --- Write the file back ----------------------------------------------------
write_table(CARDS_FILE, columns, rows)

spread = Counter(r["card rating"] for r in rows)
print(f"Re-rated {sum(len(g) for g in groups.values())} cards ({changed} changed).",
      "Spread:", ", ".join(f"{k}: {spread[k]}" for k in sorted(spread)))
