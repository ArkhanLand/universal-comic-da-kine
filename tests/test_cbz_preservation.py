from dataclasses import replace
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from tests.test_cbz_input import archive_at, image_bytes
from ucd.input.cbz import CBZInputAdapter
from ucd.output.cbz import write_cbz

XML = b"""<?xml version="1.0" encoding="utf-8"?>
<?preserve this-processing-instruction?>
<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
           xmlns:xsd="http://www.w3.org/2001/XMLSchema"
           xmlns:custom="urn:example" custom:flag="keep">
  <!-- Keep comments, whitespace, prefixes, and CDATA too. -->
  <Title>Preserved</Title><Notes><![CDATA[Created with X & Y]]></Notes>
  <Year>2017</Year><Month>9</Month><Manga>No</Manga><PageCount>35</PageCount>
  <custom:Future attribute="opaque">Not understood by UCD</custom:Future>
  <Pages>
    <Page Image="0" Bookmark="Start" ImageWidth="20" ImageHeight="30" />
    <Page Image="1" Type="FrontCover" DoublePage="true" Bookmark="Cover" custom:fold="up" />
  </Pages>
</ComicInfo>"""


def test_exact_xml_and_zip(tmp_path):
    source = tmp_path / "source.cbz"
    with ZipFile(source, "w") as archive:
        for name, data in [
            ("p10.png", image_bytes()),
            ("p2.png", image_bytes("blue")),
            ("metadata/ComicInfo.xml", XML),
            ("__MACOSX/._p2.png", b"resource"),
        ]:
            entry = ZipInfo(name, date_time=(2001, 2, 3, 4, 5, 6))
            entry.compress_type = ZIP_DEFLATED
            entry.comment = b"member comment\x00\xff"
            entry.extra = b"\xfe\xca\x03\x00xyz"
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    before = source.read_bytes()
    pub = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(source))
    assert pub.ready_comic_metadata().comicinfo == XML
    output = tmp_path / "output.cbz"
    write_cbz(pub, output)
    after = output.read_bytes()
    # Nothing but the EOCD comment length and new comment may change.
    assert after[: len(before) - 2] == before[:-2]
    comment = pub.ready_comic_metadata().comicbookinfo
    assert after[len(before) - 2 :] == len(comment).to_bytes(2, "little") + comment
    with ZipFile(source) as original, ZipFile(output) as converted:
        assert original.namelist() == converted.namelist()
        assert converted.read("metadata/ComicInfo.xml") == XML
        for old, new in zip(original.infolist(), converted.infolist(), strict=True):
            assert old.comment == new.comment
            assert old.extra == new.extra
            assert old.date_time == new.date_time
            assert old.external_attr == new.external_attr
            assert converted.read(new) == original.read(old)
    assert source.read_bytes() == before


@pytest.mark.parametrize(
    "comment",
    [
        b"opaque\xff",
        b'{"unknown":42}',
        b'{"ComicBookInfo/1.0":{"title":"Conflicting","custom":true}}',
    ],
    ids=["opaque", "unknown", "conflict"],
)
def test_existing_comment(tmp_path, comment):
    source = archive_at(tmp_path, XML, comment)
    pub = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(source))
    assert pub.title == "Preserved"
    assert pub.ready_comic_metadata().comicbookinfo == comment
    output = tmp_path / "output.cbz"
    write_cbz(pub, output)
    assert output.read_bytes() == source.read_bytes()


@pytest.mark.parametrize(
    "xml", [None, b"<broken", b"<!DOCTYPE ComicInfo><ComicInfo/>"], ids=["missing", "broken", "dtd"]
)
def test_raw_metadata(tmp_path, xml):
    source = archive_at(tmp_path, xml)
    pub = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(source))
    output = tmp_path / "output.cbz"
    write_cbz(pub, output)
    with ZipFile(source) as old, ZipFile(output) as new:
        assert new.namelist() == old.namelist() + (["ComicInfo.xml"] if xml is None else [])
        for name in old.namelist():
            assert old.read(name) == new.read(name)
        assert new.comment


@pytest.mark.parametrize("edit", ["title", "order", "numbers"], ids=["title", "order", "numbers"])
def test_refuse_stale(tmp_path, edit):
    source = archive_at(
        tmp_path,
        XML,
        entries=[
            (f"p{i}.png", image_bytes(color)) for i, color in enumerate(("red", "green", "blue"))
        ],
    )
    pub = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(source))
    if edit == "title":
        pub = replace(pub, title="Changed")
    elif edit == "order":
        pub = replace(pub, narrative=tuple(reversed(pub.narrative)))
    else:
        pub = replace(pub, narrative=(replace(pub.narrative[0], numbers=(42,)), *pub.narrative[1:]))
    output = tmp_path / "output.cbz"
    with pytest.raises(ValueError, match="reconciliation"):
        write_cbz(pub, output)
    assert not output.exists()
    with pytest.raises(ValueError, match="reconciliation"):
        pub.ready_comic_metadata()


@pytest.mark.parametrize("target", ["source", "image"])
def test_refuse_corruption(tmp_path, target):
    source = archive_at(tmp_path, XML)
    pub = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(source))
    assert pub.source_representation is not None
    path = pub.source_representation.path if target == "source" else pub.cover.path
    path.write_bytes(b"corrupted")
    output = tmp_path / "output.cbz"
    with pytest.raises(ValueError):
        write_cbz(pub, output)
    assert not output.exists()


def test_trailing_data(tmp_path):
    source = archive_at(tmp_path, XML)
    with source.open("ab") as stream:
        stream.write(b"unrecognized trailing bytes")
    pub = CBZInputAdapter(cache_dir=tmp_path / "cache").get_publication(str(source))
    output = tmp_path / "output.cbz"
    with pytest.raises(ValueError, match="trailing data"):
        write_cbz(pub, output)
    assert not output.exists()
