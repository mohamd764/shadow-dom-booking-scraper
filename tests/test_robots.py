import pytest

from booking_scraper.robots import RobotsDisallowed, check_robots, robots_allow

UA = "ShadowDOMBookingScraper/1.0"
URL = "https://example.com/book"


def test_explicit_disallow_blocks_the_user_agent():
    body = "User-agent: *\nDisallow: /book\n"
    assert robots_allow(body, UA, URL) is False
    with pytest.raises(RobotsDisallowed, match="disallows"):
        check_robots(URL, UA, ignore=False, loader=lambda: body)


def test_allow_and_empty_policy():
    assert robots_allow("User-agent: *\nAllow: /\n", UA, URL) is True
    check_robots(URL, UA, ignore=False, loader=lambda: "User-agent: *\nAllow: /\n")
    check_robots(URL, UA, ignore=False, loader=lambda: "")


def test_unreadable_policy_does_not_raise():
    check_robots(URL, UA, ignore=False, loader=lambda: None)


def test_ignore_flag_skips_the_loader():
    def boom():
        raise AssertionError("loader should not run")

    check_robots(URL, UA, ignore=True, loader=boom)


def test_file_urls_skip_robots():
    check_robots("file:///tmp/fixture.html", UA, ignore=False, loader=lambda: "nope")
