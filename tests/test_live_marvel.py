from pathlib import Path

import pytest

from comic_downloader.services.marvel_unlimited import MarvelService


@pytest.mark.network
def test_marvel_live_digital_id() -> None:
    service = MarvelService(cookie_file=Path("cookies.txt"))

    assert service.get_digital_id("72984") == "51975"


@pytest.mark.network
def test_marvel_live_metadata() -> None:
    """Test the web code that looks up live Marvel data"""
    service = MarvelService(cookie_file=Path("cookies.txt"))

    meta = service.get_metadata("51975")

    assert meta["id"] == 51975, "Digital ID"
    assert meta["catalog_id"] == 72984, "Catalog ID"
    assert meta["title"] == "House Of X (2019) #1", "Title"
    assert meta["series_title"] == "House Of X (2019)", "Series Title"
    assert meta["release_date"] == "2019-07-24", "Release Date"
