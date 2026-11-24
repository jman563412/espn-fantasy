// Live-draft bot for ESPN snake drafts. Sits in the draft room and, when we're on the clock,
// clicks DRAFT on the best available player from draft/rankings.json (see rank.py).
//
// Usage (via draft.sh, which sets LD_LIBRARY_PATH/DISPLAY):
//   node draft/draftbot.js --league <leagueId> --team <teamId> [--selftest]
//   node draft/draftbot.js --sport nba --league <id> --team <id>   # NBA: rankings-nba.json, season 2027
//
// Lessons from the 2026-09-27 draft (don't regress these):
// - Draft room URL needs &memberId={SWID}, otherwise "Page not found".
// - The draft can start before draftSettings.date, and mDraftDetail picks stay playerId -1
//   until the draft ends -- everything live has to be read from the page.
// - Click by button[data-player-id] (= ESPN player id = rankings id). The button reads QUEUE normally
//   and DRAFT when we're on the clock -- but right as we go on the clock every row button shows a
//   ':01' countdown and only ESPN's "your autopick would be X" banner button reads DRAFT. Count all
//   player buttons regardless of label, and wait for our target's own button to read DRAFT. Name-based row matching drafted the wrong player repeatedly.
// - The player grid is a virtualized FixedDataTable: only rows that fit the window exist in the DOM,
//   and it ignores scrollTop (wheel events only). Headless runs use a 9000px-tall window so ~200
//   players are on the page; the "Player Name" search box surfaces anyone deeper.
// - The pre-draft pickOrder is NOT final (ESPN reshuffles at draft start). Detect our turn from the page's
//   "You are on the clock" text, never from precomputed pick numbers.
// - Only one live session per account: opening the room elsewhere kicks this one (Reconnect popup,
//   handled in the loop). Don't run --selftest while a bot is drafting.
// - Needs the logged-in persistent profile (chrome-profile/). Headless works fine (verified 2026-09-27);
//   the room just shows "Loading your draft" until the draft actually opens, so keep reloading.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const NBA = arg('sport', 'nfl') === 'nba';
const LEAGUE = +arg('league'), TEAM = +arg('team'), SEASON = +arg('season', NBA ? 2027 : 2026);
const GAME = NBA ? 'fba' : 'ffl', SITE = NBA ? 'basketball' : 'football';
const SELFTEST = process.argv.includes('--selftest');
const HEADED = process.argv.includes('--headed');
const MOCK = process.argv.includes('--mock'); // ESPN mock room: no league API, enter via waiting room
// Wait-a-round survival estimate: espn = ESPN rank (casual leagues: autopick and the room's default sort follow it),
// adp = ADP from all ESPN leagues (leagues of experienced drafters), off = never wait, always take our top player.
const WAIT = arg('wait', process.env.DRAFT_WAIT || 'espn');
if (!['espn', 'adp', 'off'].includes(WAIT)) { console.error(`--wait must be espn, adp or off (got ${WAIT})`); process.exit(1); }
const PROFILE = arg('profile', path.join(ROOT, 'chrome-profile'));
const LOGDIR = path.join(__dirname, 'logs');
fs.mkdirSync(LOGDIR, { recursive: true });
const c = JSON.parse(fs.readFileSync(path.join(ROOT, 'cookies.json')));
const rankings = JSON.parse(fs.readFileSync(path.join(__dirname, NBA ? 'rankings-nba.json' : 'rankings.json')));
const CAP = { QB: 2, RB: 6, WR: 6, TE: 2, K: 1, DST: 1 };
// XPath predicate: button whose text is QUEUE or DRAFT, any case
const UP = "translate(normalize-space(.),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ')";
const QD = `${UP}='QUEUE' or ${UP}='DRAFT'`;
const log = m => console.log(new Date().toTimeString().slice(0, 8), m);

