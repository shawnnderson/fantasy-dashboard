# Fantasy Dashboard

One page for all four teams: what needs attention this week, which free agents are
worth adding, and who to watch over the next few weeks.

A GitHub Action refreshes the data on a schedule and publishes the page to GitHub
Pages. No server, no cost, no installs.

```
GitHub Actions (daily + Tuesday night + Sunday morning)
   │
   ├── Sleeper API ────┐   leagues, rosters, projections, trends
   ├── ESPN API ───────┼──► build.py ──► docs/data.json ──► GitHub Pages
   └── nflverse ───────┘   target share, carries, air yards
```

## Setup

1. **Push this folder to a GitHub repository.** GitHub Pages needs a public repo on
   a free account. Nothing secret lives in the code, but see the warning below.

2. **Turn on Pages**: repository *Settings → Pages → Build and deployment → Source:
   **GitHub Actions***.

3. **Add your ESPN login cookies** so the three private ESPN leagues can load:

   - Sign in at [fantasy.espn.com](https://fantasy.espn.com) in your browser.
   - Open developer tools (`⌥⌘I` on a Mac), then **Application → Cookies →
     `https://fantasy.espn.com`**.
   - Copy the values of `espn_s2` (long) and `SWID` (short, keep the curly braces).
   - Add them as repository secrets under *Settings → Secrets and variables →
     Actions → New repository secret*, named `ESPN_S2` and `ESPN_SWID`. Or from a
     terminal:

     ```bash
     gh secret set ESPN_S2
     gh secret set ESPN_SWID
     ```

4. **Run it**: *Actions → Update dashboard → Run workflow*. The site appears at
   `https://<your-username>.github.io/<repo-name>/`.

> [!WARNING]
> Those two cookies are your ESPN login. Keep them in GitHub secrets only, never in
> a file in the repository. They expire every so often; when the ESPN leagues show
> "needs login" on the site, set the secrets again. The published page itself is
> public, so anyone with the link can see your rosters.

## Running it locally

```bash
python3 build.py && python3 -m http.server 8000 --directory docs
```

Then open http://localhost:8000. For the ESPN leagues, prefix the command with
`ESPN_S2='...' ESPN_SWID='{...}'`.

Add or remove leagues in `config.json`. Sleeper leagues are found automatically
from your username; ESPN leagues are listed by the `leagueId=` number in the
league's web address.

## How the suggestions work

Everything is scored off Sleeper's weekly projections, converted to each league's
scoring, so the four leagues stay comparable.

- **Suggested pickups** compare your best possible lineup with and without each
  free agent across the next three weeks, trying every bench player as the drop. A
  move only appears when it adds real points short-term *and* doesn't cost you
  points over the next eight weeks, so a hot waiver add never pushes out someone
  about to return from a bye or injury. Kickers and defenses are treated as
  week-to-week streamers.
- **Watch list** ranks free agents by projected points above replacement level (the
  best player nobody in your league would start), adjusted for what you already
  have at that position, then boosted by usage signals: snap share, targets and
  carries, players in line behind an injured starter, and how many Sleeper managers
  are adding them.
- **Needs attention** covers injured or on-bye starters, empty lineup spots, bench
  players out-projecting your starters, and bye weeks coming up in the next month.
- **Waiver wire** on the overview page is one sortable table across all four
  leagues. Filter by position, by name, or by the league a player is free in, then
  sort by projection, target share, share of team carries, snap share or how many
  managers are adding them. Points columns follow the scoring of whichever league
  you filter to. Usage figures are per-game averages over the weeks a player was
  actually on the field, so a player returning from injury isn't punished for the
  weeks he missed.

Usage data comes from [nflverse](https://github.com/nflverse/nflverse-data), read
straight from its weekly CSV. That avoids installing `nflreadpy`, which needs
Python 3.10+ and polars; if nflverse is ever unreachable the build carries on
without those columns.

Tuning lives at the top of [`fantasy/analysis.py`](fantasy/analysis.py):
`MIN_GAIN` (how big a gain is worth a roster move), `STAR_PPG` (who is never
suggested as a drop), and the size of each list.

## Layout

| Path | What it does |
| --- | --- |
| `build.py` | Pulls everything and writes `docs/data.json` |
| `fantasy/sleeper.py` | Sleeper leagues, plus the projections, stats and trends all leagues use |
| `fantasy/espn.py` | ESPN leagues, translated to Sleeper player ids |
| `fantasy/nflverse.py` | Weekly target share, carries and air yards |
| `fantasy/match.py` | Matching outside player names to Sleeper ids |
| `fantasy/analysis.py` | Lineup maths, pickups, watch list, alerts |
| `docs/index.html` | The page (plain HTML, reads `data.json`) |
| `tests/test_espn.py` | Checks the ESPN parser against a sample payload |

## When something breaks

ESPN's fantasy API is not official and has changed address before. If the ESPN
leagues stop loading while Sleeper keeps working, that's the likely cause: check
the failed job's log under *Actions*. `tests/test_espn.py` exercises the parser
without needing a login:

```bash
python3 tests/test_espn.py
```
