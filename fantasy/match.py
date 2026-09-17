"""Matching outside player names to Sleeper player ids.

Sleeper carries an ESPN id for only about a quarter of active players and a
gsis id (used by nflverse) for fewer still, so name plus position does most of
the work. Among active players at fantasy positions there are currently no
duplicate name/position pairs, and team breaks any tie that does appear."""

import re

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


def normalize_name(name):
    name = re.sub(r"[^a-z ]", "", (name or "").lower().replace("-", " "))
    return " ".join(_SUFFIX.sub("", name).split())


class NameIndex:
    def __init__(self, players):
        self.players = players
        self.index = {}
        for pid, p in players.items():
            pos = p.get("position")
            if pos in FANTASY_POSITIONS and pos != "DEF":
                name = p.get("full_name") or "%s %s" % (p.get("first_name", ""), p.get("last_name", ""))
                self.index.setdefault((normalize_name(name), pos), []).append(pid)

    def find(self, name, pos, team=None):
        candidates = self.index.get((normalize_name(name), pos), [])
        if len(candidates) > 1:
            candidates = sorted(
                candidates,
                key=lambda c: (self.players[c].get("team") != team, not self.players[c].get("active")),
            )
        return candidates[0] if candidates else None
