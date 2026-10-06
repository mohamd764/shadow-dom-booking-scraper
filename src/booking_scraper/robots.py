"""robots.txt check for the configured user agent.

An explicit disallow stops the run. A missing file or a transport error is
logged and does not pretend to be permission; see the README limitations.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger("booking_scraper.robots")

Loader = Callable[[], str | None]


class RobotsDisallowed(RuntimeError):
    """The published robots.txt policy disallows this user agent."""


def robots_allow(robots_txt: str, user_agent: str, url: str) -> bool:
    parser = RobotFileParser()
    parser.parse(robots_txt.splitlines())
    return parser.can_fetch(user_agent, url)


def fetch_robots_txt(url: str, user_agent: str, timeout: float = 10.0) -> str | None:
    """Return robots.txt body, or None when no policy could be read."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    request = urllib.request.Request(robots_url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(200_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.info("no robots.txt at %s (HTTP 404)", robots_url)
            return ""
        logger.warning("could not read %s (%s)", robots_url, exc)
        return None
    except OSError as exc:
        logger.warning("could not read %s (%s)", robots_url, exc)
        return None

    stripped = body.lstrip().lower()
    if stripped.startswith("<!doctype html") or stripped.startswith("<html"):
        logger.warning("%s returned HTML rather than a robots file", robots_url)
        return None
    return body


def check_robots(
    url: str,
    user_agent: str,
    *,
    ignore: bool,
    loader: Loader,
) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return
    if ignore:
        logger.warning(
            "skipping robots.txt for %s because --ignore-robots was set",
            url,
        )
        return

    body = loader()
    if body is None:
        logger.warning(
            "robots.txt for %s could not be read. Continuing without a policy. "
            "Confirm the site's terms before scheduling this run.",
            url,
        )
        return
    if body == "":
        logger.info("no robots.txt policy for %s", parsed.netloc)
        return
    if robots_allow(body, user_agent, url):
        logger.info("robots.txt allows %s to fetch %s", user_agent, url)
        return
    raise RobotsDisallowed(
        f"robots.txt disallows {user_agent} from fetching {url}. "
        "Pick another target, or pass --ignore-robots only when you control "
        "the site or have permission that the file does not reflect."
    )
