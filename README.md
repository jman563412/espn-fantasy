# espn-fantasy

Tools for running ESPN fantasy football (NFL) and basketball (NBA) teams through ESPN's undocumented API, built to be
driven by an AI agent:

- **API wrapper scripts** (Python, stdlib only): free-agent search with weekly usage, matchups and rosters, NBA daily
  lineups (optimal slot assignment), adds/drops/claims, and a watcher for notable drops, including trade-forced ones
  ESPN's activity feed never shows.
- **Draft bot** (Node + Playwright): sits in ESPN's live draft room and picks from its own rankings (projections,
  positional replacement value, roster rules, and a "wait a round" rule). It survives reconnects, and you can
  rehearse in ESPN mock rooms first.
- **Lineup guards:** `guard.py` generates a script that checks your starters at set times before kickoff and benches
  anyone ruled out for the best healthy backup. It runs on any machine.
- **An AI agent skill** (`.claude/skills/espn-fantasy/SKILL.md`, used by Claude Code; `AGENTS.md` points other agents
  at it): the management playbook, with rules you can configure per sport.

`docs/espn-api.md` documents the API as reverse-engineered: hosts, views, filters, and the transaction format for
lineups, claims, drops and trades.

## Setup

```bash
npm install && npx playwright install chromium      # browser automation (login + draft bot)
node fetch_cookies.js                               # log in to ESPN in the window; saves cookies.json
cp config.example.json config.json                  # then add your leagues (league and team IDs from the URL)
python3 fa_search.py --week 1 --limit 5             # check it works
```

`cookies.json` is a live ESPN login. It's gitignored; never share it. Rerun `node fetch_cookies.js` when calls start
failing with 401 or redirects. Python 3.10+ and nothing to install.

## Config and rules

`config.json` lists your leagues (`name`, `sport`, `league_id`, `team_id`, `draft_wait`). Scripts default to the
first league of each sport; `--league <name|id>` picks another. `rules` drives the agent skill. Every rule is on by
default and adjustable:

| key | default | what it does |
|---|---|---|
| `confirm_writes` | `true` | propose every roster move and wait for an OK; `false` = act, then report |
| `ask_draft_wait_before_drafting` | `true` | ask `espn` / `adp` / `off` before any draft |
| `go_team_name` | `"{city} Claude Squad"` | team name `draft.sh go` uses in a joined public league |
| `nfl.close_call_points` | `3` | within this many projected points, start the steadier role |
| `nfl.injury_designation_reopens_close_call` | `true` | a practice limitation or designation reopens a close call |
| `nfl.ros_positions_for_add_drop` | `RB, WR, TE` | adds/drops compare rest-of-season projections |
| `nfl.streamable_positions` | `QB, K, D/ST` | streamed on this week's projection |
| `nfl.keep_streamable_if_ros_beats_fa_per_week` | `2` | ...unless rest-of-season beats the best FA by this × weeks left |
| `nfl.start_sit_uses_ros` | `false` | start/sit uses this week only |
| `nfl.qb_count` / `nfl.max_qbs` | `league_majority` / `2` | carry as many QBs as most other teams, capped |
| `nba.daily_lineups_start_everyone_playing` | `true` | start everyone who plays each day |

## Commands

```bash
python3 fa_search.py --week 12 --pos WR --sort proj        # NFL free agents / waivers
python3 fetch_week.py --week 12                            # NFL matchups and rosters
python3 nba.py roster | lineup [--execute] | matchup | fa  # NBA
python3 nba.py add --add ID [--drop ID] [--waiver] [--execute]
python3 drop_watch.py --hours 48 [--issue]                 # notable drops (--issue: open a GitHub issue each)
python3 auto_swap.py --week 12 --player ID --backups ID ID --dry-run
```

Writes only happen with `--execute` (`nba.py`) or without `--dry-run` (`auto_swap.py`). The agent skill handles
football lineup, claim and trade transactions per `docs/espn-api.md`.

## Draft bot

```bash
draft/draft.sh mockrooms                    # upcoming ESPN mock rooms
draft/draft.sh mock <roomId> --wait espn    # rehearse
draft/draft.sh bot <league> <team> --wait adp
draft/draft.sh nba bot <league> <team>      # basketball
draft/draft.sh go                           # join a public NFL league, rename the team, draft it
draft/draft.sh stop
```

- `bot`, `mock` and `go` rebuild rankings first, from the drafted league's scoring, team count and starting lineup
  slots (`mock` ranks through your default league). Run `python3 draft/rank.py --league <id>` (or
  `rank_nba.py`) yourself to inspect a board.
- `--wait` decides when to pass on the top player because he should last to your next pick: `espn` (managers follow
  ESPN's list), `adp`, or `off`.
- ESPN allows one draft-room session per account, so don't open the draft yourself while the bot runs.
- Headless by default; add `--headed` to watch.
- The manual-draft board is `python3 draft/cheatsheet_nba.py` (writes `draft/cheatsheet-nba.html`).
- **WSL:** Chromium needs `libnss3` / `libnspr4`. `draft.sh` looks for them in `~/.local/lib/espn-nss` (unpack the
  Debian packages there if you can't `apt install` them).

## Scheduling

**Lineup guards.** Generate one for a game window:
```bash
python3 guard.py make --week 12 --player 4262921 --backups 4695883 4832800 \
    --check 10:45 --check 11:20 --date 2026-11-29 [--shutdown-after] -o guard_wk12.sh
```
Then pick how to run it:

| where | how |
|---|---|
| any always-on machine (PC, VPS, Raspberry Pi) | `nohup ./guard_wk12.sh &`: it sleeps until each check (on the wall clock, so suspend or clock syncs can't make it early) and skips past ones |
| Linux / macOS, one-off | `echo "$PWD/guard_wk12.sh --now" \| at 10:45 Nov 29` |
| cron | `45 10 29 11 * /path/to/espn-fantasy/guard_wk12.sh --now` |
| Windows (WSL) | Task Scheduler action: `wsl.exe -e /path/to/espn-fantasy/guard_wk12.sh --now` |
| systemd | a `.timer` with `OnCalendar=2026-11-29 10:45 America/Chicago` running `guard_wk12.sh --now` |
| GitHub Actions | a `workflow_dispatch`/`schedule` job with `ESPN_S2`/`ESPN_SWID` secrets running the same command; scheduled runs can start very late, so give it a wide margin or trigger it by hand |

`--shutdown-after` powers the machine off two minutes after the last check (WSL: `shutdown.exe`), so a PC left on for
a guard turns itself off.

**Drop watcher.** Run it hourly, e.g. `17 * * * * cd /path/to/espn-fantasy && python3 drop_watch.py --hours 2`. Add
`--issue` inside GitHub Actions to get an issue per alert.

`auto_swap.py` and `drop_watch.py` use `ESPN_S2` / `ESPN_SWID` from the environment when set (e.g. secrets on a
server or in Actions), otherwise `cookies.json`. Generated guards pass the league and team explicitly, so a server
doesn't need your `config.json`.

## License

MIT
