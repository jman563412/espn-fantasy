#!/usr/bin/env python3
"""ESPN fantasy basketball (NBA) helper: roster, daily lineup, free agents, matchup, add/drop.

Usage:
    python3 nba.py --league L --team T roster              # our roster: avg pts, injuries, games left this matchup
    python3 nba.py --league L --team T lineup [--day N]    # best lineup for day N (default today); prints moves
    python3 nba.py --league L --team T lineup --execute    # ...and submits them
    python3 nba.py --league L --team T matchup             # score + games left for both sides
    python3 nba.py --league L fa [--limit 30] [--pos C] [--sort l15|season|proj|games]
    python3 nba.py --league L --team T add --add ID [--drop ID] [--waiver] [--execute]

NBA scoring periods are days (period 1 = opening night, 2026-10-20). A matchup spans several days, so in
H2H points the number of games a player has left matters as much as his average. Lineups lock per player
at his game's tip-off (rosterSettings.lineupLocktimeType INDIVIDUAL_GAME), so a lineup can be fixed up
until each game starts. Writes use the same transactions endpoint as football (see NOTES.md); bench is
slot 12 and IR is 13 here, not 20/21.
"""
import argparse
import datetime
import json
import os
import urllib.request
import config

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, "cookies.json")
SEASON = config.season("nba")  # ESPN names NBA seasons by the year they end
READ = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba"
WRITE = "https://lm-api-writes.fantasy.espn.com/apis/v3/games/fba"
POS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C"}
SLOT = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F", 7: "SG/SF", 8: "G/F", 9: "PF/C", 10: "F/C",
        11: "UTIL", 12: "BE", 13: "IR"}
BENCH, IR = 12, 13
BAD = {"OUT", "INJURY_RESERVE", "SUSPENSION"}
SLOT_FOR_POS = {"PG": 0, "SG": 1, "SF": 2, "PF": 3, "C": 4, "G": 5, "F": 6}


def cookies():
    if os.environ.get("ESPN_S2") and os.environ.get("ESPN_SWID"):
        return {"espn_s2": os.environ["ESPN_S2"], "SWID": os.environ["ESPN_SWID"]}
    with open(COOKIE_FILE) as f:
        return json.load(f)


def request(url, body=None, filt=None):
    c = cookies()
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None)
    req.add_header("Cookie", f"espn_s2={c['espn_s2']}; SWID={c['SWID']}")
    if body:
        req.add_header("Content-Type", "application/json")
    if filt:
        req.add_header("X-Fantasy-Filter", json.dumps(filt))
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code}: {e.read()[:500].decode(errors='replace')}")


def league_url(league, views, day=None):
    q = "&".join(f"view={v}" for v in views) + (f"&scoringPeriodId={day}" if day else "")
    return f"{READ}/seasons/{SEASON}/segments/0/leagues/{league}?{q}"


def today():
    return request(READ)["currentSeason"]["currentScoringPeriod"]["id"]


def schedule():
    """proTeamId -> {scoringPeriodId: game start (datetime)}."""
    d = request(f"{READ}/seasons/{SEASON}?view=proTeamSchedules_wl")
    out = {}
    for t in d["settings"]["proTeams"]:
        out[t["id"]] = {int(k): datetime.datetime.fromtimestamp(g[0]["date"] / 1000)
                        for k, g in (t.get("proGamesByScoringPeriod") or {}).items()}
    return out


def matchup_days(d, day):
    """(matchup period, its days) for a day. settings.matchupPeriods maps matchups to week units, not days, and
    ESPN doesn't publish the day ranges ahead of time. Units are Mon-Sun weeks ending on finalScoringPeriod,
    except the first, which absorbs the odd opening days (2027: 167 days = 13-day opening week + 22 x 7)."""
    periods = d["settings"]["scheduleSettings"]["matchupPeriods"]
    units = max(u for us in periods.values() for u in us)
    first_end = d["status"]["finalScoringPeriod"] - 7 * (units - 1)
    unit_days = lambda u: range(1 if u == 1 else first_end + 7 * (u - 2) + 1, first_end + 7 * (u - 1) + 1)
    for mp, us in periods.items():
        days = [x for u in us for x in unit_days(u)]
        if day in days:
            return int(mp), days
    return None, [day]


