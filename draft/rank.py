#!/usr/bin/env python3
"""Build draft rankings (value over replacement) from ESPN projections.

Usage:
    python3 draft/rank.py [--league NAME|ID] [--week N] [--teams N] [--top 60]   # week defaults to ESPN's current one

Writes draft/rankings.json, which draftbot.js reads. Pulls the player pool through the league being drafted
(default: the first NFL league in config.json). Points come from ESPN's `appliedTotal`, which is already scored
with that league's settings (PPR, bonuses, ...). Replacement level comes from the league's size and starting
lineup slots (mSettings), so 12-team, 2-QB and superflex leagues are valued correctly; --teams overrides the size.
ppg = projection (half this week's projection, half full-season projection / 17), blended with
this season's actual points per game weighted by games played: w = games / (games + 6), so 3 games
counts ~1/3. Injured players are discounted.
"""
import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import config  # noqa: E402
COOKIE_FILE = os.path.join(HERE, "..", "cookies.json")
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}


def cookies():
    with open(COOKIE_FILE) as f:
        return json.load(f)


def current_week():
    req = urllib.request.Request("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl")
    c = cookies()
    req.add_header("Cookie", f"espn_s2={c['espn_s2']}; SWID={c['SWID']}")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)["currentSeason"]["currentScoringPeriod"]["id"]


# ESPN lineupSlotId -> position for dedicated starting slots
SLOT_POS = {"0": "QB", "2": "RB", "4": "WR", "6": "TE", "16": "DST", "17": "K"}
# Flex slots: how many of each position one slot adds to league-wide starter demand. RB/WR/TE (23) keeps the
# shares this ranker always used for a standard flex; the others follow the same leaning. OP (7) = superflex,
# which teams nearly always fill with a QB.
FLEX_SHARE = {
    "23": {"RB": 0.5, "WR": 0.8, "TE": 0.2},   # RB/WR/TE
    "3": {"RB": 0.5, "WR": 0.8},               # RB/WR
    "5": {"WR": 0.8, "TE": 0.2},               # WR/TE
    "7": {"QB": 0.8, "RB": 0.3, "WR": 0.3, "TE": 0.1},   # OP (superflex)
}
STANDARD_SLOTS = {"0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "16": 1, "17": 1}


def replacement_counts(teams: int, slots: dict) -> dict:
    """Replacement level per position = the Nth best, N = league-wide starters at that position (flex shared out)."""
    per_team = {pos: 0.0 for pos in ("QB", "RB", "WR", "TE", "K", "DST")}
    for slot, count in slots.items():
        slot = str(slot)
        if slot in SLOT_POS:
            per_team[SLOT_POS[slot]] += count
        for pos, share in FLEX_SHARE.get(slot, {}).items():
            per_team[pos] += share * count
    return {pos: int(round(teams * n, 6)) for pos, n in per_team.items()}


def league_shape(league, season):
    """(team count, starting lineup slot counts) from the league's mSettings; standard 10-team if unreadable."""
    c = cookies()
    req = urllib.request.Request(
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}"
        f"?view=mSettings")
    req.add_header("Cookie", f"espn_s2={c['espn_s2']}; SWID={c['SWID']}")
    try:
        with urllib.request.urlopen(req) as resp:
            settings = json.load(resp)["settings"]
        return settings["size"], settings["rosterSettings"]["lineupSlotCounts"], None
    except Exception as exc:  # any transport or shape problem: say so, rank as a standard league
        return 10, STANDARD_SLOTS, f"could not read league settings ({exc}); using a standard 10-team lineup"


