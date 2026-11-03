# Website change alert bot

> **Demo / sample build. Not client work.** It watches pages on [books.toscrape.com](https://books.toscrape.com) (a public practice site made for scraping demos) plus a test page in this repo.

This bot checks web pages every 30 minutes and alerts you the moment something you care about changes: a price drops, an item comes back in stock, a status message updates. Nobody has to keep refreshing the page.

## What it watches (see `watches.yml`)

| Watch | Rule |
|---|---|
| A book's price | Alert when it drops **below £55** |
| A book's stock message | Alert on **any change** |
| `demo/status.html` in this repo | Alert on **any change**. Edit the file to see one. |

Adding a page is three lines in `watches.yml`: a name, the URL, and the part of the page to watch.

## How you get alerted

| Channel | Setup |
|---|---|
| **GitHub Issue** | None. Every alert opens an Issue, and the GitHub phone app pushes it to you. |
| **Phone push (ntfy)** | Install the free ntfy app, pick a secret topic name, add it as the `NTFY_TOPIC` secret. |
| **Email** | Add `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS` (e.g. a Gmail app password) and `ALERT_TO` as secrets. |

## How it's built to keep running

- **Alerts once per change:** it remembers what it saw (`state.json`), so you get one alert, not one every 30 minutes.
- **Rules, not noise:** price watches only alert when the rule *becomes* true, and again only if it goes false and true again.
- **Watches the right part:** it checks one section of the page (a CSS selector), so ads and timestamps elsewhere don't trigger false alarms.
- **Retries:** if a page fails to load, it waits and tries again.
- **Fails loudly:** if a site is redesigned and the watched section disappears, you get a "watch broken" alert and the run is marked failed.
- **One channel down doesn't stop the others:** if email fails, the Issue and push still go out.
- **Tested:** 10 offline tests run before every check (`tests/`).

## Run it yourself

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python watch.py                        # first run saves a baseline
python -m pytest -q                    # optional: run the tests
```

## Runbook (plain English)

- **Watch a new page:** add an entry to `watches.yml`. To find the selector: right-click the price or text in Chrome → **Inspect** → right-click the highlighted line → **Copy → Copy selector**.
- **Price rule:** add `below: 50` or `above: 100` to a watch. Without a rule, any change alerts.
- **Run it now:** Actions tab → *Website change alerts* → **Run workflow**.
- **See a test alert:** edit the text in `demo/status.html` and commit. The next run opens a "Changed" Issue.
- **Change how often:** edit the `cron` line in `.github/workflows/watch.yml`. GitHub's free scheduler can start late on busy days; for minute-exact checks, run it on a small server instead.
- **"Watch broken" alert:** the site changed its layout. Update that watch's `selector` in `watches.yml`.
- **Be polite:** check the site's terms and robots.txt, and don't check more often than you need.

## Tech

Python · Requests · BeautifulSoup · PyYAML · GitHub REST API (Issues) · ntfy · SMTP · pytest · GitHub Actions (scheduled)

---

Built by [brandonbuildsco](https://github.com/Brandonbuildsco). Want alerts for a page you keep refreshing? Hire me on Upwork or Fiverr: **brandonbuildsco**.
