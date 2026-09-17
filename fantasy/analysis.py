"""Turns a normalised league (from sleeper.py or espn.py) into roster alerts,
add/drop suggestions and a watch list.

How pickups are scored: for every free agent, try swapping them onto your roster
(dropping each bench player in turn) and re-pick your best projected lineup for
each of the next few weeks. A move is suggested when it adds real projected
points over the short window without costing points over the longer one, so a
hot waiver add never pushes out a player who's about to come back from a bye or
injury."""

import math

SCORE_KEY = {"ppr": "pts_ppr", "half_ppr": "pts_half_ppr", "std": "pts_std"}
FIXED_SLOTS = ["QB", "RB", "WR", "TE", "K", "DEF"]
# Most restrictive first, so filling slots greedily still gives the best lineup.
FLEX_SLOTS = [
    ("REC_FLEX", {"WR", "TE"}),
    ("WRRB_FLEX", {"RB", "WR"}),
    ("FLEX", {"RB", "WR", "TE"}),
    ("SUPER_FLEX", {"QB", "RB", "WR", "TE"}),
]
SLOT_LABELS = {"REC_FLEX": "W/T", "WRRB_FLEX": "W/R", "SUPER_FLEX": "SF", "DEF": "DEF"}
OUT = {"Out", "IR", "PUP", "Sus", "NA", "DNR", "COV"}
LAST_FANTASY_WEEK = 17

MIN_GAIN = 4.5      # projected points over the short window before a move is worth it
STAR_PPG = 14.0     # never suggest dropping someone projected for this much per game
MAX_MOVES = 5
MAX_WATCH = 8
MAX_PER_POSITION = 2


class SeasonData:
    """League-independent data shared by every league."""

    def __init__(self, players, projections, stats, byes, adds, drops, week, usage=None):
        self.players = players
        self.projections = projections  # {week: {pid: {pts_ppr, ..., opp}}}
        self.stats = stats              # {week: {pid: {pts_ppr, off_snp, ...}}}
        self.byes = byes
        self.adds = adds
        self.drops = drops
        self.usage = usage or {}   # nflverse: {pid: {week: {target_share, carries, ...}}}
        self.week = week
        self.short_weeks = [w for w in range(week, week + 3) if w <= LAST_FANTASY_WEEK]
        self.long_weeks = [w for w in range(week, week + 8) if w <= LAST_FANTASY_WEEK]
        self.depth = self._depth_charts()
        self._ppg = {}

    def player(self, pid):
        return self.players.get(pid) or {}

    def pos(self, pid):
        return self.player(pid).get("position")

    def name(self, pid):
        p = self.player(pid)
        if p.get("position") == "DEF":
            return "%s D/ST" % p.get("last_name", pid)
        return p.get("full_name") or ("%s %s" % (p.get("first_name", ""), p.get("last_name", ""))).strip() or pid

    def points(self, pid, week, scoring):
        entry = self.projections.get(week, {}).get(pid)
        if not entry:
            return 0.0
        if week == self.week and self.player(pid).get("injury_status") in OUT:
            return 0.0
        return entry.get(SCORE_KEY[scoring], 0.0)

    def ros_ppg(self, pid, scoring, healthy_only=False):
        """Average projection over the long window, skipping the bye week.
        healthy_only also skips weeks the player is projected to miss."""
        key = (pid, scoring, healthy_only)
        if key not in self._ppg:
            bye = self.byes.get(self.player(pid).get("team"))
            pts = [self.points(pid, w, scoring) for w in self.long_weeks if w != bye]
            if healthy_only:
                pts = [p for p in pts if p > 0]
            self._ppg[key] = sum(pts) / len(pts) if pts else 0.0
        return self._ppg[key]

    def recent(self, pid, scoring):
        """Week by week: Sleeper's points and snaps, nflverse's usage shares."""
        out = []
        deep = self.usage.get(pid, {})
        for w in sorted(set(self.stats) | set(deep)):
            s = self.stats.get(w, {}).get(pid)
            d = deep.get(w)
            if not s and not d:
                continue
            s = s or {}
            d = d or {}
            snap = s["off_snp"] / s["tm_off_snp"] if s.get("tm_off_snp") and s.get("off_snp") else None
            out.append({
                "w": w,
                "pts": round(s.get(SCORE_KEY[scoring], 0.0), 1),
                "snap": round(snap, 2) if snap is not None else None,
                "touches": int(s.get("rec_tgt", 0) + s.get("rush_att", 0)),
                "targets": int(d["targets"]) if d.get("targets") is not None else None,
                "carries": int(d["carries"]) if d.get("carries") is not None else None,
                "tgt_share": round(d["target_share"], 3) if d.get("target_share") is not None else None,
                "carry_share": round(d["carry_share"], 3) if d.get("carry_share") is not None else None,
                "ay_share": round(d["air_yards_share"], 3) if d.get("air_yards_share") is not None else None,
            })
        return out

    def played(self, pid, scoring):
        """Only the weeks the player was actually on the field."""
        return [r for r in self.recent(pid, scoring)
                if (r["snap"] or 0) > 0 or r["targets"] or r["carries"]]

    def usage_summary(self, pid, scoring):
        """Per-game averages over the weeks played, for sorting and filtering."""
        weeks = self.played(pid, scoring)
        if not weeks:
            return {"g": 0}

        def avg(key):
            vals = [w[key] for w in weeks if w.get(key) is not None]
            return round(sum(vals) / len(vals), 3) if vals else None

        return {
            "g": len(weeks),
            "snap": avg("snap"),
            "tgt_share": avg("tgt_share"),
            "carry_share": avg("carry_share"),
            "ay_share": avg("ay_share"),
            "targets": avg("targets"),
            "carries": avg("carries"),
            "pts": avg("pts"),
        }

    def _depth_charts(self):
        charts = {}
        for pid, p in self.players.items():
            if p.get("team") and p.get("depth_chart_order") and p.get("position") in ("QB", "RB", "WR", "TE"):
                key = (p["team"], p.get("depth_chart_position") or p["position"])
                charts.setdefault(key, []).append((p["depth_chart_order"], pid))
        return {k: [pid for _, pid in sorted(v)] for k, v in charts.items()}

    def injured_ahead(self, pid):
        """The injured starter this player would replace, if any."""
        p = self.player(pid)
        chart = self.depth.get((p.get("team"), p.get("depth_chart_position") or p.get("position"))) or []
        if pid not in chart:
            return None
        for ahead in chart[: chart.index(pid)]:
            if self.player(ahead).get("injury_status") in OUT | {"Doubtful"}:
                return ahead
        return None


