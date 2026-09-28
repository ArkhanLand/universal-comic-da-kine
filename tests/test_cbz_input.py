import hashlib
import json
import xml.etree.ElementTree as ET
from io import BytesIO
from zipfile import ZipFile

import pytest
from PIL import Image
from typer.testing import CliRunner

from ucd.cli import app
from ucd.exceptions import InvalidComicInputError
from ucd.input.cbz import CBZInputAdapter
from ucd.models import PartialDate
from ucd.output.cbz import write_cbz


@pytest.fixture(autouse=True)
def cbz_cache(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    monkeypatch.setattr("ucd.input.cbz.CACHE_PATH", root)
    return root


def image_bytes(color="red", size=(20, 30)):
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG")
    return stream.getvalue()


def archive_at(tmp_path, xml=None, comment=b"", entries=None, name="input.cbz"):
    path = tmp_path / name
    with ZipFile(path, "w") as archive:
        for filename, data in entries or [
            ("01.png", image_bytes()),
            ("02.png", image_bytes("blue")),
        ]:
            archive.writestr(filename, data)
        if xml is not None:
            archive.writestr("ComicInfo.xml", xml)
        archive.comment = comment
    return path


def test_xml_adds_comment(tmp_path, cbz_cache):
    xml = b"""<ComicInfo><Title>Example #1</Title><Series>Example</Series>
    <Number>1</Number><Writer>Alice, Bob</Writer><Publisher>Publisher</Publisher>
    <Year>2020</Year><Month>5</Month><Day>6</Day><LanguageISO>en</LanguageISO>
    <Pages><Page Image="0" Type="FrontCover"/><Page Image="1" DoublePage="true"/>
    </Pages></ComicInfo>"""
    source = archive_at(tmp_path, xml)
    original = source.read_bytes()
    publication = CBZInputAdapter().get_publication(str(source))
    assert publication.series == "Example"
    assert publication.narrative[0].numbers == (1, 2)
    assert publication.logical_page_count == 2
    destination = tmp_path / "result.cbz"
    write_cbz(publication, destination)
    with ZipFile(source) as old, ZipFile(destination) as new:
        assert old.comment == b""
        info = json.loads(new.comment)["ComicBookInfo/1.0"]
        assert info["title"] == "Example #1"
        assert info["series"] == "Example"
        assert info["issue"] == "1"
        assert info["publicationYear"] == 2020
        assert info["credits"] == [
            {"person": "Alice", "role": "Writer"},
            {"person": "Bob", "role": "Writer"},
        ]
        assert new.read("01.png") == old.read("01.png")
        assert new.read("02.png") == old.read("02.png")
        assert ET.fromstring(new.read("ComicInfo.xml")).findtext("Series") == "Example"
    assert source.read_bytes() == original
    digest = hashlib.sha256(original).hexdigest()
    assert (cbz_cache / "objects" / f"{digest}.cbz").read_bytes() == original
    record = json.loads((cbz_cache / "sources" / f"{digest}.json").read_text())
    assert [item["name"] for item in record["images"]] == ["01.png", "02.png"]
    reread = CBZInputAdapter().get_publication(str(destination))
    assert reread.creators == publication.creators
    assert reread.publication_date == publication.publication_date
    assert reread.narrative[0].numbers == (1, 2)


@pytest.mark.parametrize(
    "xml", [b"<ComicInfo><Title>XML</Title></ComicInfo>", b"<ComicInfo/>"], ids=["full", "empty"]
)
def test_xml_precedence(tmp_path, xml):
    comment = json.dumps(
        {"ComicBookInfo/1.0": {"title": "Comment", "publisher": "Ignored"}}
    ).encode()
    publication = CBZInputAdapter().get_publication(str(archive_at(tmp_path, xml, comment)))
    assert publication.title == ("XML" if b"Title" in xml else "input")
    assert publication.publisher is None


@pytest.mark.parametrize(
    "xml",
    [None, b"<broken", b"<other/>", b"<!DOCTYPE ComicInfo><ComicInfo/>"],
    ids=["missing", "broken", "root", "dtd"],
)
def test_comment_fallback(tmp_path, xml):
    comment = json.dumps(
        {
            "ComicBookInfo/1.0": {
                "title": "Comment",
                "issue": 2,
                "tags": ["a", 42],
                "credits": [{"person": "Alice", "role": "Writer"}],
            }
        }
    ).encode()
    publication = CBZInputAdapter().get_publication(str(archive_at(tmp_path, xml, comment)))
    assert publication.title == "Comment"
    assert publication.issue_number == "2"
    assert publication.tags == ("a",)
    assert publication.creators[0].name == "Alice"


@pytest.mark.parametrize(
    "comment",
    [b"", b"broken", b"[]", b'{"ComicBookInfo/1.0": null}'],
    ids=["empty", "broken", "list", "null"],
)
def test_minimal_metadata(tmp_path, comment):
    publication = CBZInputAdapter().get_publication(str(archive_at(tmp_path, b"<bad", comment)))
    assert publication.title == "input"
    assert publication.cover.numbers == ()
    assert publication.narrative[0].numbers is None


def test_order_and_cover(tmp_path):
    entries = [
        ("p10.png", image_bytes("blue")),
        ("p2.png", image_bytes("green")),
        ("p1.png", image_bytes()),
    ]
    xml = (
        b"<ComicInfo>"
        b"<Pages>"
        b'<Page Image="1" Type="FrontCover"/>'
        b'<Page Image="0" DoublePage="false"/>'
        b'<Page Image="2" DoublePage="true"/>'
        b"</Pages>"
        b"</ComicInfo>"
    )
    source = archive_at(tmp_path, xml, entries=entries)
    progress = []
    metadata = []
    result = CBZInputAdapter().get_publication(
        str(source),
        progress=lambda *args: progress.append(args),
        metadata_ready=lambda *args: metadata.append(args),
    )
    assert result.cover.path.read_bytes() == entries[1][1]
    assert [p.path.read_bytes() for p in result.narrative] == [entries[2][1], entries[0][1]]
    assert [p.numbers for p in result.narrative] == [(1,), (2, 3)]
    assert [event[1:] for event in progress] == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert metadata == [("input", None, None)]


def test_unknown_span(tmp_path):
    xml = (
        b"<ComicInfo>"
        b"<Pages>"
        b'<Page Image="0" Type="FrontCover"/>'
        b'<Page Image="2" DoublePage="true"/>'
        b"</Pages>"
        b"<PageCount>99</PageCount>"
        b"</ComicInfo>"
    )
    entries = [(f"{i}.png", image_bytes()) for i in range(3)]
    result = CBZInputAdapter().get_publication(str(archive_at(tmp_path, xml, entries=entries)))
    assert [p.numbers for p in result.narrative] == [None, None]
    assert result.logical_page_count is None


def test_bad_fields(tmp_path, caplog):
    xml = (
        b"<ComicInfo>"
        b"<Year>2020</Year>"
        b"<Month>2</Month>"
        b"<Count>oops</Count>"
        b"<Pages>"
        b'<Page Image="999"/>'
        b'<Page Image="-1"/>'
        b"</Pages>"
        b"<Notes>Keep me</Notes>"
        b"</ComicInfo>"
    )
    result = CBZInputAdapter().get_publication(str(archive_at(tmp_path, xml)))
    assert result.publication_date == PartialDate(2020, 2)
    assert result.series_count is None
    assert "page index" in caplog.text
    assert "Unsupported ComicInfo" in caplog.text


@pytest.mark.parametrize(
    "entry", ["../page.png", "/page.png", "dir\\page.png", "script.py", "page.svg"]
)
def test_reject_entries(tmp_path, entry):
    source = archive_at(tmp_path, entries=[(entry, image_bytes())])
    with pytest.raises(InvalidComicInputError):
        CBZInputAdapter().get_publication(str(source))


def test_reject_duplicates(tmp_path):
    source = archive_at(tmp_path, entries=[("a.png", image_bytes()), ("A.PNG", image_bytes())])
    with pytest.raises(InvalidComicInputError, match="Duplicate"):
        CBZInputAdapter().get_publication(str(source))


def test_reject_random_zip(tmp_path):
    source = archive_at(tmp_path, name="random.zip")
    with pytest.raises(InvalidComicInputError, match="local .cbz"):
        CBZInputAdapter().get_publication(str(source))


def test_reject_bad_image(tmp_path):
    source = archive_at(tmp_path, entries=[("1.png", b"not an image")])
    with pytest.raises(InvalidComicInputError):
        CBZInputAdapter().get_publication(str(source))


def test_reject_animation(tmp_path):
    stream = BytesIO()
    Image.new("RGB", (10, 10), "red").save(
        stream,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (10, 10), "blue")],
        duration=100,
    )
    source = archive_at(tmp_path, entries=[("1.gif", stream.getvalue())])
    with pytest.raises(InvalidComicInputError, match="static image"):
        CBZInputAdapter().get_publication(str(source))


