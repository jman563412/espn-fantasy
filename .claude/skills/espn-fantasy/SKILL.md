---
name: espn-fantasy
description: Use when the user wants help running ESPN fantasy football (NFL) or basketball (NBA) teams — "set my lineup", "who should I start", "any good waiver adds", "should I drop X", "check injuries before kickoff", "set up a guard for Sunday", "draft for me", "run a mock draft", "how's my matchup", "trade ideas". Reads league data through ESPN's private API, proposes or makes roster moves under the user's configured rules, generates lineup-guard scripts, and runs the Playwright draft bot.
---

# ESPN fantasy manager

## Where things are

This skill lives at `.claude/skills/espn-fantasy/` inside the repo, so the repo root is three levels up from this
skill's base directory. The skill may be loaded through a symlink (e.g. `~/.claude/skills/espn-fantasy`), so resolve
it physically: `cd "$(realpath '<this skill's base directory>/../../..')"`, and run every command below from there.

- `config.json`: the user's leagues and rules (from `config.example.json`). Load it at the start of a session:
  `python3 -c "import config, json; print(json.dumps(config.load(), indent=1))"`. Every rule below names its key.
- `docs/espn-api.md`: the reverse-engineered API (hosts, views, filters, the transaction format) for anything the
  scripts don't cover.
- `cookies.json`: the user's live ESPN session. **Never print, log or paste its values** (or `ESPN_S2` / `ESPN_SWID`).
  If API calls return 401, redirect (302) or HTML, the session expired: ask the user to run `node fetch_cookies.js`
  (headed; they log in in the window).

Scripts default to the first configured league of each sport. Pass `--league <name|id>` (and `--team`) for others.

## Reading the league

| need | command |
|---|---|
| NFL free agents / waivers | `python3 fa_search.py --week N [--pos RB] [--name X] [--sort season\|proj\|owned]` |
| NFL matchups and rosters for a week | `python3 fetch_week.py --week N` |
| NBA roster, today's lineup, matchup | `python3 nba.py roster`, `python3 nba.py lineup`, `python3 nba.py matchup` |
| NBA free agents | `python3 nba.py fa [--pos C] [--sort auto\|l15\|season\|proj\|games]` |
| notable drops (incl. trade-forced) | `python3 drop_watch.py [--hours 48]` |

For rest-of-season (ROS) projections and anything else, call the API directly (see `docs/espn-api.md`), using
`kona_player_info` with `filterIds` and the season projection stat split.

## Writes and `rules.confirm_writes`

Writes are lineup changes, adds/drops, waiver claims and trade proposals: the transaction POST in `docs/espn-api.md`,
`nba.py ... --execute`, or `auto_swap.py` without `--dry-run`.

- **`confirm_writes: true` (default):** propose first. List every move: who goes in or out, the slot, the reason, and
  the rule it follows. Wait for the user's explicit OK before writing anything. An OK covers only the moves you showed.
- **`confirm_writes: false`:** make the moves, then report exactly what executed (transaction IDs and status).
- **Always, whatever the setting:** if a write call is interrupted, rejected or errors, it may still have gone
  through. Read `view=mTransactions2` for the team before saying nothing happened or resubmitting. Duplicate claims
  and trades come from skipping this.
- Verify every write afterwards (`mTransactions2`, or re-read the roster). `nba.py` writes were the least exercised:
  dry-run first, then confirm the first real one with `nba.py roster`.

## NFL rules (`rules.nfl`)

- **Close call (`close_call_points`, default 3):** when two start/sit candidates are within this many ESPN projected
  points, start the one with the steadier, established role. Don't flip weekly on small projection gaps. Default to
  the established player unless there's a concrete reason (matchup, injury, role change), and say what it is.
  - With `injury_designation_reopens_close_call` (default on): a practice limitation or injury designation after the
    lineup is set reopens the decision. Re-compare before lock; the early-week ranking isn't final.
- **Adds/drops at `ros_positions_for_add_drop` (default RB, WR, TE):** before recommending any add or drop, compare
  ROS projections for every droppable player on the roster and the top free-agent candidates. Pick the drop by lowest
  ROS value, not just "on bye / small role this week". Show the comparison as one small table *before* the first
  recommendation, not after the user asks.
