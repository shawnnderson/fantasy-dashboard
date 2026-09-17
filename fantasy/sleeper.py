"""Sleeper: the shared data backbone (players, projections, stats, trends)
plus loading your Sleeper leagues.

Docs: https://docs.sleeper.com  (the /projections and /stats endpoints are
unofficial but are what sleeper.com itself uses)."""

from collections import Counter

from .net import get_json

BASE = "https://api.sleeper.app"
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]
_POS_QUERY = "&".join("position%5B%5D=" + p for p in POSITIONS)

# Only the stat fields the dashboard uses, to keep memory and data.json small.
_KEEP = ("pts_ppr", "pts_half_ppr", "pts_std", "off_snp", "tm_off_snp", "rec_tgt", "rush_att")


def nfl_state():
    return get_json(BASE + "/v1/state/nfl")


def user_id(username):
    user = get_json("%s/v1/user/%s" % (BASE, username))
    if not user:
        raise ValueError("Sleeper user '%s' not found" % username)
    return user["user_id"]


def players():
    """Full NFL player database (~15 MB). Sleeper asks for at most one call per day."""
    return get_json(BASE + "/v1/players/nfl")


def _weekly(kind, season, week):
    url = "%s/%s/nfl/%s/%d?season_type=regular&%s" % (BASE, kind, season, week, _POS_QUERY)
    out = {}
    for row in get_json(url) or []:
        stats = row.get("stats") or {}
        entry = {k: stats[k] for k in _KEEP if stats.get(k) is not None}
        entry["opp"] = row.get("opponent")
        out[row["player_id"]] = entry
    return out


def projections(season, week):
    return _weekly("projections", season, week)


def stats(season, week):
    return _weekly("stats", season, week)


def trending(kind, hours=48, limit=100):
    url = "%s/v1/players/nfl/trending/%s?lookback_hours=%d&limit=%d" % (BASE, kind, hours, limit)
    return {row["player_id"]: row["count"] for row in get_json(url)}


def bye_weeks(season):
    """{team: bye week}, derived from the regular-season schedule."""
    games = get_json("%s/schedule/nfl/regular/%s" % (BASE, season)) or []
    teams = {g[side] for g in games for side in ("home", "away")}
    playing = {}
    for g in games:
        playing.setdefault(g["week"], set()).update((g["home"], g["away"]))
    byes = {}
    for week, playing_teams in playing.items():
        for team in teams - playing_teams:
            byes[team] = week
    return byes


def leagues(uid, season):
    return get_json("%s/v1/user/%s/leagues/nfl/%s" % (BASE, uid, season)) or []


def _scoring(settings):
    rec = settings.get("rec", 0)
    return "ppr" if rec >= 1 else "half_ppr" if rec >= 0.5 else "std"


def load_league(league, uid):
    """Normalise a Sleeper league into the shape analysis.py expects."""
    lid = league["league_id"]
    rosters = get_json("%s/v1/league/%s/rosters" % (BASE, lid))
    users = {u["user_id"]: u for u in get_json("%s/v1/league/%s/users" % (BASE, lid))}

    mine = next(
        (r for r in rosters if r.get("owner_id") == uid or uid in (r.get("co_owners") or [])),
        None,
    )
    if mine is None:
        raise ValueError("Couldn't find your roster in this league")

    positions = league["roster_positions"]
    slots = Counter(p for p in positions if p != "BN")
    starting_slots = [p for p in positions if p != "BN"]

    current = {}
    for slot, pid in zip(starting_slots, mine.get("starters") or []):
        if pid and pid != "0":
            current[pid] = slot
    for pid in mine.get("reserve") or []:
        current[pid] = "IR"
    for pid in mine.get("taxi") or []:
        current[pid] = "TAXI"
    for pid in mine.get("players") or []:
        current.setdefault(pid, "BN")

    def standing(r):
        s = r.get("settings") or {}
        return (s.get("wins", 0), s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100.0)

    ordered = sorted(rosters, key=standing, reverse=True)
    s = mine.get("settings") or {}
    owner = users.get(mine.get("owner_id")) or {}
    lsettings = league.get("settings") or {}

    waiver = {"priority": s.get("waiver_position")}
    if lsettings.get("waiver_type") == 2:
        waiver["faab_left"] = lsettings.get("waiver_budget", 100) - s.get("waiver_budget_used", 0)

    return {
        "platform": "sleeper",
        "id": lid,
        "name": league.get("name") or "Sleeper league",
        "team_name": (owner.get("metadata") or {}).get("team_name") or owner.get("display_name") or "My team",
        "url": "https://sleeper.com/leagues/%s/team" % lid,
        "record": "%d-%d%s" % (
            s.get("wins", 0), s.get("losses", 0), "-%d" % s["ties"] if s.get("ties") else ""),
        "points_for": round(standing(mine)[1], 1),
        "rank": ordered.index(mine) + 1,
        "teams": len(rosters),
        "scoring": _scoring(league.get("scoring_settings") or {}),
        "slots": {k: v for k, v in slots.items() if k != "IR"},
        "bench": positions.count("BN"),
        "ir_slots": lsettings.get("reserve_slots", 0),
        "roster": current,  # {sleeper player id: current slot}
        "rostered": {pid for r in rosters for pid in (r.get("players") or [])},
        "waiver": waiver,
        "unmatched": [],
    }
