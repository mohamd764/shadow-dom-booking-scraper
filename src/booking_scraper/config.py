"""Runtime configuration from a JSON file and CLI overrides."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlparse

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "booking_widget.html"

DEFAULT_USER_AGENT = (
    "ShadowDOMBookingScraper/1.0 (appointment availability research; respects robots.txt)"
)

DEMO_SOURCE = "demo.local"
DEMO_VENUE = "Example Studio"
DEMO_LOCATION = "Example City"


class ConfigError(ValueError):
    """The config file or CLI flags cannot build a run."""


@dataclass
class SelectorProfile:
    widget: str = (
        'booking-widget[data-type="appointments"], healcode-widget[data-type="appointments"]'
    )
    cookie_accept: tuple[str, ...] = (
        "[data-cky-tag='accept-button']",
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
    )
    service_select: tuple[str, ...] = (
        "select[id*='Service' i]",
        "select[name*='Service' i]",
        "select[id*='session' i]",
        "select[name*='session' i]",
        "select[id*='Treatment' i]",
        "select[name*='Treatment' i]",
    )
    staff_select: tuple[str, ...] = (
        "select[id*='Staff' i]",
        "select[name*='Staff' i]",
        "select[id*='Instructor' i]",
        "select[name*='Instructor' i]",
        "select[id*='Therapist' i]",
        "select[name*='Therapist' i]",
    )
    open_calendar: tuple[str, ...] = (
        "button:has-text('Search')",
        "a:has-text('Search')",
        "button:has-text('Find')",
        "input[type='submit']",
        "button[type='submit']",
        ".hc-view-schedule",
    )
    open_calendar_css: tuple[str, ...] = (
        "button.hc-search",
        "button[type='submit']",
        "input[type='submit']",
        ".hc-view-schedule",
    )
    day_cell: str = "[data-date]:not([disabled])"
    next_month: tuple[str, ...] = (
        "button[aria-label*='Next' i]",
        "a[aria-label*='Next' i]",
        ".hc-next",
        "[data-action='next']",
    )
    slot_card: tuple[str, ...] = (
        "li.hc-appointment",
        "li.appointment",
        ".hc-schedule-item",
    )
    slot_title: str = ".hc-appointment__title, h3, .title"
    slot_staff: str = ".hc-appointment__staff, .staff, .instructor"
    book_link: str = "a[href], button[data-url], a[data-url]"
    empty_state: str = ".hc-empty"
    expand_times: tuple[str, ...] = (
        "button:has-text('Show times')",
        "a:has-text('Show times')",
        ".hc-show-times",
        ".hc-view-schedule",
    )

    @classmethod
    def from_mapping(cls, data: dict) -> SelectorProfile:
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ConfigError(f"unknown selector keys: {', '.join(unknown)}")
        current = cls()
        updates: dict[str, object] = {}
        for key, value in data.items():
            default = getattr(current, key)
            if isinstance(default, tuple):
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise ConfigError(f"selectors.{key} must be a list of strings")
                updates[key] = tuple(value)
            elif isinstance(default, str):
                if not isinstance(value, str) or not value.strip():
                    raise ConfigError(f"selectors.{key} must be a non-empty string")
                updates[key] = value
            else:
                raise ConfigError(f"cannot set selectors.{key}")
        return cls(**{**current.__dict__, **updates})


@dataclass
class ScrapeConfig:
    url: str
    demo: bool
    source_site: str
    venue_name: str
    venue_location: str
    months: int = 2
    max_days: int = 31
    service_index: int = 0
    staff_index: int = 0
    headless: bool = True
    delay_seconds: float = 1.0
    timeout_seconds: float = 20.0
    result_timeout_seconds: float = 12.0
    settle_seconds: float = 0.4
    output_csv: Path = field(default_factory=lambda: Path("data/appointments.csv"))
    diagnostics_dir: Path = field(default_factory=lambda: Path("diagnostics"))
    save_diagnostics: bool = False
    ignore_robots: bool = False
    user_agent: str = DEFAULT_USER_AGENT
    csv_attempts: int = 8
    selectors: SelectorProfile = field(default_factory=SelectorProfile)

    @property
    def timeout_ms(self) -> int:
        return int(self.timeout_seconds * 1000)


_FILE_KEYS = {
    "url",
    "source_site",
    "venue_name",
    "venue_location",
    "months",
    "max_days",
    "service_index",
    "staff_index",
    "headless",
    "delay_seconds",
    "timeout_seconds",
    "result_timeout_seconds",
    "settle_seconds",
    "output_csv",
    "diagnostics_dir",
    "save_diagnostics",
    "ignore_robots",
    "user_agent",
    "csv_attempts",
    "selectors",
}


def load_config_file(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    if path.suffix.lower() != ".json":
        raise ConfigError(f"unsupported config format {path.suffix}; use JSON")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config file must be a JSON object")
    unknown = sorted(set(data) - _FILE_KEYS)
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(unknown)}")
    return data


def build_config(
    *,
    demo: bool = False,
    config_path: Path | None = None,
    url: str | None = None,
    months: int | None = None,
    max_days: int | None = None,
    service_index: int | None = None,
    staff_index: int | None = None,
    headless: bool | None = None,
    delay_seconds: float | None = None,
    timeout_seconds: float | None = None,
    result_timeout_seconds: float | None = None,
    settle_seconds: float | None = None,
    output_csv: Path | None = None,
    diagnostics_dir: Path | None = None,
    save_diagnostics: bool | None = None,
    ignore_robots: bool | None = None,
    user_agent: str | None = None,
    source_site: str | None = None,
    venue_name: str | None = None,
    venue_location: str | None = None,
) -> ScrapeConfig:
    file_data = load_config_file(config_path) if config_path else {}
    selectors_data = file_data.get("selectors", {})
    if not isinstance(selectors_data, dict):
        raise ConfigError("selectors must be a JSON object")
    selectors = SelectorProfile.from_mapping(selectors_data)

    resolved_url = url or file_data.get("url") or ""
    if demo:
        if not FIXTURE_PATH.is_file():
            raise ConfigError(f"demo fixture is missing: {FIXTURE_PATH}")
        resolved_url = FIXTURE_PATH.resolve().as_uri()

    if not demo and not resolved_url:
        raise ConfigError(
            "pass --url or a config file with url, or use --demo for the offline fixture"
        )

    if not demo:
        parsed = urlparse(resolved_url)
        if parsed.scheme not in {"http", "https"}:
            raise ConfigError("url must start with http:// or https://")

    def pick(name: str, override, default):
        if override is not None:
            return override
        if name in file_data:
            return file_data[name]
        return default

    if demo:
        delay_default = 0.1
        settle_default = 0.15
        result_default = 4.0
        output_default = Path("data/demo_appointments.csv")
        source_default = DEMO_SOURCE
        venue_default = DEMO_VENUE
        location_default = DEMO_LOCATION
    else:
        delay_default = 1.0
        settle_default = 0.4
        result_default = 12.0
        output_default = Path("data/appointments.csv")
        source_default = urlparse(resolved_url).netloc
        venue_default = ""
        location_default = ""

    config = ScrapeConfig(
        url=resolved_url,
        demo=demo,
        source_site=str(pick("source_site", source_site, source_default)),
        venue_name=str(pick("venue_name", venue_name, venue_default)),
        venue_location=str(pick("venue_location", venue_location, location_default)),
        months=int(pick("months", months, 2)),
        max_days=int(pick("max_days", max_days, 31)),
        service_index=int(pick("service_index", service_index, 0)),
        staff_index=int(pick("staff_index", staff_index, 0)),
        headless=bool(pick("headless", headless, True)),
        delay_seconds=float(pick("delay_seconds", delay_seconds, delay_default)),
        timeout_seconds=float(pick("timeout_seconds", timeout_seconds, 20)),
        result_timeout_seconds=float(
            pick("result_timeout_seconds", result_timeout_seconds, result_default)
        ),
        settle_seconds=float(pick("settle_seconds", settle_seconds, settle_default)),
        output_csv=Path(pick("output_csv", output_csv, output_default)),
        diagnostics_dir=Path(pick("diagnostics_dir", diagnostics_dir, Path("diagnostics"))),
        save_diagnostics=bool(pick("save_diagnostics", save_diagnostics, False)),
        ignore_robots=bool(pick("ignore_robots", ignore_robots, False)),
        user_agent=str(pick("user_agent", user_agent, DEFAULT_USER_AGENT)),
        csv_attempts=int(file_data.get("csv_attempts", 8)),
        selectors=selectors,
    )
    _validate(config)
    return config


def _validate(config: ScrapeConfig) -> None:
    if config.months < 1:
        raise ConfigError("months must be at least 1")
    if config.max_days < 1:
        raise ConfigError("max_days must be at least 1")
    if config.service_index < 0 or config.staff_index < 0:
        raise ConfigError("service-index and staff-index must be 0 or greater")
    if config.delay_seconds < 0 or config.settle_seconds < 0:
        raise ConfigError("delays cannot be negative")
    if config.timeout_seconds <= 0 or config.result_timeout_seconds <= 0:
        raise ConfigError("timeouts must be positive")
    if config.csv_attempts < 1:
        raise ConfigError("csv_attempts must be at least 1")
    if not config.user_agent.strip():
        raise ConfigError("user_agent cannot be empty")
