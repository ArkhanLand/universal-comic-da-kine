import base64
import json
from copy import deepcopy

import httpx
import pytest
import typer
from typer.testing import CliRunner

from ucd.auth.libby import Connection, LibbyAuthenticationError, Library, Session
from ucd.cli import main, register_development_commands
from ucd.exceptions import ServiceResponseError, UnavailableError
from ucd.input.libby_overdrive_read import OverDriveReadClient, ReadRendition, decode_openbook

WEB = "https://dewey-a10b92.read.libbyapp.com/"
BOOK = {
    "title": {"main": "Synthetic 漫画"},
    "spine": [
        {"path": "html/cover.xhtml", "linear": False, "rendition-layout": "pre-paginated"},
        {"path": "html/page002.xhtml", "linear": True, "rendition-layout": "pre-paginated"},
    ],
    "nav": {"landmarks": [{"type": "cover", "path": "html/cover.xhtml"}]},
    "i18n-page-progression-direction": "rtl",
    "-odread-msg-access": "synthetic-secret",
}


def encoded_page(book=BOOK, buid="a10b92"):
    # Independently invert the reference transform for a synthetic source fixture.
    plain = base64.b64encode(json.dumps({"b": book}, ensure_ascii=False).encode()).decode()
    output = []
    key = buid[::-1]
    for index, target in enumerate(plain):
        digit = key[index % len(key)]
        if digit not in "123456789":
            output.append(target)
            continue
        shift = (index + int(digit)) % 94
        for candidate in range(32, 127):
            decoded = candidate + shift
            if decoded > 126:
                decoded = decoded % 126 + 32
            if decoded == ord(target):
                output.append(chr(candidate))
                break
        else:
            raise AssertionError("fixture cannot encode character")
    return "window.eData = " + json.dumps(["".join(output)]) + "; SPARK.bifocalPath='test';"


def test_decode_unicode():
    assert decode_openbook(encoded_page(), "a10b92") == BOOK


def wrapped_page(literal, parameter="d", assigned=None):
    assigned = parameter if assigned is None else assigned
    return (
        f"(function ({parameter}) {{ try {{ Object.defineProperty(window, 'eData', {{ "
        f"value: {parameter}, writable: true, enumerable: true, configurable: true "
        f"}}); }} catch (e) {{ window.eData = {assigned}; }} }})({literal});"
    )


@pytest.mark.parametrize(
    "parameter", ["d", "encodedPayload", "$data"], ids=["short", "long", "dollar"]
)
def test_decode_wrapper(parameter):
    literal = encoded_page().split("; SPARK.bifocalPath", 1)[0].split("=", 1)[1].strip()
    assert decode_openbook(wrapped_page(literal, parameter), "a10b92") == BOOK


def test_reject_wrapper_binding():
    literal = encoded_page().split("; SPARK.bifocalPath", 1)[0].split("=", 1)[1].strip()
    with pytest.raises(ServiceResponseError):
        decode_openbook(wrapped_page(literal, assigned="different"), "a10b92")


@pytest.mark.parametrize("literal", ["runCode()", '["text", runCode()]'], ids=["call", "mixed"])
def test_reject_wrapper_expression(literal):
    with pytest.raises(ServiceResponseError):
        decode_openbook(wrapped_page(literal), "a10b92")


@pytest.mark.parametrize(
    "tail",
    ["", "; window.tDataLocale={}; SPARK.bifocalPath='test';", "\n</script>"],
    ids=["bare", "between", "tag"],
)
def test_envelope_framing(tail):
    page = encoded_page().split("; SPARK.bifocalPath", 1)[0] + tail
    assert decode_openbook(page, "a10b92") == BOOK


def test_array_string_boundaries():
    from ucd.input.libby_overdrive_read import _embedded_array

    literal = json.dumps(['bracket ] and quote "', "second"])
    html = literal + "; window.tDataLocale={};"
    assert _embedded_array(html, 0) == literal


def test_js_string_escapes():
    # A nonnumeric BUID leaves base64 untransformed; JS strings still need parsing.
    payload = base64.b64encode(json.dumps({"b": BOOK}).encode()).decode()
    literal = "'\\x" + format(ord(payload[0]), "02x") + payload[1:] + "'"
    html = "window.eData=[" + literal + "]; SPARK.bifocalPath='test';"
    assert decode_openbook(html, "abcdef") == BOOK


@pytest.mark.parametrize(
    "html",
    [
        "<html>No envelope</html>",
        "window.eData=[runCode()]; SPARK.bifocalPath='x';",
        'window.eData=["bad!"]; SPARK.bifocalPath="x";',
        "window.eData=[]; SPARK.bifocalPath='x';",
        'window.eData=["unterminated];',
        'window.eData=["value", runCode()];',
    ],
    ids=["missing", "call", "bad-data", "empty", "quote", "mixed"],
)
def test_reject_bad_envelope(html):
    with pytest.raises(ServiceResponseError):
        decode_openbook(html, "a10b92")


