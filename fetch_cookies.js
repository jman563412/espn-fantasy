// Grabs espn_s2 + SWID from a real logged-in ESPN session via Playwright,
// so you never have to copy them out of devtools (or paste them into a chat) again.
//
// First run: opens a visible Chrome window. Log into ESPN normally (handles
// 2FA/captcha fine since it's a real browser). The script polls until it
// sees the auth cookies, then saves them and closes.
//
// Later runs: reuses the same browser profile (./chrome-profile), which
// stays logged in, so it can run with --headless and finish in seconds.
//
// Usage:
//   node fetch_cookies.js            # headed, use for first login
//   node fetch_cookies.js --headless # after you're already logged in once

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const PROFILE_DIR = path.join(__dirname, 'chrome-profile');
const COOKIE_FILE = path.join(__dirname, 'cookies.json');
const LEAGUE_URL = 'https://fantasy.espn.com/';  // any ESPN fantasy page; logging in sets the cookies
const HEADLESS = process.argv.includes('--headless');
const TIMEOUT_MS = 5 * 60 * 1000;

async function main() {
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: HEADLESS,
    viewport: { width: 1200, height: 900 },
  });
  const page = context.pages()[0] || (await context.newPage());
  await page.goto(LEAGUE_URL, { waitUntil: 'domcontentloaded' });

  if (!HEADLESS) {
    console.log('If prompted, log into ESPN in the opened window. Waiting for login...');
  }

  const deadline = Date.now() + TIMEOUT_MS;
  let s2, swid;
  while (Date.now() < deadline) {
    const cookies = await context.cookies('https://fantasy.espn.com');
    s2 = cookies.find((c) => c.name === 'espn_s2');
    swid = cookies.find((c) => c.name === 'SWID');
    if (s2 && swid) break;
    await page.waitForTimeout(2000);
  }

  await context.close();

  if (!s2 || !swid) {
    console.error('Timed out waiting for login. Run again without --headless to log in.');
    process.exit(1);
  }

  const out = { espn_s2: s2.value, SWID: swid.value, savedAt: new Date().toISOString() };
  fs.writeFileSync(COOKIE_FILE, JSON.stringify(out, null, 2), { mode: 0o600 });
  fs.chmodSync(COOKIE_FILE, 0o600);
  console.log(`Saved cookies to ${COOKIE_FILE} (chmod 600).`);
}

main();
