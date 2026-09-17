"""Exercise the ESPN parser against a synthetic payload shaped like ESPN's."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from fantasy import espn, sleeper

# The player database is a 15 MB download, so keep a copy between runs.
CACHE = os.path.join(tempfile.gettempdir(), "sleeper_players.json")
if not os.path.exists(CACHE):
    with open(CACHE, "w") as f:
        json.dump(sleeper.players(), f)
players = json.load(open(CACHE))
PRO_TEAMS = espn.pro_teams("2026")
SWID = "{ABCD1234-0000-1111-2222-EEEEFFFF0001}"

def mk(pid, espn_id, name, pos_id, team_id, slot):
    return {"playerId": pid, "lineupSlotId": slot,
            "playerPoolEntry": {"player": {"id": espn_id, "fullName": name,
                                           "defaultPositionId": pos_id, "proTeamId": team_id}}}

roster_mine = [
    mk(1, int(players["4034"]["espn_id"]), "Christian McCaffrey", 2, 25, 2),
    mk(2, 111111, "Brock Purdy", 1, 25, 0),   # no ESPN id in Sleeper -> name match
    mk(3, 999999, "Ja'Marr Chase Jr.", 3, 4, 4),     # unknown ESPN id -> matched by name
    mk(4, -16027, "Buccaneers D/ST", 16, 27, 16),     # D/ST -> Sleeper team id
    mk(5, 888888, "Totally Fake Person", 3, 4, 20),   # should land in "unmatched"
]
payload = {
  "teams": [
    {"id": 1, "name": "My Squad", "owners": [SWID.lower()], "waiverRank": 3,
     "record": {"overall": {"wins": 1, "losses": 0, "ties": 0, "pointsFor": 101.5}},
     "transactionCounter": {"acquisitionBudgetSpent": 12},
     "roster": {"entries": roster_mine}},
    {"id": 2, "location": "Other", "nickname": "Guy", "owners": ["{SOMEONE-ELSE}"],
     "record": {"overall": {"wins": 1, "losses": 0, "pointsFor": 120.0}},
     "roster": {"entries": [mk(6, 222222, "Kyren Williams", 2, 14, 2)]}},
  ],
  "settings": {"name": "Test League",
    "rosterSettings": {"lineupSlotCounts": {"0":1,"2":2,"4":2,"6":1,"23":1,"16":1,"17":1,"20":6,"21":1}},
    "scoringSettings": {"scoringItems": [{"statId": 53, "points": 0.5}]},
    "acquisitionSettings": {"isUsingAcquisitionBudget": True, "acquisitionBudget": 100}},
}

espn.get_json = lambda url, **kw: payload
matcher = espn.PlayerMatcher(players, PRO_TEAMS)
lg = espn.load_league(4242, "2026", "cookie", SWID, matcher)
for k, v in lg.items():
    print("%-12s %s" % (k, sorted(v) if isinstance(v, set) else v))

assert lg["scoring"] == "half_ppr", lg["scoring"]
assert lg["slots"] == {"QB":1,"RB":2,"WR":2,"TE":1,"FLEX":1,"DEF":1,"K":1}, lg["slots"]
assert lg["bench"] == 6 and lg["ir_slots"] == 1
assert lg["roster"]["4034"] == "RB" and lg["roster"]["8183"] == "QB" and lg["roster"]["TB"] == "DEF"
assert lg["team_name"] == "My Squad" and lg["record"] == "1-0" and lg["rank"] == 2 and lg["teams"] == 2
assert lg["waiver"] == {"priority": 3, "faab_left": 88}, lg["waiver"]
assert lg["unmatched"] == ["Totally Fake Person"], lg["unmatched"]
assert "8150" in lg["rostered"] and len(lg["rostered"]) == 5
chase = [pid for pid, p in players.items() if p.get("full_name") == "Ja'Marr Chase"][0]
assert lg["roster"].get(chase) == "WR", "name matching failed"

# wrong SWID -> clear error, and no cookies -> auth error
for bad, expect in [((None, None), "private"), (("c", "{NOBODY}"), "Couldn't find your team")]:
    try:
        espn.load_league(4242, "2026", bad[0], bad[1], matcher)
        raise SystemExit("expected failure for %s" % (bad,))
    except Exception as e:
        assert expect in str(e), e
        print("error path ok:", e)
print("\nALL ESPN PARSER CHECKS PASSED")
