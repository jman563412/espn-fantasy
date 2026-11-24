"""Leagues, seasons and management rules, from config.json (gitignored).

Copy config.example.json to config.json and fill in your leagues. Anything you leave out falls back to DEFAULTS
below, so a config only needs what differs. Set ESPN_FANTASY_CONFIG to use a file elsewhere.

Scripts call `league(sport, which)` with their `--league` value: a configured league name, a league ID (configured or
not), or None for the first league of that sport. An unconfigured ID still works; it just has no default team.
"""
from __future__ import annotations

import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "season": {"nfl": 2026, "nba": 2027},   # ESPN names NBA seasons by the year they end
    "leagues": [],
    "rules": {
        # Propose every roster move and wait for an explicit OK before writing. False = act, then report.
        "confirm_writes": True,
        # Ask which draft wait setting (espn | adp | off) to use before starting a draft bot or board.
        "ask_draft_wait_before_drafting": True,
        # Team name `draft/draft.sh go` gives a public league it joins; {city} comes from the league's name.
        "go_team_name": "{city} Claude Squad",
        "nfl": {
            # Start/sit: two candidates within this many projected points -> start the steadier, established role.
            "close_call_points": 3,
            # A practice limitation or injury designation after the lineup is set reopens a close call.
            "injury_designation_reopens_close_call": True,
            # Adds/drops at these positions compare rest-of-season (ROS) projections, not just this week.
            "ros_positions_for_add_drop": ["RB", "WR", "TE"],
            # These are streamed on this week's projection/matchup...
            "streamable_positions": ["QB", "K", "D/ST"],
            # ...unless the rostered one's ROS beats the best free agent's by at least this many points per week left.
            "keep_streamable_if_ros_beats_fa_per_week": 2,
            # Start/sit between players already rostered never uses ROS.
            "start_sit_uses_ros": False,
            # Carry as many QBs as most of the OTHER teams in the league ("league_majority"), never more than max_qbs.
            "qb_count": "league_majority",
            "max_qbs": 2,
        },
        "nba": {
            # Lineups lock per player at tip-off: each day, start everyone who plays that day (nba.py lineup).
            "daily_lineups_start_everyone_playing": True,
        },
    },
}

LEAGUE_DEFAULTS = {"name": None, "sport": "nfl", "league_id": None, "team_id": None, "draft_wait": "espn"}


class ConfigError(Exception):
    pass


def _path() -> str:
    return os.environ.get("ESPN_FANTASY_CONFIG") or os.path.join(HERE, "config.json")


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    """DEFAULTS deep-merged with config.json (if it exists)."""
    try:
        with open(_path()) as f:
            user = json.load(f)
    except FileNotFoundError:
        user = {}
    return _merge(DEFAULTS, user)


def season(sport: str) -> int:
    return int(load()["season"][sport])


def rules(sport: str | None = None) -> dict:
    r = load()["rules"]
    return r[sport] if sport else r


def league(sport: str, which: str | int | None = None) -> dict:
    """The configured league for `sport`: by name, by ID, or the first one when `which` is None.
    An ID that isn't configured is returned as-is with no team."""
    leagues = [_merge(LEAGUE_DEFAULTS, lg) for lg in load()["leagues"]]
    mine = [lg for lg in leagues if lg["sport"] == sport]
    if which is None:
        if not mine:
            raise ConfigError(f"no {sport} league in config.json (copy config.example.json)")
        return mine[0]
    text = str(which)
    for lg in mine:
        if lg["name"] == text or str(lg["league_id"]) == text:
            return lg
    if text.isdigit():
        return {**LEAGUE_DEFAULTS, "sport": sport, "league_id": int(text)}
    raise ConfigError(f"no {sport} league named {text!r} in config.json")


def cli_league(parser, sport: str, which=None, team: int | None = None,
               need_team: bool = False) -> tuple[int, int | None]:
    """(league_id, team_id) for a script's --league/--team flags, with config defaults. Problems become a clean
    argparse error (exit 2) instead of a traceback."""
    try:
        lg = league(sport, which)
    except ConfigError as exc:
        parser.error(str(exc))
    team_id = team if team is not None else lg["team_id"]
    if need_team and team_id is None:
        parser.error(f"--team is required for league {lg['league_id']} (not in config.json)")
    return lg["league_id"], team_id
