"""
Offline tests: saved HTML snippets and fake senders, no network. Run with:
    python -m pytest -q
"""

import json

import watch

PRICE_PAGE = '<html><body><p class="price_color">£51.77</p></body></html>'
STOCK_PAGE = '<html><body><p class="instock availability"> In stock (22 available) </p></body></html>'

PRICE_WATCH = {"name": "Book price", "url": "https://x/book", "selector": "p.price_color", "below": 55}
CHANGE_WATCH = {"name": "Stock", "url": "https://x/stock", "selector": "p.instock.availability"}


def test_extract_reads_the_part_that_matters():
    assert watch.extract(STOCK_PAGE, "p.instock.availability") == "In stock (22 available)"
    assert watch.extract(STOCK_PAGE, "#missing") is None


def test_to_number_handles_currency_and_commas():
    assert watch.to_number("£1,299.50") == 1299.5
    assert watch.to_number("$49") == 49.0
    assert watch.to_number("Sold out") is None


def test_change_watch_first_run_is_baseline_only():
    alert, state = watch.check(CHANGE_WATCH, "In stock (22 available)", None)
    assert alert is None and state["value"] == "In stock (22 available)"


def test_change_watch_alerts_once_per_change():
    _, first = watch.check(CHANGE_WATCH, "In stock (22 available)", None)
    alert, second = watch.check(CHANGE_WATCH, "Out of stock", first)
    assert alert["title"] == "🔔 Changed: Stock"
    assert "Out of stock" in alert["body"] and "In stock (22 available)" in alert["body"]
    again, _ = watch.check(CHANGE_WATCH, "Out of stock", second)
    assert again is None                          # same value -> no repeat alert


def test_price_rule_alerts_when_it_becomes_true_only():
    alert, state = watch.check(PRICE_WATCH, "£51.77", None)
    assert alert["title"] == "🔔 Book price: now £51.77 (below 55)"
    repeat, state = watch.check(PRICE_WATCH, "£50.00", state)
    assert repeat is None                         # still below: don't nag
    _, state = watch.check(PRICE_WATCH, "£60.00", state)
    assert state["rule_met"] is False
    back, _ = watch.check(PRICE_WATCH, "£52.00", state)
    assert back is not None                       # dropped below again -> alert again


def test_broken_selector_alerts_once():
    _, ok = watch.check(CHANGE_WATCH, "In stock", None)
    alert, broken = watch.check(CHANGE_WATCH, None, ok)
    assert alert["title"].startswith("⚠️ Watch broken")
    again, _ = watch.check(CHANGE_WATCH, None, broken)
    assert again is None


def test_one_failing_channel_does_not_block_others(monkeypatch):
    def boom(alert): raise RuntimeError("SMTP down")
    monkeypatch.setattr(watch, "SENDERS", [("email", boom), ("ntfy push", lambda a: True)])
    assert watch.send({"title": "t", "body": "b"}) == ["ntfy push"]


def test_full_run_saves_state_and_alerts(monkeypatch, tmp_path):
    """End to end with fake pages: baseline run, then a change, then nothing new."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "watches.yml").write_text(
        "watches:\n"
        "  - {name: Book price, url: 'https://x/book', selector: p.price_color, below: 55}\n"
        "  - {name: Stock, url: 'https://x/stock', selector: p.instock.availability}\n")
    pages = {"https://x/book": PRICE_PAGE, "https://x/stock": STOCK_PAGE}
    monkeypatch.setattr(watch, "fetch", lambda session, url: pages[url])
    sent = []
    monkeypatch.setattr(watch, "send", lambda alert: sent.append(alert["title"]))

    assert watch.main() == 0
    assert sent == ["🔔 Book price: now £51.77 (below 55)"]        # rule alert; stock = baseline
    pages["https://x/stock"] = STOCK_PAGE.replace("22 available", "Out of stock")
    assert watch.main() == 0
    assert sent[-1] == "🔔 Changed: Stock"
    assert watch.main() == 0 and len(sent) == 2                     # nothing new -> no alert
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["Stock"]["value"] == "In stock (Out of stock)"


def test_missing_section_marks_run_failed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "watches.yml").write_text(
        "watches:\n  - {name: Stock, url: 'https://x/stock', selector: '#gone'}\n")
    monkeypatch.setattr(watch, "fetch", lambda session, url: STOCK_PAGE)
    monkeypatch.setattr(watch, "send", lambda alert: None)
    assert watch.main() == 1


def test_channels_skip_when_not_set_up(monkeypatch):
    """Empty secrets (GitHub passes them as "") mean 'not set up', not a crash."""
    for key in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "NTFY_TOPIC", "SMTP_HOST", "SMTP_USER",
                "SMTP_PASS", "ALERT_TO", "SMTP_PORT"):
        monkeypatch.setenv(key, "")
    alert = {"title": "t", "body": "b"}
    assert not watch.send_github_issue(alert)
    assert not watch.send_ntfy(alert)
    assert not watch.send_email(alert)


def test_decode_fixes_missing_charset():
    """Sites that don't declare a charset must still give '£', not 'Â£'."""
    import requests
    resp = requests.Response()
    resp._content = "<p class='price_color'>£51.77</p>".encode("utf-8")
    resp.headers["Content-Type"] = "text/html"            # no charset given
    resp.encoding = "ISO-8859-1"                           # what requests would guess
    assert watch.extract(watch.decode(resp), "p.price_color") == "£51.77"
