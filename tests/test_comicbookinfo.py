import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from tests.helpers import make_test_clf
from ucd.models import Creator
from ucd.output.cbz import write_cbz
from ucd.output.comicbookinfo import make_comicbookinfo


def test_comicbookinfo_contains_metadata(tmp_path: Path) -> None:
    clf = replace(
        make_test_clf(tmp_path),
        series="Example Series",
        issue_number="1",
        volume=2,
        series_count=12,
        publication_date=date(2026, 9, 22),
        publisher="Example Comics",
        imprint="Example Imprint",
        language="en",
        age_rating="Teen",
        description="Example description",
        genres=("Superhero", "Science Fiction"),
        tags=("Magic", "Space"),
        story_arcs=("Example Arc",),
        characters=("Example Hero",),
        teams=("Example Team",),
        locations=("Example City",),
        creators=(
            Creator("Wanda Writer", "writer"),
            Creator("Pat Penciller", "penciller"),
            Creator("Casey Cover", "cover artist"),
        ),
    )

    data = json.loads(make_comicbookinfo(clf))

    assert data["appID"] == "Universal Comic Da Kine"

    info = data["ComicBookInfo/1.0"]

    assert "volume" not in info
    assert info["title"] == "Example #1"
    assert info["series"] == "Example Series"
    assert info["issue"] == "1"
    assert info["numberOfIssues"] == 12
    assert info["publisher"] == "Example Comics"
    assert info["publicationYear"] == 2026
    assert info["publicationMonth"] == 9
    assert info["genre"] == "Superhero, Science Fiction"
    assert info["tags"] == ["Magic", "Space"]
    assert info["language"] == clf.language
    assert info["lang"] == clf.language
    assert info["credits"] == [
        {"person": "Wanda Writer", "role": "Writer"},
        {"person": "Pat Penciller", "role": "Penciller"},
        {"person": "Casey Cover", "role": "CoverArtist"},
    ]


def test_cbz_rejects_oversized_comicbookinfo_comment(
    tmp_path: Path,
) -> None:
    clf = replace(
        make_test_clf(tmp_path),
        description="x" * 70000,
    )
    destination = tmp_path / "example.cbz"

    with pytest.raises(ValueError, match="ZIP comments are limited"):
        write_cbz(clf, destination)

    assert not destination.exists()
