"""Pure parsing and dedupe helpers for appointment slot cards.

The browser layer hands this module text and attributes. Keeping the rules
here means they can be tested without launching Chromium.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

FIELDNAMES: tuple[str, ...] = (
    "source_site",
    "venue_name",
    "venue_location",
    "date_local",
    "start_time_local",
    "end_time_local",
    "duration_min",
    "service",
    "staff",
    "room",
    "price",
    "capacity",
    "spots_left",
    "booked_count",
    "book_url",
    "slot_id",
    "timezone_hint",
    "notes",
    "scrape_timestamp_utc",
)

DEDUP_FIELDS: tuple[str, ...] = (
    "date_local",
    "start_time_local",
    "service",
    "staff",
)

TIME_RE = re.compile(r"\b(\d{1,2}:\d{2}\s?(?:AM|PM)?)\b", re.IGNORECASE)
PRICE_RE = re.compile(r"([£$€]\s?\d+(?:[.,]\d{2})?)")
DURATION_RE = re.compile(r"(\d{1,3})\s*(?:min|mins|minutes)\b", re.IGNORECASE)
SPOTS_RE = re.compile(
    r"(?:spots?\s*left|remaining)\s*[:\-]?\s*(\d+)"
    r"|\b(\d+)\s*spots?\s*left\b(?!\s*[:\-])",
    re.IGNORECASE,
)
CAPACITY_RE = re.compile(r"capacity\s*[:\-]?\s*(\d+)", re.IGNORECASE)
STAFF_RE = re.compile(r"(?:with|by)\s+([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,3})")
ROOM_RE = re.compile(
    r"\b(?:room|studio|resource)\s*[:\-]\s*([A-Za-z0-9][A-Za-z0-9 .'\-]{0,40})",
    re.IGNORECASE,
)
TZ_RE = re.compile(r"\b(UTC|GMT|BST|CET|CEST|EST|EDT|PST|PDT)\b", re.IGNORECASE)
_TIME_PART_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)?", re.IGNORECASE)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def dedup_key(
    row: Mapping[str, str],
    fields: Sequence[str] = DEDUP_FIELDS,
) -> tuple[str, ...]:
    return tuple((row.get(field) or "").strip() for field in fields)


def is_meaningful(
    row: Mapping[str, str],
    fields: Sequence[str] = DEDUP_FIELDS,
) -> bool:
    """Drop blank placeholder rows that have no slot identity."""
    return any(dedup_key(row, fields))


def dedupe_rows(
    rows: Iterable[Mapping[str, str]],
    fields: Sequence[str] = DEDUP_FIELDS,
) -> list[dict[str, str]]:
    """Keep the first meaningful row for each dedupe key."""
    seen: set[tuple[str, ...]] = set()
    kept: list[dict[str, str]] = []
    for row in rows:
        if not is_meaningful(row, fields):
            continue
        key = dedup_key(row, fields)
        if key in seen:
            continue
        seen.add(key)
        kept.append({field: (row.get(field) or "") for field in FIELDNAMES})
    return kept


def parse_slot(
    text: str,
    *,
    attributes: Mapping[str, str] | None = None,
    date_hint: str | None = None,
    book_url: str = "",
    title: str = "",
    staff_text: str = "",
    source_site: str = "",
    venue_name: str = "",
    venue_location: str = "",
    scraped_at: datetime | None = None,
) -> dict[str, str]:
    """Turn one slot card into a CSV row. Missing fields stay empty strings."""
    attrs = {key: (value or "").strip() for key, value in (attributes or {}).items()}
    raw = text or ""
    flat = normalize_space(raw)

    times = [_normalize_time(match) for match in TIME_RE.findall(raw)]
    start = times[0] if times else ""
    end = times[1] if len(times) > 1 else ""

    price_match = PRICE_RE.search(flat)
    price = price_match.group(1).replace(" ", "") if price_match else ""

    duration = ""
    duration_match = DURATION_RE.search(flat)
    if duration_match:
        duration = str(int(duration_match.group(1)))
    if not duration:
        for key in ("duration", "data-duration", "length", "minutes"):
            value = attrs.get(key, "")
            if value.isdigit():
                duration = str(int(value))
                break

    spots = _first_int(SPOTS_RE, flat)
    capacity = _first_int(CAPACITY_RE, flat)
    booked = ""
    if capacity.isdigit() and spots.isdigit() and int(spots) <= int(capacity):
        booked = str(int(capacity) - int(spots))

    staff = _clean_staff(staff_text) or _clean_staff(attrs.get("staff", ""))
    if not staff:
        staff_match = STAFF_RE.search(flat)
        if staff_match:
            staff = _clean_staff(staff_match.group(1))

    room = ""
    room_match = ROOM_RE.search(flat)
    if room_match:
        room = normalize_space(room_match.group(1)).strip(" -·")

    tz_match = TZ_RE.search(flat)
    timezone_hint = tz_match.group(1).upper() if tz_match else ""

    service = normalize_space(title) or _infer_service(raw)
    date_local = attrs.get("date") or (date_hint or "").strip()
    slot_id = attrs.get("id") or attrs.get("data-id") or ""

    moment = scraped_at or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)

    return {
        "source_site": source_site,
        "venue_name": venue_name,
        "venue_location": venue_location,
        "date_local": date_local,
        "start_time_local": start,
        "end_time_local": end,
        "duration_min": duration,
        "service": service,
        "staff": staff,
        "room": room,
        "price": price,
        "capacity": capacity,
        "spots_left": spots,
        "booked_count": booked,
        "book_url": (book_url or "").strip(),
        "slot_id": slot_id,
        "timezone_hint": timezone_hint,
        "notes": flat[:500],
        "scrape_timestamp_utc": moment.astimezone(UTC).isoformat(timespec="seconds"),
    }


def _normalize_time(token: str) -> str:
    match = _TIME_PART_RE.fullmatch(token.strip())
    if not match:
        return normalize_space(token).upper()
    hour = int(match.group(1))
    minute = match.group(2)
    suffix = match.group(3)
    if suffix:
        return f"{hour:02d}:{minute} {suffix.upper()}"
    return f"{hour:02d}:{minute}"


def _first_int(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    for group in match.groups():
        if group and group.isdigit():
            return str(int(group))
    return ""


def _clean_staff(value: str) -> str:
    text = normalize_space(value)
    text = re.sub(r"^(?:with|by)\s+", "", text, flags=re.IGNORECASE)
    return text.strip(" -")


def _infer_service(text: str) -> str:
    for line in text.splitlines():
        candidate = normalize_space(line)
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in {"book", "book now"}:
            continue
        if lowered.startswith(("with ", "by ", "capacity", "spots", "room:", "studio:")):
            continue
        compact = candidate.replace(" ", "")
        if PRICE_RE.fullmatch(compact):
            continue
        if DURATION_RE.fullmatch(candidate):
            continue
        if _TIME_PART_RE.fullmatch(candidate):
            continue
        return candidate
    return ""
