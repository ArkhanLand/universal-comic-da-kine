# tests/helpers.py

from pathlib import Path

from ucd.models import Page, Publication


def make_test_publication(tmp_path: Path) -> Publication:
    cover = tmp_path / "cover-source.jpg"
    cover.write_bytes(b"cover")

    page1 = tmp_path / "page1-source.jpg"
    page1.write_bytes(b"page1")

    page2 = tmp_path / "page2-source.jpg"
    page2.write_bytes(b"page2")

    return Publication(
        service="example",
        service_id="1",
        service_series_id="2",
        title="Example #1",
        cover=Page(
            numbers=(),
            path=cover,
            width=100,
            height=150,
            content_type="image/jpeg",
            mode="RGB",
        ),
        narrative=(
            Page(
                numbers=(1,),
                path=page1,
                width=100,
                height=150,
                content_type="image/jpeg",
                mode="RGB",
            ),
            Page(
                numbers=(2,),
                path=page2,
                width=100,
                height=150,
                content_type="image/jpeg",
                mode="RGB",
            ),
        ),
    )
