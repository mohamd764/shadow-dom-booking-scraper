from datetime import UTC, datetime

from booking_scraper.parse import dedupe_rows, is_meaningful, parse_slot

SCRAPED_AT = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)

CARD = """
Assisted Stretch (50 mins)
with Avery Example
09:30 AM – 10:20 AM
£48.00
Capacity: 1 · spots left: 1 · Room: Studio A · GMT
Book
"""


def test_parse_full_card():
    row = parse_slot(
        CARD,
        attributes={
            "id": "nl-20260407-0930",
            "date": "2026-04-07",
            "duration": "50",
            "staff": "Avery Example",
        },
        title="Assisted Stretch (50 mins)",
        staff_text="with Avery Example",
        book_url="https://example.com/book/nl-20260407-0930",
        source_site="demo.local",
        venue_name="Example Studio",
        venue_location="Example City",
        scraped_at=SCRAPED_AT,
    )
    assert row["date_local"] == "2026-04-07"
    assert row["start_time_local"] == "09:30 AM"
    assert row["end_time_local"] == "10:20 AM"
    assert row["duration_min"] == "50"
    assert row["service"] == "Assisted Stretch (50 mins)"
    assert row["staff"] == "Avery Example"
    assert row["room"] == "Studio A"
    assert row["price"] == "£48.00"
    assert row["capacity"] == "1"
    assert row["spots_left"] == "1"
    assert row["booked_count"] == "0"
    assert row["timezone_hint"] == "GMT"
    assert row["slot_id"] == "nl-20260407-0930"
    assert row["book_url"].endswith("nl-20260407-0930")
    assert row["scrape_timestamp_utc"] == "2026-04-01T12:00:00+00:00"
    assert row["source_site"] == "demo.local"
    assert row["venue_name"] == "Example Studio"


def test_parse_derives_booked_count_and_normalizes_time():
    text = (
        "Recovery Session (30 mins)\nwith Jordan Example\n"
        "3:15 pm – 3:45 pm\n$32.00\nCapacity: 6 spots left: 4"
    )
    row = parse_slot(
        text,
        title="Recovery Session (30 mins)",
        scraped_at=SCRAPED_AT,
    )
    assert row["start_time_local"] == "03:15 PM"
    assert row["end_time_local"] == "03:45 PM"
    assert row["spots_left"] == "4"
    assert row["capacity"] == "6"
    assert row["booked_count"] == "2"
    assert row["price"] == "$32.00"
    assert row["duration_min"] == "30"


def test_duration_falls_back_to_attribute():
    row = parse_slot(
        "with Sam Example\n08:45 AM",
        attributes={"duration": "30", "staff": "Sam Example"},
        title="Recovery Session",
        date_hint="2026-05-12",
        scraped_at=SCRAPED_AT,
    )
    assert row["duration_min"] == "30"
    assert row["date_local"] == "2026-05-12"
    assert row["staff"] == "Sam Example"
    assert row["end_time_local"] == ""
    assert row["booked_count"] == ""


def test_attribute_date_wins_over_hint():
    row = parse_slot("09:00 AM", attributes={"date": "2026-04-08"}, date_hint="2026-04-01")
    assert row["date_local"] == "2026-04-08"


def test_blank_rows_are_not_meaningful_and_duplicates_collapse():
    blank = parse_slot("Book", scraped_at=SCRAPED_AT)
    assert is_meaningful(blank) is False

    first = parse_slot(
        "09:30 AM\n£10",
        title="Stretch",
        staff_text="Avery Example",
        date_hint="2026-04-07",
        scraped_at=SCRAPED_AT,
    )
    duplicate = dict(first)
    duplicate["price"] = "£99"
    duplicate["scrape_timestamp_utc"] = "2026-05-01T00:00:00+00:00"
    other = parse_slot(
        "11:00 AM\n£10",
        title="Stretch",
        staff_text="Avery Example",
        date_hint="2026-04-07",
        scraped_at=SCRAPED_AT,
    )
    kept = dedupe_rows([blank, first, duplicate, other])
    assert len(kept) == 2
    assert kept[0]["price"] == "£10"
    assert kept[0]["scrape_timestamp_utc"] == "2026-04-01T12:00:00+00:00"
    assert kept[1]["start_time_local"] == "11:00 AM"