def test_reader_handshake():
    requests = []

    def handler(request):
        requests.append(request)
        assert "Authorization" not in request.headers
        if "message" in request.url.params:
            return httpx.Response(
                302,
                headers={
                    "Location": "/ready",
                    "Set-Cookie": "read=ephemeral; Path=/; Secure",
                },
            )
        assert "read=ephemeral" in request.headers["Cookie"]
        if request.url.path == "/ready":
            return httpx.Response(200)
        return httpx.Response(200, text=encoded_page())

    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer must-not-forward"}
    )
    rendition = OverDriveReadClient(client).fetch_openbook(
        {"urls": {"web": WEB}, "message": "message=signed-secret"},
    )
    assert rendition.openbook == BOOK
    assert len(requests) == 3
    assert rendition.summary() == {
        "spine_components": 2,
        "cover_components": 1,
        "narrative_components": 1,
        "nonlinear_components": 1,
        "reading_direction": "rtl",
    }
    assert "synthetic-secret" not in repr(rendition)
    assert "dewey" not in repr(rendition)


@pytest.mark.parametrize(
    "web",
    [
        "http://dewey-a10b92.read.libbyapp.com/",
        "https://example.com/",
        "https://dewey-a10b92.listen.libbyapp.com/",
        "https://user:password@dewey-a10b92.read.libbyapp.com/",
    ],
    ids=["http", "foreign", "listen", "userinfo"],
)
def test_reject_reader_host(web):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("request")))
    with pytest.raises(ServiceResponseError):
        OverDriveReadClient(client).fetch_openbook({"urls": {"web": web}, "message": "secret"})


def test_redirect_origin():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://example.com/?secret=value"})

    with pytest.raises(ServiceResponseError):
        OverDriveReadClient(httpx.Client(transport=httpx.MockTransport(handler))).fetch_openbook(
            {"urls": {"web": WEB}, "message": "secret"},
        )
    assert len(calls) == 1


def test_redirect_limit():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "/loop"})

    with pytest.raises(ServiceResponseError, match="Too many redirects"):
        OverDriveReadClient(httpx.Client(transport=httpx.MockTransport(handler))).fetch_openbook(
            {"urls": {"web": WEB}, "message": "secret"},
        )
    assert len(calls) == 8


def test_image_cdn():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                302, headers={"Location": "https://odrresources.cachefly.net/images/cover.jpg"}
            )
        assert "Authorization" not in request.headers
        assert "Cookie" not in request.headers
        return httpx.Response(200, content=b"original")

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "private-token", "Cookie": "read=private-cookie"},
    )
    reader = OverDriveReadClient(client)
    response = reader.resource(WEB + "images/cover.jpg", WEB.rstrip("/"), image_cdn=True)
    assert response.content == b"original"
    assert [r.url.host for r in calls] == [
        "dewey-a10b92.read.libbyapp.com",
        "odrresources.cachefly.net",
    ]


@pytest.mark.parametrize(
    "target",
    [
        "http://odrresources.cachefly.net/image.jpg",
        "https://odrresources.cachefly.net.evil.test/image.jpg",
        "https://user@odrresources.cachefly.net/image.jpg",
        "https://odrresources.cachefly.net:444/image.jpg",
        "https://odrresources.cachefly.net/image.jpg#fragment",
        "https://dewey-other.read.libbyapp.com/image.jpg",
    ],
    ids=["http", "suffix", "userinfo", "port", "fragment", "reader"],
)
def test_image_cdn_reject(target):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": target})

    reader = OverDriveReadClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ServiceResponseError):
        reader.resource(WEB + "image.jpg", WEB.rstrip("/"), image_cdn=True)
    assert len(calls) == 1


def test_cdn_document_reject():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://odrresources.cachefly.net/doc"})

    reader = OverDriveReadClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ServiceResponseError):
        reader.resource(WEB + "doc", WEB.rstrip("/"))
    assert len(calls) == 1


def test_cdn_initial_reject():
    reader = OverDriveReadClient(
        httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("request")))
    )
    with pytest.raises(ServiceResponseError):
        reader.resource("https://odrresources.cachefly.net/image.jpg", WEB, image_cdn=True)


def test_cdn_chain_reject():
    calls = []

    def handler(request):
        calls.append(request)
        target = (
            "https://odrresources.cachefly.net/image.jpg"
            if len(calls) == 1
            else "https://example.com/image.jpg"
        )
        return httpx.Response(302, headers={"Location": target})

    reader = OverDriveReadClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ServiceResponseError):
        reader.resource(WEB + "image.jpg", WEB.rstrip("/"), image_cdn=True)
    assert len(calls) == 2


