# ESPN Fantasy API notes

ESPN's fantasy API is undocumented. These notes were worked out by observation (2026 NFL season, 2026-27 NBA
season). Everything needs the `espn_s2` and `SWID` cookies from a logged-in session (`node fetch_cookies.js`). The
scripts in this repo wrap all of it, so read this when extending them.

## Football (`ffl`)

- **Read host:** `lm-api-reads.fantasy.espn.com` — works reliably with cookies.
  `fantasy.espn.com/apis/v3/...` redirects/302s even with valid cookies; don't use it for reads.
- Base path: `/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{leagueId}`
- Useful `view` params (combine multiple `?view=X&view=Y`): `mStatus`, `mMatchupScore`,
  `mTeam`, `mRoster`, `mSettings`, `mTransactions2`, `kona_player_info` (free agents/waivers —
  requires the `X-Fantasy-Filter` header below).
- **The schedule's `totalPoints` field is NOT populated live.** Sum each starter's
  `player.stats[].appliedTotal` yourself, filtered to `statSourceId==0` (actual, not
  projected) and `scoringPeriodId==<week>`. Exclude `lineupSlotId` 20 (bench) and 21 (IR).
- Position map (`player.defaultPositionId`): 1 QB, 2 RB, 3 WR, 4 TE, 5 K, 16 D/ST.
- Roster slot map (`lineupSlotId`): 20 BE, 21 IR, 23 FLEX(RB/WR/TE); dedicated QB/RB/WR/TE/K/DST
  slots use their own IDs per `mSettings` → `settings.rosterSettings.lineupSlotCounts`.
- **Free agent / waiver search:** GET `kona_player_info` with header
  `X-Fantasy-Filter: {"players":{"filterStatus":{"value":["FREEAGENT","WAIVERS"]},"sortPercOwned":{"sortPriority":1,"sortAsc":false},"limit":60}}`
  — combine or split `FREEAGENT`/`WAIVERS` in `filterStatus` to check which one a specific
  player is in (add `"filterIds":{"value":[<playerId>]}` to check one player).
  **Any `limit` in the filter must be paired with a sort key** (`sortPercOwned` or
  `sortAppliedStatTotal`), or ESPN returns 400 `FILTER_LIMIT_MISSING_SORT` — this is what broke
  `filterIds`/`filterName` lookups. `fa_search.py` wraps all of this (position/name filters, sort by
  season pts / week projection / %owned, per-week actuals + targets/carries). Projection sort key is
  `"11{season}{week}"` and must also appear in `filterStatsForTopScoringPeriodIds.additionalValue`.
  A player whose NFL team has already played that week shows as `WAIVERS`, not `FREEAGENT`,
  even if unowned — an instant add won't work, you need a waiver claim (see below).

### Submitting a waiver claim (write API)

- **Write host:** `lm-api-writes.fantasy.espn.com` (different from the read host).
- `POST /apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{leagueId}/transactions/`
- Cookies same as reads. `Content-Type: application/json`.
- Body:
  ```json
  {
    "isLeagueManager": false,
    "teamId": <teamId>,
    "type": "WAIVER",
    "memberId": "{SWID}",
    "scoringPeriodId": <week>,
    "executionType": "EXECUTE",
    "items": [
      {"playerId": <addId>, "type": "ADD",  "fromTeamId": 0, "toTeamId": <teamId>,
       "fromLineupSlotId": -1, "toLineupSlotId": 20, "isKeeper": false},
      {"playerId": <dropId>, "type": "DROP", "fromTeamId": <teamId>, "toTeamId": 0,
       "fromLineupSlotId": 20, "toLineupSlotId": -1, "isKeeper": false}
    ]
  }
  ```
  `fromTeamId`/`toTeamId` are required (0 = free agent pool) — first attempt failed with
  `"Required field toTeamId missing from ADD TransactionItem"` until added. A successful
  submit returns `"status":"PENDING"` with a transaction id — it does **not** execute
  immediately, it queues for the next waiver run. Use `type: "FREEAGENT"` instead of
  `"WAIVER"` for a player who's actually in `FREEAGENT` status (should execute instantly,
  untested at the time).
  **`type: "FREEAGENT"` verified 2026-09-18** — executes instantly (`"status":"EXECUTED"` in the
  response). The ADD's `toLineupSlotId` can be a starting slot (used 16 = D/ST) to add straight
  into the lineup instead of the bench.
- **Lineup changes (verified 2026-09-18):** same endpoint, `"type": "ROSTER"`, items of
  `{"playerId", "type": "LINEUP", "fromTeamId": 0, "toTeamId": 0, "fromLineupSlotId": <cur>,
  "toLineupSlotId": <new>, "isKeeper": false}` — one item per player, swap both directions in
  the same transaction (e.g. TE 6→20 and 20→6). Executes instantly.
- **Trade proposal (verified 2026-09-23):** same endpoint, `"type": "TRADE_PROPOSAL"`, `"executionType": "EXECUTE"`,
  `"comment": ""`, `"expirationDate": <ms epoch>`, items `{"playerId", "type": "TRADE", "fromTeamId", "toTeamId"}`
  (no slot fields). Returns `PENDING` — it's sent to the other manager. `executionType: "VALIDATE"` → 400.
- **Drop only (verified 2026-10-20):** `type: "ROSTER"` with one DROP item (`fromTeamId`: us, `toTeamId`: 0, `fromLineupSlotId`: current slot, `toLineupSlotId`: -1).
  `type: "FREEAGENT"` with only a DROP → 409 "may only contain one item for MoveType: ADD". ~~Bench is unlimited~~ WRONG (2026-11-10): roster cap 16 applies to IR→BE moves; `isBenchUnlimited` is misleading.
- Verify with `view=mTransactions2`, filter `t["teamId"] == <teamId>` — shows `status`
  (`PENDING`/`EXECUTED`) and the `items` (ADD/DROP with playerIds).

## Basketball (`fba`)

Same hosts, paths and transaction format as football, with `games/fba` in place of `games/ffl`.

- ESPN game `fba`, seasons named by the year they end (2026-27 = `2027`). Default league = H2H Points (`leaguedefaults/2`): PTS 1, REB 1, AST 2, STL 4, BLK 4,
  3PM 1, FGM 2, FGA -1, FTM 1, FTA -1, TO -2. Slots: PG SG SF PF C G F UTIL×3, BE×3, IR×1, so the draft is 13 rounds. Waivers traditional.
- lineupSlotId: 0 PG, 1 SG, 2 SF, 3 PF, 4 C, 5 G, 6 F, 11 UTIL, **12 BE, 13 IR** (football uses 20/21). Players carry `eligibleSlots`.
- Scoring periods are **days** (1 = opening day; `finalScoringPeriod` in `mStatus` is the last day). `matchupPeriods` maps matchups to week units, not days.
  Units are Mon–Sun weeks (the first can be longer). `nba.py` derives this from `finalScoringPeriod`.
  Lineups lock per player at tip-off (INDIVIDUAL_GAME), so daily lineups matter: start everyone who plays that night.
- Stat split ids: `00{yr}` season actual, `01/02/03{yr}` last 7/15/30, `10{yr}` season projection; `appliedAverage` is per game and
  already scored with the league's settings. Raw stat 42 = GP. Pro schedule: `games/fba/seasons/{season}?view=proTeamSchedules_wl`.
- Draft: `draft/draft.sh nba bot <league> <team>` (reranks with that league's scoring first). `draft/draft.sh nba rank [league]` prints the board.