// NBA starting slots (lineupSlotId): PG SG SF PF C G F UTIL x3 (ESPN default; replaced from mSettings when we have the league)
let NBA_STARTERS = [0, 1, 2, 3, 4, 5, 6, 11, 11, 11];
// Depth target: every non-UTIL starting slot plus a second PG/SG/SF/PF/C. Lineups are daily and a player plays
// ~3.5 nights a week, so a roster that can only fill each slot once leaves C/F holes most nights.
const nbaDepth = () => [...NBA_STARTERS.filter(s => s !== 11), 0, 1, 2, 3, 4];
const NBA_OUT = ['OUT', 'INJURY_RESERVE', 'SUSPENSION'];
let NBA_IR = 1; // IR slots (lineupSlotId 13), from mSettings
// How many of `slots` `players` can fill at once (bipartite matching on eligibleSlots).
function filled(players, slots = NBA_STARTERS) {
  const owner = new Array(slots.length).fill(-1);
  const tryPlace = (i, seen) => {
    for (let s = 0; s < slots.length; s++) {
      if (seen[s] || !players[i].slots.includes(slots[s])) continue;
      seen[s] = true;
      if (owner[s] < 0 || tryPlace(owner[s], seen)) { owner[s] = i; return true; }
    }
    return false;
  };
  return players.reduce((n, _, i) => n + (tryPlace(i, []) ? 1 : 0), 0);
}
// NBA: take the best value unless it would leave the depth target (usually C or F) impossible to fill with
// the picks we have left. Only one OUT player: there is one IR slot, and a second would eat a bench spot.
// (Mock 2026-10-18 without these rules: 7 guards, 2 centers, and both Ingram and Butler OUT.)
// Injury cap: at most IR slots + 1 players who are fragile (missed 40%+ of games over three seasons) or a long-term
// stash (OUT 30+ days, or no return date): one sits in IR, one on the bench, and nobody has to be dropped to make room
// (user, 2026-10-21: the board gave them Davis/Embiid/Jerome/Murray and pushed Butler on top).
const STASH_DAYS = 30, FRAGILE_TRACK = 50;
const isStash = r => NBA_OUT.includes(r.inj) && (!r.ret || Date.parse(r.ret) - Date.now() > STASH_DAYS * 864e5);
const isFragile = r => !isStash(r) && r.track != null && r.track < FRAGILE_TRACK;
// NBA value at a given round: round 1 counts a missed game as 0 (vor), the last round as covered by a streamer (vor_late),
// linear in between, so the injury penalty eases off as the draft goes on (user, 2026-10-21).
const nbaVal = (r, round, rounds) => { const w = rounds > 1 ? Math.max(0, (rounds - round) / (rounds - 1)) : 1; return w * r.vor + (1 - w) * (r.vor_late ?? r.vor); };
function okNba(r, mine, round, rounds) {
  const left = rounds - round, team = [...mine, r], depth = nbaDepth();
  if (NBA_OUT.includes(r.inj) && mine.some(m => NBA_OUT.includes(m.inj))) return false;
  if (team.filter(m => isStash(m) || isFragile(m)).length > NBA_IR + 1) return false;
  return filled(team) + left >= NBA_STARTERS.length && filled(team, depth) + left >= depth.length;
}
// Both sports: reorder candidates so we don't reach. If ESPN's rank says the best one will likely still be there at our
// next pick, take the best player within 15% of his value who probably won't be. Casual drafters and autopick
// follow ESPN's own rank (the room's default sort), so it predicts survival better than ADP from all leagues
// (8-team sim 2026-10-21 vs ESPN-rank drafters: +50..140 season starter pts over ADP). ADP is the fallback.
// Only wait if there is a next pick, and only if he'd still pass the position rules after this pick (`fits`): mock
// 2026-10-21 waited on Jaren Jackson Jr. for the very next pick, then the depth rule ruled him out there.
const survival = c => WAIT === 'adp' ? c.adp || c.espn : c.espn || c.adp;
const nextPick = (cur, teams) => cur + 2 * (teams - 1 - (cur - 1) % teams) + 1;
function defer(cands, cur, teams, picks, fits) {
  const next = nextPick(cur, teams), best = cands[0];
  if (WAIT === 'off' || !best || next > picks || !survival(best) || survival(best) - 4 <= next) return cands;
  const now = cands.find(c => survival(c) && survival(c) + 4 <= next && (c.val ?? c.vor) >= 0.85 * (best.val ?? best.vor) && fits(best, c, Math.ceil(next / teams)));
  return now ? [now, ...cands.filter(c => c !== now)] : cands;
}

