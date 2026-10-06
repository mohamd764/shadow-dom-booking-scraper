import json
from pathlib import Path

import pytest

from booking_scraper.config import FIXTURE_PATH, ConfigError, build_config

ROOT = Path(__file__).resolve().parents[1]


def test_demo_uses_the_local_fixture_and_does_not_require_a_url():
    config = build_config(demo=True)
    assert config.url == FIXTURE_PATH.resolve().as_uri()
    assert config.demo is True
    assert config.source_site == "demo.local"
    assert config.venue_name == "Example Studio"
    assert config.output_csv == Path("data/demo_appointments.csv")
    assert config.delay_seconds == 0.1
    assert "booking-widget" in config.selectors.widget


def test_live_run_requires_an_http_url():
    with pytest.raises(ConfigError):
        build_config()
    with pytest.raises(ConfigError):
        build_config(url="file:///tmp/page.html")


def test_cli_and_file_overrides(tmp_path):
    path = tmp_path / "site.json"
    path.write_text(
        json.dumps(
            {
                "url": "https://example.com/book",
                "months": 3,
                "delay_seconds": 2,
                "venue_name": "Example Studio",
            }
        ),
        encoding="utf-8",
    )
    config = build_config(config_path=path, months=4, url="https://example.com/other")
    assert config.url == "https://example.com/other"
    assert config.months == 4
    assert config.delay_seconds == 2
    assert config.venue_name == "Example Studio"
    assert config.source_site == "example.com"


def test_unknown_key_is_rejected(tmp_path):
    path = tmp_path / "site.json"
    path.write_text('{"url": "https://example.com/book", "password": "nope"}', encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown config keys"):
        build_config(config_path=path)


def test_example_config_loads():
    config = build_config(config_path=ROOT / "config" / "example.json")
    assert config.url == "https://example.com/book"
    assert config.selectors.day_cell.startswith("[data-date]")
    assert config.ignore_robots is False
