"""Incremental CSV merges with lock retries.

Excel and sync clients often hold the output file open. A locked write is
retried with backoff. If it never clears, a timestamped sibling file keeps
the rows instead of dropping them.
"""

from __future__ import annotations

import csv
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from booking_scraper.parse import DEDUP_FIELDS, FIELDNAMES, dedupe_rows, is_meaningful

logger = logging.getLogger("booking_scraper.storage")

Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class MergeStats:
    path: Path
    added: int
    total: int
    fallback: bool


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [
            {key: (value if value is not None else "") for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def write_csv(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    fieldnames: Sequence[str] = FIELDNAMES,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fieldnames),
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def merge_csv(
    path: Path,
    incoming: Sequence[Mapping[str, str]],
    *,
    attempts: int = 8,
    sleeper: Sleeper = time.sleep,
    fieldnames: Sequence[str] = FIELDNAMES,
    keys: Sequence[str] = DEDUP_FIELDS,
) -> MergeStats:
    """Merge ``incoming`` into ``path``. Existing keys win, so history is kept."""
    fresh = [row for row in incoming if is_meaningful(row, keys)]
    backoff = 0.5
    last_error: PermissionError | None = None

    for attempt in range(1, attempts + 1):
        try:
            existing = load_csv(path)
            merged, added = _merge(existing, fresh, keys)
            write_csv(path, merged, fieldnames)
            logger.info("wrote %s rows (%s new) to %s", len(merged), added, path)
            return MergeStats(path=path, added=added, total=len(merged), fallback=False)
        except PermissionError as exc:
            last_error = exc
            logger.warning(
                "CSV is locked (%s); retry %s/%s in %.1fs",
                path,
                attempt,
                attempts,
                backoff,
            )
            sleeper(backoff)
            backoff = min(backoff * 1.5, 3.0)

    fallback = path.with_name(f"{path.stem}_{_utc_stamp()}{path.suffix}")
    logger.error(
        "could not unlock %s (%s); writing %s",
        path,
        last_error,
        fallback,
    )
    try:
        existing = load_csv(path)
    except (OSError, csv.Error):
        existing = []
    merged, added = _merge(existing, fresh, keys)
    write_csv(fallback, merged, fieldnames)
    logger.info("wrote %s rows (%s new) to fallback %s", len(merged), added, fallback)
    return MergeStats(path=fallback, added=added, total=len(merged), fallback=True)


def _merge(
    existing: Sequence[Mapping[str, str]],
    incoming: Sequence[Mapping[str, str]],
    keys: Sequence[str],
) -> tuple[list[dict[str, str]], int]:
    prior = dedupe_rows(existing, keys)
    seen = {_key(row, keys) for row in prior}
    added_rows: list[Mapping[str, str]] = []
    for row in dedupe_rows(incoming, keys):
        key = _key(row, keys)
        if key in seen:
            continue
        seen.add(key)
        added_rows.append(row)
    return prior + [{field: row.get(field, "") for field in FIELDNAMES} for row in added_rows], len(
        added_rows
    )


def _key(row: Mapping[str, str], keys: Sequence[str]) -> tuple[str, ...]:
    return tuple((row.get(field) or "").strip() for field in keys)


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
