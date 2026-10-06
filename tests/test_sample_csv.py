from pathlib import Path

from tests.expected_slots import EXPECTED_IDS, FIRST_SLOT

from booking_scraper.storage import load_csv

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_appointments.csv"
REQUIRED = (
    "date_local",
    "start_time_local",
    "end_time_local",
    "service",
    "staff",
    "duration_min",
    "price",
    "spots_left",
)


def test_sample_csv_matches_the_fixture_contract():
    assert SAMPLE.is_file(), "examples/sample_appointments.csv is missing"
    rows = load_csv(SAMPLE)
    assert [row["slot_id"] for row in rows] == EXPECTED_IDS
    first = rows[0]
    for key, value in FIRST_SLOT.items():
        assert first[key] == value
    for row in rows:
        for field in REQUIRED:
            assert row[field], field
        assert row["source_site"] == "demo.local"
        assert row["venue_name"] == "Example Studio"
