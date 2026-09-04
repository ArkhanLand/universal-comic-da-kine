import httpx
import pytest

from comic_downloader.services.marvel_unlimited import MarvelService
from pathlib import Path


def test_matches_marvel_issue_url() -> None:
    """Test that my matches_url code is working"""
    service = MarvelService()

    assert service.matches_url("https://www.marvel.com/comics/issue/72984/house_of_x_2019_1")
    assert service.matches_url("https://marvel.com/comics/issue/49123")
    assert service.matches_url("https://www.marvel.com/comics/issue/49123/")

    # Not implemented yet
    assert not service.matches_url("72519")

    assert not service.matches_url("https://www.dc.com/comics/foo")


@pytest.mark.parametrize(
    ("comic_input", "expected"),
    [
        ("https://www.marvel.com/comics/issue/72984/house_of_x_2019_1", "72984"),
        ("https://marvel.com/comics/issue/72984/house_of_x_2019_1", "72984"),
        ("https://www.marvel.com/comics/issue/72984/", "72984"),
        ("https://www.marvel.com/comics/issue/72984", "72984"),
        ("72984", ValueError),
    ],
    ids=[
        "Full URL w/comic title",
        "Same as ^^^ without www",
        "Trailing slash only",
        "No trailing slash",
        "ValueError",
    ],
)
def test_get_catalog_id(comic_input: str, expected: str) -> None:
    """Test that get_catalog_id returns correct values"""
    service = MarvelService()

    if expected is ValueError:
        with pytest.raises(ValueError):
            service.get_catalog_id(comic_input)
    else:
        assert service.get_catalog_id(comic_input) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('foo "digitalComicID":"51975" bar', "51975"),
        ("no digital id here", ValueError),
    ],
    ids=[
        "digitalComicID found",
        "digitalComicID missing",
    ],
)
def test_get_digital_id(body: str, expected: str) -> None:
    """Test the web code that looks up the digital id"""
    catalog_id = "72984"
    url = f"https://www.marvel.com/comics/issue/{catalog_id}"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == url
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    service = MarvelService(client=client)

    if expected is ValueError:
        with pytest.raises(ValueError):
            service.get_digital_id(catalog_id)
    else:
        assert service.get_digital_id(catalog_id) == expected

@pytest.mark.network
def test_marvel_live_digital_id() -> None:
    service = MarvelService(cookie_file=Path("cookies.txt"))

    assert service.get_digital_id("72984") == "51975"
