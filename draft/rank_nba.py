#!/usr/bin/env python3
"""Build NBA draft rankings (season value over replacement) from ESPN projections.

Usage:
    python3 draft/rank_nba.py [--league ID] [--teams 10] [--top 60]

Writes draft/rankings-nba.json, which draftbot.js reads with --sport nba. With --league, the league's own
scoring settings are applied (appliedTotal comes back scored per league). Without one, ESPN's default
H2H Points scoring is used (leaguedefaults/2: PTS 1, REB 1, AST 2, STL 4, BLK 4, 3PM 1, FGM 2, FGA -1, FTM 1, FTA -1, TO -2).

ppg = 70% ESPN's projection for this season + 30% last season's actual average (only if he played 20+ games).
games = half ESPN's projection, half his track record: games played over the last three seasons, weighted 3/2/1 toward
the most recent (user, 2026-10-21: one season let Kawhi/Embiid/George reset after a healthier year, and the mock roster
was all injury histories). ESPN's projections run generous for injury-prone players (Embiid 54g after 39/19/38).
A fully missed season (Kyrie, Haliburton: one acute injury) counts at half weight. Seasons he wasn't in the league don't count, nor do seasons
spent outside the NBA (NOT_IN_NBA).
No separate risk discount (removed 2026-10-21, user): value x (1 - 0.5 x missed share) charged his history a second
time on top of the games projection, and once missed games scored 0 early it was a third. Durability is all in games.
Current injuries (user, 2026-10-21): only games after his return count. While he's out he sits in IR and a free agent
(about replacement level) covers him, so value is his points above replacement after he's back. Return dates come
from ESPN's public injury feed; games = his team's fantasy-season games from that date on. ESPN's games projection
assumes health for short absences (Claxton 69, Knueppel 78) but already cuts long ones (Butler 39, DiVincenzo 4), so:
missing < 20 games -> projection x share of games left; 20+ -> projection capped at games left. Out for the season
-> 0. No return date: OUT x 0.85, day-to-day -2 games. Football discounts ppg instead because it values one week.
Multiple positions (user, 2026-10-21): +0.5 ppg per extra position (PG/SG/SF/PF/C), as lineup flexibility for daily
lineups. A tiebreaker, about 1-1.5% for a mid-round player.
Value = (ppg - replacement ppg) * projected games - replacement ppg * missed games. A missed game counts as 0 points,
not as a streamer at replacement level (user, 2026-10-21: the friends' draft stacked Davis/Embiid/Jerome/Murray on one
IR slot; early picks have to be consistent contributors). Under the old streamer assumption Davis ranked 22, Embiid 37. Replacement is half league-wide (the Nth best ppg, N = teams *
drafted roster size) and half positional (the ~2.4-per-team-th best at the scarcest position he's eligible for).
Center is the most top-heavy position (2027 projections: #1 C outscores any #1 by 7+ ppg, but the 20th C trails the
20th at every other spot) and PG the deepest, so this nudges bigs up and pure PGs down. ESPN's own points-league
tiers article (2026-27) reads the positions the same way.
"""
import argparse
import datetime
import json
import os
import sys
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import config  # noqa: E402
COOKIE_FILE = os.path.join(HERE, "..", "cookies.json")
POS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C"}  # eligibleSlots ids that are real positions
GP = "42"
OUT = ("OUT", "INJURY_RESERVE", "SUSPENSION")
FLEX = 0.5  # ppg bonus per extra position
SEASON_GAMES = 82
# seasons (ending year) spent outside the NBA: ESPN shows them as 0 games, same as a season lost to injury, so they're listed by hand (user 2026-10-22)
NOT_IN_NBA = {
    4397140: {2024},        # Kevin Porter Jr.: Olympiacos
    3059409: {2024, 2025},  # Nigel Hayes-Davis: Fenerbahce
    3136196: {2026},        # Trey Lyles: Real Madrid
}


def cookies():
    with open(COOKIE_FILE) as f:
        return json.load(f)


def get(url, filt=None):
    c = cookies()
    req = urllib.request.Request(url)
    req.add_header("Cookie", f"espn_s2={c['espn_s2']}; SWID={c['SWID']}")
    if filt:
        req.add_header("X-Fantasy-Filter", json.dumps(filt))
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def injury_returns():
    """ESPN player id -> estimated return date (YYYY-MM-DD) from ESPN's public NBA injury feed. {} if it's unreachable."""
    try:
        with urllib.request.urlopen("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries", timeout=20) as resp:
            feed = json.load(resp)
    except Exception as e:
        print(f"!! injury feed unavailable ({e}); using flat injury discounts")
        return {}
    out = {}
    for team in feed.get("injuries", []):
        for i in team.get("injuries", []):
            link = next((l["href"] for l in i["athlete"].get("links", []) if "/id/" in l.get("href", "")), "")
            m = re.search(r"/id/(\d+)", link)
            date = (i.get("details") or {}).get("returnDate")
            if m and date:
                out[int(m.group(1))] = date
    return out


