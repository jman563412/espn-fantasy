// Rename one of our teams via the Team Settings page.
// Usage (via draft.sh env): node draft/rename.js <leagueId> <teamId> "<Team Name>" <ABBR>
const { chromium } = require('playwright');
const path = require('path');
const [LEAGUE, TEAM, NAME, ABBR] = process.argv.slice(2);
(async () => {
  const ctx = await chromium.launchPersistentContext(path.resolve(__dirname, '..', 'chrome-profile'), { headless: true, viewport: { width: 1300, height: 1400 } });
  const p = ctx.pages()[0] || await ctx.newPage();
  p.on('response', r => { if (r.request().method() !== 'GET' && /lm-api-writes/.test(r.url())) console.log('WRITE', r.status(), r.request().method(), r.url().replace(/\?.*/, '')); });
  await p.goto(`https://fantasy.espn.com/football/team/settings?leagueId=${LEAGUE}&teamId=${TEAM}&view=myTeam`, { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {});
  await p.waitForTimeout(1500);
  const inputs = p.locator('input[type="text"]');
  const cur = await inputs.nth(0).inputValue();
  await inputs.nth(0).fill(NAME);
  await inputs.nth(1).fill(ABBR);
  await p.getByText('Save Changes', { exact: true }).click();
  await p.waitForTimeout(3000);
  console.log(`renamed "${cur}" -> "${NAME}" (${ABBR})`);
  await ctx.close();
})();
