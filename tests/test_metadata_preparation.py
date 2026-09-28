import json
import xml.etree.ElementTree as ET
from dataclasses import replace
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from tests.helpers import make_test_publication
from tests.test_cbz_input import archive_at, image_bytes
from ucd.input.cbz import CBZInputAdapter
from ucd.metadata import prepare_comic_metadata
from ucd.models import PartialDate
from ucd.output.cbz import write_cbz


def test_comment_builds_xml(tmp_path, monkeypatch):
    comment = json.dumps(
        {
            "appID": "Other tool",
            "extra": ["retain"],
            "ComicBookInfo/1.0": {
                "title": "Example",
                "series": "A Series",
                "issue": "1.5",
                "publisher": "Publisher",
                "publicationYear": 2017,
                "publicationMonth": 9,
                "language": "en",
                "credits": [{"person": "A Writer", "role": "Writer"}],
                "unknown": "opaque",
            },
        },
        indent=2,
    ).encode()
    source = archive_at(tmp_path, comment=comment)
    original = source.read_bytes()
    pub = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(source))
    prepared = pub.ready_comic_metadata()
    assert prepared.comicinfo_origin == "generated"
    assert prepared.comicbookinfo_origin == "source"
    assert prepared.comicbookinfo == comment
    root = ET.fromstring(prepared.comicinfo)
    for name, expected in {
        "Title": "Example",
        "Series": "A Series",
        "Number": "1.5",
        "Publisher": "Publisher",
        "Year": "2017",
        "Month": "9",
        "Writer": "A Writer",
        "LanguageISO": "en",
    }.items():
        assert root.findtext(name) == expected
    assert root.find("Day") is None
    assert pub.publication_date == PartialDate(2017, 9)

    def forbidden(*args, **kwargs):
        raise AssertionError("Exporter tried to generate metadata")

    monkeypatch.setattr("ucd.metadata.make_comicinfo", forbidden)
    monkeypatch.setattr("ucd.metadata.make_comicbookinfo", forbidden)
    output = tmp_path / "converted.cbz"
    write_cbz(pub, output)
    assert output.read_bytes().startswith(original)
    with ZipFile(source) as old, ZipFile(output) as new:
        assert new.comment == comment
        assert new.read("ComicInfo.xml") == prepared.comicinfo
        for name in old.namelist():
            assert new.read(name) == old.read(name)
    assert source.read_bytes() == original


def test_added_xml_zip_info(tmp_path):
    source = tmp_path / "source.cbz"
    with ZipFile(source, "w") as archive:
        member = ZipInfo("chapter/page1.png", date_time=(2001, 2, 3, 4, 5, 6))
        member.comment = b"member-comment\x00\xff"
        member.extra = b"\xfe\xca\x03\x00xyz"
        member.external_attr = 0o100644 << 16
        member.compress_type = ZIP_DEFLATED
        archive.writestr(member, image_bytes())
        archive.comment = b'{"ComicBookInfo/1.0":{"title":"Kept"}}'
    pub = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(source))
    output = tmp_path / "output.cbz"
    write_cbz(pub, output)
    with ZipFile(source) as old, ZipFile(output) as new:
        first = old.infolist()[0]
        second = new.infolist()[0]
        for attr in (
            "filename",
            "date_time",
            "comment",
            "extra",
            "external_attr",
            "flag_bits",
            "compress_type",
            "CRC",
            "header_offset",
        ):
            assert getattr(first, attr) == getattr(second, attr)
        assert new.comment == old.comment
    # Reimport uses generated XML, and a second conversion is byte-identical.
    reimport = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(output))
    second_output = tmp_path / "second.cbz"
    write_cbz(reimport, second_output)
    assert second_output.read_bytes() == output.read_bytes()


def test_xml_prepared_early(tmp_path):
    xml = b'<ComicInfo xmlns:xsi="urn:unused"><Title>XML</Title></ComicInfo>'
    source = archive_at(tmp_path, xml)
    pub = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(source))
    metadata = pub.ready_comic_metadata()
    assert metadata.comicinfo == xml
    assert metadata.comicinfo_origin == "source"
    assert metadata.comicbookinfo_origin == "generated"
    assert json.loads(metadata.comicbookinfo)["ComicBookInfo/1.0"]["title"] == "XML"


def test_missing_both(tmp_path):
    source = archive_at(tmp_path)
    pub = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(source))
    metadata = pub.ready_comic_metadata()
    assert metadata.comicinfo_origin == metadata.comicbookinfo_origin == "generated"
    output = tmp_path / "out.cbz"
    write_cbz(pub, output)
    with ZipFile(output) as archive:
        assert archive.read("ComicInfo.xml") == metadata.comicinfo
        assert archive.comment == metadata.comicbookinfo


def test_requires_preparation(tmp_path):
    pub = make_test_publication(tmp_path)
    destination = tmp_path / "out.cbz"
    with pytest.raises(ValueError, match="not prepared"):
        write_cbz(pub, destination)
    assert not destination.exists()
    prepared = prepare_comic_metadata(pub)
    assert prepare_comic_metadata(prepared) is prepared
    changed = replace(prepared, title="changed")
    with pytest.raises(ValueError, match="stale"):
        write_cbz(changed, destination)
    assert not destination.exists()


def test_year_precision(tmp_path):
    pub = replace(make_test_publication(tmp_path), publication_date=PartialDate(2017))
    prepared = prepare_comic_metadata(pub).ready_comic_metadata()
    xml = ET.fromstring(prepared.comicinfo)
    info = json.loads(prepared.comicbookinfo)["ComicBookInfo/1.0"]
    assert xml.findtext("Year") == "2017"
    assert xml.find("Month") is None and xml.find("Day") is None
    assert info["publicationYear"] == 2017
    assert "publicationMonth" not in info


def test_zip64_local(tmp_path):
    source = tmp_path / "source.cbz"
    with (
        ZipFile(source, "w") as archive,
        archive.open("page.png", "w", force_zip64=True) as image,
    ):
        image.write(image_bytes())
    pub = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(source))
    output = tmp_path / "out.cbz"
    write_cbz(pub, output)
    with ZipFile(output) as archive:
        assert archive.read("page.png") == image_bytes()
        assert archive.read("ComicInfo.xml") == pub.ready_comic_metadata().comicinfo