def test_reject_empty(tmp_path):
    source = tmp_path / "empty.cbz"
    with ZipFile(source, "w"):
        pass
    with pytest.raises(InvalidComicInputError, match="needs images"):
        CBZInputAdapter().get_publication(str(source))


def test_reject_size(tmp_path):
    source = archive_at(tmp_path)
    with pytest.raises(InvalidComicInputError, match="byte limit"):
        CBZInputAdapter(max_bytes=10).get_publication(str(source))


def test_ignore_os_files(tmp_path):
    source = archive_at(
        tmp_path,
        entries=[("1.png", image_bytes()), ("__MACOSX/._1.png", b"junk"), (".DS_Store", b"junk")],
    )
    result = CBZInputAdapter().get_publication(str(source))
    assert result.interior_image_count == 0


def test_convert_cli(tmp_path):
    source = archive_at(tmp_path, b"<ComicInfo><Title>Converted</Title></ComicInfo>")
    output = tmp_path / "output"
    result = CliRunner().invoke(app, ["convert", str(source), "-o", str(output)])
    assert result.exit_code == 0, result.output
    destination = output / "Converted.cbz"
    with ZipFile(destination) as archive:
        assert json.loads(archive.comment)["ComicBookInfo/1.0"]["title"] == "Converted"
    before = destination.read_bytes()
    result = CliRunner().invoke(app, ["convert", str(source), "-o", str(output)])
    assert result.exit_code == 1
    assert destination.read_bytes() == before
    result = CliRunner().invoke(app, ["convert", str(source), "-o", str(output), "--overwrite"])
    assert result.exit_code == 0, result.output


