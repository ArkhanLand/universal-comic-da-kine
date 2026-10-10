import hashlib
import json
from copy import deepcopy
from io import BytesIO
from threading import Barrier, Event
from zipfile import ZipFile

import httpx
import pytest
from PIL import Image
from typer.testing import CliRunner

from tests.test_libby_read import BOOK, WEB, encoded_page
from ucd.auth.libby import (
    Connection,
    LibbyAuthenticationError,
    LibbyClient,
    Library,
    Session,
)
from ucd.cli import app
from ucd.exceptions import ServiceResponseError, UnavailableError
from ucd.input.libby_assets import component_html, css_images
from ucd.input.libby_overdrive import LibbyOverDriveReadAdapter, bibliographic
from ucd.metadata import prepare_comic_metadata
from ucd.models import PartialDate
from ucd.output.cbz import write_cbz


def jpeg(color, size=(100, 150)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


class Store:
    def select_loan(self, client, title_id, name):
        return (
            Connection("test", Library("test", "Test", "34"), "reference"),
            Session("private-token", "private-card"),
            {},
        )


class Server:
    def __init__(self):
        self.book = deepcopy(BOOK)
        self.book["spine"].append(
            {"path": "html/page010.xhtml", "linear": True, "rendition-layout": "pre-paginated"}
        )
        self.book["-odread-cmpt-params"] = ["secret=a", "secret=b", "secret=c"]
        self.catalog = {
            "id": "123",
            "title": "Test Book",
            "description": "<b>Description</b>",
            "publisher": {"name": "Publisher"},
            "imprint": {"name": "Imprint"},
            "publishDate": "2025-04",
            "series": "Series",
            "detailedSeries": {"seriesId": 12, "readingOrder": "9"},
            "languages": [{"id": "en"}],
            "creators": [{"name": "Artist", "role": "Illustrator"}],
        }
        self.images = {"cover.jpg": jpeg("red"), "z.jpg": jpeg("blue"), "a.jpg": jpeg("green")}
        self.calls = []
        self.fail = None
        self.css = (
            '#page010 {background-image: url("../images/a.jpg")}\n'
            "#cover {background-image: url(../images/cover.jpg)}\n"
            '#page002 {background-image: url("../images/z.jpg")}'
        )

    def handle(self, request):
        self.calls.append(request)
        assert "Authorization" not in request.headers
        path = request.url.path
        if path == "/":
            return httpx.Response(
                200,
                text=encoded_page(self.book),
                headers={
                    "Content-Type": "text/html",
                    "Set-Cookie": "read=private-cookie; Path=/; Secure",
                },
            )
        assert "read=private-cookie" in request.headers.get("Cookie", "")
        if path.startswith("/html/"):
            key = path.rsplit("/", 1)[1].split(".")[0]
            return httpx.Response(
                200,
                text=(f'<link rel="stylesheet" href="../styles/pages.css"><div id="{key}"></div>'),
            )
        if path == "/styles/pages.css":
            return httpx.Response(200, text=self.css, headers={"Content-Type": "text/css"})
        name = path.rsplit("/", 1)[1]
        if name == self.fail:
            return httpx.Response(500)
        assert name in self.images
        return httpx.Response(
            200, content=self.images[name], headers={"Content-Type": "image/jpeg"}
        )

    def adapter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            LibbyClient,
            "open_loan",
            lambda *a, **kw: {"urls": {"web": WEB}, "message": "message=private-fulfillment"},
        )
        monkeypatch.setattr(LibbyClient, "catalog_media", lambda *a: self.catalog)
        return LibbyOverDriveReadAdapter(
            store=Store(),
            api_client=httpx.Client(transport=httpx.MockTransport(self.handle)),
            read_client=httpx.Client(transport=httpx.MockTransport(self.handle)),
            cache_dir=tmp_path,
        )


