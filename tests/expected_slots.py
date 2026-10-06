"""Slots baked into the offline fixture. Names and prices are synthetic."""

EXPECTED_IDS = [
    "nl-20260407-0930",
    "nl-20260407-1100",
    "nl-20260407-1800",
    "nl-20260408-0715",
    "nl-20260408-1300",
    "nl-20260410-1630",
    "nl-20260501-1000",
    "nl-20260501-1515",
    "nl-20260505-1200",
    "nl-20260512-0845",
    "nl-20260512-1900",
]

FIRST_SLOT = {
    "slot_id": "nl-20260407-0930",
    "date_local": "2026-04-07",
    "start_time_local": "09:30 AM",
    "end_time_local": "10:20 AM",
    "duration_min": "50",
    "service": "Assisted Stretch (50 mins)",
    "staff": "Avery Example",
    "room": "Studio A",
    "price": "£48.00",
    "capacity": "1",
    "spots_left": "1",
    "booked_count": "0",
    "timezone_hint": "GMT",
    "source_site": "demo.local",
    "venue_name": "Example Studio",
    "venue_location": "Example City",
    "book_url": "https://example.com/book/nl-20260407-0930",
}