def test_reader_auth_privacy():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(LibbyAuthenticationError) as error:
        OverDriveReadClient(client).fetch_openbook({"urls": {"web": WEB}, "message": "secret"})
    assert "secret" not in str(error.value)
    assert "rerun ucd init" in str(error.value)


@pytest.mark.parametrize(
    "mutation",
    ["layout", "cover-linear", "missing-cover", "linearity"],
    ids=["layout", "cover", "landmark", "linear"],
)
def test_reject_bad_rendition(mutation):
    book = deepcopy(BOOK)
    if mutation == "layout":
        book["spine"][1]["rendition-layout"] = "reflowable"
    elif mutation == "cover-linear":
        book["spine"][0]["linear"] = True
    elif mutation == "missing-cover":
        book["nav"]["landmarks"] = []
    else:
        book["spine"][1]["linear"] = "true"
    with pytest.raises((ServiceResponseError, UnavailableError)):
        ReadRendition(WEB, book).summary()


def toc_book():
    """Synthetic form of the collection with no linear fields or landmarks."""
    book = deepcopy(BOOK)
    for entry in book["spine"]:
        del entry["linear"]
    book["nav"] = {
        "toc": [
            {"title": "Cover", "path": "html/cover.xhtml"},
            {"title": "Chapter One", "path": "html/page002.xhtml"},
        ]
    }
    del book["i18n-page-progression-direction"]
    return book


def test_toc_roles():
    book = toc_book()
    source = deepcopy(book)
    rendition = ReadRendition(WEB, book)
    assert rendition.component_roles() == ("cover", "narrative"), "TOC identifies the cover"
    assert rendition.summary() == {
        "spine_components": 2,
        "cover_components": 1,
        "narrative_components": 1,
        "nonlinear_components": 1,
        "reading_direction": "unknown",
    }, "Summary must count inferred roles and preserve unknown direction"
    assert book == source, "Role interpretation must not alter source provenance"


@pytest.mark.parametrize("reference", ["path", "original", "id", "fragment"])
def test_toc_reference(reference):
    book = toc_book()
    entry = book["spine"][0]
    if reference == "original":
        entry["-odread-original-path"] = entry["path"]
        entry["path"] = "delivery/cover.xhtml"
    elif reference == "id":
        entry["id"] = "cover-id"
        book["nav"]["toc"][0]["path"] = "cover-id"
    elif reference == "fragment":
        book["nav"]["toc"][0]["path"] += "#start"
    book["nav"]["toc"][0]["title"] = " COVER "
    assert ReadRendition(WEB, book).component_roles() == ("cover", "narrative"), (
        f"Cover reference must resolve via {reference}"
    )


def test_cover_not_first():
    book = toc_book()
    book["spine"].reverse()
    assert ReadRendition(WEB, book).component_roles() == ("narrative", "cover"), (
        "Cover selection must follow navigation rather than spine position"
    )


@pytest.mark.parametrize("index", [0, 1])
def test_cover_override(index):
    book = toc_book()
    book["nav"]["toc"] = [{"title": "Chapter One", "path": "html/page002.xhtml"}]
    source = deepcopy(book)
    rendition = ReadRendition(WEB, book, cover_index=index)
    expected = ("cover", "narrative") if index == 0 else ("narrative", "cover")
    assert rendition.component_roles() == expected, (
        "Explicit choice must determine cover membership"
    )
    assert book == source, "An override must not manufacture source navigation or linearity"
    # Omitting the override must retain automatic validation for this book.
    with pytest.raises(ServiceResponseError):
        ReadRendition(WEB, book).summary()


def test_override_precedence():
    book = deepcopy(BOOK)
    book["spine"][0]["linear"] = True
    rendition = ReadRendition(WEB, book, cover_index=1)
    assert rendition.component_roles() == ("narrative", "cover"), (
        "Explicit choice must take precedence over navigation and selected cover linearity"
    )


@pytest.mark.parametrize("index", [-1, 2, True, 0.5], ids=["negative", "range", "bool", "float"])
def test_invalid_cover_index(index):
    # Python callers must not wrap negative indices or coerce invalid types.
    with pytest.raises(ServiceResponseError, match="Cover index"):
        ReadRendition(WEB, toc_book(), cover_index=index).summary()


@pytest.mark.parametrize("mutation", ["layout", "linearity"])
def test_override_keeps_checks(mutation):
    book = toc_book()
    if mutation == "layout":
        book["spine"][1]["rendition-layout"] = "reflowable"
    else:
        book["spine"][1]["linear"] = "true"
    # Selecting a cover cannot bypass validation of the component metadata.
    with pytest.raises((ServiceResponseError, UnavailableError)):
        ReadRendition(WEB, book, cover_index=0).summary()