def test_exact_order(tmp_path, monkeypatch):
    server = Server()
    updates = []
    adapter = server.adapter(tmp_path, monkeypatch)
    publication = adapter.get_publication("123", progress=lambda *args: updates.append(args))
    assert publication.cover.path.read_bytes() == server.images["cover.jpg"]
    assert [p.path.read_bytes() for p in publication.narrative] == [
        server.images["z.jpg"],
        server.images["a.jpg"],
    ]
    assert [p.numbers for p in publication.narrative] == [(1,), (2,)]
    assert publication.reading_direction == "rtl"
    assert publication.first_page_side is None
    assert publication.publication_date == PartialDate(2025, 4)
    assert publication.issue_number is None
    assert publication.volume is None
    assert publication.creators[0].role == "artist"
    assert updates == [("Test Book", n, 3) for n in range(4)]
    output = tmp_path / "book.cbz"
    write_cbz(prepare_comic_metadata(publication), output)
    with ZipFile(output) as archive:
        assert archive.read("00000.jpg") == server.images["cover.jpg"]
        assert archive.read("00001.jpg") == server.images["z.jpg"]
        assert archive.read("00002.jpg") == server.images["a.jpg"]
    manifest = json.loads(next(tmp_path.glob("libby-overdrive/123/captures/*.json")).read_text())
    assert manifest["complete"]
    for record in manifest["assets"]:
        data = (tmp_path / "libby-overdrive/123" / record["object"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == record["sha256"]
        assert len(data) == record["byte_count"]
    saved = "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    for secret in (
        "private-token",
        "private-card",
        "private-cookie",
        "private-fulfillment",
        "synthetic-secret",
        "dewey-",
        "secret=a",
    ):
        assert secret not in saved


def test_cdn_originals(tmp_path, monkeypatch):
    server = Server()
    original_handler = server.handle

    def handler(request):
        if request.url.host == "odrresources.cachefly.net":
            server.calls.append(request)
            assert "Authorization" not in request.headers
            assert "Cookie" not in request.headers
            name = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(
                200, content=server.images[name], headers={"Content-Type": "image/jpeg"}
            )
        if request.url.path.startswith("/images/"):
            server.calls.append(request)
            return httpx.Response(
                302,
                headers={"Location": "https://odrresources.cachefly.net" + request.url.path},
            )
        return original_handler(request)

    server.handle = handler
    result = server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert result.cover.path.read_bytes() == server.images["cover.jpg"]
    assert [p.path.read_bytes() for p in result.narrative] == [
        server.images["z.jpg"],
        server.images["a.jpg"],
    ]
    capture = json.loads(next(tmp_path.glob("libby-overdrive/123/captures/*.json")).read_text())
    assert capture["complete"]
    for asset in capture["assets"]:
        content = (tmp_path / "libby-overdrive/123" / asset["object"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == asset["sha256"]


def test_mapping_counts(tmp_path, monkeypatch):
    server = Server()
    adapter = server.adapter(tmp_path, monkeypatch)
    events = []

    def mapped(title, completed, total):
        assert not any(r.url.path.endswith(".jpg") for r in server.calls)
        events.append((title, completed, total))

    adapter.mapping_progress = mapped
    adapter.get_publication("123")
    assert events == [("Test Book", count, 3) for count in range(4)]


def test_parallel_book(tmp_path, monkeypatch):
    server = Server()
    original_handler = server.handle
    documents = Barrier(3)
    images = Barrier(3)
    last_image = Event()

    def handler(request):
        if request.url.path.startswith("/html/"):
            documents.wait(timeout=5)
        if request.url.path.startswith("/images/"):
            images.wait(timeout=5)
            if request.url.path.endswith("cover.jpg"):
                assert last_image.wait(timeout=5)
            elif request.url.path.endswith("a.jpg"):
                last_image.set()
        return original_handler(request)

    server.handle = handler
    adapter = server.adapter(tmp_path, monkeypatch)
    adapter.workers = 3
    updates = []
    result = adapter.get_publication("123", progress=lambda *args: updates.append(args))
    assert result.cover.path.read_bytes() == server.images["cover.jpg"]
    assert [p.path.read_bytes() for p in result.narrative] == [
        server.images["z.jpg"],
        server.images["a.jpg"],
    ]
    assert updates == [("Test Book", count, 3) for count in range(4)]
    assert sum(r.url.path == "/styles/pages.css" for r in server.calls) == 1
    capture = json.loads(next(tmp_path.glob("libby-overdrive/123/captures/*.json")).read_text())
    assert capture["complete"]
    assert [asset["spine_index"] for asset in capture["assets"]] == [0, 1, 2]


def test_parallel_failure(tmp_path, monkeypatch):
    server = Server()
    server.fail = "z.jpg"
    adapter = server.adapter(tmp_path, monkeypatch)
    adapter.workers = 3
    with pytest.raises(ServiceResponseError, match="HTTP 500"):
        adapter.get_publication("123")
    capture = json.loads(next(tmp_path.glob("libby-overdrive/123/captures/*.json")).read_text())
    assert not capture["complete"]
    assert [asset["role"] for asset in capture["assets"]] == ["cover"]
    stored = tmp_path / "libby-overdrive/123" / capture["assets"][0]["object"]
    assert stored.read_bytes() == server.images["cover.jpg"]


def test_cli_workers(tmp_path, monkeypatch):
    server = Server()
    adapter = server.adapter(tmp_path / "cache", monkeypatch)

    def factory(**kwargs):
        adapter.workers = kwargs["workers"]
        adapter.mapping_progress = kwargs["mapping_progress"]
        adapter.status = kwargs["status"]
        adapter.title_ready = kwargs["title_ready"]
        return adapter

    monkeypatch.setattr("ucd.cli.LibbyOverDriveReadAdapter", factory)
    result = CliRunner().invoke(
        app,
        [
            "download",
            "123",
            "--service",
            "libby-overdrive",
            "--workers",
            "3",
            "--output-dir",
            str(tmp_path / "output"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert adapter.workers == 3
    assert "Mapping: 3/3 (100%)" in result.output
    assert "Images: 3/3 (100%)" in result.output
    assert result.output.startswith("Acquiring Test Book\nGathering metadata....\n")
    archive = next((tmp_path / "output").glob("*.cbz"))
    with ZipFile(archive) as cbz:
        assert cbz.read("00001.jpg") == server.images["z.jpg"]


def test_partial_receipts(tmp_path, monkeypatch):
    server = Server()
    server.fail = "a.jpg"
    with pytest.raises(ServiceResponseError):
        server.adapter(tmp_path, monkeypatch).get_publication("123")
    records = list(tmp_path.glob("libby-overdrive/123/acquisitions/*.json"))
    assert len(records) == 2
    capture = json.loads(next(tmp_path.glob("libby-overdrive/123/captures/*.json")).read_text())
    assert not capture["complete"]
    assert len(capture["assets"]) == 2
    server.fail = None
    server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert len(list(tmp_path.glob("libby-overdrive/123/objects/*"))) == 3
    assert len(list(tmp_path.glob("libby-overdrive/123/acquisitions/*.json"))) == 5


def test_map_before_images(tmp_path, monkeypatch):
    server = Server()
    server.css = server.css.replace("#page010", "#missing")
    with pytest.raises(ServiceResponseError, match="unambiguous"):
        server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert not any(r.url.path.startswith("/images/") for r in server.calls)


@pytest.mark.parametrize(
    "css",
    [
        "#p {background-image: url(a), url(b)}",
        "#p {background-image: url(a)} #p {background-image: url(b)}",
        "@media screen {#p {background-image: url(a)}}",
        "#p div {background-image: url(a)}",
    ],
    ids=["layers", "conflict", "media", "selector"],
)
def test_css_ambiguity(css):
    with pytest.raises(ServiceResponseError):
        css_images(css, {"p"})


def test_skip_before_images(tmp_path, monkeypatch):
    server = Server()
    adapter = server.adapter(tmp_path, monkeypatch)

    def skip(*args):
        raise FileExistsError("already.cbz")

    with pytest.raises(FileExistsError):
        adapter.get_publication("123", metadata_ready=skip)
    assert not any(r.url.path.startswith("/images/") for r in server.calls)


def test_rendition_roles(tmp_path, monkeypatch):
    server = Server()
    # Cover need not be first; narrative order follows spine, not filenames.
    server.book.pop("-odread-cmpt-params")
    server.book["spine"] = [
        server.book["spine"][2],
        server.book["spine"][0],
        server.book["spine"][1],
    ]
    result = server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert [p.path.read_bytes() for p in result.narrative] == [
        server.images["a.jpg"],
        server.images["z.jpg"],
    ]


def test_mime_and_bytes(tmp_path, monkeypatch):
    server = Server()
    server.images["z.jpg"] = b"not an image"
    with pytest.raises(ServiceResponseError, match="invalid image"):
        server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert len(list(tmp_path.glob("libby-overdrive/123/acquisitions/*.json"))) == 1


def test_foreign_image(tmp_path, monkeypatch):
    server = Server()
    server.css = server.css.replace("../images/z.jpg", "https://example.com/private")
    with pytest.raises(ServiceResponseError):
        server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert all(r.url.host != "example.com" for r in server.calls)


def test_title_url(tmp_path, monkeypatch):
    adapter = Server().adapter(tmp_path, monkeypatch)
    assert adapter.matches_url("https://share.libbyapp.com/title/123")
    assert not adapter.matches_url("https://share.libbyapp.com/title/123?secret=x")
    assert adapter.get_publication("https://share.libbyapp.com/title/123").service_id == "123"


def test_date_precision():
    record = bibliographic("123", {"title": "A", "publishDate": "2025"}, {})
    assert record["publication_date"] == PartialDate(2025)


@pytest.mark.parametrize(
    "error", [LibbyAuthenticationError, UnavailableError], ids=["auth", "title"]
)
def test_cli_stop(tmp_path, monkeypatch, error):
    calls = []

    class Adapter:
        def __init__(self, **kw):
            pass

        def get_publication(self, source, **kw):
            calls.append(source)
            raise error("Test failure")

        def cleanup(self):
            pass

    monkeypatch.setattr("ucd.cli.LibbyOverDriveReadAdapter", Adapter)
    result = CliRunner().invoke(
        app,
        [
            "download",
            "1",
            "2",
            "--service",
            "libby-overdrive",
            "--continue-on-error",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert calls == (["1"] if error is LibbyAuthenticationError else ["1", "2"])
    assert not list(tmp_path.glob("*.cbz"))


def test_cli_atomic(tmp_path, monkeypatch):
    server = Server()
    adapter = server.adapter(tmp_path, monkeypatch)
    monkeypatch.setattr("ucd.cli.LibbyOverDriveReadAdapter", lambda **kw: adapter)

    def fail(publication, destination, **kw):
        destination.write_bytes(b"partial")
        raise OSError("Disk full")

    monkeypatch.setattr("ucd.cli.write_cbz", fail)
    result = CliRunner().invoke(
        app,
        [
            "download",
            "123",
            "--service",
            "libby-overdrive",
            "--output-dir",
            str(tmp_path / "output"),
        ],
    )
    assert result.exit_code == 1
    assert not list((tmp_path / "output").iterdir())


def test_cache_where(tmp_path):
    result = CliRunner().invoke(app, ["cache", "where", "--cache-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert result.output.strip() == str(tmp_path)


def test_component_body():
    import base64
    import re

    body = '<div id="page002">Synthetic 漫画</div>'
    plain = base64.b64encode(body.encode()).decode()
    cipher = re.sub(r"(.)(.)(.)(.)", r"\4\2\3\1", plain)
    parsed = component_html(
        '<base target="_parent"><link rel="stylesheet" href="styles.css">'
        + "<script>parent.__bif_cfc1(self, '"
        + cipher
        + "')</script>"
    )
    assert parsed.ids == {"page002"}
    assert parsed.stylesheets == ["styles.css"]
    assert parsed.base is None


def test_document_base(tmp_path, monkeypatch):
    server = Server()
    handler = server.handle

    def based(request):
        response = handler(request)
        if request.url.path.startswith("/html/"):
            return httpx.Response(
                200,
                text='<base href="/">'
                + response.text.replace("../styles/pages.css", "styles/pages.css"),
            )
        return response

    server.handle = based
    assert len(server.adapter(tmp_path, monkeypatch).get_publication("123").narrative) == 2


def test_foreign_base(tmp_path, monkeypatch):
    server = Server()
    handler = server.handle

    def based(request):
        response = handler(request)
        if request.url.path.startswith("/html/"):
            return httpx.Response(200, text='<base href="https://example.com/">' + response.text)
        return response

    server.handle = based
    with pytest.raises(ServiceResponseError):
        server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert all(r.url.host != "example.com" for r in server.calls)


class InlineServer(Server):
    """Synthetic publisher-named XHTML components, each with a direct image."""

    def __init__(self):
        super().__init__()
        self.component_images = {}
        for number, (part, image) in enumerate(
            zip(self.book["spine"], ("cover.jpg", "z.jpg", "a.jpg"), strict=True), start=1
        ):
            path = f"OEBPS/publisher_001_{number:03}_r1.xhtml"
            part["path"] = path
            self.component_images["/" + path] = image
        self.book["nav"]["landmarks"][0]["path"] = self.book["spine"][0]["path"]
        self.extra_markup = ""
        self.foreign = False
        self.base = None

    def handle(self, request):
        if request.url.path in self.component_images:
            self.calls.append(request)
            assert "Authorization" not in request.headers
            assert "read=private-cookie" in request.headers["Cookie"]
            assert request.url.params["secret"] in {"a", "b", "c"}
            name = self.component_images[request.url.path]
            source = f"images/{name}"
            if self.foreign:
                source = "https://example.com/private-image.jpg"
            base = f'<base href="{self.base}">' if self.base else ""
            return httpx.Response(
                200,
                text=(
                    base
                    + '<link rel="stylesheet" href="../styles/pages.css">'
                    + f'<img src="{source}"/>'
                    + self.extra_markup
                ),
            )
        if request.url.path == "/styles/pages.css":
            self.calls.append(request)
            return httpx.Response(
                200, text="img {width: 100%}", headers={"Content-Type": "text/css"}
            )
        return super().handle(request)


def test_inline_originals(tmp_path, monkeypatch):
    server = InlineServer()
    result = server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert result.cover.path.read_bytes() == server.images["cover.jpg"]
    assert [page.path.read_bytes() for page in result.narrative] == [
        server.images["z.jpg"],
        server.images["a.jpg"],
    ]
    image_requests = [r.url.path for r in server.calls if r.url.path.endswith(".jpg")]
    assert image_requests == [
        "/OEBPS/images/cover.jpg",
        "/OEBPS/images/z.jpg",
        "/OEBPS/images/a.jpg",
    ]
    assert result.reading_direction == "rtl"
    assert [page.numbers for page in result.narrative] == [(1,), (2,)]
    capture = json.loads(next(tmp_path.glob("libby-overdrive/123/captures/*.json")).read_text())
    assert capture["complete"]
    assert capture["assets"][0]["component"] == "OEBPS/publisher_001_001_r1.xhtml"
    assert capture["assets"][0]["source_path"] == "/OEBPS/images/cover.jpg"


@pytest.mark.parametrize(
    "markup",
    [
        '<img src="images/z.jpg"/>',
        '<img src="images/cover.jpg"/>',
        '<svg><image href="images/z.jpg"/></svg>',
    ],
    ids=["second", "repeated", "svg"],
)
def test_inline_ambiguity(tmp_path, monkeypatch, markup):
    server = InlineServer()
    server.extra_markup = markup
    with pytest.raises(ServiceResponseError, match="unambiguous"):
        server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert not any(r.url.path.endswith(".jpg") for r in server.calls)


def test_inline_foreign(tmp_path, monkeypatch):
    server = InlineServer()
    server.foreign = True
    with pytest.raises(ServiceResponseError):
        server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert all(r.url.host != "example.com" for r in server.calls)


def test_inline_base(tmp_path, monkeypatch):
    server = InlineServer()
    server.base = "/publisher/"
    server.adapter(tmp_path, monkeypatch).get_publication("123")
    image_requests = [r.url.path for r in server.calls if r.url.path.endswith(".jpg")]
    assert image_requests == [
        "/publisher/images/cover.jpg",
        "/publisher/images/z.jpg",
        "/publisher/images/a.jpg",
    ]


@pytest.mark.parametrize(
    "path",
    [
        "../outside.xhtml",
        "OEBPS/../outside.xhtml",
        "https://example.com/page.xhtml",
        "OEBPS/page.xhtml?token=secret",
    ],
    ids=["parent", "traversal", "absolute", "query"],
)
def test_source_paths(tmp_path, monkeypatch, path):
    server = InlineServer()
    server.book["spine"][1]["-odread-original-path"] = path
    with pytest.raises(ServiceResponseError, match="component path"):
        server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert not any(r.url.path.endswith(".xhtml") for r in server.calls)


def test_inline_encoded(tmp_path, monkeypatch):
    import base64
    import re

    server = InlineServer()
    handler = server.handle

    def encoded(request):
        response = handler(request)
        if request.url.path in server.component_images:
            head, body = response.text.split("<img", 1)
            plain = base64.b64encode(("<img" + body).encode()).decode()
            cipher = re.sub(r"(.)(.)(.)(.)", r"\4\2\3\1", plain)
            return httpx.Response(
                200, text=(head + "<script>parent.__bif_cfc1(self, '" + cipher + "')</script>")
            )
        return response

    server.handle = encoded
    result = server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert result.cover.path.read_bytes() == server.images["cover.jpg"]
    assert [page.path.read_bytes() for page in result.narrative] == [
        server.images["z.jpg"],
        server.images["a.jpg"],
    ]


def test_mixed_mapping(tmp_path, monkeypatch):
    server = Server()
    handler = server.handle

    def mixed(request):
        response = handler(request)
        if request.url.path.startswith("/html/"):
            return httpx.Response(200, text=response.text + '<img src="../images/cover.jpg"/>')
        return response

    server.handle = mixed
    with pytest.raises(ServiceResponseError, match="mixes"):
        server.adapter(tmp_path, monkeypatch).get_publication("123")
    assert not any(r.url.path.endswith(".jpg") for r in server.calls)
