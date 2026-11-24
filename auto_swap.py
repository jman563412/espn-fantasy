#!/usr/bin/env python3
"""If a starter is OUT/inactive, swap in the best healthy, unlocked bench player at that position.

Usage:
    python3 auto_swap.py --week 3 --player 4695883 --backups 4832800 4047650 [--league NAME|ID --team ID] [--dry-run]

Backups are tried in order of this week's ESPN projection (highest first); any backup that is
OUT/DOUBTFUL/IR or already locked is skipped. Uses the ROSTER/LINEUP write documented in NOTES.md.
"""
import argparse
import datetime
import json
import os
import urllib.request
import config

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, "cookies.json")
SEASON = config.season("nfl")
LEAGUE, TEAM = None, None  # from config.json, or --league/--team
BAD = {"OUT", "DOUBTFUL", "INJURY_RESERVE", "SUSPENSION"}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def cookies():
    # GitHub Actions passes these as secrets; locally fall back to cookies.json
    if os.environ.get("ESPN_S2") and os.environ.get("ESPN_SWID"):
        return {"espn_s2": os.environ["ESPN_S2"], "SWID": os.environ["ESPN_SWID"]}
    with open(COOKIE_FILE) as f:
        return json.load(f)


def request(url, body=None):
    c = cookies()
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None)
    req.add_header("Cookie", f"espn_s2={c['espn_s2']}; SWID={c['SWID']}")
    if body:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def base():
    return f"/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leagues/{LEAGUE}"


def roster(week):
    d = request(f"https://lm-api-reads.fantasy.espn.com{base()}?view=mRoster&scoringPeriodId={week}")
    team = next(t for t in d["teams"] if t["id"] == TEAM)
    return {e["playerId"]: e for e in team["roster"]["entries"]}


def proj(player, week):
    for s in player.get("stats", []):
        if s.get("scoringPeriodId") == week and s.get("statSourceId") == 1:
            return s.get("appliedTotal") or 0.0
    return 0.0


def main():
    global LEAGUE, TEAM
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--player", type=int, required=True, help="starter to protect")
    ap.add_argument("--backups", type=int, nargs="+", required=True)
    ap.add_argument("--league", help="config.json league name or ID (default: first NFL league)")
    ap.add_argument("--team", type=int, help="default: that league's team in config.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-as-out", action="store_true", help="with --dry-run: pretend the starter is OUT")
    args = ap.parse_args()
    LEAGUE, TEAM = config.cli_league(ap, "nfl", args.league, args.team, need_team=True)
    log(f"league {LEAGUE} team {TEAM}")

    r = roster(args.week)
    starter = r.get(args.player)
    if not starter:
        return log(f"player {args.player} not on roster, nothing to do")
    sp = starter["playerPoolEntry"]["player"]
    status, slot = sp.get("injuryStatus"), starter["lineupSlotId"]
    log(f"{sp['fullName']}: status={status} slot={slot} locked={starter['playerPoolEntry'].get('lineupLocked')}")
    if slot in (20, 21):
        return log("already on bench, nothing to do")
    if status not in BAD and not (args.dry_run and args.test_as_out):
        return log("not OUT, leaving lineup alone")
    if starter["playerPoolEntry"].get("lineupLocked"):
        return log("starter is locked, too late to swap")

    candidates = []
    for pid in args.backups:
        e = r.get(pid)
        if not e:
            continue
        p = e["playerPoolEntry"]["player"]
        ok = e["lineupSlotId"] == 20 and not e["playerPoolEntry"].get("lineupLocked") and p.get("injuryStatus") not in BAD
        log(f"  backup {p['fullName']}: status={p.get('injuryStatus')} slot={e['lineupSlotId']} proj={proj(p, args.week):.1f} ok={ok}")
        if ok:
            candidates.append((proj(p, args.week), pid, p["fullName"]))
    if not candidates:
        return log("no eligible backup, leaving lineup alone")
    _, bid, bname = max(candidates)

    body = {
        "isLeagueManager": False,
        "teamId": TEAM,
        "type": "ROSTER",
        "memberId": cookies()["SWID"],
        "scoringPeriodId": args.week,
        "executionType": "EXECUTE",
        "items": [
            {"playerId": args.player, "type": "LINEUP", "fromTeamId": 0, "toTeamId": 0,
             "fromLineupSlotId": slot, "toLineupSlotId": 20, "isKeeper": False},
            {"playerId": bid, "type": "LINEUP", "fromTeamId": 0, "toTeamId": 0,
             "fromLineupSlotId": 20, "toLineupSlotId": slot, "isKeeper": False},
        ],
    }
    if args.dry_run:
        return log(f"DRY RUN: would swap {sp['fullName']} -> bench, {bname} -> slot {slot}")
    resp = request(f"https://lm-api-writes.fantasy.espn.com{base()}/transactions/", body)
    log(f"swapped {sp['fullName']} -> bench, {bname} -> slot {slot}: status={resp.get('status')} id={resp.get('id')}")


if __name__ == "__main__":
    main()