def test_linear_defaults():
    book = deepcopy(BOOK)
    del book["spine"][1]["linear"]
    auxiliary = dict(book["spine"][1], path="html/notes.xhtml", linear=False)
    book["spine"].insert(1, auxiliary)
    rendition = ReadRendition(WEB, book)
    assert rendition.component_roles() == ("cover", "nonlinear", "narrative"), (
        "Omitted linearity defaults to narrative; explicit false stays auxiliary"
    )
    assert rendition.summary()["nonlinear_components"] == 2, (
        "Nonlinear count must include both the cover and auxiliary component"
    )


@pytest.mark.parametrize("toc_kind", ["agrees", "conflicts", "duplicate", "malformed"])
def test_landmark_precedence(toc_kind):
    book = deepcopy(BOOK)
    toc = [{"title": "Cover", "path": "html/cover.xhtml"}]
    if toc_kind == "conflicts":
        toc[0]["path"] = "html/page002.xhtml"
    elif toc_kind == "duplicate":
        toc.append(dict(toc[0]))
    book["nav"]["toc"] = {} if toc_kind == "malformed" else toc
    assert ReadRendition(WEB, book).component_roles() == ("cover", "narrative"), (
        f"A valid landmark must remain authoritative with a {toc_kind} TOC"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "missing",
        "unmatched",
        "ambiguous",
        "linear",
        "null-linear",
        "integer-linear",
        "empty-landmarks",
        "bad-landmarks",
        "bad-toc",
    ],
)
def test_reject_toc_cover(mutation):
    book = toc_book()
    nav = book["nav"]
    if mutation == "duplicate":
        nav["toc"].append(dict(nav["toc"][0]))
    elif mutation == "missing":
        nav["toc"][0]["title"] = "First Page"
    elif mutation == "unmatched":
        nav["toc"][0]["path"] = "html/missing.xhtml"
    elif mutation == "ambiguous":
        book["spine"][1]["id"] = "html/cover.xhtml"
    elif mutation == "linear":
        book["spine"][0]["linear"] = True
    elif mutation == "null-linear":
        book["spine"][1]["linear"] = None
    elif mutation == "integer-linear":
        book["spine"][1]["linear"] = 1
    elif mutation == "empty-landmarks":
        nav["landmarks"] = []
    elif mutation == "bad-landmarks":
        nav["landmarks"] = None
    elif mutation == "bad-toc":
        nav["toc"] = {}
    # Invalid selected cover evidence or linearity must fail validation.
    with pytest.raises(ServiceResponseError):
        ReadRendition(WEB, book).summary()


def test_inspect_summary(monkeypatch):
    from ucd.auth.libby import ConnectionStore, LibbyClient

    connection = Connection("phoenix", Library("phoenix", "Phoenix", "34"), "keyring-ref")
    monkeypatch.setattr("ucd.auth.libby.system_secret_store", lambda: object())
    monkeypatch.setattr(
        ConnectionStore,
        "select_loan",
        lambda *args, **kwargs: (connection, Session("token", "card"), {}),
    )
    monkeypatch.setattr(
        LibbyClient, "open_loan", lambda *args, **kwargs: {"message": "signed-secret"}
    )
    monkeypatch.setattr(
        OverDriveReadClient, "fetch_openbook", lambda *args: ReadRendition(WEB, BOOK)
    )
    monkeypatch.setenv("UCD_DEV_COMMANDS", "1")
    dev_app = typer.Typer()
    dev_app.callback()(main)
    register_development_commands(dev_app)
    result = CliRunner().invoke(
        dev_app, ["inspect-loan", "11103570", "--service", "libby-overdrive"]
    )
    assert result.exit_code == 0, result.output
    assert "Spine components: 2" in result.output
    assert "Reading direction: rtl" in result.output
    for diagnostic in ("Session:", "device role=", "primary device=", "chip fields="):
        assert diagnostic not in result.output
    for secret in ("synthetic-secret", "signed-secret", "token", "dewey"):
        assert secret not in result.output


@pytest.mark.parametrize(
    "value", [None, "", "0", "true", "1"], ids=["unset", "empty", "zero", "true", "one"]
)
def test_developer_flag(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("UCD_DEV_COMMANDS", raising=False)
    else:
        monkeypatch.setenv("UCD_DEV_COMMANDS", value)
    target = typer.Typer()
    target.callback()(main)

    @target.command()
    def ordinary():
        pass

    register_development_commands(target)
    runner = CliRunner()
    help_result = runner.invoke(target, ["--help"], color=False)
    assert help_result.exit_code == 0
    assert ("inspect-loan" in help_result.output) is (value == "1")
    if value != "1":
        result = runner.invoke(target, ["inspect-loan", "11103570", "--service", "libby-overdrive"])
        assert result.exit_code == 2