- **Streaming `streamable_positions` (default QB, K, D/ST):** pick on this week's projection and matchup, using the
  close-call rule for ties.
  - Exception (`keep_streamable_if_ros_beats_fa_per_week`, default 2): don't drop a rostered one whose ROS beats the
    best available free agent's ROS at that position by at least that many points × weeks remaining (≈20 points with
    10 weeks left).
- **Start/sit (`start_sit_uses_ros`, default false):** between players already on the roster, decide on this week's
  projection and matchup plus the close-call rule. Never cite ROS for a start/sit.
- **QB count (`qb_count: "league_majority"`, `max_qbs`, default 2):** carry as many QBs as most of the *other* teams in
  that league carry, never more than `max_qbs`. Apply it the same way in every league and recount at each review.
- **Trades:** 2-for-1 and 1-for-2 are fine in either direction if both sides are equal value. Judge value on ESPN ROS
  rank and projection, not just roster need. Don't pitch trades the user has ruled out.
- **Before a lock:** check injury designations and inactives. For a starter who might sit, offer a lineup guard
  (below) if the user won't be around at lock.

## NBA rules (`rules.nba`)

- **Daily lineups (`daily_lineups_start_everyone_playing`, default on):** NBA lineups lock per player at tip-off and
  scoring periods are days. Each day, start everyone who plays that day. `nba.py lineup` finds the best assignment of
  who plays to eligible slots, skipping locked players; review it, then `--execute` per `confirm_writes`.
- **Draft durability (built into `draft/rank_nba.py` and the bot):** a missed game counts as 0 points in early rounds,
  easing linearly to the streamer view by the last round. A roster holds at most IR slots + 1 injury-prone or
  long-term-injured players. Don't justify a pick with "you can stream his missed games."

## Lineup guards (`guard.py`)

For a lock the user can't be around for: generate a script that checks at set times and swaps an OUT/doubtful starter
for the best healthy, unlocked backup (`auto_swap.py`).
```bash
python3 guard.py make --week 12 --player <starterId> --backups <id> <id> --check 10:45 --check 11:20 \
    [--league main] [--date YYYY-MM-DD] [--tz America/Chicago] [--shutdown-after] [--dry-run] -o guard_wk12.sh
```
- Check times: a first one shortly after inactives are announced (about 90 minutes before kickoff), plus a later one
  in case of late scratches. Repeat `--player/--backups` for several starters.
- Running it: `./guard_wk12.sh` waits and runs each check (any always-on machine). `./guard_wk12.sh --now` runs once,
  for cron / `at` / Task Scheduler / systemd / GitHub Actions (README → Scheduling).
- With `confirm_writes: true`, show the user the guard (who it protects, the backups, the times) and get an OK before
  starting it: the guard itself writes unattended. Test it first with `--dry-run` and `--now`.

## Drafts (`draft/draft.sh`)

- **Wait setting (`ask_draft_wait_before_drafting`, default on):** before starting a bot, a mock or a board, ask which
  setting to use, even when the league has a `draft_wait` in config:
  - `espn`: casual leagues where managers follow ESPN's list or autopick (the default)
  - `adp`: experienced managers
  - `off`: never pass on the top player
  - pass it as `--wait <setting>` (for `go`: `DRAFT_WAIT=<setting>`)
- **Commands:** `draft/draft.sh [nba] bot <league> <team>` drafts a real league. `mockrooms` / `mock <roomId>` rehearse
  in ESPN mock rooms. `rank` rebuilds rankings. `go` joins a public NFL league, names the team from
  `rules.go_team_name`, and starts the bot. `bot`, `mock` and `go` always rebuild rankings first, using the drafted
  league's scoring, team count and lineup slots (so 12-team, 2-QB and superflex leagues are valued correctly).
- **During a draft:** post each pick in chat as it's confirmed. The user must not open the draft room on the same
  account (ESPN allows one session; it kicks the bot). Draft-room gotchas are at the top of `draft/draftbot.js`.
- **WSL:** `draft.sh` sets `LD_LIBRARY_PATH` for Chromium's missing libs (see README). `--headed` shows the browser.

## Presenting

- Name players and teams, with each player's projection and status, and the rule behind each recommendation.
- For any add/drop, show the ROS table. For close calls, show both projections and which way the rule broke the tie.
- Say what you couldn't check (stale session, missing projection) instead of guessing.
