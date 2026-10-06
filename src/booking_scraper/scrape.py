"""Playwright walk of a shadow-DOM booking widget."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from booking_scraper.config import ScrapeConfig
from booking_scraper.parse import normalize_space, parse_slot
from booking_scraper.robots import check_robots, fetch_robots_txt
from booking_scraper.storage import MergeStats, merge_csv

logger = logging.getLogger("booking_scraper.scrape")

SKIP_OPTION_SNIPPETS = ("select", "choose", "all ")

# Playwright CSS locators pierce open shadow DOM. `:light()` turns that off,
# so slot queries use ordinary CSS. This script is the fallback for controls
# that a locator click does not reach: it walks every open shadow root.
SHADOW_CLICK_JS = """
(host, selectors) => {
  const roots = [];
  const visit = (node) => {
    if (!node || !node.querySelectorAll) return;
    roots.push(node);
    for (const element of node.querySelectorAll("*")) {
      if (element.shadowRoot) visit(element.shadowRoot);
    }
  };
  visit(host.shadowRoot || host);
  for (const selector of selectors) {
    for (const root of roots) {
      const node = root.querySelector(selector);
      if (!node) continue;
      node.click();
      return true;
    }
  }
  return false;
}
"""

SHADOW_HTML_JS = """
() => {
  function cloneNode(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.cloneNode();
    if (node.nodeType !== Node.ELEMENT_NODE) return document.createTextNode("");
    const copy = node.cloneNode(false);
    if (node.shadowRoot) {
      const shadow = document.createElement("template");
      shadow.setAttribute("data-shadow-root", "open");
      for (const child of node.shadowRoot.childNodes) {
        shadow.content.appendChild(cloneNode(child));
      }
      copy.appendChild(shadow);
    }
    for (const child of node.childNodes) copy.appendChild(cloneNode(child));
    return copy;
  }
  return "<!DOCTYPE html>\\n" + cloneNode(document.documentElement).outerHTML;
}
"""


def scrape(config: ScrapeConfig) -> tuple[list[dict[str, str]], MergeStats]:
    """Open the page, collect slots, and merge them into the CSV."""
    _ensure_logging()
    logger.info(
        "starting scrape demo=%s months=%s delay=%ss output=%s",
        config.demo,
        config.months,
        config.delay_seconds,
        config.output_csv,
    )
    check_robots(
        config.url,
        config.user_agent,
        ignore=config.ignore_robots,
        loader=lambda: fetch_robots_txt(config.url, config.user_agent),
    )
    rows = _browse(config)
    stats = merge_csv(
        config.output_csv,
        rows,
        attempts=config.csv_attempts,
    )
    logger.info("collected %s slot(s) this run", len(rows))
    return rows, stats


def click_in_shadow(scope: Locator, selectors: Sequence[str]) -> bool:
    """Click the first matching node inside an open shadow tree."""
    if not selectors:
        return False
    try:
        return bool(scope.evaluate(SHADOW_CLICK_JS, list(selectors)))
    except PlaywrightError:
        logger.debug("shadowRoot click failed", exc_info=True)
        return False


def _browse(config: ScrapeConfig) -> list[dict[str, str]]:
    page: Page | None = None
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=config.headless,
                args=["--disable-gpu", "--no-sandbox"],
            )
        except PlaywrightError as exc:
            if "Executable doesn't exist" in str(exc):
                raise PlaywrightError(
                    "Chromium is not installed. Run: python -m playwright install chromium"
                ) from exc
            raise
        try:
            context = browser.new_context(
                user_agent=config.user_agent,
                viewport={"width": 1280, "height": 900},
                locale="en-GB",
            )
            page = context.new_page()
            page.set_default_timeout(config.timeout_ms)
            logger.info("opening %s", config.url)
            page.goto(config.url, wait_until="domcontentloaded")
            _accept_cookies(page, config)
            host = _widget(page, config)
            chosen_service = _choose_select(
                host,
                config.selectors.service_select,
                config.service_index,
                "service",
            )
            chosen_staff = _choose_select(
                host,
                config.selectors.staff_select,
                config.staff_index,
                "staff",
            )
            _pause(page, config.delay_seconds)
            rows: list[dict[str, str]] = []
            for month_index in range(config.months):
                if not _ensure_calendar(page, host, config):
                    break
                before_days = _day_ids(host, config)
                rows.extend(
                    _collect_days(
                        page,
                        host,
                        config,
                        chosen_service=chosen_service,
                        chosen_staff=chosen_staff,
                    )
                )
                if month_index >= config.months - 1:
                    break
                if not _advance_month(page, host, config, before_days):
                    logger.warning(
                        "stopped after month %s; the next-month control did not move the calendar",
                        month_index + 1,
                    )
                    break
            if config.save_diagnostics and page is not None:
                _capture(page, config)
            return rows
        except Exception:
            if page is not None:
                _capture(page, config)
            raise
        finally:
            browser.close()


def _widget(page: Page, config: ScrapeConfig) -> Locator:
    matches = page.locator(config.selectors.widget)
    matches.first.wait_for(state="attached", timeout=config.timeout_ms)
    count = matches.count()
    if count > 1:
        logger.warning("found %s widget hosts; using the first", count)
    host = matches.first
    try:
        host.scroll_into_view_if_needed(timeout=3000)
    except PlaywrightError:
        logger.debug("could not scroll the widget into view", exc_info=True)
    logger.info("widget attached")
    return host


def _accept_cookies(page: Page, config: ScrapeConfig) -> None:
    for selector in config.selectors.cookie_accept:
        locator = page.locator(selector)
        try:
            if locator.count() == 0 or not locator.first.is_visible():
                continue
            locator.first.click(timeout=1500)
            logger.info("dismissed cookie banner via %s", selector)
            return
        except PlaywrightError:
            logger.debug("cookie selector failed: %s", selector, exc_info=True)


def _choose_select(
    scope: Locator,
    selectors: Sequence[str],
    index: int,
    kind: str,
) -> str:
    """Pick the 1-based real option. ``index`` 0 leaves the control alone."""
    if index <= 0:
        return ""
    target = _first_present(scope, selectors)
    if target is None:
        logger.info("no %s select found", kind)
        return ""
    options = target.locator("option")
    valid: list[tuple[int, str]] = []
    for option_index in range(options.count()):
        option = options.nth(option_index)
        text = (option.inner_text() or "").strip()
        value = (option.get_attribute("value") or "").strip()
        if not text or not value:
            continue
        lowered = text.lower()
        if any(snippet in lowered for snippet in SKIP_OPTION_SNIPPETS):
            continue
        valid.append((option_index, text))
    if not valid:
        logger.info("%s select has no concrete options", kind)
        return ""
    chosen_index, label = valid[min(index - 1, len(valid) - 1)]
    target.select_option(index=chosen_index)
    try:
        target.evaluate(
            """el => {
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }"""
        )
    except PlaywrightError:
        logger.debug("could not dispatch change on %s select", kind, exc_info=True)
    logger.info("selected %s: %s", kind, label)
    return label


def _ensure_calendar(page: Page, scope: Locator, config: ScrapeConfig) -> bool:
    if _day_visible(scope, config):
        return True
    clicked = _click_matching(scope, config.selectors.open_calendar, config.timeout_ms)
    if not clicked:
        clicked = click_in_shadow(scope, config.selectors.open_calendar_css)
        if clicked:
            logger.info("used shadowRoot fallback to open the calendar")
    if not clicked:
        logger.warning("could not find a control that opens the calendar")
    try:
        scope.locator(config.selectors.day_cell).first.wait_for(
            state="visible",
            timeout=config.timeout_ms,
        )
        return True
    except PlaywrightTimeout:
        logger.warning("calendar days did not become visible")
        return False


def _day_visible(scope: Locator, config: ScrapeConfig) -> bool:
    cells = scope.locator(config.selectors.day_cell)
    try:
        return cells.count() > 0 and cells.first.is_visible()
    except PlaywrightError:
        return False


def _collect_days(
    page: Page,
    scope: Locator,
    config: ScrapeConfig,
    *,
    chosen_service: str,
    chosen_staff: str,
) -> list[dict[str, str]]:
    total = min(config.max_days, scope.locator(config.selectors.day_cell).count())
    logger.info("selectable days in view: %s", total)
    rows: list[dict[str, str]] = []
    for index in range(total):
        cells = scope.locator(config.selectors.day_cell)
        if index >= cells.count():
            break
        cell = cells.nth(index)
        label = (cell.get_attribute("data-date") or cell.inner_text() or "").strip()
        previous = _card_signature(scope, config)
        try:
            cell.scroll_into_view_if_needed(timeout=1000)
            cell.click(timeout=config.timeout_ms)
        except PlaywrightError:
            logger.warning("could not open day %s", label or index + 1, exc_info=True)
            continue
        logger.info("opened %s", label or index + 1)
        if not _wait_for_change(page, scope, config, previous):
            logger.info("no result change for %s", label or index + 1)
            _pause(page, config.delay_seconds)
            continue
        if _expand_times(scope, config):
            _wait_for_change(page, scope, config, previous)
        _pause(page, config.delay_seconds)
        found = _extract_slots(
            scope,
            config,
            date_hint=label,
            chosen_service=chosen_service,
            chosen_staff=chosen_staff,
        )
        logger.info("collected %s slot(s) for %s", len(found), label or index + 1)
        rows.extend(found)
    return rows


def _advance_month(
    page: Page,
    scope: Locator,
    config: ScrapeConfig,
    previous_ids: tuple[str, ...],
) -> bool:
    clicked = _click_matching(scope, config.selectors.next_month, config.timeout_ms)
    if not clicked:
        clicked = click_in_shadow(scope, config.selectors.next_month)
        if clicked:
            logger.info("used shadowRoot fallback to open the next month")
    if not clicked:
        return False
    deadline = time.monotonic() + config.result_timeout_seconds
    while time.monotonic() < deadline:
        current = _day_ids(scope, config)
        if current and current != previous_ids:
            _pause(page, config.settle_seconds)
            logger.info("moved to the next month")
            return True
        _pause(page, 0.1)
    return False


def _extract_slots(
    scope: Locator,
    config: ScrapeConfig,
    *,
    date_hint: str,
    chosen_service: str,
    chosen_staff: str,
) -> list[dict[str, str]]:
    cards = _first_nonempty(scope, config.selectors.slot_card)
    if cards is None:
        return []
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for index in range(cards.count()):
        card = cards.nth(index)
        try:
            raw = card.inner_text() or ""
        except PlaywrightError:
            continue
        attributes = card.evaluate(
            """el => ({
                id: el.getAttribute('data-id') || el.id || '',
                date: el.getAttribute('data-date') || '',
                duration: el.getAttribute('data-duration')
                    || el.getAttribute('data-length')
                    || el.getAttribute('data-minutes')
                    || '',
                staff: el.getAttribute('data-staff') || el.getAttribute('data-instructor') || ''
            })"""
        )
        row = parse_slot(
            raw,
            attributes=attributes,
            date_hint=date_hint,
            book_url=_attribute(card, config.selectors.book_link, "href")
            or _attribute(card, config.selectors.book_link, "data-url"),
            title=_text_of(card, config.selectors.slot_title),
            staff_text=_text_of(card, config.selectors.slot_staff),
            source_site=config.source_site,
            venue_name=config.venue_name,
            venue_location=config.venue_location,
        )
        if chosen_service and not row["service"]:
            row["service"] = chosen_service
        if chosen_staff and not row["staff"]:
            row["staff"] = chosen_staff
        identity = (
            row["date_local"],
            row["start_time_local"],
            row["service"],
            row["staff"],
            row["slot_id"],
        )
        if identity in seen or not any(identity[:4]):
            continue
        seen.add(identity)
        rows.append(row)
    return rows


def _wait_for_change(
    page: Page,
    scope: Locator,
    config: ScrapeConfig,
    previous: str,
) -> bool:
    deadline = time.monotonic() + config.result_timeout_seconds
    last = previous
    stable_since: float | None = None
    while time.monotonic() < deadline:
        current = _card_signature(scope, config)
        now = time.monotonic()
        if current != last:
            last = current
            stable_since = now
            if current != previous and config.settle_seconds <= 0:
                return True
        elif current != previous and stable_since is not None:
            if now - stable_since >= config.settle_seconds:
                return True
        _pause(page, 0.05)
    return False


def _card_signature(scope: Locator, config: ScrapeConfig) -> str:
    parts: list[str] = []
    cards = _first_nonempty(scope, config.selectors.slot_card)
    if cards is not None:
        for index in range(min(cards.count(), 40)):
            try:
                parts.append(normalize_space(cards.nth(index).inner_text() or "")[:180])
            except PlaywrightError:
                parts.append("")
    empty_selector = config.selectors.empty_state
    if empty_selector:
        empty = scope.locator(empty_selector)
        try:
            if empty.count() and empty.first.is_visible():
                parts.append("EMPTY:" + normalize_space(empty.first.inner_text() or "")[:80])
        except PlaywrightError:
            pass
    return "|".join(parts)


def _day_ids(scope: Locator, config: ScrapeConfig) -> tuple[str, ...]:
    cells = scope.locator(config.selectors.day_cell)
    ids: list[str] = []
    try:
        count = min(cells.count(), config.max_days)
    except PlaywrightError:
        return ()
    for index in range(count):
        try:
            ids.append(cells.nth(index).get_attribute("data-date") or str(index))
        except PlaywrightError:
            ids.append(str(index))
    return tuple(ids)


def _expand_times(scope: Locator, config: ScrapeConfig) -> int:
    clicked = 0
    for selector in config.selectors.expand_times:
        locator = scope.locator(selector)
        try:
            count = min(locator.count(), 10)
        except PlaywrightError:
            continue
        if count == 0:
            continue
        for index in range(count):
            try:
                locator.nth(index).click(timeout=1500)
                clicked += 1
            except PlaywrightError:
                logger.debug("could not expand %s", selector, exc_info=True)
        if clicked:
            break
    if clicked:
        logger.info("expanded %s schedule control(s)", clicked)
    return clicked


def _click_matching(scope: Locator, selectors: Sequence[str], timeout_ms: int) -> bool:
    for selector in selectors:
        locator = scope.locator(selector)
        try:
            if locator.count() == 0:
                continue
            locator.first.scroll_into_view_if_needed(timeout=timeout_ms)
            locator.first.click(timeout=timeout_ms)
            logger.info("clicked %s", selector)
            return True
        except PlaywrightError:
            logger.debug("could not click %s", selector, exc_info=True)
    return False


def _first_present(scope: Locator, selectors: Sequence[str]) -> Locator | None:
    for selector in selectors:
        locator = scope.locator(selector)
        try:
            if locator.count():
                return locator.first
        except PlaywrightError:
            logger.debug("selector failed: %s", selector, exc_info=True)
    return None


def _first_nonempty(scope: Locator, selectors: Sequence[str]) -> Locator | None:
    for selector in selectors:
        locator = scope.locator(selector)
        try:
            if locator.count():
                return locator
        except PlaywrightError:
            logger.debug("selector failed: %s", selector, exc_info=True)
    return None


def _text_of(scope: Locator, selector: str) -> str:
    if not selector:
        return ""
    locator = scope.locator(selector)
    try:
        if locator.count() == 0:
            return ""
        return (locator.first.inner_text() or "").strip()
    except PlaywrightError:
        return ""


def _attribute(scope: Locator, selector: str, name: str) -> str:
    if not selector:
        return ""
    locator = scope.locator(selector)
    try:
        if locator.count() == 0:
            return ""
        return (locator.first.get_attribute(name) or "").strip()
    except PlaywrightError:
        return ""


def _pause(page: Page, seconds: float) -> None:
    if seconds <= 0:
        return
    page.wait_for_timeout(int(seconds * 1000))


def _capture(page: Page, config: ScrapeConfig) -> None:
    directory = config.diagnostics_dir
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    html_path = directory / f"page_{stamp}.html"
    png_path = directory / f"page_{stamp}.png"
    try:
        html_path.write_text(page.evaluate(SHADOW_HTML_JS), encoding="utf-8")
    except PlaywrightError:
        logger.debug("shadow HTML capture failed; saving page.content()", exc_info=True)
        html_path.write_text(page.content(), encoding="utf-8")
    try:
        page.screenshot(path=str(png_path), full_page=True)
    except PlaywrightError:
        logger.debug("screenshot failed", exc_info=True)
    logger.info("diagnostics saved in %s", directory)


def _ensure_logging() -> None:
    parent = logging.getLogger("booking_scraper")
    if parent.handlers:
        return
    parent.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    parent.addHandler(handler)
    parent.propagate = False
