from pathlib import Path

import pytest

from ucd.input.marvel_unlimited import MarvelUnlimitedAdapter


@pytest.mark.network
def test_marvel_live_get_issue_data() -> None:
    service = MarvelUnlimitedAdapter(cookie_file=Path("cookies.txt"))

    meta = service.get_issue_data("72984")

    assert meta.catalog_id == "72984", "Store Catalog ID"
    assert meta.digital_id == "51975", "Store Service ID"
    assert meta.issue_number == "1", "Issue Number"
    assert meta.series_id == "26338", "Series ID"


@pytest.mark.network
def test_marvel_live_get_metadata() -> None:
    """Test the web code that looks up live Marvel data"""
    service = MarvelUnlimitedAdapter(cookie_file=Path("cookies.txt"))

    meta = service.get_metadata("51975")

    assert meta["id"] == 51975, "Digital ID"
    assert meta["catalog_id"] == 72984, "Catalog ID"
    assert meta["title"] == "House Of X (2019) #1", "Title"
    assert meta["series_title"] == "House Of X (2019)", "Series Title"
    assert meta["release_date"] == "2019-07-24", "Release Date"


@pytest.mark.network
def test_marvel_live_get_page_sources() -> None:
    service = MarvelUnlimitedAdapter(cookie_file=Path("cookies.txt"))

    sources = service.get_page_sources("51975")

    assert sources.cover.number is None
    assert sources.cover.url.startswith("https://cdn.marvel.com/")

    assert len(sources.pages) > 0
    assert sources.pages[0].number == 1
    assert sources.pages[0].url.startswith("https://cdn.marvel.com/")

    assert sources.pages[1].number == 2
    assert sources.pages[1].url.startswith("https://cdn.marvel.com/")


@pytest.mark.network
def test_marvel_live_get_pages() -> None:
    service = MarvelUnlimitedAdapter(cookie_file=Path("cookies.txt"))

    digital_id = "51975"
    sources = service.get_page_sources(digital_id)

    pages = service.get_pages(
        digital_id,
        sources,
    )

    assert pages.cover.number is None
    assert pages.cover.path.exists()
    assert pages.cover.path.stat().st_size > 0
    assert pages.cover.width > 0
    assert pages.cover.height > 2
    assert pages.cover.content_type.startswith("image/")

    # Marvel comics always have page count divisible by four
    assert len(pages.pages) % 4 == 0, (
        f"Expected a Marvel page count to be divisible by four; got {len(pages.pages)}"
    )

    page_no = 1

    for page in pages.pages:
        assert page.path.exists()
        assert page.path.stat().st_size > 0
        assert page.width > 0
        assert page.height > 0
        assert page.content_type.startswith("image/")
        assert page.number == page_no
        page_no = page_no + 1