// Starters-first constraints: no K/DST until the last two rounds, one QB/TE until late.
function ok(r, cnt, round, rounds) {
  const n = pos => cnt[pos] || 0;
  if (n(r.pos) >= CAP[r.pos]) return false;
  if ((r.pos === 'K' || r.pos === 'DST') && round < rounds - 1) return false;
  if (round === rounds - 1 && n('DST') === 0 && r.pos !== 'DST') return false;
  if (round === rounds && n('K') === 0 && r.pos !== 'K') return false;
  if (r.pos === 'QB' && n('QB') >= 1 && round < 10) return false;
  if (r.pos === 'TE' && n('TE') >= 1 && round < 11) return false;
  if (round >= 11 && round < rounds - 1 && n('QB') === 0 && r.pos !== 'QB') return false;
  if (round >= 12 && round < rounds - 1 && n('TE') === 0 && r.pos !== 'TE') return false;
  return true;
}

async function myPickNumbers() {
  const r = await fetch(`https://lm-api-reads.fantasy.espn.com/apis/v3/games/${GAME}/seasons/${SEASON}/segments/0/leagues/${LEAGUE}?view=mDraftDetail&view=mSettings`,
    { headers: { Cookie: `espn_s2=${c.espn_s2}; SWID=${c.SWID}` } });
  const d = await r.json();
  if (NBA) NBA_STARTERS = Object.entries(d.settings.rosterSettings.lineupSlotCounts).filter(([k]) => +k < 12).flatMap(([k, n]) => Array(n).fill(+k));
  if (NBA) NBA_IR = d.settings.rosterSettings.lineupSlotCounts['13'] ?? NBA_IR;
  const picks = (d.draftDetail && d.draftDetail.picks) || [];
  if (picks.length) return picks.filter(p => p.teamId === TEAM).map(p => p.overallPickNumber);
  // no pick list yet: derive from pick order (snake)
  const order = d.settings.draftSettings.pickOrder, n = order.length, slot = order.indexOf(TEAM) + 1;
  const rounds = Object.values(d.settings.rosterSettings.lineupSlotCounts).reduce((a, b) => a + b, 0) - 1; // minus IR
  const out = []; for (let rd = 1; rd <= rounds; rd++) out.push((rd - 1) * n + (rd % 2 ? slot : n + 1 - slot));
  return out;
}

// Roster counts from the "Roster Limits" footer; innerText has no separators: "QB1/4RB4/8WR6/8TE1/3K0/3D/ST0/3".
function rosterCounts(txt) {
  const tail = txt.slice(txt.lastIndexOf('Roster Limits'));
  const cnt = {};
  for (const [pos, key] of [['QB', 'QB'], ['RB', 'RB'], ['WR', 'WR'], ['TE', 'TE'], ['K', 'K'], ['DST', 'D/ST']]) {
    const m = tail.match(new RegExp('(?<![A-Za-z])' + key.replace('/', '\\/') + '\\s*(\\d+)\\s*\\/\\s*\\d+'));
    cnt[pos] = m ? +m[1] : 0;
  }
  return cnt;
}