def avg(player, split):
    """Per-game fantasy points. split: 'season' actual, 'l7'/'l15'/'l30' actual, 'proj' season projection,
    'last' = last season. appliedAverage is already scored with the league's settings."""
    sid = {"season": f"00{SEASON}", "l7": f"01{SEASON}", "l15": f"02{SEASON}", "l30": f"03{SEASON}",
           "proj": f"10{SEASON}", "last": f"00{SEASON - 1}"}[split]
    for s in player.get("stats", []):
        if s.get("id") == sid:
            return s.get("appliedAverage") or None
    return None


def value(player):
    """Best guess at tonight's points: recent form when we have it, else projection."""
    season, l15, proj = avg(player, "season"), avg(player, "l15"), avg(player, "proj")
    games = next((s["stats"].get("42", 0) for s in player.get("stats", []) if s.get("id") == f"00{SEASON}"), 0)
    if season and games >= 3:
        recent = l15 or season
        w = min(games, 15) / 20  # up to 75% actual once he's played 15 games
        return (1 - w) * (proj or season) + w * (0.5 * season + 0.5 * recent)
    return proj or avg(player, "last") or 0.0


def pos_str(p):
    return "/".join(POS[i] for i in p["eligibleSlots"] if i in POS)


def best_lineup(players, starters):
    """Max-value assignment of players to starting slots. Greedy by value with augmenting paths is optimal
    here (every slot holds one player and a player's value doesn't depend on the slot: a transversal matroid).
    players: list of (value, entry). starters: list of slot ids (repeats for UTIL). Returns {slot index: entry}."""
    owner = {}

    def place(e, seen):
        for i, s in enumerate(starters):
            if i in seen or s not in e["playerPoolEntry"]["player"]["eligibleSlots"]:
                continue
            seen.add(i)
            if i not in owner or place(owner[i], seen):
                owner[i] = e
                return True
        return False

    for _, e in sorted(players, key=lambda x: -x[0]):
        place(e, set())
    return owner


def load(args, day):
    d = request(league_url(args.league, ["mSettings", "mStatus", "mRoster", "mTeam"], day))
    team = next(t for t in d["teams"] if t["id"] == args.team) if args.team else None
    return d, team


def cmd_roster(args):
    day = args.day or today()
    d, team = load(args, day)
    sched = schedule()
    mp, days = matchup_days(d, day)
    left = [x for x in days if x >= day]
    print(f"{team['name']} — day {day}, matchup {mp} (days {days[0]}-{days[-1]}, {len(left)} left)")
    print(f"{'slot':<6}{'player':<26}{'pos':<10}{'today':<7}{'left':>4} {'val':>5} {'seas':>5} {'l15':>5} {'proj':>5}  status")
    rows = []
    for e in team["roster"]["entries"]:
        p = e["playerPoolEntry"]["player"]
        games = sched.get(p["proTeamId"], {})
        g_today = games.get(day)
        rows.append((e["lineupSlotId"] >= BENCH, -value(p), e, p, g_today, sum(1 for x in left if x in games)))
    for _, _, e, p, g_today, n_left in sorted(rows, key=lambda r: r[:2]):
        f = lambda v: f"{v:5.1f}" if v else "    -"
        inj = p.get("injuryStatus") or "ACTIVE"
        print(f"{SLOT[e['lineupSlotId']]:<6}{p['fullName'][:25]:<26}{pos_str(p):<10}{g_today.strftime('%H:%M') if g_today else '-':<7}"
              f"{n_left:>4} {f(value(p))} {f(avg(p, 'season'))} {f(avg(p, 'l15'))} {f(avg(p, 'proj'))}  "
              f"{'' if inj == 'ACTIVE' else inj}{' LOCKED' if e['playerPoolEntry'].get('lineupLocked') else ''}  id{p['id']}")