def best_lineup(pids, slots, week_points, pos_of):
    """Greedy optimal lineup. Returns (total points, [(slot, pid or None)])."""
    ranked = sorted(pids, key=lambda p: -week_points.get(p, 0.0))
    used, lineup, total = set(), [], 0.0
    for slot, eligible in [(s, {s}) for s in FIXED_SLOTS] + FLEX_SLOTS:
        for _ in range(slots.get(slot, 0)):
            pick = next((p for p in ranked if p not in used and pos_of(p) in eligible), None)
            lineup.append((slot, pick))
            if pick:
                used.add(pick)
                total += week_points.get(pick, 0.0)
    return total, lineup


def _compact(n):
    if n >= 1000000:
        return "%.1fM" % (n / 1000000.0)
    return "%dK" % round(n / 1000.0) if n >= 1000 else str(n)


# Rough share of each flex slot that goes to each position across a league.
FLEX_SHARE = {
    "FLEX": {"RB": 0.45, "WR": 0.45, "TE": 0.1},
    "WRRB_FLEX": {"RB": 0.5, "WR": 0.5},
    "REC_FLEX": {"WR": 0.8, "TE": 0.2},
    "SUPER_FLEX": {"QB": 0.8, "RB": 0.1, "WR": 0.1},
}


def replacement_levels(league, data, pool):
    """Projected PPG of the best player who wouldn't start on any team in this league.
    Comparing against it lets QBs, RBs, WRs and TEs share one ranking."""
    teams, slots, scoring = league.get("teams") or 12, league["slots"], league["scoring"]
    starters = {pos: teams * slots.get(pos, 0) for pos in ("QB", "RB", "WR", "TE")}
    for slot, shares in FLEX_SHARE.items():
        for pos, share in shares.items():
            starters[pos] += teams * slots.get(slot, 0) * share
    levels = {}
    for pos, count in starters.items():
        ppgs = sorted((data.ros_ppg(p, scoring) for p in pool if data.pos(p) == pos), reverse=True)
        levels[pos] = ppgs[int(count)] if len(ppgs) > count else 0.0
    return levels


def _plural(n, word, plural=None):
    return "%d %s" % (n, word if n == 1 else (plural or word + "s"))


