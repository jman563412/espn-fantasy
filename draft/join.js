// Join an ESPN public league (Pro / PPR / Standard / Snake / Draft Now) and print its ids.
// Needs the logged-in persistent profile (chrome-profile/, from fetch_cookies.js) -- the
// espn_s2/SWID cookies alone hit a MyDisney login wall on join.
//
// Usage (via draft.sh): node draft/join.js [--scoring PPR|Half-PPR|No-PPR] [--when now|later|tomorrow]
// Prints a final line: JOINED <leagueId> <teamId>
const { chromium } = require('playwright');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const SCORING = arg('scoring', 'PPR');
const WHEN = { now: '-2', top: '-1', later: '0', tomorrow: '1' }[arg('when', 'now')];

async function myTeams(p) {
  await p.goto('https://fantasy.espn.com/football/welcome', { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {});
  const hrefs = await p.evaluate(() => [...document.querySelectorAll('a')].map(a => a.href).filter(h => /team\?leagueId=\d+/.test(h)));
  const out = new Map();
  for (const h of hrefs) { const u = new URL(h); out.set(u.searchParams.get('leagueId'), u.searchParams.get('teamId')); }
  return out;
}

(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(ROOT, 'chrome-profile'), { headless: !process.argv.includes('--headed'), viewport: { width: 1200, height: 1400 } });
  const p = ctx.pages()[0] || await ctx.newPage();
  const before = await myTeams(p);

  await p.goto('https://fantasy.espn.com/football/league/joinpublic', { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {});
  await p.waitForTimeout(2000);
  for (const t of ['Pro', SCORING, 'Standard', 'Snake']) { await p.getByText(t, { exact: true }).first().click(); await p.waitForTimeout(500); }
  await p.selectOption('select', WHEN);
  await p.getByText('Join a League', { exact: true }).last().click();
  await p.waitForTimeout(12000);
  if (/login|registerdisney/i.test(p.url()) || /Enter your email/i.test(await p.evaluate(() => document.body.innerText))) {
    console.log('LOGIN WALL: run `node fetch_cookies.js` headed and log in, then retry');
    await ctx.close(); process.exit(2);
  }

  const after = await myTeams(p);
  await ctx.close();
  const added = [...after].filter(([lid]) => !before.has(lid));
  if (added.length !== 1) { console.log('could not identify the new league; after:', [...after]); process.exit(1); }
  console.log(`JOINED ${added[0][0]} ${added[0][1]}`);
})();
