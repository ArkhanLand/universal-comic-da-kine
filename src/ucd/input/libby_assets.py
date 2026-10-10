"""Resolve fixed-layout reader components to exact original image bytes.

The openbook spine establishes order and roles, but not image locations.
map_assets() fetches each component, decodes its HTML body, and connects the
expected page element to its stylesheet's background image. It validates the
complete mapping before download_image() is allowed to acquire an original.

The parser supports a single inline XHTML image or the observed page-element
background convention with plain CSS ID selectors. It does not render pages
or implement a browser cascade;
ambiguous mappings fail rather than producing a guessed publication. Reader
URLs and component authorization remain ephemeral. Image redirects may use
the explicitly allowed asset CDN; documents stay on the reader origin.
"""

import base64
import re
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import tinycss2  # type: ignore[import-untyped]
from PIL import Image, UnidentifiedImageError

from ucd.download.parallel import fetch_ordered
from ucd.exceptions import ServiceResponseError
from ucd.input.libby_overdrive_read import OverDriveReadClient, ReadRendition, _js_string
from ucd.models import DownloadedImageFile


class ComponentHTML(HTMLParser):
    """Keep image references, element IDs, and stylesheet/base locations."""

    def __init__(self) -> None:
        super().__init__()
        self.stylesheets: list[str] = []
        self.ids: set[str] = set()
        self.base: str | None = None
        # Some publishers put the page image directly in XHTML instead of CSS.
        # Keep these references transient, like stylesheet and base URLs.
        self.images: list[str] = []
        self.svg_images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        identifier = fields.get("id")
        if identifier:
            self.ids.add(identifier)

        source = fields.get("src")
        if tag == "img" and source:
            self.images.append(source)

        svg_source = fields.get("href") or fields.get("xlink:href")
        if tag == "image" and svg_source:
            self.svg_images.append(svg_source)

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
    """Validate source document identities before fetching components.

    Publishers choose their own XHTML filenames. A filename is provenance,
    not proof that the document contains an element with the same name.
    Filename stems are only used by the supported CSS-background convention.
    """
    keys: set[str] = set()
    paths: set[str] = set()

    for part in spine:
        original = part.get("-odread-original-path", part.get("path"))
        if (
            not isinstance(original, str)
            or not re.fullmatch(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.xhtml", original)
            or any(segment in {".", ".."} for segment in original.split("/"))
        ):
            raise ServiceResponseError("Unsupported fixed-layout component path.")

        if original in paths:
            raise ServiceResponseError("Duplicate fixed-layout component identity.")

        paths.add(original)
        keys.add(Path(original).stem)

    return keys


def _load_component(
    reader: OverDriveReadClient,
    rendition: ReadRendition,
    origin: str,
    part: dict[str, Any],
    index: int,
) -> tuple[ComponentHTML, str]:
    """Fetch and decode one authorized page document before mapping its image."""
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
    return document, url


def map_assets(
    reader: OverDriveReadClient,
    rendition: ReadRendition,
    progress: Callable[[int, int], None] | None = None,
    workers: int = 1,
) -> list[Asset]:
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
    if progress:
        progress(0, len(spine))

    # Only independent component requests run in workers. Shared stylesheets
    # are resolved once by the consumer, avoiding races in the CSS cache.
    def fetch_component(index: int, part: dict[str, Any]) -> tuple[ComponentHTML, str]:
        return _load_component(reader, rendition, origin, part, index)

    with closing(fetch_ordered(spine, fetch_component, workers)) as components:
        for index, (document, url) in components:
            part = spine[index]
            original = part.get("-odread-original-path", part["path"])
            key = Path(original).stem

            # Resolve markup relative to its validated document base.
            base = urljoin(url, document.base) if document.base else url
            reader.validate_resource(base, origin)

            # A single img is a direct document-to-image association. Do not
            # assume it has an ID, or that its name matches the XHTML filename.
            # Multiple images or SVG compositions need a separate rendering policy.
            if len(document.images) > 1 or document.svg_images:
                raise ServiceResponseError("Component is not one unambiguous inline page image.")

            inline = {urljoin(base, source) for source in document.images}

            # CSS backgrounds still require the observed filename-to-element
            # binding. An inline image does not need that CSS-specific convention.
            if not inline and key not in document.ids:
                raise ServiceResponseError("Component markup lacks its expected page element.")

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
                if key in document.ids and key in styles[stylesheet]:
                    mapped.add(styles[stylesheet][key])

            if inline and mapped:
                raise ServiceResponseError("Component mixes inline and background page images.")

            mapped.update(inline)
            if len(mapped) != 1:
                raise ServiceResponseError("Component lacks one unambiguous page image.")

            # A single validated association is retained in source spine order.
            image_url = mapped.pop()
            reader.validate_resource(image_url, origin)
            path = urlsplit(image_url).path
            if not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", path):
                raise ServiceResponseError("Image has an unsupported source path.")

            is_cover = cover_target in (part["path"], original, part.get("id"))
            role = "cover" if is_cover else "narrative" if part["linear"] else "nonlinear"
            assets.append(Asset(original, index, role, path, image_url))
            # Report only a fully resolved component, including its stylesheet
            # and image association. This phase fetches documents, not images.
            if progress:
                progress(index + 1, len(spine))

    return assets


def download_image(
    reader: OverDriveReadClient, asset: Asset, origin: str, filename: Path
) -> DownloadedImageFile:
    """Inspect an original image without changing the bytes written to disk."""
    # The reader may redirect an image to its public CacheFly asset host.
    # Documents and stylesheets continue to require the reader origin.
    response = reader.resource(asset.url, origin, image_cdn=True)
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
