import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest

from tests.helpers import make_test_publication
from ucd.output.cbz import (
    make_cbz_filename,
    make_cbz_filename_from_metadata,
    write_cbz,
)


def test_filename_metadata(tmp_path: Path) -> None:
    publication = replace(
        make_test_publication(tmp_path),
        series="House Of X (2019)",
        issue_number="1",
        publication_date=date(2019, 7, 24),
    )

    assert make_cbz_filename(publication) == "House Of X (2019) #1.cbz"


def test_filename_variant(tmp_path: Path) -> None:
    publication = replace(
        make_test_publication(tmp_path),
        series="The Unbeatable Squirrel Girl (2015B)",
        issue_number="1",
        publication_date=date(2015, 10, 28),
    )

    assert make_cbz_filename(publication) == "The Unbeatable Squirrel Girl (2015B) #1.cbz"


def test_filename_sanitize(tmp_path: Path) -> None:
    publication = replace(
        make_test_publication(tmp_path),
        series="Example: Alpha/Beta",
        issue_number="7",
        publication_date=date(2026, 9, 13),
    )

    assert make_cbz_filename(publication) == "Example_ Alpha_Beta #7.cbz"


def test_filename_early() -> None:
    assert (
        make_cbz_filename_from_metadata(
            "House Of X (2019) #1",
            "House Of X (2019)",
            "1",
        )
        == "House Of X (2019) #1.cbz"
    )


def test_archive_content(tmp_path: Path) -> None:
    publication = make_test_publication(tmp_path)

    destination = tmp_path / "example.cbz"
    write_cbz(publication, destination)

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


def test_reject_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "example.cbz"
    destination.write_bytes(b"existing")

    publication = make_test_publication(tmp_path)

    with pytest.raises(FileExistsError):
        write_cbz(publication, destination)


def test_allow_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "example.cbz"
    destination.write_bytes(b"existing")

    publication = make_test_publication(tmp_path)

    write_cbz(publication, destination, overwrite=True)

    with ZipFile(destination) as archive:
        assert "ComicInfo.xml" in archive.namelist()


@pytest.mark.parametrize(
    ("series", "expected"),
    [
        pytest.param(
            "Doctor Strange (2022-2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="hyphen-1",
        ),
        pytest.param(
            "Doctor Strange (2022 - 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="hyphen-2",
        ),
        pytest.param(
            "Doctor Strange (2022- 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="hyphen-3",
        ),
        pytest.param(
            "Doctor Strange (2022 -2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="hyphen-4",
        ),
        pytest.param(
            "Doctor Strange (2022–2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="en-1",
        ),
        pytest.param(
            "Doctor Strange (2022– 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="en-2",
        ),
        pytest.param(
            "Doctor Strange (2022 –2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="en-3",
        ),
        pytest.param(
            "Doctor Strange (2022 – 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="en-4",
        ),
        pytest.param(
            "Doctor Strange (2022—2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="em-1",
        ),
        pytest.param(
            "Doctor Strange (2022— 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="em-2",
        ),
        pytest.param(
            "Doctor Strange (2022 —2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="em-3",
        ),
        pytest.param(
            "Doctor Strange (2022 — 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="em-4",
        ),
    ],
)
def test_year_ranges(
    series: str,
    expected: str,
) -> None:
    assert (
        make_cbz_filename_from_metadata(
            f"{series} #1",
            series,
            "1",
        )
        == expected
    )


def test_retain_other_dashes() -> None:
    assert (
        make_cbz_filename_from_metadata(
            "Spider-Man - Deadpool #1",
            "Spider-Man - Deadpool",
            "1",
        )
        == "Spider-Man - Deadpool #1.cbz"
    ), "Only normalize year ranges, nothing else."


def test_archive_comment(tmp_path: Path) -> None:
    publication = make_test_publication(tmp_path)
    destination = tmp_path / "example.cbz"

    write_cbz(publication, destination)

    with ZipFile(destination) as archive:
        data = json.loads(archive.comment.decode("utf-8"))

    assert data["appID"] == "Universal Comic Da Kine"
    assert data["ComicBookInfo/1.0"]["title"] == "Example #1"


def test_asset_order(tmp_path: Path) -> None:
    publication = make_test_publication(tmp_path)
    mappings = [(18, 19), (), None, (17,), (20, 21, 22, 23)]
    pages = []
    for index, numbers in enumerate(mappings):
        path = tmp_path / f"source-{index}.png"
        path.write_bytes(f"image-{index}".encode())
        pages.append(replace(publication.narrative[0], numbers=numbers, path=path))
    publication = replace(publication, narrative=tuple(pages))
    destination = tmp_path / "mixed.cbz"
    write_cbz(publication, destination)
    with ZipFile(destination) as archive:
        assert archive.namelist() == [
            "ComicInfo.xml",
            "00000.jpg",
            *[f"{i:05}.png" for i in range(1, 6)],
        ]
        assert archive.read("00000.jpg") == b"cover"
        for index in range(5):
            assert archive.read(f"{index + 1:05}.png") == f"image-{index}".encode()
        assert b"<PageCount>" not in archive.read("ComicInfo.xml")
