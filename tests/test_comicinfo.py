import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import date
from pathlib import Path

from tests.helpers import make_test_clf
from ucd.models import Creator
from ucd.output.comicinfo import make_comicinfo


def test_comicinfo_contains_metadata(tmp_path: Path) -> None:
    clf = replace(
        make_test_clf(tmp_path),
        source_url="https://example.com/comic/1",
        series="Example",
        issue_number="1",
        volume=2,
        series_count=12,
        publication_date=date(2026, 9, 3),
        publisher="Example Comics",
        imprint="Example Imprint",
        language="en",
        age_rating="RATED T+",
        description="Example description",
        genres=("Superhero", "Science Fiction"),
        tags=("Example Tag",),
        story_arcs=("Example Arc",),
        characters=("Alice", "Bob"),
        teams=("Example Team",),
        locations=("Example City",),
        creators=(
            Creator("Jane Writer", "writer"),
            Creator("Pat Pencil", "penciller"),
            Creator("Casey Cover", "cover artist"),
        ),
    )

    root = ET.fromstring(make_comicinfo(clf))

    def value(tag: str) -> str | None:
        element = root.find(tag)
        return element.text if element is not None else None

    assert value("Title") == "Example #1"
    assert value("Series") == "Example"
    assert value("Number") == "1"
    assert value("Count") == "12"
    assert value("Volume") == "2"
    assert value("Summary") == "Example description"
    assert value("Publisher") == "Example Comics"
    assert value("Imprint") == "Example Imprint"
    assert value("Genre") == "Superhero, Science Fiction"
    assert value("Tags") == "Example Tag"
    assert value("Web") == "https://example.com/comic/1"
    assert value("PageCount") == "3"
    assert value("LanguageISO") == "en"
    assert value("AgeRating") == "Teen"
    assert value("StoryArc") == "Example Arc"
    assert value("Characters") == "Alice, Bob"
    assert value("Teams") == "Example Team"
    assert value("Locations") == "Example City"
    assert value("Year") == "2026"
    assert value("Month") == "9"
    assert value("Day") == "3"
    assert value("Writer") == "Jane Writer"
    assert value("Penciller") == "Pat Pencil"
    assert value("CoverArtist") == "Casey Cover"
