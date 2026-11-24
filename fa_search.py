#!/usr/bin/env python3
"""Search the ESPN free agent / waiver pool with weekly production and usage.

Usage:
    python3 fa_search.py --week 3                      # top 40 by season points, all positions
    python3 fa_search.py --week 3 --pos RB --limit 25
    python3 fa_search.py --week 3 --sort proj          # by this week's ESPN projection
    python3 fa_search.py --week 3 --name Coleman       # name search (any status, shows owner)

Any `limit` in X-Fantasy-Filter MUST come with a sort key, or ESPN returns
400 FILTER_LIMIT_MISSING_SORT.
"""
import argparse
import json
import os
import urllib.request
import config

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, "cookies.json")
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
SLOT_FOR_POS = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "DST": 16, "K": 17}
# raw stat ids: 23 rush att, 24 rush yds, 53 receptions, 58 targets, 42 rec yds
RUSH_ATT, RECS, TARGETS = "23", "53", "58"


def fetch(league_id, season, week, filt):
    with open(COOKIE_FILE) as f:
        c = json.load(f)
    url = (
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
        f"/segments/0/leagues/{league_id}?view=kona_player_info&scoringPeriodId={week}"
    )
    req = urllib.request.Request(url)
    req.add_header("Cookie", f"espn_s2={c['espn_s2']}; SWID={c['SWID']}")
    req.add_header("X-Fantasy-Filter", json.dumps(filt))
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def build_filter(args):
    season = str(args.season)
    players = {
        "limit": args.limit,
        # returns per-week actuals for the last N completed weeks; the 11{season}{week} entry adds
        # this week's projection, and is required for sort=proj to be honored
        "filterStatsForTopScoringPeriodIds": {
            "value": args.week - 1,
            "additionalValue": [f"00{season}", f"10{season}", f"11{season}{args.week}"],
        },
    }
    if args.name:
        players["filterName"] = {"value": args.name}
        players["filterStatus"] = {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]}
    else:
        players["filterStatus"] = {"value": ["FREEAGENT", "WAIVERS"]}
    if args.pos:
        players["filterSlotIds"] = {"value": [SLOT_FOR_POS[args.pos]]}
    if args.sort == "owned":
        players["sortPercOwned"] = {"sortPriority": 1, "sortAsc": False}
    elif args.sort == "proj":
        players["sortAppliedStatTotal"] = {"sortPriority": 1, "sortAsc": False, "value": f"11{season}{args.week}"}
    else:
        players["sortAppliedStatTotal"] = {"sortPriority": 1, "sortAsc": False, "value": f"00{season}"}
    return {"players": players}


def week_stat(player, week, source):
    for s in player.get("stats", []):
        if s.get("scoringPeriodId") == week and s.get("statSourceId") == source:
            return s
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, required=True, help="upcoming scoring period")
    ap.add_argument("--pos", choices=list(SLOT_FOR_POS))
    ap.add_argument("--name")
    ap.add_argument("--sort", choices=["season", "proj", "owned"], default="season")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--league", help="config.json league name or ID (default: first NFL league)")
    ap.add_argument("--season", type=int, help="default: config.json season.nfl")
    args = ap.parse_args()
    args.league, _ = config.cli_league(ap, "nfl", args.league)
    args.season = args.season or config.season("nfl")

    data = fetch(args.league, args.season, args.week, build_filter(args))
    past = range(1, args.week)
    hdr = "".join(f" w{w:<5}" for w in past)
    print(f"{'pos':<4}{'player':<25}{'status':<10}{'own%':>5} {hdr}  proj   usage(tgt/att per wk)")
    for x in data["players"]:
        p = x["player"]
        pos = POS.get(p["defaultPositionId"], "?")
        pts, usage = "", []
        for w in past:
            s = week_stat(p, w, 0)
            pts += f" {(round(s['appliedTotal'], 1) if s else '-')!s:<6}"
            if s and pos in ("RB", "WR", "TE"):
                st = s.get("stats", {})
                usage.append(f"{int(st.get(TARGETS, 0))}/{int(st.get(RUSH_ATT, 0))}")
        proj = week_stat(p, args.week, 1)
        status = x.get("status", "")
        if status == "ONTEAM":
            status = f"team{x.get('onTeamId')}"
        inj = p.get("injuryStatus", "")
        inj = "" if inj in ("ACTIVE", "", None) else f" [{inj}]"
        print(
            f"{pos:<4}{p['fullName'][:24]:<25}{status:<10}{p.get('ownership', {}).get('percentOwned', 0):5.0f} {pts}"
            f"{(round(proj['appliedTotal'], 1) if proj else '-')!s:<6} {' '.join(usage)}{inj}  id{p['id']}"
        )


if __name__ == "__main__":
    main()
