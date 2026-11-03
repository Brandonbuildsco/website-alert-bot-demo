"""
Website change alert bot (demo / sample build).

What it does, in plain English:
  1. Reads watches.yml: a list of web pages to keep an eye on, and which part
     of each page matters (a price, a status line, a stock message...).
  2. Visits each page and pulls out just that part.
  3. Compares it with what it saw last time (saved in state.json).
  4. If something changed, or a rule like "price drops below $50" just became
     true, it sends an alert:
       - a GitHub Issue (no setup; the GitHub phone app pushes it to you)
       - a phone push through ntfy.sh   (optional: set NTFY_TOPIC)
       - an email                        (optional: set the SMTP_* settings)
  5. Saves what it saw, so it only alerts once per change.

If a page can't be loaded or the part it watches disappears (for example,
the site was redesigned), it alerts about that too and marks the run failed.
"""

import difflib
import hashlib
import json
import logging
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

WATCHES_FILE = Path("watches.yml")
STATE_FILE = Path("state.json")
LOG_DIR = Path("logs")
MAX_RETRIES = 3
TIMEOUT = 20
HEADERS = {"User-Agent": "brandonbuildsco-alert-bot/1.0 (demo; polite, low-frequency checks)"}

log = logging.getLogger("watch")


def setup_logging():
    """Log to the screen and to logs/watch.log."""
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_DIR / "watch.log")],
    )


# ---------------------------------------------------------------- reading pages

def fetch(session, url):
    """Download a page, retrying network errors and 5xx (waits 1s, then 2s)."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT)
            if resp.status_code < 500:
                resp.raise_for_status()           # 404 etc. stop right away
                return decode(resp)
            problem = f"server returned {resp.status_code}"
        except (requests.ConnectionError, requests.Timeout) as err:
            problem = str(err)
        if attempt == MAX_RETRIES:
            raise RuntimeError(f"gave up after {MAX_RETRIES} tries: {problem}")
        wait = 2 ** (attempt - 1)
        log.warning("%s: attempt %d failed (%s), retrying in %ss", url, attempt, problem, wait)
        time.sleep(wait)


def decode(resp):
    """
    Turn the downloaded bytes into text with the right character set.
    Some sites don't say which one they use, and the fallback guess turns
    "£" into "Â£". If the server didn't name one, detect it from the content.
    """
    if "charset" not in resp.headers.get("Content-Type", "").lower():
        resp.encoding = resp.apparent_encoding
    return resp.text


def extract(html, selector):
    """
    Pull out the text of the part of the page we care about.
    `selector` is a CSS selector, like ".price_color" or "#status".
    Returns None if that part isn't on the page anymore.
    """
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one(selector)
    if node is None:
        return None
    return " ".join(node.get_text(" ", strip=True).split())   # tidy up spacing


def to_number(text):
    """Turn text like '£1,299.50' or '$49' into 1299.5 / 49.0. None if no number."""
    match = re.search(r"\d[\d,]*(?:\.\d+)?", text or "")
    return float(match.group().replace(",", "")) if match else None


# ---------------------------------------------------------------- deciding what to alert

def rule_met(watch, value):
    """
    For price/number watches: is the rule true right now?
      below: 50   -> alert when the number drops below 50
      above: 100  -> alert when the number goes above 100
    Plain "change" watches have no rule (returns None).
    """
    number = to_number(value)
    if number is None:
        return None
    if "below" in watch:
        return number < float(watch["below"])
    if "above" in watch:
        return number > float(watch["above"])
    return None


def short_diff(old, new, limit=1200):
    """A small before/after view of what changed, for the alert message."""
    lines = difflib.unified_diff(
        (old or "").split(". "), (new or "").split(". "),
        fromfile="before", tofile="now", lineterm="", n=1)
    text = "\n".join(lines)
    return text[:limit] + ("\n..." if len(text) > limit else "")


def check(watch, value, previous):
    """
    Compare what we just saw with what we saw last time.
    Returns (alert or None, new_state_for_this_watch).
    An alert is a dict with a title and a body.
    """
    name, url = watch["name"], watch["url"]
    prev = previous or {}

    if value is None:                                   # the part we watch is gone
        alert = None
        if not prev.get("broken"):                      # alert once, not every run
            alert = {"title": f"⚠️ Watch broken: {name}",
                     "body": f"Couldn't find `{watch['selector']}` on {url}.\n"
                             "The page may have been redesigned. The selector in watches.yml needs updating."}
        return alert, dict(prev, broken=True)

    digest = hashlib.sha256(value.encode()).hexdigest()
    state = {"value": value, "hash": digest, "broken": False}
    met = rule_met(watch, value)
    if met is not None:
        state["rule_met"] = met

    first_run = "hash" not in prev
    if "below" in watch or "above" in watch:
        # Rule watches alert when the rule *becomes* true (including the first run).
        if met and not prev.get("rule_met"):
            limit = watch.get("below", watch.get("above"))
            word = "below" if "below" in watch else "above"
            return {"title": f"🔔 {name}: now {value} ({word} {limit})",
                    "body": f"{name} is now **{value}**, which is {word} your limit of {limit}.\n\n{url}"}, state
        return None, state

    if first_run:
        log.info("%s: first check, saved a baseline (no alert)", name)
        return None, state
    if digest != prev["hash"]:
        return {"title": f"🔔 Changed: {name}",
                "body": f"{name} changed.\n\n**Before:** {prev.get('value', '')[:300]}\n\n"
                        f"**Now:** {value[:300]}\n\n```diff\n{short_diff(prev.get('value'), value)}\n```\n\n{url}"}, state
    return None, state


# ---------------------------------------------------------------- sending alerts

def send_github_issue(alert):
    """Open a GitHub Issue in this repo. Uses the token GitHub Actions provides."""
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        return False
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"title": alert["title"], "body": alert["body"]},
        timeout=TIMEOUT)
    resp.raise_for_status()
    return True


def send_ntfy(alert):
    """Phone push through ntfy.sh. Install the free ntfy app and subscribe to your topic."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    resp = requests.post(f"https://ntfy.sh/{topic}", data=alert["body"].encode(),
                         headers={"Title": alert["title"].encode("utf-8")}, timeout=TIMEOUT)
    resp.raise_for_status()
    return True


