from io import BytesIO
from typing import Any

import httpx
import pytest
from PIL import Image

from ucd.exceptions import (
    IneligibleError,
    InvalidComicInputError,
    ServiceResponseError,
)
from ucd.input.marvel_unlimited import MarvelUnlimitedAdapter, _MarvelPageSource, _MarvelPageSources


def test_matches_marvel_issue_url() -> None:
    """Test that my matches_url code is working"""
    service = MarvelUnlimitedAdapter()

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
        ("72984", "72984"),
        ("LOLWUT", InvalidComicInputError),
    ],
    ids=[
        "Full URL w/comic title",
        "Same as ^^^ without www",
        "Trailing slash only",
        "No trailing slash",
        "Bare catalog_id",
        "Service Response Error",
    ],
)
def test_get_catalog_id(comic_input: str, expected: str) -> None:
    """Test that get_catalog_id returns correct values"""
    service = MarvelUnlimitedAdapter()

    if expected is InvalidComicInputError:
        with pytest.raises(InvalidComicInputError):
            service.get_catalog_id(comic_input)
    else:
        assert service.get_catalog_id(comic_input) == expected


ComicIssuebody = """
<html>
<script>
window['__marvel-fitt__']={
    "page": {
        "content": {
            "issueDetails": {
                "id": "72984",
                "digitalComicID": "51975",
                "issue": 1,
                "seriesId": 26338
            }
        }
    }
};
</script>
</html>
"""


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (ComicIssuebody, "51975"),
        ("no digital id here", ServiceResponseError),
    ],
    ids=[
        "digitalComicID found",
        "digitalComicID missing",
    ],
)
def test_get_issue_data(body: str, expected: str) -> None:
    """Test the web code that looks up the digital id"""
    catalog_id = "72984"
    url = f"https://www.marvel.com/comics/issue/{catalog_id}"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == url
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    service = MarvelUnlimitedAdapter(client=client)

    if expected is ServiceResponseError:
        with pytest.raises(ServiceResponseError):
            service.get_issue_data(catalog_id)
    else:
        issue_data = service.get_issue_data(catalog_id)

        assert issue_data.catalog_id == "72984"
        assert issue_data.digital_id == "51975"
        assert issue_data.issue_number == "1"
        assert issue_data.series_id == "26338"


meta_body = {
    "code": 200,
    "status": "OK",
    "data": {
        "results": [
            {
                "id": 51975,
                "issue_meta": {
                    "id": 51975,
                    "catalog_id": 72984,
                    "title": "House Of X (2019) #1",
                    "series_title": "House Of X (2019)",
                    "release_date": "2019-07-24",
                },
            }
        ]
    },
}


@pytest.mark.parametrize(
    ("digital_id", "expected"),
    [
        ("51975", meta_body),
        ("no metadata here", InvalidComicInputError),
    ],
    ids=[
        "Metadata found",
        "invalid digital_id",
    ],
)
def test_get_metadata(digital_id: str, expected: dict[str, Any] | type[ValueError]) -> None:
    """Test getting metadata"""

    url = f"https://bifrost.marvel.com/v1/catalog/digital-comics/metadata/{digital_id}"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == url
        return httpx.Response(200, json=meta_body)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    service = MarvelUnlimitedAdapter(client=client)

    if expected is InvalidComicInputError:
        with pytest.raises(InvalidComicInputError):
            meta = service.get_metadata(digital_id)
    else:
        meta = service.get_metadata(digital_id)

        assert meta["id"] == 51975, "Digital ID"
        assert meta["catalog_id"] == 72984, "Catalog ID"
        assert meta["title"] == "House Of X (2019) #1", "Title"
        assert meta["series_title"] == "House Of X (2019)", "Series Title"
        assert meta["release_date"] == "2019-07-24", "Release Date"


page_sources_body = {
    "data": {
        "results": [
            {
                "auth_state": {
                    "subscriber": True,
                },
                "pages": [
                    {
                        "assets": {
                            "source": "https://example.com/page1.jpg",
                        }
                    },
                    {
                        "assets": {
                            "source": "https://example.com/page2.jpg",
                        }
                    },
                    {
                        "assets": {
                            "source": "https://example.com/page3.jpg",
                        }
                    },
                ],
            }
        ]
    }
}


