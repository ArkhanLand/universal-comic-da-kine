from dataclasses import replace
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest

from tests.helpers import make_test_clf
from ucd.output.cbz import (
    make_cbz_filename,
    make_cbz_filename_from_metadata,
    write_cbz,
)


def test_cbz_filename_uses_authoritative_series_and_issue(tmp_path: Path) -> None:
    clf = replace(
        make_test_clf(tmp_path),
        series="House Of X (2019)",
        issue_number="1",
        publication_date=date(2019, 7, 24),
    )

    assert make_cbz_filename(clf) == "House Of X (2019) #1.cbz"


def test_cbz_filename_preserves_series_variant(tmp_path: Path) -> None:
    clf = replace(
        make_test_clf(tmp_path),
        series="The Unbeatable Squirrel Girl (2015B)",
        issue_number="1",
        publication_date=date(2015, 10, 28),
    )

    assert make_cbz_filename(clf) == "The Unbeatable Squirrel Girl (2015B) #1.cbz"


def test_cbz_filename_sanitizes_colon_and_slash(tmp_path: Path) -> None:
    clf = replace(
        make_test_clf(tmp_path),
        series="Example: Alpha/Beta",
        issue_number="7",
        publication_date=date(2026, 9, 13),
    )

    assert make_cbz_filename(clf) == "Example_ Alpha_Beta #7.cbz"


def test_cbz_filename_can_be_generated_before_pages() -> None:
    assert (
        make_cbz_filename_from_metadata(
            "House Of X (2019) #1",
            "House Of X (2019)",
            "1",
        )
        == "House Of X (2019) #1.cbz"
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


@pytest.mark.parametrize(
    ("series", "expected"),
    [
        pytest.param(
            "Doctor Strange (2022-2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range Hyphen 1",
        ),
        pytest.param(
            "Doctor Strange (2022 - 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range Hyphen 2",
        ),
        pytest.param(
            "Doctor Strange (2022- 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range Hyphen 3",
        ),
        pytest.param(
            "Doctor Strange (2022 -2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range Hyphen 4",
        ),
        pytest.param(
            "Doctor Strange (2022–2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range en dash 1",
        ),
        pytest.param(
            "Doctor Strange (2022– 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range en dash 2",
        ),
        pytest.param(
            "Doctor Strange (2022 –2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range en dash 3",
        ),
        pytest.param(
            "Doctor Strange (2022 – 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range en dash 4",
        ),
        pytest.param(
            "Doctor Strange (2022—2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range em dash 1",
        ),
        pytest.param(
            "Doctor Strange (2022— 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range em dash 2",
        ),
        pytest.param(
            "Doctor Strange (2022 —2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range em dash 3",
        ),
        pytest.param(
            "Doctor Strange (2022 — 2024)",
            "Doctor Strange (2022–2024) #1.cbz",
            id="Date Range em dash 4",
        ),
    ],
)
def test_cbz_filename_year_ranges(
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


def test_cbz_filename_does_not_normalize_non_year_dashes() -> None:
    assert (
        make_cbz_filename_from_metadata(
            "Spider-Man - Deadpool #1",
            "Spider-Man - Deadpool",
            "1",
        )
        == "Spider-Man - Deadpool #1.cbz"
    ), "Only normalize year ranges, nothing else."