def _names(data, pids):
    names = [data.name(p) for p in pids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def eligible_positions(slots):
    """Positions that can fill a starting spot in this league."""
    positions = set(s for s in FIXED_SLOTS if slots.get(s))
    for slot, allowed in FLEX_SLOTS:
        if slots.get(slot):
            positions |= allowed
    return positions


def analyze(league, data):
    scoring = league["scoring"]
    slots = league["slots"]
    roster = league["roster"]
    week = data.week
    active = [pid for pid, slot in roster.items() if slot not in ("IR", "TAXI")]
    startable = eligible_positions(slots)

    week_points = {}

    def pts_table(w):
        if w not in week_points:
            week_points[w] = {}
        return week_points[w]

    def value(pids, weeks):
        total = 0.0
        for w in weeks:
            table = pts_table(w)
            for p in pids:
                if p not in table:
                    table[p] = data.points(p, w, scoring)
            total += best_lineup(pids, slots, table, data.pos)[0]
        return total

    def lineup(pids, w):
        value(pids, [w])  # warm the points cache
        return best_lineup(pids, slots, pts_table(w), data.pos)[1]

    def signals(pid):
        tags = []
        p = data.player(pid)
        pos = p.get("position")
        if data.adds.get(pid):
            tags.append({"kind": "trend", "text": "%s adds in 48h" % _compact(data.adds[pid])})
        ahead = data.injured_ahead(pid)
        if ahead:
            tags.append({"kind": "opportunity",
                         "text": "Next up: %s is %s" % (data.name(ahead), data.player(ahead).get("injury_status"))})

        weeks = data.played(pid, scoring)
        if pos in ("RB", "WR", "TE") and weeks:
            first, last = weeks[0], weeks[-1]
            key, label = ("carry_share", "of team carries") if pos == "RB" else ("tgt_share", "target share")
            if last.get(key) is not None:
                if len(weeks) > 1 and first.get(key) is not None and last[key] - first[key] >= 0.08:
                    tags.append({"kind": "usage", "text": "%s %d%% → %d%% (Wk %d–%d)" % (
                        label.capitalize() if key == "tgt_share" else "Carries",
                        first[key] * 100, last[key] * 100, first["w"], last["w"])})
                else:
                    tags.append({"kind": "usage", "text": "%d%% %s in Wk %d" % (last[key] * 100, label, last["w"])})
            elif last.get("snap") is not None:
                tags.append({"kind": "usage", "text": "%d%% of snaps in Wk %d" % (last["snap"] * 100, last["w"])})

            # Plenty of managers adding him, not much actually happening on the field.
            if data.adds.get(pid, 0) >= 50000 and (last.get("tgt_share") or 0) < 0.1 \
                    and (last.get("carry_share") or 0) < 0.15 and last.get("targets") is not None:
                tags.append({"kind": "caution", "text": "Hype ahead of usage: %s, %s in Wk %d" % (
                    _plural(last["targets"] or 0, "target"), _plural(last["carries"] or 0, "carry", "carries"),
                    last["w"])})

        if p.get("injury_status") in OUT:
            back = next((w for w in data.long_weeks if data.points(pid, w, scoring) > 0), None)
            if back:
                tags.append({"kind": "injury", "text": "%s now, projected back Wk %d" % (p["injury_status"], back)})
        bye = data.byes.get(p.get("team"))
        if bye in data.short_weeks:
            tags.append({"kind": "bye", "text": "Bye Wk %d" % bye})
        return tags

    def card(pid):
        p = data.player(pid)
        recent = data.recent(pid, scoring)
        return {
            "id": pid,
            "name": data.name(pid),
            "pos": p.get("position"),
            "team": p.get("team"),
            "injury": p.get("injury_status"),
            "bye": data.byes.get(p.get("team")),
            "opp": (data.projections.get(week, {}).get(pid) or {}).get("opp"),
            "proj": round(data.points(pid, week, scoring), 1),
            "next": [round(data.points(pid, w, scoring), 1) for w in data.short_weeks],
            "ros": round(data.ros_ppg(pid, scoring), 1),
            "last": recent[-1] if recent else None,
            "usage": data.usage_summary(pid, scoring),
            "adds": data.adds.get(pid),
            "drops": data.drops.get(pid),
        }

    # ---- Free agents worth evaluating ------------------------------------------
    pool = set()
    for w in data.long_weeks:
        pool.update(data.projections.get(w, {}).keys())
    free_agents = [
        pid for pid in pool
        if pid not in league["rostered"] and data.pos(pid) in startable and data.player(pid).get("team")
    ]
    by_pos = {}
    for pid in free_agents:
        by_pos.setdefault(data.pos(pid), []).append(pid)
    shortlist = set(pid for pid in free_agents if data.adds.get(pid))
    for pos, pids in by_pos.items():
        shortlist.update(sorted(pids, key=lambda p: -sum(data.points(p, w, scoring) for w in data.short_weeks))[:12])
        shortlist.update(sorted(pids, key=lambda p: -data.ros_ppg(p, scoring))[:12])

    # ---- Add / drop suggestions -------------------------------------------------
    roster_limit = sum(slots.values()) + league["bench"]
    open_spots = roster_limit - len(active)
    base_short = value(active, data.short_weeks)
    base_long = value(active, data.long_weeks)
    droppable = [p for p in active if data.ros_ppg(p, scoring, healthy_only=True) < STAR_PPG]

    moves = []
    for add in shortlist if data.short_weeks else []:
        streamer = data.pos(add) in ("K", "DEF")
        if open_spots > 0:
            options = [None]
        elif streamer:
            options = [p for p in droppable if data.pos(p) == data.pos(add)]
        else:
            options = droppable
        best = None
        for drop in options:
            new = [p for p in active if p != drop] + [add]
            gain = value(new, data.short_weeks) - base_short
            if gain < MIN_GAIN:
                continue
            gain_long = 0.0 if streamer else value(new, data.long_weeks) - base_long
            if gain_long < 0:
                continue
            drop_value = data.ros_ppg(drop, scoring, healthy_only=True) if drop else 0
            key = (round(gain, 1), round(gain_long, 1), -drop_value)
            if best is None or key > best[0]:
                best = (key, drop, gain, gain_long, new)
        if best:
            _, drop, gain, gain_long, new = best
            starts, displaced = [], []
            for w in data.short_weeks:
                old_ids = {p for _, p in lineup(active, w) if p}
                new_ids = {p for _, p in lineup(new, w) if p}
                if add in new_ids:
                    starts.append(w)
                    displaced += [p for p in old_ids - new_ids if p not in displaced]
            reasons = []
            if starts:
                reasons.append("Would start for you in Wk " + ", ".join(str(w) for w in starts))
            if displaced:
                reasons.append("Replaces %s in your lineup" % _names(data, displaced))
            if streamer:
                reasons.append("Streaming pick: short-term only")
            moves.append({
                "add": card(add),
                "drop": card(drop) if drop else None,
                "gain": round(gain, 1),
                "gain_long": round(gain_long, 1),
                "reasons": reasons,
                "tags": signals(add),
            })
    moves.sort(key=lambda m: (-m["gain"], -m["gain_long"]))
    moves = moves[:MAX_MOVES]

    # ---- Watch list: stashes and risers that aren't instant upgrades -------------
    levels = replacement_levels(league, data, pool)
    superflex = bool(slots.get("SUPER_FLEX"))

    # How many startable players you already have at each position, versus how many
    # you can start. Someone with three good tight ends doesn't need a fourth.
    need = {pos: slots.get(pos, 0) for pos in ("QB", "RB", "WR", "TE")}
    for slot, shares in FLEX_SHARE.items():
        for pos, share in shares.items():
            need[pos] += slots.get(slot, 0) * share

    def over_replacement(pid):
        pos = data.pos(pid)
        if pos not in levels:
            return 0.0
        ppg = data.ros_ppg(pid, scoring)
        if data.player(pid).get("injury_status") in OUT:  # value a stash on what they'll do once back
            ppg = max(ppg, 0.8 * data.ros_ppg(pid, scoring, healthy_only=True))
        # A backup QB is nearly worthless in a one-quarterback league.
        return ppg - levels[pos] - (4.0 if pos == "QB" and not superflex else 0)

    surplus = {}
    for pos in need:
        have = sum(1 for p in active if data.pos(p) == pos and over_replacement(p) > 0)
        surplus[pos] = min(4.0, 2.0 * max(0.0, have - need[pos]))

    # Your least valuable bench players: who a stash would replace.
    starting_soon = set()
    for w in data.long_weeks:
        starting_soon.update(p for _, p in lineup(active, w) if p)
    bench = sorted(
        (p for p in active if p not in starting_soon and data.pos(p) in levels),
        key=over_replacement,
    )

    suggested = {m["add"]["id"] for m in moves}
    watch = []
    for pid in free_agents:
        if pid in suggested or data.pos(pid) not in levels:
            continue
        tags = signals(pid)
        kinds = {t["kind"] for t in tags}
        score = over_replacement(pid) - surplus.get(data.pos(pid), 0)
        if data.adds.get(pid, 0) > 1000:
            score += min(3.0, math.log10(data.adds[pid]) - 3)
        if "opportunity" in kinds:
            score += 3
        if any(t["text"].startswith("Snaps up") for t in tags):
            score += 2
        watch.append((score, pid, tags))
    watch.sort(key=lambda x: -x[0])

    drop_candidates = [card(p) for p in bench[:3]]
    watch_cards = []
    per_position = {}
    for score, pid, tags in watch:
        pos = data.pos(pid)
        if per_position.get(pos, 0) >= MAX_PER_POSITION or len(watch_cards) >= MAX_WATCH:
            continue
        per_position[pos] = per_position.get(pos, 0) + 1
        watch_cards.append(dict(card(pid), tags=tags))
    watch = watch_cards

    # ---- Roster + alerts ----------------------------------------------------------
    alerts = []
    starters = {pid: slot for pid, slot in roster.items() if slot not in ("BN", "IR", "TAXI")}
    for pid in starters:
        status = data.player(pid).get("injury_status")
        if data.byes.get(data.player(pid).get("team")) == week:
            alerts.append(("high", "%s is on bye this week but in your lineup" % data.name(pid)))
        elif status in OUT or status == "Doubtful":
            alerts.append(("high", "%s is %s but in your lineup" % (data.name(pid), status)))
        elif status == "Questionable":
            alerts.append(("medium", "%s is Questionable. Check before kickoff" % data.name(pid)))
    empty = sum(slots.values()) - len(starters)
    if empty > 0:
        alerts.append(("high", "%d empty starting spot%s" % (empty, "s" if empty > 1 else "")))

    optimal = {p for _, p in lineup(active, week) if p}
    table = pts_table(week)
    better = sorted(optimal - set(starters), key=lambda p: -table.get(p, 0))
    worse = sorted(set(starters) - optimal, key=lambda p: table.get(p, 0))
    for in_p, out_p in zip(better, worse):
        if table.get(in_p, 0) - table.get(out_p, 0) >= 1.5:
            alerts.append(("medium", "Start %s (%.1f proj) over %s (%.1f)" % (
                data.name(in_p), table[in_p], data.name(out_p), table.get(out_p, 0))))

    ir_used = sum(1 for s in roster.values() if s == "IR")
    if league.get("ir_slots", 0) > ir_used:
        for pid in active:
            if roster[pid] == "BN" and data.player(pid).get("injury_status") in ("IR", "PUP"):
                alerts.append(("info", "Move %s to IR to open a roster spot" % data.name(pid)))

    for w in range(week + 1, min(week + 5, LAST_FANTASY_WEEK + 1)):
        holes = [SLOT_LABELS.get(s, s) for s, p in lineup(active, w) if p is None]
        on_bye = [p for p in optimal if data.byes.get(data.player(p).get("team")) == w]
        if holes:
            alerts.append(("medium", "Wk %d: nobody to start at %s (byes or injuries)" % (w, ", ".join(holes))))
        elif len(on_bye) >= 2:
            alerts.append(("info", "Wk %d: %d starters on bye (%s)" % (w, len(on_bye), _names(data, on_bye))))

    most_dropped = set(sorted(data.drops, key=lambda p: -data.drops[p])[:25])
    for pid in roster:
        if pid in most_dropped:
            alerts.append(("info", "%s is one of the most-dropped players (%s drops in 48h)" % (
                data.name(pid), _compact(data.drops[pid]))))
    if league.get("unmatched"):
        alerts.append(("info", "Couldn't match %d ESPN player(s): %s" % (
            len(league["unmatched"]), ", ".join(league["unmatched"]))))

    level_rank = {"high": 0, "medium": 1, "info": 2}
    alerts.sort(key=lambda a: level_rank[a[0]])

    slot_order = {s: i for i, s in enumerate(FIXED_SLOTS[:4] + [s for s, _ in FLEX_SLOTS] + FIXED_SLOTS[4:] + ["BN", "TAXI", "IR"])}
    rows = []
    for pid, slot in sorted(roster.items(), key=lambda kv: (slot_order.get(kv[1], 99), -table.get(kv[0], data.points(kv[0], week, scoring)))):
        rows.append(dict(card(pid), slot=SLOT_LABELS.get(slot, slot), optimal=pid in optimal))

    out = {k: v for k, v in league.items() if k not in ("roster", "rostered", "unmatched")}
    out.update({
        "alerts": [{"level": lvl, "text": text} for lvl, text in alerts],
        "moves": moves,
        "watch": watch,
        "drop_candidates": drop_candidates,
        "roster": rows,
        "projected": round(best_lineup(active, slots, table, data.pos)[0], 1),
    })
    return out
