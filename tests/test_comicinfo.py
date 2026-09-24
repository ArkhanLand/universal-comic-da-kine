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
    assert value("PageCount") == "2"
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


def test_comicinfo_counts_logical_pages_and_indexes_images(tmp_path: Path) -> None:
    clf = make_test_clf(tmp_path)
    page = clf.pages.pages[0]
    clf = replace(
        clf,
        pages=replace(
            clf.pages,
            pages=(
                replace(page, numbers=()),
                replace(page, numbers=(17,), width=9000),
                replace(page, numbers=(18, 19)),
                replace(page, numbers=(20, 21, 22, 23)),
            ),
        ),
    )
    root = ET.fromstring(make_comicinfo(clf))
    assert root.findtext("PageCount") == "7"
    entries = root.findall("Pages/Page")
    assert [entry.get("Image") for entry in entries] == ["0", "1", "2", "3", "4"]
    assert entries[0].get("Type") == "FrontCover"
    assert [entry.get("DoublePage") for entry in entries] == [
        "false",
        "false",
        "false",
        "true",
        None,
    ]


def test_comicinfo_omits_unknown_count_and_span(tmp_path: Path) -> None:
    clf = make_test_clf(tmp_path)
    clf = replace(clf, pages=replace(clf.pages, pages=(replace(clf.pages.pages[0], numbers=None),)))
    root = ET.fromstring(make_comicinfo(clf))
    assert root.find("PageCount") is None
    assert root.findall("Pages/Page")[1].get("DoublePage") is None


def test_comicinfo_cover_only_has_zero_publication_pages(tmp_path: Path) -> None:
    clf = make_test_clf(tmp_path)
    clf = replace(clf, pages=replace(clf.pages, pages=()))
    root = ET.fromstring(make_comicinfo(clf))
    assert root.findtext("PageCount") == "0"
    assert len(root.findall("Pages/Page")) == 1


def test_comicinfo_preserves_known_rtl_direction(tmp_path: Path) -> None:
    clf = make_test_clf(tmp_path)
    assert ET.fromstring(make_comicinfo(clf)).find("Manga") is None
    rtl = replace(clf, reading_direction="rtl", first_page_side="left")
    assert ET.fromstring(make_comicinfo(rtl)).findtext("Manga") == "YesAndRightToLeft"
    assert rtl.first_page_side == "left"
