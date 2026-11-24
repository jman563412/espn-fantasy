// List upcoming ESPN snake mock-draft rooms (name, time, members, waiting-room URL with leagueId).
// Usage (via draft.sh): draft/draft.sh [nba] mockrooms
const { chromium } = require('playwright');
(async () => {
  const ctx = await chromium.launchPersistentContext(require('path').resolve(__dirname, '..', 'chrome-profile'), { headless: true, viewport: { width: 1400, height: 1200 } });
  const p = ctx.pages()[0] || await ctx.newPage();
  await p.goto(`https://fantasy.espn.com/${process.argv.includes('--nba') ? 'basketball' : 'football'}/mockdraftlobby`, { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log(e.message));
  await p.waitForTimeout(3000);
  const rows = await p.evaluate(() => [...document.querySelectorAll('tr')].map(tr => ({ t: tr.innerText.replace(/\s+/g, ' ').trim(), links: [...tr.querySelectorAll('a,button')].map(a => (a.innerText || '').trim() + '=>' + (a.href || '')) })).filter(r => /snake/i.test(r.t)));
  rows.slice(0, 12).forEach(r => console.log(r.t, '|', r.links.join(' ; ')));
  await ctx.close();
})();
