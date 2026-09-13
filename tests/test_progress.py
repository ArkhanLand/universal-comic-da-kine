from io import BytesIO

import httpx
from PIL import Image

from ucd.input.marvel_unlimited import (
    MarvelUnlimitedAdapter,
    _MarvelPageSource,
    _MarvelPageSources,
)


def test_get_pages_reports_progress(tmp_path, monkeypatch) -> None:
    images: dict[str, bytes] = {}

    for number in range(1, 4):
        image = Image.new("RGB", (100, 200))
        buffer = BytesIO()
        image.save(buffer, format="JPEG")
        images[f"https://example.com/page{number}"] = buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=images[str(request.url)],
            headers={"Content-Type": "image/jpeg"},
        )

    monkeypatch.setattr("ucd.input.marvel_unlimited.WORK_PATH", tmp_path)

    adapter = MarvelUnlimitedAdapter(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    sources = _MarvelPageSources(
        cover=_MarvelPageSource(number=None, url="https://example.com/page1"),
        pages=[
            _MarvelPageSource(number=1, url="https://example.com/page2"),
            _MarvelPageSource(number=2, url="https://example.com/page3"),
        ],
    )
    updates: list[tuple[int, int]] = []

    adapter.get_pages(
        "51975",
        sources,
        progress=lambda completed, total: updates.append((completed, total)),
    )

    assert updates == [(0, 3), (1, 3), (2, 3), (3, 3)]
