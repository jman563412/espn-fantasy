#!/usr/bin/env python3
"""Build the NBA draft-day cheat sheet (draft/cheatsheet-nba.html) from draft/rankings-nba.json.

Usage:
    python3 draft/rank_nba.py [--league ID]   # first: rankings with the league's scoring
    python3 draft/cheatsheet_nba.py [--wait espn|adp|off]   # then: the page (publish it as an artifact)

The page mirrors draftbot.js's NBA rules (okNba / defer) so its suggested pick matches what the bot would do.
Tiers break on a big value gap or a 15% drop from the tier's top.
"""
import argparse
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Research notes, 2026-10-18, injuries updated 10-24 (ESPN points tiers, Yahoo, NBC, Rotowire, CBS, Fantasy Alarm). Shown under the name.
NOTES = {
    "Nikola Jokic": "Top fantasy scorer six seasons running. Own tier.",
    "Victor Wembanyama": "The only other must-reach center.",
    "Domantas Sabonis": "Last center before the position opens up. Played 19 games last season.",
    "Brandon Ingram": "Partial Achilles tear repaired in May. ESPN estimates a return around Nov 8.",
    "Jimmy Butler III": "Torn right ACL. Could return between January and February. An IR stash.",
    "Joel Embiid": "57 of 164 games over two seasons. Fine at his ADP, not before.",
    "Kyrie Irving": "Back from the ACL tear (~19 months). Missed all of last season.",
    "Tyrese Haliburton": "Back from the Achilles tear. Missed all of last season.",
    "Jayson Tatum": "Back from the Achilles tear. 16 games last season.",
    "Anthony Davis": "Long injury history. 20 games last season.",
    "Walker Kessler": "Breakout pick (CBS model). 5 games last season before shoulder surgery.",
    "Kel'el Ware": "Common sleeper pick. Strong per-36 production.",
    "Immanuel Quickley": "Late-round points value (LeagueWinner).",
    "Brandin Podziemski": "Late-round points value (LeagueWinner).",
    "Jaime Jaquez Jr.": "Late-round points value (LeagueWinner).",
    "Bam Adebayo": "Faded by NBC and Yahoo (ESPN ranks him much higher than Rotoworld).",
    "Derik Queen": "ESPN ranks him far above Rotoworld. Missi has been starting at C.",
    "Ausar Thompson": "Low scorer. ESPN ranks him well above Rotoworld.",
    "Cooper Flagg": "Mild left shoulder sprain (10-24). Re-evaluated next week, ESPN estimates ~Nov 2. Shooting percentages matter less in points leagues.",
    "Paolo Banchero": "Right high-ankle sprain (10-24). Out four to six weeks, ESPN estimates ~Nov 22.",
    "Jalen Duren": "Right hamstring strain. Out Sunday, ESPN estimates ~Oct 27.",
    "Cade Cunningham": "High floor in points formats (Yahoo).",
    "Nic Claxton": "Hamstring. Out, re-evaluated in two weeks.",
    "AJ Dybantsa": "Sprained ankle in the preseason finale. Short-term.",
    "Bradley Beal": "Won't be ready for the start of the season.",
    "Coby White": "Calf strain. Out for the opener.",
}


def tiers(rows):
    """New tier when the value gap to the previous player is big, or when value falls 15% below the tier's top."""
    out, tier, top = [], 1, rows[0]["vor"]
    for i, r in enumerate(rows):
        if i and r["vor"] > 0:
            prev = rows[i - 1]["vor"]
            if prev - r["vor"] >= max(40, 0.08 * prev) or (r["vor"] < 0.85 * top and top - r["vor"] >= 60):
                tier, top = tier + 1, r["vor"]
        elif i and rows[i - 1]["vor"] > 0:
            tier += 1  # everyone at or below replacement: one depth tier
        out.append(tier)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", choices=["espn", "adp", "off"], default="espn",
                    help="the board's default wait rule (same as draftbot.js --wait); viewers can still change it")
    args = ap.parse_args()
    rows = json.load(open(os.path.join(HERE, "rankings-nba.json"), encoding="utf-8"))
    t = tiers(rows)
    players = [{
        "id": r["id"], "n": r["name"], "t": r.get("team", ""), "p": r["pos"], "s": [x for x in r["slots"] if x < 12],
        "ppg": r["ppg"], "g": r["games"], "v": r["vor"], "vl": r.get("vor_late", r["vor"]), "e": r["espn"], "a": r["adp"],
        "i": r["inj"], "r": r.get("ret"), "tr": r.get("track"), "tier": t[i], "note": NOTES.get(r["name"], ""),
    } for i, r in enumerate(rows)]
    # league shape written by rank_nba.py; ESPN default 10-team points league if it's missing
    try:
        lg = json.load(open(os.path.join(HERE, "league-nba.json"), encoding="utf-8"))
    except FileNotFoundError:
        lg = {"teams": 10, "rounds": 13, "starters": [0, 1, 2, 3, 4, 5, 6, 11, 11, 11]}
    now = datetime.datetime.now()
    data = {"players": players, "teams": lg["teams"], "rounds": lg["rounds"], "starters": lg["starters"], "ir": lg.get("ir", 1), "wait": args.wait, "name": lg.get("name"),
            "generated": now.strftime("%b ") + str(now.day) + now.strftime(", ") + now.strftime("%I:%M %p").lstrip("0")}
    tpl = open(os.path.join(HERE, "cheatsheet_nba.template.html"), encoding="utf-8").read()
    out = os.path.join(HERE, "cheatsheet-nba.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(tpl.replace("__DATA__", json.dumps(data, separators=(",", ":"))))
    print(f"wrote {out} ({len(players)} players, {max(t)} tiers, {lg['teams']} teams x {lg['rounds']} rounds)")


if __name__ == "__main__":
    main()
