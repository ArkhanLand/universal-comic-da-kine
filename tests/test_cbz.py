from pathlib import Path
from zipfile import ZipFile

import pytest

from ucd.models import CLF, Page, Pages
from ucd.output.cbz import write_cbz


def make_test_clf(tmp_path: Path) -> CLF:
    cover = tmp_path / "cover-source.jpg"
    cover.write_bytes(b"cover")

    page1 = tmp_path / "page1-source.jpg"
    page1.write_bytes(b"page1")

    page2 = tmp_path / "page2-source.jpg"
    page2.write_bytes(b"page2")

    return CLF(
        service="example",
        service_id="1",
        service_series_id="2",
        title="Example #1",
        pages=Pages(
            cover=Page(
                number=None,
                path=cover,
                width=100,
                height=150,
                content_type="image/jpeg",
                mode="RGB",
            ),
            pages=(
                Page(
                    number=1,
                    path=page1,
                    width=100,
                    height=150,
                    content_type="image/jpeg",
                    mode="RGB",
                ),
                Page(
                    number=2,
                    path=page2,
                    width=100,
                    height=150,
                    content_type="image/jpeg",
                    mode="RGB",
                ),
            ),
        ),
    )


def test_cbz_contains_metadata_cover_and_pages(tmp_path: Path) -> None:
    clf = make_test_clf(tmp_path)

    destination = tmp_path / "example.cbz"
    write_cbz(clf, destination)

    with ZipFile(destination) as archive:
        assert archive.namelist() == [
            "ComicInfo.xml",
            "00000.jpg",
            "00001.jpg",
            "00002.jpg",
        ]

        assert archive.read("00000.jpg") == b"cover"
        assert archive.read("00001.jpg") == b"page1"
        assert archive.read("00002.jpg") == b"page2"


def test_cbz_refuses_to_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "example.cbz"
    destination.write_bytes(b"existing")

    clf = make_test_clf(tmp_path)

    with pytest.raises(FileExistsError):
        write_cbz(clf, destination)


def test_cbz_overwrites_when_requested(tmp_path: Path) -> None:
    destination = tmp_path / "example.cbz"
    destination.write_bytes(b"existing")

    clf = make_test_clf(tmp_path)

    write_cbz(clf, destination, overwrite=True)

    with ZipFile(destination) as archive:
        assert "ComicInfo.xml" in archive.namelist()