(async () => {
  const mine = MOCK ? null : await myPickNumbers();
  const rounds = MOCK ? +arg('rounds', NBA ? 13 : 16) : mine.length;
  const teams = MOCK ? +arg('teams', 10) : mine[1] ? (mine[0] + mine[1] - 1) / 2 : 10; // snake: 1st + 2nd pick = 2n+1
  log((MOCK ? `mock room ${LEAGUE}, ${teams} teams` : `league ${LEAGUE} team ${TEAM}: my picks ${mine.join(',')}`) + `; wait rule: ${WAIT}`);

  const ctx = await chromium.launchPersistentContext(PROFILE, { headless: !HEADED, viewport: { width: 1400, height: HEADED ? 1000 : 9000 } });
  const p = ctx.pages()[0] || await ctx.newPage();
  let url = MOCK ? `https://fantasy.espn.com/${SITE}/waitingroom?leagueId=${LEAGUE}` : `https://fantasy.espn.com/${SITE}/draft?leagueId=${LEAGUE}&seasonId=${SEASON}&teamId=${TEAM}&memberId=${encodeURIComponent(c.SWID)}`;
  // The player grid is a virtualized FixedDataTable: it renders only the rows that fit the window
  // (18 at 1000px, ~118 at 5000px) and ignores scrollTop. So we run headless with a very tall window.
  const boardIds = () => p.evaluate(() => [...new Set([...document.querySelectorAll('button[data-player-id]')].map(b => +b.dataset.playerId))]).catch(() => []);
  const linkNames = () => p.evaluate(() => [...document.querySelectorAll('a')].map(a => a.innerText.trim()).filter(Boolean)).catch(() => []);
  // Every row's QUEUE/DRAFT button carries data-player-id (= ESPN player id = rankings id), so we
  // address the button by id -- no name matching. Returns the button's label, or null if absent.
  // (Name-based row lookup drafted the wrong player several times on 2026-09-27.)
  const search = async q => { const box = p.getByPlaceholder('Player Name'); await box.fill(q).catch(() => {}); await p.waitForTimeout(q ? 700 : 300); };
  const rowAction = async (id, click, name) => {
    let label = await rowActionRaw(id, click);
    if (label === null && name) { await search(name.replace(' D/ST', '')); label = await rowActionRaw(id, click); }
    return label;
  };
  const rowActionRaw = (id, click) => p.evaluate(([id, click]) => {
    const bs = [...document.querySelectorAll(`button[data-player-id="${id}"]`)];
    if (!bs.length) return null;
    const d = bs.find(b => b.innerText.trim().toUpperCase() === 'DRAFT');
    if (d && click) d.click();
    return d ? 'DRAFT' : bs[0].innerText.trim().toUpperCase();
  }, [id, click]);

  // The board re-renders right after other teams' picks, and a single read has come back with 1 player
  // (took Pollard over Parker Washington, mock 2026-09-27). Union two reads; retry while it looks thin.
  const stableIds = async () => {
    const ids = new Set();
    for (let t = 0; t < 4 && ids.size < 30; t++) { (await boardIds()).forEach(i => ids.add(i)); await p.waitForTimeout(400); (await boardIds()).forEach(i => ids.add(i)); }
    return ids;
  };
  const myPlayers = []; // NBA: rankings rows we drafted (for slot feasibility)
  const available = async (cnt, round) => {
    const ids = await stableIds(); lastBoard = ids.size;
    if (!NBA) return rankings.filter(r => ids.has(r.id) && ok(r, cnt, round, rounds));
    return rankings.filter(r => ids.has(r.id) && okNba(r, myPlayers, round, rounds))
      .map(r => Object.assign(r, { val: nbaVal(r, round, rounds) })).sort((a, b) => b.val - a.val);
  };

  // load the room; it says "Loading your draft" until the draft opens, so keep reloading (up to ~60 min)
  for (let attempt = 1; ; attempt++) {
    await p.goto(attempt > 1 && /\/draft\?/.test(p.url()) ? p.url() : url, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(e => log('goto: ' + e.message));
    let ready = false;
    for (let t = 0; t < 45 && !ready; t++) { await p.waitForTimeout(1000);
      if (MOCK && /waitingroom/.test(p.url())) { // once joined, our team id is in the Edit Team Settings link
        const tid = await p.evaluate((lid) => { const a = [...document.querySelectorAll('a')].find(a => /teamId=\d+/.test(a.href) && new URL(a.href).searchParams.get('leagueId') === String(lid)); return a ? new URL(a.href).searchParams.get('teamId') : null; }, LEAGUE).catch(() => null);
        if (tid) { url = `https://fantasy.espn.com/${SITE}/draft?leagueId=${LEAGUE}&seasonId=${SEASON}&teamId=${tid}&memberId=${encodeURIComponent(c.SWID)}`; log(`mock team ${tid}; switching to draft room`); await p.goto(url, { waitUntil: 'domcontentloaded' }).catch(() => {}); continue; }
      }
      if (MOCK) { const j = p.locator('button, a', { hasText: /^\s*(join( this league| draft)?|enter( the)?( draft)?( room)?|go to( the)? draft( room)?|launch draft( room)?)\s*$/i }).first(); if (await j.isVisible().catch(() => false)) { log('waiting room: clicking ' + (await j.innerText()).trim()); await j.click().catch(() => {}); } } ready = await p.evaluate(() => [...document.querySelectorAll('button')].some(b => /^(queue|draft)$/i.test(b.innerText.trim()))).catch(() => false); }
    if (ready) break;
    log(`room not ready (attempt ${attempt}), reloading`);
    if (attempt % 5 === 1) await p.screenshot({ clip: { x: 0, y: 0, width: 1400, height: 1000 }, path: path.join(LOGDIR, `load-wait.png`) });
    if (attempt >= 60) { log('giving up; ESPN autopick will cover'); process.exit(1); }
  }
  for (let i = 0; i < 20; i++) { const rm = p.locator('button', { hasText: /^\s*remove\s*$/i }).first(); if (!(await rm.isVisible().catch(() => false))) break; await rm.click(); await p.waitForTimeout(300); log('removed a queued player'); }
  const top = (await available({}, NBA ? 1 : 5))[0] || (!NBA && (await available({ QB: 0 }, rounds))[0]);
  if (!top) log('SELFTEST: no ranked player visible (late draft?)'); else {
  const label = await rowAction(top.id, false);
  log(`SELFTEST best available: ${top.name} (${top.pos}); its row button: ${label || '!! NO UNIQUE ROW'}; players on board: ${lastBoard}`); }
  await p.screenshot({ clip: { x: 0, y: 0, width: 1400, height: 1000 }, path: path.join(LOGDIR, 'selftest.png') });
  if (SELFTEST) { await ctx.close(); return; }

  const done = new Set();
  while (done.size < rounds) {
    await p.waitForTimeout(700);
    // a second session on this account (e.g. a selftest) kicks this one; ESPN shows a Reconnect popup
    const rc = p.locator('button', { hasText: /reconnect/i }).first();
    if (await rc.isVisible().catch(() => false)) { log('kicked (Reconnect popup); reloading the room'); await p.goto(url, { waitUntil: 'domcontentloaded' }).catch(() => {}); await p.waitForTimeout(3000); continue; }
    let txt; try { txt = await p.evaluate(() => document.body.innerText); } catch { continue; }
    const m = txt.match(/ON THE CLOCK:\s*PICK\s*(\d+)/i);
    if (!m) { if (/draft (is )?complete/i.test(txt)) break; continue; }
    const cur = +m[1];
    // Trust only the page. Pick numbers derived before the draft can be wrong: ESPN reshuffles the order at draft
    // start (Indianapolis 2026-09-27: pre-draft order said slot 3, actual was 10).
    const ours = /You are on the clock/i.test(txt);
    if (!ours || done.has(cur)) continue;
    done.add(cur);
    if (p._reloadedFor && p._reloadedFor !== cur) p._reloadedFor = 0;
    await p.waitForTimeout(1200); // let the list settle after the previous pick
    txt = await p.evaluate(() => document.body.innerText);
    const round = Math.ceil(cur / teams), cnt = rosterCounts(txt);
    let cands = await available(cnt, round);
    if (lastBoard < 5) { // diagnose thin board reads: what is on screen?
      await p.screenshot({ clip: { x: 0, y: 0, width: 1400, height: 1000 }, path: path.join(LOGDIR, `thin-${cur}.png`) });
      const info = await p.evaluate(() => {
        const qd = [...document.querySelectorAll('button')].filter(b => /^(queue|draft)$/i.test(b.innerText.trim()));
        const pid = document.querySelectorAll('button[data-player-id]').length;
        const grid = document.querySelectorAll('[role="gridcell"]').length;
        const labels = [...new Set([...document.querySelectorAll('button[data-player-id]')].map(b => b.innerText.trim()))].slice(0, 8);
        return { qdButtons: qd.length, pidButtons: pid, gridcells: grid, pidLabels: labels, search: (document.querySelector('input[placeholder="Player Name"]') || {}).value };
      }).catch(e => e.message);
      log(`  THIN BOARD: ${JSON.stringify(info)}`);
    }
    if (!cands.length) { // forced position not visible in the loaded list: relax to caps only
      const ids = await stableIds();
      cands = rankings.filter(r => ids.has(r.id) && (NBA || ((cnt[r.pos] || 0) < CAP[r.pos] && !['K', 'DST'].includes(r.pos))));
      if (NBA) cands = cands.map(r => Object.assign(r, { val: nbaVal(r, round, rounds) })).sort((a, b) => b.val - a.val);
      log(`  no candidate under position rules, relaxed; footer: ${JSON.stringify(txt.slice(txt.lastIndexOf('Roster Limits'), txt.lastIndexOf('Roster Limits') + 80))}`);
    }
    const best = cands[0];
    // candidates already passed the position rules; `fits` asks whether `b` still would at our next pick if we take `n` now
    const fits = (b, n, rd) => NBA ? okNba(b, [...myPlayers, n], rd, rounds) : ok(b, { ...cnt, [n.pos]: (cnt[n.pos] || 0) + 1 }, rd, rounds);
    cands = defer(cands, cur, teams, teams * rounds, fits);
    if (best && cands[0] !== best) log(`  WAITING on ${best.name} (ESPN rank ${survival(best)}, our next pick ${nextPick(cur, teams)}); taking ${cands[0].name} (ESPN rank ${survival(cands[0])}) now`);
    log(`ON CLOCK pick ${cur} (rd ${round}) roster ${NBA ? myPlayers.map(r => `${r.name} ${r.pos}`).join(', ') || '-' : JSON.stringify(cnt)} board ${lastBoard} top: ${cands.slice(0, 4).map(r => r.name).join(', ')}`);
    let drafted = false;
    for (const r of cands.slice(0, 3)) {
      try {
        let label = null;
        const seen = new Set();
        for (let t = 0; t < (r === cands[0] ? 80 : 8) && (label = await rowAction(r.id, false, r.name)) !== 'DRAFT' && label !== null; t++) { seen.add(label); await p.waitForTimeout(250); }
        if (label !== 'DRAFT') log(`  ${r.name}: labels seen ${JSON.stringify([...seen])}`);
        if (label !== 'DRAFT') { log(`  ${r.name}: row button ${label}, skipping`); continue; }
        if ((await rowAction(r.id, true, r.name)) !== 'DRAFT') { log(`  ${r.name}: row vanished before click`); continue; }
        await p.waitForTimeout(600);
        const confirm = p.locator('button', { hasText: /^\s*(confirm|draft player)\s*$/i }).first();
        if (await confirm.count() && await confirm.isVisible()) { await confirm.click(); log('  clicked confirm'); }
        await p.waitForTimeout(800);
        const got = ((await p.evaluate(() => document.body.innerText)).match(/You drafted ([^\n!]+)/) || [])[1];
        log(`  DRAFTED ${r.name} (id ${r.id}) ${r.pos} ppg ${r.ppg} vor ${r.vor}` + (got && got.trim() !== r.name ? `  !! MISMATCH: ESPN says ${got.trim()}` : got ? '  (confirmed)' : ''));
        drafted = true;
        if (NBA) myPlayers.push(got ? rankings.find(x => x.name === got.trim()) || r : r);
        break;
      } catch (e) { log(`  ${r.name}: ${e.message.split('\n')[0]}`); }
    }
    if (!drafted && !p._reloadedFor) { // buttons stuck on QUEUE = stale session (seen after a Reconnect); reload and retry
      p._reloadedFor = cur; done.delete(cur); log('  buttons never turned DRAFT; reloading the room and retrying this pick');
      await p.goto(url, { waitUntil: 'domcontentloaded' }).catch(() => {}); await p.waitForTimeout(3000); continue;
    }
    if (!drafted) { // last resort: best rendered row at a position that isn't full (ESPN's order)
      const want = Object.keys(CAP).filter(k => (cnt[k] || 0) < CAP[k]).map(k => k === 'DST' ? 'D/ST' : k);
      const got = await p.evaluate(([want, nba]) => {
        for (const b of document.querySelectorAll('button[data-player-id]')) {
          if (b.innerText.trim().toUpperCase() !== 'DRAFT') continue;
          if (nba) { b.click(); return `player ${b.dataset.playerId}`; } // any NBA player fits UTIL
          let row = b; while (row && !row.querySelector('[title]')) row = row.parentElement;
          const pos = row && [...row.querySelectorAll('span')].map(x => x.innerText.trim()).find(t => /^(QB|RB|WR|TE|K|D\/ST)$/.test(t));
          if (pos && want.includes(pos)) { b.click(); return `${row.querySelector('[title]').title} ${pos}`; }
        }
        return null;
      }, [want, NBA]);
      log(got ? `  DRAFTED ${got} (fallback: best rendered at an open position)` : '  could not draft anyone; ESPN autopick will cover');
      const fid = got && NBA && rankings.find(x => `player ${x.id}` === got); if (fid) myPlayers.push(fid);
    }
    await search('');
    await p.waitForTimeout(2500);
    if (!drafted) await p.screenshot({ clip: { x: 0, y: 0, width: 1400, height: 1000 }, path: path.join(LOGDIR, `failed-pick-${cur}.png`) });
  }
  log('all my picks done');
  await ctx.close();
})();