def cmd_lineup(args):
    day = args.day or today()
    d, team = load(args, day)
    sched = schedule()
    counts = d["settings"]["rosterSettings"]["lineupSlotCounts"]
    starters = [int(k) for k, n in sorted(counts.items(), key=lambda kv: int(kv[0])) if int(k) < BENCH for _ in range(n)]
    entries = [e for e in team["roster"]["entries"] if e["lineupSlotId"] != IR]
    locked = [e for e in entries if e["playerPoolEntry"].get("lineupLocked")]
    free = [e for e in entries if not e["playerPoolEntry"].get("lineupLocked")]

    # locked starters keep their slots; fill the rest from unlocked players who play today and aren't out
    open_slots = list(starters)
    for e in locked:
        if e["lineupSlotId"] in open_slots:
            open_slots.remove(e["lineupSlotId"])
    cands = []
    for e in free:
        p = e["playerPoolEntry"]["player"]
        if day in sched.get(p["proTeamId"], {}) and (p.get("injuryStatus") or "ACTIVE") not in BAD:
            cands.append((value(p), e))
    owner = best_lineup(cands, open_slots)
    target = {id(e): open_slots[i] for i, e in owner.items()}

    moves, total = [], 0.0
    for e in free:
        p = e["playerPoolEntry"]["player"]
        to = target.get(id(e), BENCH)
        if to != BENCH:
            total += value(p)
        if to != e["lineupSlotId"]:
            moves.append((e, to))
    for e in locked:
        if e["lineupSlotId"] < BENCH:
            total += value(e["playerPoolEntry"]["player"])
    playing = sum(1 for _, e in cands)
    print(f"{team['name']} — day {day}: {playing} healthy players with a game, {len(starters)} starting slots, "
          f"{len(locked)} locked; lineup value ~{total:.1f}")
    for i, s in enumerate(open_slots):
        e = owner.get(i)
        print(f"  {SLOT[s]:<6}{e['playerPoolEntry']['player']['fullName'] if e else '(empty: nobody eligible plays)'}"
              + (f"  {value(e['playerPoolEntry']['player']):.1f}" if e else ""))
    if not moves:
        return print("lineup already optimal, no moves")
    for e, to in moves:
        print(f"  move {e['playerPoolEntry']['player']['fullName']}: {SLOT[e['lineupSlotId']]} -> {SLOT[to]}")
    if not args.execute:
        return print("(dry run; add --execute to submit)")
    items = [{"playerId": e["playerId"], "type": "LINEUP", "fromTeamId": 0, "toTeamId": 0,
              "fromLineupSlotId": e["lineupSlotId"], "toLineupSlotId": to, "isKeeper": False} for e, to in moves]
    resp = transact(args, "ROSTER", day, items)
    print(f"submitted: status={resp.get('status')} id={resp.get('id')}")


def transact(args, kind, day, items):
    body = {"isLeagueManager": False, "teamId": args.team, "type": kind, "memberId": cookies()["SWID"],
            "scoringPeriodId": day, "executionType": "EXECUTE", "items": items}
    return request(f"{WRITE}/seasons/{SEASON}/segments/0/leagues/{args.league}/transactions/", body)


def cmd_matchup(args):
    day = args.day or today()
    d = request(league_url(args.league, ["mSettings", "mStatus", "mRoster", "mTeam", "mMatchupScore"], day))
    sched = schedule()
    mp, days = matchup_days(d, day)
    left = [x for x in days if x >= day]
    m = next((m for m in d["schedule"] if m["matchupPeriodId"] == mp and args.team in (m["home"]["teamId"], m.get("away", {}).get("teamId"))), None)
    if not m:
        return print(f"no matchup found for team {args.team} in matchup period {mp}")
    teams = {t["id"]: t for t in d["teams"]}
    print(f"matchup {mp}, days {days[0]}-{days[-1]} ({len(left)} left incl. today)")
    for side in ("home", "away"):
        t = teams[m[side]["teamId"]]
        pts = m[side].get("totalPointsLive", m[side].get("totalPoints", 0))
        # rough rest-of-matchup projection: each healthy player's games left x value, capped by starting slots per day
        starters = sum(n for k, n in d["settings"]["rosterSettings"]["lineupSlotCounts"].items() if int(k) < BENCH)
        proj = 0.0
        for x in left:
            vals = sorted((value(e["playerPoolEntry"]["player"]) for e in t["roster"]["entries"]
                           if e["lineupSlotId"] != IR and x in sched.get(e["playerPoolEntry"]["player"]["proTeamId"], {})
                           and (e["playerPoolEntry"]["player"].get("injuryStatus") or "ACTIVE") not in BAD), reverse=True)
            proj += sum(vals[:starters])  # ignores slot eligibility; good enough for a comparison
        print(f"  {t['name']:<32} {pts:7.1f} now, ~{proj:6.1f} more to come")


