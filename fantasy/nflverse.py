"""Deeper weekly usage from nflverse: target share, air yards share, carries.

nflverse publishes weekly player stats as one small gzipped CSV per season
(about 100 KB mid-season), so this reads the file directly rather than
installing nflreadpy, which needs Python 3.10+, polars and pydantic. Same data,
no dependencies.

Source: https://github.com/nflverse/nflverse-data/releases/tag/stats_player"""

import csv
import gzip
import io

from .match import NameIndex
from .net import get_bytes

URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
       "stats_player/stats_player_week_%s.csv.gz")

NUMERIC = ("targets", "target_share", "air_yards_share", "wopr", "carries",
           "receiving_air_yards", "receptions", "rushing_yards", "receiving_yards")


def _float(row, key):
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


def weekly_usage(season, players):
    """{sleeper player id: {week: {targets, target_share, carries, carry_share, ...}}}

    Returns {} if nflverse is unavailable: these stats sharpen the advice but
    nothing depends on them."""
    try:
        raw = get_bytes(URL % season)
    except Exception:
        return {}

    rows = list(csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), "utf-8")))
    if not rows:
        return {}

    # Team carries per week, so a back's workload can be read as a share.
    team_carries = {}
    for row in rows:
        key = (row.get("team"), row.get("week"))
        team_carries[key] = team_carries.get(key, 0) + (_float(row, "carries") or 0)

    by_gsis = {}
    for pid, p in players.items():
        if p.get("gsis_id"):
            by_gsis[p["gsis_id"].strip()] = pid
    names = NameIndex(players)

    usage = {}
    for row in rows:
        pos = row.get("position")
        if pos not in ("QB", "RB", "WR", "TE"):
            continue
        pid = by_gsis.get((row.get("player_id") or "").strip()) or names.find(
            row.get("player_display_name"), pos, row.get("team"))
        if not pid:
            continue
        try:
            week = int(row["week"])
        except (KeyError, TypeError, ValueError):
            continue
        entry = {k: _float(row, k) for k in NUMERIC}
        carries = entry.get("carries") or 0
        team_total = team_carries.get((row.get("team"), row.get("week")), 0)
        entry["carry_share"] = carries / team_total if team_total else None
        usage.setdefault(pid, {})[week] = entry
    return usage