def games_played(season):
    """ESPN player id -> games played that season (whole player pool, ESPN default league)."""
    filt = {"players": {"limit": 1000, "sortAppliedStatTotal": {"sortPriority": 1, "sortAsc": False, "value": f"00{season}"},
                        "filterStatsForSplitTypeIds": {"value": [0]}, "filterStatsForSourceIds": {"value": [0]}}}
    try:
        ps = get(f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}/segments/0/leaguedefaults/2"
                 "?view=kona_player_info", filt)["players"]
    except Exception as e:
        print(f"!! no {season} games history ({e}); using the seasons we have")
        return {}
    return {x["player"]["id"]: s["stats"].get(GP, 0) for x in ps for s in x["player"].get("stats", []) if s.get("id") == f"00{season}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", help="NBA league name or ID whose scoring to rank by (default: first NBA league in "
                    "config.json; with none configured, ESPN's default H2H Points scoring)")
    ap.add_argument("--teams", type=int, help="default: the league's team count, else 10")
    ap.add_argument("--season", type=int, help="ESPN names NBA seasons by the year they end (default: config.json season.nba)")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()
    args.season = args.season or config.season("nba")
    try:
        args.league = config.league("nba", args.league)["league_id"]
    except config.ConfigError:
        if args.league is not None:            # a name that isn't configured
            ap.error(f"no nba league named {args.league!r} in config.json")
        args.league = None                     # nothing configured: ESPN default scoring

    base = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{args.season}/segments/0/"
    base += f"leagues/{args.league}" if args.league else "leaguedefaults/2"
    league = get(base + "?view=mSettings")
    s = league["settings"]
    final = (league.get("status") or {}).get("finalScoringPeriod") or 167  # 167 = 2027-04-04, ESPN default
    teams = args.teams or s.get("size") or 10
    slots = s["rosterSettings"]["lineupSlotCounts"]
    roster_size = sum(v for k, v in slots.items() if k != "13")  # minus IR
    print(f"{s['name']}: {s['scoringSettings']['scoringType']}, {teams} teams, {roster_size} rounds")
    if s["scoringSettings"]["scoringType"] != "H2H_POINTS" and "POINTS" not in s["scoringSettings"]["scoringType"]:
        print("!! not a points league: these rankings value fantasy points, not categories")

    filt = {"players": {
        "limit": 400,
        "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
        "filterStatsForSplitTypeIds": {"value": [0]},
        "filterStatsForSourceIds": {"value": [0, 1]},
    }}
    players = get(base + "?view=kona_player_info", filt)["players"]
    pro = get(f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{args.season}?view=proTeamSchedules_wl")["settings"]["proTeams"]
    teams_by_id = {t["id"]: t["abbrev"] for t in pro}
    # fantasy-season game dates still to come, per pro team
    now = datetime.datetime.now().timestamp() * 1000
    game_dates = {t["id"]: sorted(g["date"] for gs in (t.get("proGamesByScoringPeriod") or {}).values() for g in gs
                                  if g["date"] >= now and g.get("scoringPeriodId", 0) <= final) for t in pro}
    returns = injury_returns()
    history = {yr: games_played(yr) for yr in (args.season - 3, args.season - 2)}  # last season comes with `players`

    cur, last = f"10{args.season}", f"00{args.season - 1}"
    rows, zeros = [], {}
    for x in players:
        p = x["player"]
        st = {t["id"]: t for t in p.get("stats", [])}
        proj = st.get(cur)
        if not proj or not proj.get("appliedTotal"):
            continue
        games = proj["stats"].get(GP, 0)
        if not games:
            continue
        proj_ppg = proj["appliedTotal"] / games
        prev = st.get(last)
        prev_games = prev["stats"].get(GP, 0) if prev else 0
        # track record: (games, weight) for each of the last three seasons he was in the league
        away = NOT_IN_NBA.get(p["id"], ())
        record = [(prev_games, 3) if prev else None] + [(history[yr][p["id"]], w) if p["id"] in history[yr] else None
                                                        for yr, w in ((args.season - 2, 2), (args.season - 3, 1))]
        record = [x if x and yr not in away else None for x, yr in zip(record, (args.season - 1, args.season - 2, args.season - 3))]
        zeros[p["id"]] = [yr for x, yr in zip(record, (args.season - 1, args.season - 2, args.season - 3)) if x and x[0] == 0]
        record = [(g, w * (0.5 if g == 0 else 1)) for g, w in filter(None, record)]
        track = sum(g * w for g, w in record) / sum(w for _, w in record) if record else None
        prev_ppg = prev["appliedTotal"] / prev_games if prev_games else None
        ppg = 0.7 * proj_ppg + 0.3 * prev_ppg if prev_games >= 20 else proj_ppg
        if track is not None:
            games = 0.5 * games + 0.5 * track
        inj = p.get("injuryStatus") or "ACTIVE"
        ret = returns.get(p["id"]) if inj != "ACTIVE" else None
        dates = game_dates.get(p["proTeamId"], [])
        if ret and dates:
            back = datetime.datetime.fromisoformat(ret).timestamp() * 1000
            left = sum(1 for d in dates if d >= back)
            missed = len(dates) - left
            games = min(games, left) if missed >= 20 else games * left / len(dates)
        elif inj in OUT:
            games *= 0.85
        elif inj == "DAY_TO_DAY":
            games = max(games - 2, 0)
        elig = [i for i in p["eligibleSlots"] if i in POS]
        rows.append({
            "id": p["id"], "name": p["fullName"], "team": teams_by_id.get(p["proTeamId"], "FA"), "pos": "/".join(POS[i] for i in elig), "slots": p["eligibleSlots"],
            "ppg": round(ppg, 2), "games": round(games), "proj": round(proj_ppg, 2),
            "last": round(prev_ppg, 2) if prev_ppg else None, "last_games": round(prev_games),
            "track": round(track) if track is not None else None,
            "espn": p.get("draftRanksByRankType", {}).get("STANDARD", {}).get("rank"),
            "adp": round(p.get("ownership", {}).get("averageDraftPosition", 0), 1),
            "inj": inj, "ret": ret,
        })

    by_ppg = sorted(rows, key=lambda r: -r["ppg"])
    repl = by_ppg[min(teams * roster_size, len(by_ppg) - 1)]["ppg"]
    pos_repl = {}
    for pos in POS.values():
        ps = [r for r in by_ppg if pos in r["pos"].split("/")]
        pos_repl[pos] = ps[min(teams * 2 + 4, len(ps) - 1)]["ppg"]
    for r in rows:
        rp = min((pos_repl[p] for p in r["pos"].split("/") if p), default=repl)
        flex = FLEX * (len(r["pos"].split("/")) - 1)
        # A missed game scores 0, not a streamer's points (user, 2026-10-21): with one IR slot, several regulars out at
        # once can't all be covered, and an early pick has to play. So: his season points minus a replacement player's
        # over a full season. More games always helps, so the old below-replacement special case is gone.
        r["base"] = (r["ppg"] + flex) * r["games"] - (0.5 * repl + 0.5 * rp) * SEASON_GAMES
        # The late-round view keeps the streamer assumption: by then a fragile star who scores a lot when he plays is the
        # upside pick (user, 2026-10-21). draftbot.js / the board blend vor -> vor_late linearly from round 1 to the last.
        per = r["ppg"] + flex - (0.5 * repl + 0.5 * rp)
        r["vor_late"] = round(per * r["games"] if per > 0 else per * SEASON_GAMES - 3 * max(SEASON_GAMES - r["games"], 0))
    # That's negative for most of the draft, so measure from the last drafted player instead (same order), which keeps
    # draftable values positive for tiers and the bot's 15%-of-value wait rule.
    last = sorted((r["base"] for r in rows), reverse=True)[min(teams * roster_size, len(rows)) - 1]
    for r in rows:
        r["vor"] = round(r.pop("base") - last)
    rows.sort(key=lambda r: -r["vor"])

    out = os.path.join(HERE, "rankings-nba.json")
    with open(out, "w") as f:
        json.dump(rows, f, indent=0)
    # league shape for the cheat sheet (rankings-nba.json stays a plain list: draftbot.js reads it)
    starters = sorted(int(k) for k, v in slots.items() if int(k) < 12 for _ in range(v))
    with open(os.path.join(HERE, "league-nba.json"), "w") as f:
        json.dump({"name": s["name"], "teams": teams, "rounds": roster_size, "starters": starters, "ir": slots.get("13", 1)}, f)
    print(f"replacement level {repl} ppg overall, by position {pos_repl}")
    for i, r in enumerate(rows[: args.top], 1):
        last_s = f"{r['last']:>5} in {r['last_games']:>2}g" if r["last"] else "   -       "
        print(f"{i:>3} {r['pos']:<9}{r['name']:<26}ppg {r['ppg']:>5} (proj {r['proj']:>5} x {r['games']:>2}g, last {last_s})"
              f"  vor {r['vor']:>5}  espn {r['espn'] or '-':>3} adp {r['adp']:>5}  {r['inj'] if r['inj'] != 'ACTIVE' else ''}")
    print(f"wrote {len(rows)} players to {out}")
    flag = [f"{i:>3} {r['name']}: {', '.join(map(str, zeros[r['id']]))}" for i, r in enumerate(rows[:100], 1) if zeros.get(r["id"])]
    if flag:
        print("0-game seasons in the top 100 (injury, or outside the NBA? add those to NOT_IN_NBA):")
        print("\n".join(flag))


if __name__ == "__main__":
    main()
