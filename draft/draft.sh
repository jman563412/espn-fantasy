#!/bin/bash
# Draft tooling entrypoint. Sets up the local NSS libs (WSL lacks libnss3/libnspr4; unpacked
# without sudo into ~/.local/lib/espn-nss) and the WSLg display (only used with --headed).
#
#   draft/draft.sh rank [week]                 # rebuild draft/rankings.json (default: current week)
#   (go / bot / mock rebuild the rankings automatically first, so they reflect the latest stats + news)
#   draft/draft.sh selftest <league> <team>    # open the room, find best available's button, exit
#   draft/draft.sh bot <league> <team>         # run the bot in the background (pid in draft/bot.pid)
#   draft/draft.sh go                          # join a public league, name the team (config rules.go_team_name), start the bot
#   draft/draft.sh mockrooms                   # list upcoming snake mock rooms (rehearsal)
#   draft/draft.sh mock <roomLeagueId> [teams] # join that mock room and draft it (bot in background)
#   (add --headed to selftest/bot to watch it in a window; default is headless)
#   (add --wait espn|adp|off to bot/mock: when to pass on our top player because he'll last to our next pick.
#    espn = by ESPN's rank, for casual leagues (default); adp = by ADP, for experienced leagues; off = never wait.
#    For go: DRAFT_WAIT=adp draft/draft.sh go)
#   draft/draft.sh stop                        # stop the bot (by pid -- never pkill -f, it matches your own shell)
#   NBA: prefix any command with nba, e.g. `draft/draft.sh nba bot <league> <team>`, `draft/draft.sh nba mockrooms`,
#   `draft/draft.sh nba mock <roomId>`, `draft/draft.sh nba rank [league]` (uses rank_nba.py + rankings-nba.json; no `go`)
set -euo pipefail
SPORT=nfl
if [ "${1:-}" = nba ]; then SPORT=nba; shift; fi
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
export LD_LIBRARY_PATH="$HOME/.local/lib/espn-nss:$HOME/.local/lib/espn-nss/nss"
export DISPLAY="${DISPLAY:-:0}"
mkdir -p "$HERE/logs"
cd "$ROOT"
bot_alive() { [ -s "$HERE/bot.pid" ] && kill -0 "$(cat "$HERE/bot.pid")" 2>/dev/null; }
if [ "${1:-}" != stop ]; then
  if bot_alive; then echo "bot already running (pid $(cat "$HERE/bot.pid")) -- it holds chrome-profile; draft/draft.sh stop first"; exit 1; fi
  rm -f chrome-profile/Singleton*   # stale locks from a killed Chrome block launch
fi

rerank() {  # optional arg: the league being drafted, so its scoring, size and lineup slots are used
  echo "rebuilding rankings..."
  if [ $SPORT = nba ]; then python3 "$HERE/rank_nba.py" ${1:+--league "$1"} --top 5
  else python3 "$HERE/rank.py" ${1:+--league "$1"} ${RANK_WEEK:+--week "$RANK_WEEK"} --top 5; fi
}

start_bot() {
  local log="$HERE/logs/draft-$1.log"
  nohup node "$HERE/draftbot.js" --sport $SPORT --league "$1" --team "$2" "${@:3}" > "$log" 2>&1 &
  echo $! > "$HERE/bot.pid"
  echo "bot pid $(cat "$HERE/bot.pid"), log: $log"
}

case "${1:-}" in
  rank)     if [ $SPORT = nba ]; then python3 "$HERE/rank_nba.py" ${2:+--league "$2"}; else python3 "$HERE/rank.py" ${2:+--week "$2"}; fi ;;
  selftest) node "$HERE/draftbot.js" --sport $SPORT --league "$2" --team "$3" --selftest "${@:4}" ;;
  bot)      rerank "$2"; start_bot "$2" "$3" "${@:4}" ;;
  go)
    [ $SPORT = nfl ] || { echo "go joins public NFL leagues only; for NBA use: draft/draft.sh nba bot <league> <team>"; exit 1; }
    out="$(node "$HERE/join.js" "${@:2}" | tee /dev/stderr | tail -1)"
    read -r tag league team <<< "$out"
    [ "$tag" = JOINED ] || exit 1
    rerank "$league"   # after joining, so the rankings use this league's scoring, size and lineup
    # name the team from config rules.go_team_name, e.g. "{city} Claude Squad" -> "Houston Claude Squad"
    # (public league names look like "Houston Pro H2H Points PPR League"); abbreviation = initials, max 4
    read -r name abbr <<< "$(python3 - "$league" <<'PY'
import json, sys, urllib.request
sys.path.insert(0, ".")
import config
c = json.load(open("cookies.json"))
season = config.season("nfl")
req = urllib.request.Request(f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{sys.argv[1]}?view=mSettings")
req.add_header("Cookie", f"espn_s2={c['espn_s2']}; SWID={c['SWID']}")
league_name = json.load(urllib.request.urlopen(req))["settings"]["name"]
city = league_name.split(" Pro ")[0].split(" H2H")[0].strip() or "New"
name = config.rules()["go_team_name"].format(city=city)
abbr = "".join(w[0] for w in name.split() if w[0].isalnum()).upper()[:4] or "TEAM"
print(name.replace(" ", "\u00a0"), abbr)
PY
)"
    name="${name//$'\u00a0'/ }"
    node "$HERE/rename.js" "$league" "$team" "$name" "$abbr" || echo "rename failed (non-fatal)"
    start_bot "$league" "$team"
    ;;
  mockrooms) node "$HERE/mock_rooms.js" $([ $SPORT = nba ] && echo --nba) 2>&1 | grep -i 'snake' ;;
  mock)
    rerank   # through your default league: a mock room's league may not be readable for the player pool
    log="$HERE/logs/mock-$2.log"
    teams="${3:-10}"; extra=("${@:4}")
    case "$teams" in --*) teams=10; extra=("${@:3}") ;; esac   # `mock <room> --wait adp` without a team count
    nohup node "$HERE/draftbot.js" --sport $SPORT --mock --league "$2" --teams "$teams" "${extra[@]}" > "$log" 2>&1 &
    echo $! > "$HERE/bot.pid"; echo "bot pid $(cat "$HERE/bot.pid"), log: $log" ;;
  stop)     kill "$(cat "$HERE/bot.pid")" && rm -f "$HERE/bot.pid" ;;
  *)        sed -n '2,17p' "$0"; exit 1 ;;
esac