def send_email(alert):
    """Email through any SMTP account (e.g. a Gmail app password)."""
    host, user, password, to = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_TO"))
    if not all((host, user, password, to)):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = alert["title"], user, to
    msg.set_content(alert["body"])
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT") or 587), timeout=TIMEOUT) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
    return True


SENDERS = [("GitHub Issue", send_github_issue), ("ntfy push", send_ntfy), ("email", send_email)]


def send(alert):
    """Try every alert channel that's set up. One failing channel doesn't stop the others."""
    sent = []
    for label, sender in SENDERS:
        try:
            if sender(alert):
                sent.append(label)
        except Exception as err:
            log.error("Couldn't send %s: %s", label, err)
    log.info("ALERT %s -> %s", alert["title"], ", ".join(sent) or "no channel set up (logged only)")
    return sent


# ---------------------------------------------------------------- main

def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def main():
    setup_logging()
    watches = yaml.safe_load(WATCHES_FILE.read_text())["watches"]
    state, new_state, problems = load_state(), {}, 0
    session = requests.Session()
    session.headers.update(HEADERS)

    for watch in watches:
        name = watch["name"]
        try:
            value = extract(fetch(session, watch["url"]), watch["selector"])
        except Exception as err:
            log.error("%s: %s", name, err)
            problems += 1
            new_state[name] = state.get(name, {})       # keep the old baseline
            continue
        if value is None:
            problems += 1
        alert, new_state[name] = check(watch, value, state.get(name))
        log.info("%s: %s", name, value if value is not None else "NOT FOUND")
        if alert:
            send(alert)

    STATE_FILE.write_text(json.dumps(new_state, indent=2, ensure_ascii=False) + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
