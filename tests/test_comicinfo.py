from datetime import date
from pathlib import Path

from ucd.models import CLF, Creator, Page, Pages
from ucd.output.comicinfo import make_comicinfo


def test_comicinfo_contains_metadata() -> None:
    clf = CLF(
        service="example",
        service_id="1",
        service_series_id="2",
        title="Example #1",
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
        pages=Pages(
            cover=Page(
                number=None,
                path=Path("/tmp/cover.jpg"),
                width=100,
                height=150,
                content_type="image/jpeg",
                mode="RGB",
            ),
            pages=(
                Page(
                    number=1,
                    path=Path("/tmp/00001.jpg"),
                    width=100,
                    height=150,
                    content_type="image/jpeg",
                    mode="RGB",
                ),
                Page(
                    number=2,
                    path=Path("/tmp/00002.jpg"),
                    width=100,
                    height=150,
                    content_type="image/jpeg",
                    mode="RGB",
                ),
            ),
        ),
    )
    xml = make_comicinfo(clf).decode()

    assert "<Title>Example #1</Title>" in xml
    assert "<Series>Example</Series>" in xml
    assert "<Number>1</Number>" in xml
    assert "<Count>12</Count>" in xml
    assert "<Volume>2</Volume>" in xml
    assert "<Publisher>Example Comics</Publisher>" in xml
    assert "<Imprint>Example Imprint</Imprint>" in xml
    assert "<Genre>Superhero, Science Fiction</Genre>" in xml
    assert "<Tags>Example Tag</Tags>" in xml
    assert "<Web>https://example.com/comic/1</Web>" in xml
    assert "<PageCount>3</PageCount>" in xml
    assert "<LanguageISO>en</LanguageISO>" in xml
    assert "<AgeRating>Teen</AgeRating>" in xml
    assert "<StoryArc>Example Arc</StoryArc>" in xml
    assert "<Characters>Alice, Bob</Characters>" in xml
    assert "<Teams>Example Team</Teams>" in xml
    assert "<Locations>Example City</Locations>" in xml
    assert "<Year>2026</Year>" in xml
    assert "<Month>9</Month>" in xml
    assert "<Day>3</Day>" in xml
    assert "<Writer>Jane Writer</Writer>" in xml
    assert "<Penciller>Pat Pencil</Penciller>" in xml
    assert "<CoverArtist>Casey Cover</CoverArtist>" in xml
