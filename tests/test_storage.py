from datetime import UTC, datetime

from booking_scraper.parse import parse_slot
from booking_scraper.storage import load_csv, merge_csv

SCRAPED_AT = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


def _row(start: str, **kwargs) -> dict[str, str]:
    return parse_slot(
        f"{start}\n£48.00\nCapacity: 1 spots left: 1",
        title="Assisted Stretch (50 mins)",
        staff_text="Avery Example",
        date_hint="2026-04-07",
        scraped_at=SCRAPED_AT,
        **kwargs,
    )


def test_merge_appends_new_rows_and_keeps_the_first_copy(tmp_path):
    path = tmp_path / "appointments.csv"
    original = _row("09:30 AM")
    first = merge_csv(path, [original], sleeper=lambda _seconds: None)
    assert first.added == 1
    assert first.total == 1
    assert first.fallback is False

    newer = dict(original)
    newer["price"] = "£999.00"
    newer["spots_left"] = "0"
    newer["scrape_timestamp_utc"] = "2026-05-01T00:00:00+00:00"
    extra = _row("11:00 AM")
    second = merge_csv(path, [newer, extra, {}], sleeper=lambda _seconds: None)

    rows = load_csv(path)
    assert second.added == 1
    assert second.total == 2
    assert len(rows) == 2
    assert rows[0]["price"] == "£48.00"
    assert rows[0]["service"] == "Assisted Stretch (50 mins)"
    assert rows[0]["scrape_timestamp_utc"] == "2026-04-01T12:00:00+00:00"
    assert rows[1]["start_time_local"] == "11:00 AM"


def test_blank_identity_rows_are_dropped(tmp_path):
    path = tmp_path / "appointments.csv"
    junk = {
        "end_time_local": "02:00 PM",
        "notes": "Search",
        "source_site": "",
        "date_local": "",
        "start_time_local": "",
        "service": "",
        "staff": "",
    }
    stats = merge_csv(path, [junk], sleeper=lambda _seconds: None)
    assert stats.total == 0
    assert load_csv(path) == []


def test_locked_csv_retries_then_writes(tmp_path, monkeypatch):
    path = tmp_path / "appointments.csv"
    calls = {"n": 0}
    from booking_scraper import storage

    real_write = storage.write_csv

    def flaky(target, rows, fieldnames):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked")
        real_write(target, rows, fieldnames)

    monkeypatch.setattr(storage, "write_csv", flaky)
    stats = merge_csv(path, [_row("09:30 AM")], attempts=5, sleeper=lambda _seconds: None)
    assert calls["n"] == 3
    assert stats.path == path
    assert stats.fallback is False
    assert len(load_csv(path)) == 1


def test_locked_csv_falls_back_to_a_sibling_file(tmp_path, monkeypatch):
    path = tmp_path / "appointments.csv"
    from booking_scraper import storage

    real_write = storage.write_csv

    def flaky(target, rows, fieldnames):
        if target == path:
            raise PermissionError("locked")
        real_write(target, rows, fieldnames)

    monkeypatch.setattr(storage, "write_csv", flaky)
    stats = merge_csv(path, [_row("09:30 AM")], attempts=2, sleeper=lambda _seconds: None)
    assert stats.fallback is True
    assert stats.path != path
    assert stats.path.parent == path.parent
    assert stats.path.exists()
    assert len(load_csv(stats.path)) == 1