def cmd_fa(args):
    day = args.day or today()
    d = request(league_url(args.league, ["mSettings", "mStatus"], day))
    sched = schedule()
    mp, days = matchup_days(d, day)
    left = [x for x in days if x >= day]
    # last-15 averages mean nothing until ~2 weeks in; rank by projection before that
    sort = args.sort if args.sort in ("season", "l15", "proj") else ("l15" if day > 14 else "proj")
    sort_key = {"season": f"00{SEASON}", "l15": f"02{SEASON}", "proj": f"10{SEASON}"}[sort]
    players = {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]}, "limit": args.limit * 3 if args.sort == "games" else args.limit,
               "sortAppliedStatAverage": {"sortPriority": 1, "sortAsc": False, "value": sort_key},
               "filterStatsForSplitTypeIds": {"value": [0]}}
    if args.pos:
        players["filterSlotIds"] = {"value": [SLOT_FOR_POS[args.pos]]}
    data = request(league_url(args.league, ["kona_player_info"], day), filt={"players": players})
    rows = []
    for x in data["players"]:
        p = x["player"]
        games = sched.get(p["proTeamId"], {})
        n_left = sum(1 for g in left if g in games)
        rows.append((n_left, value(p), x, p, day in games))
    if args.sort == "games":
        rows.sort(key=lambda r: (-r[0] * r[1]))
        rows = rows[: args.limit]
    print(f"day {day}, matchup {mp}: {len(left)} days left")
    print(f"{'player':<26}{'pos':<10}{'status':<10}{'own%':>5} {'left':>4} {'today':>5} {'val':>5} {'seas':>5} {'l15':>5} {'proj':>5}")
    for n_left, v, x, p, plays in rows:
        f = lambda v: f"{v:5.1f}" if v else "    -"
        inj = p.get("injuryStatus") or "ACTIVE"
        print(f"{p['fullName'][:25]:<26}{pos_str(p):<10}{x.get('status', ''):<10}{p.get('ownership', {}).get('percentOwned', 0):5.1f} "
              f"{n_left:>4} {'yes' if plays else '-':>5} {f(v)} {f(avg(p, 'season'))} {f(avg(p, 'l15'))} {f(avg(p, 'proj'))}"
              f"  {'' if inj == 'ACTIVE' else inj}  id{p['id']}")


def cmd_add(args):
    day = args.day or today()
    items = [{"playerId": args.add, "type": "ADD", "fromTeamId": 0, "toTeamId": args.team,
              "fromLineupSlotId": -1, "toLineupSlotId": BENCH, "isKeeper": False}]
    if args.drop:
        _, team = load(args, day)
        e = next((e for e in team["roster"]["entries"] if e["playerId"] == args.drop), None)
        if not e:
            raise SystemExit(f"{args.drop} is not on our roster")
        items.append({"playerId": args.drop, "type": "DROP", "fromTeamId": args.team, "toTeamId": 0,
                      "fromLineupSlotId": e["lineupSlotId"], "toLineupSlotId": -1, "isKeeper": False})
    kind = "WAIVER" if args.waiver else "FREEAGENT"
    print(f"{kind} day {day}: +{args.add}" + (f" -{args.drop}" if args.drop else ""))
    if not args.execute:
        return print("(dry run; add --execute to submit)")
    resp = transact(args, kind, day, items)
    print(f"submitted: status={resp.get('status')} id={resp.get('id')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", help="config.json league name or ID (default: first NBA league)")
    ap.add_argument("--team", type=int, help="default: that league's team in config.json")
    ap.add_argument("--day", type=int, help="scoring period (day); default ESPN's current one")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("roster")
    lp = sub.add_parser("lineup")
    lp.add_argument("--execute", action="store_true")
    sub.add_parser("matchup")
    fp = sub.add_parser("fa")
    fp.add_argument("--limit", type=int, default=30)
    fp.add_argument("--pos", choices=list(SLOT_FOR_POS))
    fp.add_argument("--sort", choices=["auto", "l15", "season", "proj", "games"], default="auto",
                    help="auto = proj for the first 2 weeks, then l15; games = games left this matchup x value (streamers)")
    adp = sub.add_parser("add")
    adp.add_argument("--add", type=int, required=True)
    adp.add_argument("--drop", type=int)
    adp.add_argument("--waiver", action="store_true", help="player is on waivers: submit a claim instead")
    adp.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    args.league, args.team = config.cli_league(ap, "nba", args.league, args.team)
    if args.cmd != "fa" and not args.team:
        ap.error("--team is required for " + args.cmd)
    {"roster": cmd_roster, "lineup": cmd_lineup, "matchup": cmd_matchup, "fa": cmd_fa, "add": cmd_add}[args.cmd](args)


if __name__ == "__main__":
    main()
