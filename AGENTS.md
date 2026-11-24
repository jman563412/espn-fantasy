# Agent instructions

This repo manages ESPN fantasy football (NFL) and basketball (NBA) teams through ESPN's private API, plus a
Playwright draft bot.

- For anything about the user's teams (lineups, waivers, drops, trades, injuries, guards, drafts), follow
  `.claude/skills/espn-fantasy/SKILL.md`. It holds the workflow and the rules, each tied to a key in `config.json`.
- **Writes:** with `rules.confirm_writes` on (the default), propose every roster move and wait for the user's
  explicit OK before writing. After any interrupted or failed write, check `mTransactions2` before saying nothing
  happened.
- **Secrets:** never print, log or paste `cookies.json` values, `ESPN_S2` or `ESPN_SWID`. They're a live login.
- Python is stdlib-only (3.10+); run scripts from the repo root. The draft bot needs `npm install` and
  `npx playwright install chromium`.
