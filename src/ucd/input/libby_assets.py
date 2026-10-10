"""Resolve fixed-layout reader components to exact original image bytes.

The openbook spine establishes order and roles, but not image locations.
map_assets() fetches each component, decodes its HTML body, and connects the
expected page element to its stylesheet's background image. It validates the
complete mapping before download_image() is allowed to acquire an original.

The parser supports the observed cover/page document convention and plain
CSS ID selectors. It does not render pages or implement a browser cascade;
ambiguous mappings fail rather than producing a guessed publication. Reader
URLs and component authorization remain ephemeral and on the reader origin.
"""

import base64
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import tinycss2  # type: ignore[import-untyped]
from PIL import Image, UnidentifiedImageError

from ucd.exceptions import ServiceResponseError
from ucd.input.libby_overdrive_read import OverDriveReadClient, ReadRendition, _js_string
from ucd.models import DownloadedImageFile


class ComponentHTML(HTMLParser):
    """Keep just element IDs and stylesheet/base references from page markup."""

    def __init__(self) -> None:
        super().__init__()
        self.stylesheets: list[str] = []
        self.ids: set[str] = set()
        self.base: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        identifier = fields.get("id")
        if identifier:
            self.ids.add(identifier)

        href = fields.get("href")
        if tag == "link" and "stylesheet" in (fields.get("rel") or "").split() and href:
            self.stylesheets.append(href)

        if tag == "base" and href:
            if self.base is not None and self.base != href:
                raise ServiceResponseError("Component has conflicting document bases.")
            self.base = href


def component_html(html: str) -> ComponentHTML:
    """Read clear head markup and the observed obfuscated body without JS."""
    parsed = ComponentHTML()
    parsed.feed(html)

    match = re.search(r"parent\.__bif_cfc1\(\s*self\s*,\s*(['\"])(.*?)\1\s*\)", html, re.S)

    if match:
        # libby-archiver's read.mjs: swap first/fourth in each four-char
        # group, then decode base64 and UTF-8. Never execute component JS.
        try:
            blob = _js_string(match[1] + match[2] + match[1])
            shuffled = re.sub(r"(.)(.)(.)(.)", r"\4\2\3\1", blob)
            decoded = base64.b64decode(shuffled, validate=True).decode("utf-8")
            parsed.feed(decoded)
        except (ValueError, UnicodeError):
            raise ServiceResponseError("Cannot decode fixed-layout component markup.") from None

    return parsed


def _background_url(value: list[Any]) -> str:
    """Read one URL token, refusing layers or other background expressions."""
    tokens = [token for token in value if token.type not in {"whitespace", "comment"}]
    urls: list[str] = []

    for token in tokens:
        if token.type == "url":
            urls.append(token.value)
        elif token.type == "function" and token.lower_name == "url":
            arguments = [item for item in token.arguments if item.type != "whitespace"]
            if len(arguments) == 1 and arguments[0].type == "string":
                urls.append(arguments[0].value)

    if len(tokens) != 1 or len(urls) != 1 or not urls[0]:
        raise ServiceResponseError("Page background is not one unambiguous image URL.")

    return urls[0]


def css_images(css: str, wanted: set[str]) -> dict[str, str]:
    """Accept plain ID selectors and one background URL; refuse ambiguity.

    This is a source mapping reader, not a browser cascade implementation.
    Unsupported rules targeting required pages fail instead of guessing.
    """
    result: dict[str, str] = {}

    for rule in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        if rule.type == "error":
            raise ServiceResponseError("Reader stylesheet is malformed.")

        # Conditional rules cannot supply an unconditional page mapping.
        if rule.type != "qualified-rule":
            if rule.type == "at-rule" and rule.content:
                nested = tinycss2.serialize(rule.content)
                if "background" in nested and any(
                    re.search(r"#" + re.escape(key) + r"\b", nested) for key in wanted
                ):
                    raise ServiceResponseError("Page images use unsupported conditional CSS.")
            continue

        # Ignore rules that do not describe background images.
        declarations = [
            d
            for d in tinycss2.parse_declaration_list(
                rule.content, skip_comments=True, skip_whitespace=True
            )
            if d.type == "declaration" and d.lower_name in {"background-image", "background"}
        ]
        if not declarations:
            continue

        # Only plain ID selectors bind a page to an image.
        selectors = tinycss2.serialize(rule.prelude).strip().split(",")
        targets = [s.strip()[1:] for s in selectors if re.fullmatch(r"#[\w-]+", s.strip())]
        relevant = wanted.intersection(targets)
        selector_text = tinycss2.serialize(rule.prelude)
        mentioned = {
            key for key in wanted if re.search(r"#" + re.escape(key) + r"\b", selector_text)
        }
        if mentioned != relevant:
            raise ServiceResponseError("Page images use unsupported CSS selectors.")
        if not relevant:
            continue

        for declaration in declarations:
            image_url = _background_url(declaration.value)

            for target in relevant:
                if target in result and result[target] != image_url:
                    raise ServiceResponseError("Conflicting page image mappings.")
                result[target] = image_url

    return result