def fetch_pool(league, season, week):
    c = cookies()
    filt = {"players": {
        "limit": 450,
        "filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]},
        "sortAppliedStatTotal": {"sortPriority": 1, "sortAsc": False, "value": f"10{season}"},
        "filterStatsForTopScoringPeriodIds": {"value": week - 1, "additionalValue": [f"00{season}", f"10{season}", f"11{season}{week}"]},
    }}
    req = urllib.request.Request(
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}"
        f"?view=kona_player_info&scoringPeriodId={week}")
    req.add_header("Cookie", f"espn_s2={c['espn_s2']}; SWID={c['SWID']}")
    req.add_header("X-Fantasy-Filter", json.dumps(filt))
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)["players"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, help="scoring period to project (default: ESPN's current one)")
    ap.add_argument("--teams", type=int, help="override the league's team count")
    ap.add_argument("--season", type=int, help="default: config.json season.nfl")
    ap.add_argument("--league", help="any league we can read (default: first NFL league in config.json)")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()
    args.league, _ = config.cli_league(ap, "nfl", args.league)
    args.season = args.season or config.season("nfl")
    if args.week is None:
        args.week = current_week()
    print(f"ranking for week {args.week}")

    rows = []
    for x in fetch_pool(args.league, args.season, args.week):
        p = x["player"]
        pos = POS.get(p["defaultPositionId"])
        if not pos:
            continue
        stats = p.get("stats", [])
        st = {s["id"]: s["appliedTotal"] for s in stats}
        season = st.get(f"10{args.season}", 0) / 17
        wk = st.get(f"11{args.season}{args.week}")
        inj = p.get("injuryStatus") or "ACTIVE"
        proj = 0.5 * wk + 0.5 * season if wk else season
        # actual weekly scores so far (statSourceId 0 = actual, per-week split); byes have no entry
        weeks = [s["appliedTotal"] for s in stats
                 if s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 1 and 0 < s.get("scoringPeriodId", 0) < args.week]
        games = len(weeks)
        actual = sum(weeks) / games if games else None
        w = games / (games + 6)
        ppg = (1 - w) * proj + w * actual if games else proj
        if inj in ("OUT", "INJURY_RESERVE", "SUSPENSION"):
            ppg *= 0.6
        elif inj == "DOUBTFUL":
            ppg *= 0.8
        # ESPN's preseason rank (the draft room's default sort, which autopick and casual drafters follow) and ADP,
        # for draftbot.js's wait-a-round rule
        ranks = p.get("draftRanksByRankType", {})
        espn = (ranks.get("PPR") or ranks.get("STANDARD") or {}).get("rank")
        rows.append({"id": p["id"], "name": p["fullName"], "pos": pos, "ppg": round(ppg, 2), "inj": inj,
                     "proj": round(proj, 2), "actual": round(actual, 2) if games else None, "games": games,
                     "espn": espn, "adp": round(p.get("ownership", {}).get("averageDraftPosition", 0), 1)})

    # replacement level = the Nth best at each position, N = league-wide starters (flex shared out)
    size, slots, note = league_shape(args.league, args.season)
    if note:
        print(note)
    t = args.teams or size
    repl_n = replacement_counts(t, slots)
    print(f"{t} teams; replacement level at " + ", ".join(f"{p} #{n}" for p, n in repl_n.items()))
    for pos, n in repl_n.items():
        ps = sorted((r for r in rows if r["pos"] == pos), key=lambda r: -r["ppg"])
        rl = ps[min(n, len(ps) - 1)]["ppg"] if ps else 0   # n = 0 (no such slot): best player is the baseline
        for r in ps:
            r["vor"] = round(r["ppg"] - rl, 2)
    rows.sort(key=lambda r: -r["vor"])

    out = os.path.join(HERE, "rankings.json")
    with open(out, "w") as f:
        json.dump(rows, f, indent=0)
    for i, r in enumerate(rows[: args.top], 1):
        act = f"{r['actual']:>5} in {r['games']}g" if r["games"] else "   -      "
        print(f"{i:>3} {r['pos']:<4}{r['name']:<26}ppg {r['ppg']:>5} (proj {r['proj']:>5}, actual {act})  vor {r['vor']:>5}  {r['inj'] if r['inj'] != 'ACTIVE' else ''}")
    print(f"wrote {len(rows)} players to {out}")


if __name__ == "__main__":
    main()
