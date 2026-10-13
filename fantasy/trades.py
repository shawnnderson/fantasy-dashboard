"""Finds trades worth proposing, in every league at once.

The test is the same one the pickup engine uses, applied to both sides: re-pick
each roster's best lineup week by week, with and without the swap. A trade is
only suggested when it gains you points *and* gains the other manager points by
his own roster's math — an offer that only helps you is just an email nobody
answers.

Positions listed in `protected` (running backs, by default) are never offered.
"""

from . import analysis

MAX_PER_LEAGUE = 6
MIN_GAIN = 1.0          # points per week for you, over the next three weeks
MIN_LONG_GAIN = 0.5     # and still ahead across the longer horizon
MIN_THEIR_GAIN = 0.5    # their reason to say yes
FAIR_RATIO = 0.75       # what you send must be worth roughly what you ask for
GIVE_POOL = 7           # cheapest tradeable players considered for two-player packages
GET_POOL = 8            # best players on each rival roster worth asking about


def _fit(ids, limit, data, scoring, keep=()):
    """Trim a roster back to the limit by cutting its least valuable players."""
    if len(ids) <= limit:
        return ids
    droppable = sorted((p for p in ids if p not in keep), key=lambda p: data.ros_ppg(p, scoring))
    cut = set(droppable[:len(ids) - limit])
    return [p for p in ids if p not in cut]


def _cards(data, pids, scoring):
    out = []
    for pid in pids:
        p = data.player(pid)
        out.append({
            "id": pid,
            "name": data.name(pid),
            "pos": p.get("position"),
            "team": p.get("team"),
            "injury": p.get("injury_status"),
            "ros": round(data.ros_ppg(pid, scoring), 1),
        })
    return out


def find_trades(league, data, protected=()):
    scoring, slots = league["scoring"], league["slots"]
    mine = [pid for pid, slot in league["roster"].items() if slot not in ("IR", "TAXI")]
    limit = sum(slots.values()) + league.get("bench", 0)

    rivals = {}
    for pid, team in (league.get("owners") or {}).items():
        if pid not in league["roster"]:
            rivals.setdefault(team, []).append(pid)
    if not rivals or not mine:
        return []

    short, long_weeks = data.short_weeks, data.long_weeks
    if not short:
        return []

    cache = {}

    def value(ids, weeks):
        key = (frozenset(ids), weeks[0], len(weeks))
        if key not in cache:
            total = 0.0
            for w in weeks:
                table = {p: data.points(p, w, scoring) for p in ids}
                total += analysis.best_lineup(ids, slots, table, data.pos)[0]
            cache[key] = total / len(weeks)
        return cache[key]

    my_short, my_long = value(mine, short), value(mine, long_weeks)

    # What each of your players is actually worth to your lineup, so packages are
    # built out of genuine surplus rather than whoever scores least.
    tradeable = [p for p in mine if data.pos(p) not in protected]
    cost = {p: my_long - value([x for x in mine if x != p], long_weeks) for p in tradeable}
    cheapest = sorted(tradeable, key=lambda p: cost[p])[:GIVE_POOL]
    packages = [(p,) for p in tradeable]
    packages += [(a, b) for i, a in enumerate(cheapest) for b in cheapest[i + 1:]]

    found = []
    for team, their_ids in rivals.items():
        their_short = value(their_ids, short)
        their_long = value(their_ids, long_weeks)
        wanted = sorted(their_ids, key=lambda p: -data.ros_ppg(p, scoring))[:GET_POOL]

        for get in wanted:
            best = None
            for give in packages:
                if get in give:
                    continue
                my_new = [p for p in mine if p not in give] + [get]
                # Taking two for one leaves them over the roster limit, so they
                # drop their worst player — count that against their side.
                their_new = _fit([p for p in their_ids if p != get] + list(give),
                                 limit, data, scoring, keep=give)

                # Lineup maths alone will happily ask for a star in exchange for a
                # backup who happens to plug a hole. Nobody accepts that, so the
                # package has to be worth roughly what it asks for.
                if sum(data.ros_ppg(p, scoring) for p in give) < FAIR_RATIO * data.ros_ppg(get, scoring):
                    continue

                gain = value(my_new, short) - my_short
                if gain < MIN_GAIN:
                    continue
                gain_long = value(my_new, long_weeks) - my_long
                if gain_long < MIN_LONG_GAIN:
                    continue
                theirs = value(their_new, long_weeks) - their_long
                theirs_now = value(their_new, short) - their_short
                if theirs < MIN_THEIR_GAIN or theirs_now < 0:
                    continue
                # Prefer the package that costs you least for the same return.
                key = (round(gain, 1), round(theirs, 1))
                if best is None or key > best[0]:
                    best = (key, give, gain, gain_long, theirs, my_new)
            if best:
                _, give, gain, gain_long, theirs, my_new = best
                found.append({
                    "team": team,
                    "give": _cards(data, give, scoring),
                    "get": _cards(data, [get], scoring),
                    "gain": round(gain, 1),
                    "gain_long": round(gain_long, 1),
                    "their_gain": round(theirs, 1),
                    "give_value": round(sum(data.ros_ppg(p, scoring) for p in give), 1),
                    "get_value": round(data.ros_ppg(get, scoring), 1),
                    "reasons": _explain(data, league, mine, my_new, get, give, scoring),
                })

    found.sort(key=lambda t: -t["gain"])
    seen, trades = set(), []
    for t in found:
        key = t["get"][0]["id"]
        if key in seen:        # one idea per player you'd be getting
            continue
        seen.add(key)
        trades.append(t)
        if len(trades) == MAX_PER_LEAGUE:
            break
    return trades


def _explain(data, league, mine, my_new, get, give, scoring):
    """Where the gain comes from, in lineup terms."""
    slots, week = league["slots"], data.week
    reasons = []

    def lineup(ids):
        table = {p: data.points(p, w, scoring) for w in [week] for p in ids}
        return analysis.best_lineup(ids, slots, table, data.pos)[1]

    before = {p: slot for slot, p in lineup(mine) if p}
    after = {p: slot for slot, p in lineup(my_new) if p}
    if get in after:
        displaced = [p for p in before if p not in after and p not in give]
        slot = analysis.SLOT_LABELS.get(after[get], after[get])
        if displaced:
            worst = min(displaced, key=lambda p: data.ros_ppg(p, scoring))
            reasons.append("Starts at %s over %s (%.1f/wk)" % (
                slot, data.name(worst), data.ros_ppg(worst, scoring)))
        else:
            reasons.append("Steps straight into your %s spot" % slot)
    else:
        reasons.append("Depth and bye cover rather than an instant starter")

    kept = [p for p in give if p in before]
    if kept:
        reasons.append("Costs you %s out of your lineup" % analysis._names(data, kept))
    return reasons