def test_protect_source(tmp_path):
    source = archive_at(tmp_path)
    before = source.read_bytes()
    result = CliRunner().invoke(app, ["convert", str(source), "-o", str(tmp_path), "--overwrite"])
    assert result.exit_code == 1
    assert "replace an input" in result.output
    assert source.read_bytes() == before


@pytest.mark.parametrize("stop", [False, True])
def test_convert_batch(tmp_path, stop):
    source = archive_at(tmp_path)
    output = tmp_path / "output"
    args = ["convert", str(tmp_path / "missing.cbz"), str(source), "-o", str(output)]
    result = CliRunner().invoke(app, args + (["--quit-on-error"] if stop else []))
    assert result.exit_code == 1
    assert (output / "input.cbz").exists() is not stop


def test_atomic_failure(tmp_path, monkeypatch):
    source = archive_at(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "input.cbz"
    destination.write_bytes(b"existing output")

    def fail_write(publication, target):
        target.write_bytes(b"partial archive")
        raise OSError("write failed")

    monkeypatch.setattr("ucd.cli.write_cbz", fail_write)
    result = CliRunner().invoke(app, ["convert", str(source), "-o", str(output), "--overwrite"])
    assert result.exit_code == 1
    assert destination.read_bytes() == b"existing output"
    assert list(output.iterdir()) == [destination]