def test_get_page_sources() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/51975")
        return httpx.Response(200, json=page_sources_body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = MarvelUnlimitedAdapter(client=client)

    sources = adapter.get_page_sources("51975")

    assert sources.cover == _MarvelPageSource(
        number=None,
        url="https://example.com/page1.jpg",
    )

    assert sources.pages == [
        _MarvelPageSource(
            number=1,
            url="https://example.com/page2.jpg",
        ),
        _MarvelPageSource(
            number=2,
            url="https://example.com/page3.jpg",
        ),
    ]


@pytest.mark.parametrize(
    "digital_id",
    [
        "no digital id here",
        "Some other text",
    ],
)
def test_get_page_sources_invalid_digital_id(digital_id: str) -> None:
    adapter = MarvelUnlimitedAdapter()

    with pytest.raises(InvalidComicInputError):
        adapter.get_page_sources(digital_id)


def test_get_page_sources_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = MarvelUnlimitedAdapter(client=client)

    with pytest.raises(httpx.HTTPStatusError):
        adapter.get_page_sources("51975")


def test_get_page_sources_requires_subscription():
    body = {
        "data": {
            "results": [
                {
                    "auth_state": {
                        "subscriber": False,
                    },
                    "pages": [],
                }
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = MarvelUnlimitedAdapter(client=client)

    with pytest.raises(IneligibleError):
        adapter.get_page_sources("51975")


def test_download_image(tmp_path, httpx_mock):
    image = Image.new("RGB", (100, 200))
    buffer = BytesIO()

    image.save(buffer, format="JPEG")
    image_bytes = buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.com/page"

        return httpx.Response(
            200,
            content=image_bytes,
            headers={"Content-Type": "image/jpeg"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = MarvelUnlimitedAdapter(client=client)

    result = adapter._download_image("https://example.com/page", tmp_path / "page")

    assert result.filename == tmp_path / "page.jpg"
    assert result.filename.exists()
    assert result.filename.read_bytes() == image_bytes
    assert result.width == 100
    assert result.height == 200
    assert result.content_type == "image/jpeg"
    assert result.mode == "RGB"


def test_get_pages(tmp_path, monkeypatch):
    images = {}

    for number, size in ((1, (100, 200)), (2, (300, 400)), (3, (500, 600))):
        image = Image.new("RGB", size)
        buffer = BytesIO()
        image.save(buffer, format="JPEG")
        images[f"https://example.com/page{number}"] = buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=images[str(request.url)],
            headers={"Content-Type": "image/jpeg"},
        )

    monkeypatch.setattr(
        "ucd.input.marvel_unlimited.WORK_PATH",
        tmp_path,
    )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = MarvelUnlimitedAdapter(client=client)

    sources = _MarvelPageSources(
        cover=_MarvelPageSource(
            number=None,
            url="https://example.com/page1",
        ),
        pages=[
            _MarvelPageSource(
                number=1,
                url="https://example.com/page2",
            ),
            _MarvelPageSource(
                number=2,
                url="https://example.com/page3",
            ),
        ],
    )

    pages = adapter.get_pages("51975", sources)

    assert pages.cover.number is None
    assert pages.cover.width == 100
    assert pages.cover.height == 200
    assert pages.cover.content_type == "image/jpeg"
    assert pages.cover.mode == "RGB"
    assert pages.cover.path.exists()

    assert len(pages.pages) == 2

    assert pages.pages[0].number == 1
    assert pages.pages[0].width == 300
    assert pages.pages[0].height == 400
    assert pages.pages[0].content_type == "image/jpeg"
    assert pages.pages[0].mode == "RGB"
    assert pages.pages[0].path.exists()

    assert pages.pages[1].number == 2
    assert pages.pages[1].width == 500
    assert pages.pages[1].height == 600
    assert pages.pages[1].content_type == "image/jpeg"
    assert pages.pages[1].mode == "RGB"
    assert pages.pages[1].path.exists()
