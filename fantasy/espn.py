"""ESPN leagues via ESPN's (unofficial) fantasy API.

Private leagues need two cookies from a logged-in browser session: espn_s2 and
SWID. They are read from the ESPN_S2 / ESPN_SWID environment variables, which
the GitHub Action fills from repository secrets.

Every ESPN player is translated to a Sleeper player id so all four leagues share
the same projections and stats."""

import re
from collections import Counter

from .net import AuthError, get_json

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/%s"

LINEUP_SLOTS = {
    0: "QB", 2: "RB", 3: "WRRB_FLEX", 4: "WR", 5: "REC_FLEX", 6: "TE",
    7: "SUPER_FLEX", 16: "DEF", 17: "K", 20: "BN", 21: "IR", 23: "FLEX",
}
POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}
TEAM_FIXES = {"WSH": "WAS"}  # ESPN abbreviation -> Sleeper abbreviation
RECEPTION_STAT_ID = 53

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def normalize_name(name):
    name = re.sub(r"[^a-z ]", "", (name or "").lower().replace("-", " "))
    return " ".join(_SUFFIX.sub("", name).split())


class PlayerMatcher:
    """Maps ESPN players to Sleeper ids: ESPN id first, then name + position."""

    def __init__(self, players, pro_teams):
        self.players = players
        self.pro_teams = pro_teams
        self.by_espn_id = {str(p["espn_id"]): pid for pid, p in players.items() if p.get("espn_id")}
        self.by_name = {}
        for pid, p in players.items():
            if p.get("position") in POSITIONS.values() and p.get("position") != "DEF":
                name = p.get("full_name") or "%s %s" % (p.get("first_name", ""), p.get("last_name", ""))
                self.by_name.setdefault((normalize_name(name), p["position"]), []).append(pid)

    def match(self, espn_player):
        pos = POSITIONS.get(espn_player.get("defaultPositionId"))
        team = self.pro_teams.get(espn_player.get("proTeamId"))
        if pos == "DEF":
            return team if team in self.players else None
        pid = self.by_espn_id.get(str(espn_player.get("id")))
        if pid:
            return pid
        candidates = self.by_name.get((normalize_name(espn_player.get("fullName")), pos), [])
        if len(candidates) > 1:
            candidates = sorted(
                candidates,
                key=lambda c: (self.players[c].get("team") != team, not self.players[c].get("active")),
            )
        return candidates[0] if candidates else None


def pro_teams(season):
    """{ESPN pro team id: Sleeper team abbreviation}. Public endpoint, no login."""
    data = get_json((BASE % season) + "?view=proTeamSchedules_wl")
    return {
        t["id"]: TEAM_FIXES.get(t["abbrev"], t["abbrev"])
        for t in data["settings"]["proTeams"]
        if t["id"]
    }


def load_league(league_id, season, espn_s2, swid, matcher):
    if not (espn_s2 and swid):
        raise AuthError("This ESPN league is private. Add the ESPN_S2 and ESPN_SWID secrets to load it.")
    url = (BASE % season) + "/segments/0/leagues/%s?view=mTeam&view=mRoster&view=mSettings" % league_id
    try:
        data = get_json(url, cookies={"espn_s2": espn_s2, "SWID": swid})
    except AuthError:
        raise AuthError("ESPN rejected the login cookies. They may have expired; refresh ESPN_S2 and ESPN_SWID.")

    swid_norm = swid.strip().upper()
    teams = data.get("teams") or []
    mine = next(
        (t for t in teams if swid_norm in [o.upper() for o in (t.get("owners") or [])]),
        None,
    )
    if mine is None:
        raise ValueError("Couldn't find your team. Is ESPN_SWID from the account that owns it?")

    settings = data.get("settings") or {}
    slot_counts = Counter()
    for slot_id, count in ((settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}).items():
        name = LINEUP_SLOTS.get(int(slot_id))
        if name and count:
            slot_counts[name] += count

    rec_points = 0
    for item in (settings.get("scoringSettings") or {}).get("scoringItems") or []:
        if item.get("statId") == RECEPTION_STAT_ID:
            rec_points = item.get("points", 0)

    def entries(team):
        return ((team.get("roster") or {}).get("entries")) or []

    unmatched = []
    current = {}
    for entry in entries(mine):
        player = (entry.get("playerPoolEntry") or {}).get("player") or {}
        pid = matcher.match(player)
        if pid is None:
            unmatched.append(player.get("fullName") or str(entry.get("playerId")))
            continue
        current[pid] = LINEUP_SLOTS.get(entry.get("lineupSlotId"), "BN")

    rostered = set()
    for team in teams:
        for entry in entries(team):
            pid = matcher.match((entry.get("playerPoolEntry") or {}).get("player") or {})
            if pid:
                rostered.add(pid)

    def standing(team):
        overall = (team.get("record") or {}).get("overall") or {}
        return (overall.get("wins", 0), overall.get("pointsFor", 0))

    overall = (mine.get("record") or {}).get("overall") or {}
    ordered = sorted(teams, key=standing, reverse=True)
    acquisitions = settings.get("acquisitionSettings") or {}
    waiver = {"priority": mine.get("waiverRank")}
    if acquisitions.get("isUsingAcquisitionBudget"):
        spent = (mine.get("transactionCounter") or {}).get("acquisitionBudgetSpent", 0)
        waiver["faab_left"] = acquisitions.get("acquisitionBudget", 100) - spent

    team_name = mine.get("name") or ("%s %s" % (mine.get("location", ""), mine.get("nickname", ""))).strip()
    return {
        "platform": "espn",
        "id": str(league_id),
        "name": settings.get("name") or "ESPN league %s" % league_id,
        "team_name": team_name or "My team",
        "url": "https://fantasy.espn.com/football/team?leagueId=%s&teamId=%s&seasonId=%s"
        % (league_id, mine.get("id"), season),
        "record": "%d-%d%s" % (
            overall.get("wins", 0), overall.get("losses", 0),
            "-%d" % overall["ties"] if overall.get("ties") else ""),
        "points_for": round(overall.get("pointsFor", 0), 1),
        "rank": ordered.index(mine) + 1,
        "teams": len(teams),
        "scoring": "ppr" if rec_points >= 1 else "half_ppr" if rec_points >= 0.5 else "std",
        "slots": {k: v for k, v in slot_counts.items() if k not in ("BN", "IR")},
        "bench": slot_counts.get("BN", 0),
        "ir_slots": slot_counts.get("IR", 0),
        "roster": current,
        "rostered": rostered,
        "waiver": waiver,
        "unmatched": unmatched,
    }
