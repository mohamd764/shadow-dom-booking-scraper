import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from tests.expected_slots import EXPECTED_IDS, FIRST_SLOT

from booking_scraper.config import FIXTURE_PATH, build_config
from booking_scraper.scrape import click_in_shadow, scrape
from booking_scraper.storage import load_csv

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.browser


def test_light_selector_misses_shadow_slots_and_fallback_clicks():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.goto(FIXTURE_PATH.resolve().as_uri())
        host = page.locator("booking-widget")
        assert page.evaluate("() => document.querySelector('[data-date]')") is None
        assert click_in_shadow(host, ["button.hc-search"]) is True
        assert host.locator("[data-date='2026-04-07']").count() == 1
        host.locator("[data-date='2026-04-07']").click()
        assert page.evaluate("() => document.querySelector('li.hc-appointment')") is None
        assert page.locator(":light(li.hc-appointment)").count() == 0
        assert host.locator("li.hc-appointment").count() == 3
        browser.close()


def test_demo_scrape_extracts_fixture_and_second_run_does_not_duplicate(tmp_path):
    output = tmp_path / "demo.csv"
    config = build_config(demo=True, output_csv=output, save_diagnostics=False)
    rows, stats = scrape(config)
    assert [row["slot_id"] for row in rows] == EXPECTED_IDS
    assert stats.total == len(EXPECTED_IDS)
    assert stats.fallback is False
    first = next(row for row in rows if row["slot_id"] == FIRST_SLOT["slot_id"])
    for key, value in FIRST_SLOT.items():
        assert first[key] == value
    assert "2026-04-06" not in {row["date_local"] for row in rows}
    assert "2026-04-14" not in {row["date_local"] for row in rows}

    stamped = first["scrape_timestamp_utc"]
    again, again_stats = scrape(config)
    assert again_stats.added == 0
    assert again_stats.total == len(EXPECTED_IDS)
    stored = load_csv(output)
    assert len(stored) == len(EXPECTED_IDS)
    assert stored[0]["scrape_timestamp_utc"] == stamped
    assert len(again) == len(EXPECTED_IDS)


def test_cli_demo_writes_csv(tmp_path):
    output = tmp_path / "cli.csv"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "booking_scraper", "--demo", "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert completed.returncode == 0, completed.stderr
    with output.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["slot_id"] for row in rows] == EXPECTED_IDS
