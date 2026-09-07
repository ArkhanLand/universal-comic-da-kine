from datetime import date

from ucd.models import CLF, Creator
from ucd.output.comicinfo import make_comicinfo


def test_comicinfo_contains_metadata() -> None:
    clf = CLF(
        service="example",
        service_id="1",
        service_series_id="2",
        title="Example #1",
        series="Example",
        issue_number="1",
        publication_date=date(2026, 9, 3),
        creators=(Creator("Jane Writer", "writer"),),
    )
    xml = make_comicinfo(clf).decode()
    assert "<Title>Example #1</Title>" in xml
    assert "<Series>Example</Series>" in xml
    assert "<Number>1</Number>" in xml
    assert "<Year>2026</Year>" in xml
    assert "<Writer>Jane Writer</Writer>" in xml