@dataclass(frozen=True)
class Asset:
    """One spine association; url is transient, path is safe provenance."""

    component: str
    index: int
    role: str
    path: str
    url: str = field(repr=False)


def _component_keys(spine: list[dict[str, Any]]) -> set[str]:
    """Validate the supported source paths before fetching components."""
    keys: set[str] = set()

    for part in spine:
        original = part.get("-odread-original-path", part.get("path"))
        if not isinstance(original, str) or not re.fullmatch(
            r"html/(cover|page\d+)\.xhtml", original
        ):
            raise ServiceResponseError("Unsupported fixed-layout component path.")
        key = Path(original).stem

        if key in keys:
            raise ServiceResponseError("Duplicate fixed-layout component identity.")
        keys.add(key)

    return keys


def _load_component(
    reader: OverDriveReadClient,
    rendition: ReadRendition,
    origin: str,
    part: dict[str, Any],
    index: int,
    key: str,
) -> tuple[ComponentHTML, str]:
    """Fetch and decode one authorized page document, checking its element."""
    params = rendition.openbook.get("-odread-cmpt-params", [])
    url = urljoin(rendition.web_url, part["path"])

    # Authorization parameters follow source spine positions. They are used
    # only in this request and never copied into acquisition records.
    position = part.get("-odread-spine-position", index)
    if type(position) is not int or position < 0:
        raise ServiceResponseError("Invalid component source position.")

    if not isinstance(params, list):
        raise ServiceResponseError("Unsupported component authorization structure.")

    if position < len(params) and params[position]:
        if not isinstance(params[position], str) or urlsplit(url).query:
            raise ServiceResponseError("Malformed component authorization.")
        url += "?" + params[position]

    response = reader.resource(url, origin)
    document = component_html(response.text)
    if key not in document.ids:
        raise ServiceResponseError("Component markup lacks its expected page element.")

    return document, url


def map_assets(reader: OverDriveReadClient, rendition: ReadRendition) -> list[Asset]:
    """Map the entire ordered spine; fetch no image bytes during this phase."""
    rendition.summary()

    book = rendition.openbook
    spine = book["spine"]
    if not any(part["linear"] for part in spine):
        raise ServiceResponseError("Fixed-layout rendition has no narrative components.")

    # Establish roles and source identities before requesting documents.
    cover = book["nav"]["landmarks"]
    cover_target = next(item["path"] for item in cover if item.get("type") == "cover").split("#")[0]
    origin = f"https://{urlsplit(rendition.web_url).hostname}"
    styles: dict[str, dict[str, str]] = {}
    keys = _component_keys(spine)

    assets = []

    for index, part in enumerate(spine):
        original = part.get("-odread-original-path", part["path"])
        key = Path(original).stem

        # Fetch the page markup and verify its expected element first.
        document, url = _load_component(reader, rendition, origin, part, index, key)

        base = urljoin(url, document.base) if document.base else url
        reader.validate_resource(base, origin)

        # Resolve image rules against the document base and stylesheet URL.
        mapped: set[str] = set()
        for href in document.stylesheets:
            stylesheet = urljoin(base, href)
            if stylesheet not in styles:
                response = reader.resource(stylesheet, origin)
                if "text/css" not in response.headers.get("Content-Type", ""):
                    raise ServiceResponseError("Expected a reader stylesheet.")
                styles[stylesheet] = {
                    name: urljoin(stylesheet, ref)
                    for name, ref in css_images(response.text, keys).items()
                }
            if key in styles[stylesheet]:
                mapped.add(styles[stylesheet][key])

        if len(mapped) != 1:
            raise ServiceResponseError("Component lacks one unambiguous stylesheet image.")

        # A single validated association is retained in source spine order.
        image_url = mapped.pop()
        reader.validate_resource(image_url, origin)
        path = urlsplit(image_url).path
        if not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", path):
            raise ServiceResponseError("Image has an unsupported source path.")

        is_cover = cover_target in (part["path"], original, part.get("id"))
        role = "cover" if is_cover else "narrative" if part["linear"] else "nonlinear"
        assets.append(Asset(original, index, role, path, image_url))

    return assets


def download_image(
    reader: OverDriveReadClient, asset: Asset, origin: str, filename: Path
) -> DownloadedImageFile:
    """Inspect an original image without changing the bytes written to disk."""
    response = reader.resource(asset.url, origin)
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()

    expected = {"image/jpeg": ("JPEG", ".jpg"), "image/png": ("PNG", ".png")}
    if content_type not in expected:
        raise ServiceResponseError("Expected a supported original JPEG or PNG image.")

    # Decoding supplies properties; the response remains the saved source.
    try:
        with Image.open(BytesIO(response.content)) as image:
            image.load()
            if image.format != expected[content_type][0]:
                raise ServiceResponseError("Image bytes disagree with their content type.")
            width, height = image.size
            mode = image.mode
    except (OSError, ValueError, UnidentifiedImageError):
        raise ServiceResponseError("Reader returned an invalid image.") from None

    filename = filename.with_suffix(expected[content_type][1])
    filename.write_bytes(response.content)

    return DownloadedImageFile(filename, width, height, content_type, mode)
