"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from booking_scraper import __version__
from booking_scraper.config import ConfigError, build_config
from booking_scraper.robots import RobotsDisallowed
from booking_scraper.scrape import scrape


def main(argv: list[str] | None = None) -> None:
    sys.exit(run(argv))


def run(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    logger = logging.getLogger("booking_scraper.cli")
    try:
        config = build_config(
            demo=args.demo,
            config_path=args.config,
            url=args.url,
            months=args.months,
            max_days=args.max_days,
            service_index=args.service_index,
            staff_index=args.staff_index,
            headless=False if args.headed else None,
            delay_seconds=args.delay,
            timeout_seconds=args.timeout,
            result_timeout_seconds=args.result_timeout,
            output_csv=args.output,
            diagnostics_dir=args.diagnostics_dir,
            save_diagnostics=True if args.diagnostics else None,
            ignore_robots=True if args.ignore_robots else None,
            user_agent=args.user_agent,
        )
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2

    try:
        rows, stats = scrape(config)
    except RobotsDisallowed as exc:
        logger.error("%s", exc)
        return 3
    except Exception:
        logger.exception("scrape failed")
        return 1

    if config.demo and not rows:
        logger.error("demo fixture produced no slots")
        return 1
    logger.info(
        "done: %s collected, %s rows in %s",
        len(rows),
        stats.total,
        stats.path,
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="booking-scraper",
        description=(
            "Read appointment slots from a booking widget rendered in an open "
            "shadow root and merge them into a CSV. Use --demo to run offline "
            "against the built-in fixture."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--demo", action="store_true", help="run against the offline fixture")
    parser.add_argument("--url", help="page that embeds the booking widget")
    parser.add_argument("--config", type=Path, help="JSON config file (see config/example.json)")
    parser.add_argument("--months", type=int, help="how many calendar months to walk (default 2)")
    parser.add_argument("--max-days", type=int, help="maximum day cells to open per month")
    parser.add_argument(
        "--service-index",
        type=int,
        help="1-based service option to select; 0 skips the control",
    )
    parser.add_argument(
        "--staff-index",
        type=int,
        help="1-based staff option to select; 0 skips the control",
    )
    parser.add_argument("--delay", type=float, help="seconds to pause after each day and month")
    parser.add_argument("--timeout", type=float, help="Playwright timeout in seconds")
    parser.add_argument(
        "--result-timeout",
        type=float,
        help="seconds to wait for a day's results to change",
    )
    parser.add_argument("--output", type=Path, help="CSV path")
    parser.add_argument("--diagnostics-dir", type=Path, help="directory for HTML and screenshots")
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="also save HTML and a screenshot after a successful run",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="fetch the page even when robots.txt disallows it",
    )
    parser.add_argument("--user-agent", help="override the identifiable user agent")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    return parser


def _configure_logging(verbose: bool) -> None:
    logger = logging.getLogger("booking_scraper")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
