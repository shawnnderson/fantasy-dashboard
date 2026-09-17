#!/usr/bin/env python3
"""Pulls every league in config.json and writes docs/data.json for the site.

    python3 build.py

For private ESPN leagues, set ESPN_S2 and ESPN_SWID first (see README)."""

import datetime
import json
import os
import sys
import traceback

from fantasy import analysis, espn, sleeper
from fantasy.net import AuthError

ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(ROOT, "docs", "data.json")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def main():
    with open(os.path.join(ROOT, "config.json")) as f:
        config = json.load(f)

    state = sleeper.nfl_state()
    season = str(config.get("season") or state.get("league_season") or state["season"])
    week = int(state.get("display_week") or state.get("week") or 1)
    if state.get("season_type") != "regular":
        week = max(week, 1)
    log("Season %s, week %d" % (season, week))

    log("Loading player database, projections and stats...")
    players = sleeper.players()
    long_weeks = [w for w in range(week, week + 8) if w <= analysis.LAST_FANTASY_WEEK]
    projections = {w: sleeper.projections(season, w) for w in long_weeks}
    stats = {w: sleeper.stats(season, w) for w in range(max(1, week - 3), week)}
    data = analysis.SeasonData(
        players=players,
        projections=projections,
        stats=stats,
        byes=sleeper.bye_weeks(season),
        adds=sleeper.trending("add"),
        drops=sleeper.trending("drop"),
        week=week,
    )

    raw_leagues = []  # (platform, id, label, loader) so one broken league never blocks the rest
    username = config.get("sleeper_username")
    if username:
        uid = sleeper.user_id(username)
        for lg in sleeper.leagues(uid, season):
            raw_leagues.append(("sleeper", lg["league_id"], lg.get("name") or "Sleeper league",
                                lambda lg=lg: sleeper.load_league(lg, uid)))

    espn_ids = config.get("espn_league_ids") or []
    if espn_ids:
        s2, swid = os.environ.get("ESPN_S2"), os.environ.get("ESPN_SWID")
        matcher = None
        for lid in espn_ids:
            def load(lid=lid):
                nonlocal matcher
                if matcher is None:
                    matcher = espn.PlayerMatcher(players, espn.pro_teams(season))
                return espn.load_league(lid, season, s2, swid, matcher)
            raw_leagues.append(("espn", str(lid), "ESPN league %s" % lid, load))

    leagues, trending_scope = [], []
    for platform, ident, label, loader in raw_leagues:
        try:
            league = loader()
            trending_scope.append(league)
            leagues.append(analysis.analyze(league, data))
            log("  ok   %s" % label)
        except Exception as e:  # show the problem on the site instead of failing the build
            if not isinstance(e, AuthError):
                traceback.print_exc()
            log("  FAIL %s: %s" % (label, e))
            leagues.append({
                "platform": platform,
                "id": ident,
                "name": label,
                "error": str(e),
                "needs_login": isinstance(e, AuthError),
            })

    trending = []
    for pid, count in sorted(data.adds.items(), key=lambda kv: -kv[1])[:25]:
        p = data.player(pid)
        if not p:
            continue
        trending.append({
            "id": pid,
            "name": data.name(pid),
            "pos": p.get("position"),
            "team": p.get("team"),
            "injury": p.get("injury_status"),
            "adds": count,
            "available": [lg["id"] for lg in trending_scope if pid not in lg["rostered"]],
        })

    site = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "season": season,
        "week": week,
        "short_weeks": data.short_weeks,
        "long_weeks": data.long_weeks,
        "leagues": leagues,
        "trending": trending,
        "checked_leagues": [lg["id"] for lg in trending_scope],
    }
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(site, f, indent=1)
    log("Wrote %s" % OUTPUT)


if __name__ == "__main__":
    main()
