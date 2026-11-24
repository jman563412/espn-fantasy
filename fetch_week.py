#!/usr/bin/env python3
"""Pull ESPN fantasy football data for one week using cookies from fetch_cookies.js.

Usage:
    python3 fetch_week.py --week 1 [--league NAME|ID] [--season YEAR]
"""
import argparse
import json
import os
import urllib.request
import config

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, "cookies.json")


def load_cookies():
    with open(COOKIE_FILE) as f:
        c = json.load(f)
    return c["espn_s2"], c["SWID"]


def fetch(league_id, season, week):
    espn_s2, swid = load_cookies()
    url = (
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
        f"/segments/0/leagues/{league_id}?view=mMatchupScore&view=mTeam&view=mRoster"
        f"&scoringPeriodId={week}"
    )
    req = urllib.request.Request(url)
    req.add_header("Cookie", f"espn_s2={espn_s2}; SWID={swid}")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def summarize(data, week):
    teams = {}
    for t in data["teams"]:
        teams[t["id"]] = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()

    team_totals = {}
    rosters = {}
    for t in data["teams"]:
        tid = t["id"]
        total = 0.0
        starters, bench = [], []
        for e in t.get("roster", {}).get("entries", []):
            slot = e.get("lineupSlotId")
            player = e.get("playerPoolEntry", {}).get("player", {})
            name = player.get("fullName", "?")
            pts = None
            for s in player.get("stats", []):
                if s.get("scoringPeriodId") == week and s.get("statSourceId") == 0:
                    pts = s.get("appliedTotal")
            if slot == 21:
                continue
            if slot == 20:
                bench.append((name, pts))
            else:
                starters.append((name, pts))
                total += pts or 0.0
        team_totals[tid] = round(total, 2)
        rosters[tid] = {"starters": starters, "bench": bench}

    matchups = [m for m in data["schedule"] if m["matchupPeriodId"] == week]
    print(f"=== Week {week} matchups ===")
    for m in matchups:
        hid, aid = m["home"]["teamId"], m["away"]["teamId"]
        h, a = teams[hid], teams[aid]
        hs, as_ = team_totals[hid], team_totals[aid]
        print(f"{h}: {hs}  vs  {a}: {as_}")

    return teams, team_totals, rosters


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--league", help="config.json league name or ID (default: first NFL league)")
    ap.add_argument("--season", type=int, help="default: config.json season.nfl")
    args = ap.parse_args()
    args.league, _ = config.cli_league(ap, "nfl", args.league)
    args.season = args.season or config.season("nfl")

    data = fetch(args.league, args.season, args.week)
    summarize(data, args.week)
