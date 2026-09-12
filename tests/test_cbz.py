from datetime import date
from pathlib import Path
from zipfile import ZipFile

from ucd.models import CLF, Creator, Page, Pages
from ucd.output.cbz import write_cbz


def test_cbz_contains_metadata_and_page(tmp_path: Path) -> None:
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"not-a-real-jpeg")

    page = tmp_path / "00001.jpg"
    page.write_bytes(b"not-a-real-jpeg")

    clf = CLF(
        service="example",
        service_id="1",
        service_series_id="2",
        title="Example #1",
        series="Example",
        issue_number="1",
        publication_date=date(2026, 9, 3),
        creators=(Creator("Jane Writer", "writer"),),
        pages=Pages(
            cover=Page(
                number=None,
                path=cover,
                width=1,
                height=1,
                content_type="image/jpeg",
                mode="RGB",
            ),
            pages=(
                Page(
                    number=1,
                    path=page,
                    width=1,
                    height=1,
                    content_type="image/jpeg",
                    mode="RGB",
                ),
            ),
        ),
    )

    destination = tmp_path / "example.cbz"
    write_cbz(clf, [page], destination)
    with ZipFile(destination) as archive:
        assert archive.namelist() == ["ComicInfo.xml", "00001.jpg"]
