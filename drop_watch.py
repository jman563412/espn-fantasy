#!/usr/bin/env python3
"""Flag players who just hit the waiver wire because another team had to clear room for a trade.

Usage:
    python3 drop_watch.py                    # print alerts for the last 48h
    python3 drop_watch.py --hours 72 --issue # also open a GitHub issue per alert (GitHub Actions)
    python3 drop_watch.py --sp 3 --sp 4 --hours 100000   # backtest specific scoring periods

Trade-forced drops live only in the TRADE_ACCEPT / TRADE_PROPOSAL items of mTransactions2; the
league activity feed never shows them (a trade that forces a drop shows the trade, but the drop is
missing from it). Any other drop of a widely rostered player (own% >= --min-own) is flagged too.
Players already re-added by someone are skipped. Dropped players typically sit on waivers for 24h, so an
hourly run is fast enough: the race is the waiver claim, not the alert.
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
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}"


def cookies():
    # GitHub Actions passes these as secrets; locally fall back to cookies.json
    if os.environ.get("ESPN_S2") and os.environ.get("ESPN_SWID"):
        return {"espn_s2": os.environ["ESPN_S2"], "SWID": os.environ["ESPN_SWID"]}
    with open(COOKIE_FILE) as f:
        return json.load(f)


def espn(league, query, filt=None):
    c = cookies()
    req = urllib.request.Request(BASE.format(season=SEASON, league=league) + query)
    req.add_header("Cookie", f"espn_s2={c['espn_s2']}; SWID={c['SWID']}")
    if filt:
        req.add_header("X-Fantasy-Filter", json.dumps(filt))
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def find_drops(league, sps, since_ms, min_own):
    """Return {(txid, playerId): drop} for trade drops and notable regular drops since since_ms."""
    found = {}
    for sp in sps:
        for t in espn(league, f"?view=mTransactions2&scoringPeriodId={sp}").get("transactions", []):
            from_trade = t["type"].startswith("TRADE")
            # TRADE_ACCEPT carries no status; anything else only counts once it has run (a pending claim's drop hasn't happened)
            if t.get("proposedDate", 0) < since_ms or (not from_trade and t.get("status") != "EXECUTED"):
                continue
            for it in t.get("items", []):
                if it["type"] == "DROP" and it["playerId"] > 0:
                    found[(it["fromTeamId"], it["playerId"])] = {
                        "pid": it["playerId"], "team": it["fromTeamId"], "trade": from_trade,
                        "when": t["proposedDate"], "txid": t["id"],
                    }
    if not found:
        return []
    pids = sorted({d["pid"] for d in found.values()})
    filt = {"players": {
        "filterIds": {"value": pids}, "limit": len(pids),
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        "filterStatsForTopScoringPeriodIds": {"value": 3, "additionalValue": [f"00{SEASON}"]},
    }}
    info = {x["player"]["id"]: x for x in espn(league, f"?view=kona_player_info&scoringPeriodId={max(sps)}", filt)["players"]}
    out = []
    for d in found.values():
        x = info.get(d["pid"])
        if not x:
            continue
        p = x["player"]
        d.update(name=p["fullName"], pos=POS.get(p["defaultPositionId"], "?"), status=x.get("status"),
                 own=p.get("ownership", {}).get("percentOwned", 0), inj=p.get("injuryStatus") or "")
        d["season"] = next((s["appliedTotal"] for s in p.get("stats", []) if s.get("id") == f"00{SEASON}"), 0)
        recent = sorted((s for s in p.get("stats", []) if s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 1),
                        key=lambda s: s["scoringPeriodId"])
        d["recent"] = [(s["scoringPeriodId"], round(s["appliedTotal"], 1)) for s in recent[-3:]]
        if d["status"] == "ONTEAM":
            continue  # someone already grabbed him
        if not d["trade"] and (d["own"] < min_own or d["pos"] in ("K", "DST")):
            continue
        out.append(d)
    return sorted(out, key=lambda d: d["when"])


def describe(d, teams):
    when = datetime.datetime.fromtimestamp(d["when"] / 1000).strftime("%a %m-%d %H:%M")
    why = "TRADE-FORCED drop" if d["trade"] else "drop"
    recent = ", ".join(f"wk{sp} {pts}" for sp, pts in d["recent"]) or "no games"
    inj = f" [{d['inj']}]" if d["inj"] not in ("", "ACTIVE") else ""
    title = f"{d['pos']} {d['name']} on {d['status']} ({why} by {teams.get(d['team'], d['team'])}) [{d['txid'][:8]}:{d['pid']}]"
    body = (f"{why} by **{teams.get(d['team'], d['team'])}** at {when} (UTC on GitHub runners).\n\n"
            f"- {d['pos']} {d['name']}{inj}, {d['own']:.0f}% owned, {d['season']:.1f} season pts\n"
            f"- Last 3 weeks: {recent}\n- Status now: **{d['status']}** (playerId {d['pid']})\n\n"
            f"Waivers here last 24h: put in a claim before they clear.")
    return title, body


def open_issue(title, body):
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    api = f"https://api.github.com/repos/{repo}/issues"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    req = urllib.request.Request(f"{api}?state=all&per_page=100&since={since}", headers=hdr)
    key = title[title.rindex("["):]
    with urllib.request.urlopen(req) as resp:
        if any(key in i["title"] for i in json.load(resp)):
            return False
    req = urllib.request.Request(api, data=json.dumps({"title": title, "body": body}).encode(), headers=hdr)
    urllib.request.urlopen(req).close()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", help="config.json league name or ID (default: first NFL league)")
    ap.add_argument("--hours", type=float, default=48)
    ap.add_argument("--min-own", type=float, default=40, help="flag non-trade drops at or above this own%%")
    ap.add_argument("--sp", type=int, action="append", help="scoring period(s) to scan (default: current and previous)")
    ap.add_argument("--issue", action="store_true", help="open a GitHub issue per new alert")
    args = ap.parse_args()
    args.league, _ = config.cli_league(ap, "nfl", args.league)

    league = espn(args.league, "?view=mTeam")
    teams = {t["id"]: t.get("name", "").strip() for t in league["teams"]}
    sps = args.sp or sorted({max(1, league["scoringPeriodId"] - 1), league["scoringPeriodId"]})
    since = (datetime.datetime.now().timestamp() - args.hours * 3600) * 1000
    drops = find_drops(args.league, sps, since, args.min_own)
    if not drops:
        print(f"No new drops worth a look (sp {sps}, last {args.hours:g}h).")
    for d in drops:
        title, body = describe(d, teams)
        print(title)
        if args.issue:
            print("  -> issue opened" if open_issue(title, body) else "  -> already reported")


if __name__ == "__main__":
    main()
