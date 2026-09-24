import hashlib
import json
import mimetypes
import re
import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from http.cookiejar import MozillaCookieJar
from io import BytesIO
from math import isclose
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageChops

from ucd.exceptions import (
    IneligibleError,
    InvalidComicInputError,
    ServiceResponseError,
)
from ucd.input.base import InputAdapter, MetadataCallback, ProgressCallback
from ucd.models import CLF, Creator, DownloadedImageFile, Page, Pages

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)

BASE_URL = "https://www.marvel.com"
BIFROST_BASE_URL = "https://bifrost.marvel.com"
ISSUE_PATH = "/comics/issue"
COMICS_PATH = "/v1/catalog/digital-comics"
METADATA_PATH = f"{COMICS_PATH}/metadata"
ASSETS_PATH = f"{COMICS_PATH}/assets"
WORK_PATH = "/tmp/ucd/marvel"


@dataclass(frozen=True, slots=True)
class _MarvelIssueData:
    catalog_id: str
    digital_id: str
    issue_number: str
    series_id: str


@dataclass(frozen=True, slots=True)
class _MarvelPageSource:
    numbers: tuple[int, ...] | None
    url: str


@dataclass(frozen=True, slots=True)
class _MarvelPageSources:
    cover: _MarvelPageSource
    pages: list[_MarvelPageSource]


def _pagination_dimensions(page: Page) -> tuple[int, int] | None:
    """Derive image width and height excluding uniform black edge bands.

    A channel tolerance of 8 accommodates near-solid JPEG padding. Only complete
    outer rows/columns are excluded; interior gutters and white paper margins are
    left alone. Stored bytes and recorded dimensions are unchanged.
    """
    with Image.open(page.path) as original:
        image = original.convert("RGB")
    # Each channel is a grayscale image, not a scalar: its pixels hold that
    # color component's intensity (0-255) at the original image dimensions.
    red, green, blue = image.split()
    # lighter() takes the larger value at each pixel. Combining all three
    # channels gives max(R, G, B), so a pixel is near-black only if every
    # component is at most 8. This is not perceptual brightness.
    brightness = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    # point() applies a 256-entry lookup table: intensities 0-8 become zero,
    # and 9-255 become 255. getbbox() encloses all nonzero mask pixels,
    # returning (left, top, right, bottom), or None for an all-black mask.
    # The rectangle excludes black outer bands without removing dark areas
    # inside the artwork; right and bottom are exclusive coordinates.
    content = brightness.point([0] * 9 + [255] * 247).getbbox()
    if content is None:
        return None
    left, top, right, bottom = content
    return right - left, bottom - top


def _inferred_span(dimensions: tuple[int, int], reference: tuple[int, int]) -> int | None:
    """Scale the measurement to the reference height; accept integer spans within 1%."""
    width, height = dimensions
    reference_width, reference_height = reference
    if min(width, height, reference_width, reference_height) <= 0:
        return None
    scaled_width = width * reference_height / height
    relative_span = scaled_width / reference_width
    span = round(relative_span)
    return span if span >= 1 and isclose(relative_span, span, rel_tol=0.01) else None


def _infer_page_numbers(pages: Pages) -> Pages:
    """Estimate horizontal spans against the dominant portrait interior dimensions.

    This is a Marvel import heuristic, not a model invariant or source assertion.
    Explicit mappings win. An ambiguous span raises rather than silently producing
    incomplete logical pagination. Counts describe the supplied digital edition,
    not omitted print pages. Uniform multi-page assets can defeat the assumed
    single-page reference; dimensions alone cannot resolve that ambiguity.
    """
    if all(page.numbers is not None for page in pages.pages):
        return pages
    candidates = Counter(
        (page.width, page.height)
        for page in pages.pages
        if 0 < page.width < page.height and (page.numbers is None or len(page.numbers) == 1)
    )
    if candidates:
        reference = candidates.most_common(1)[0][0]
    elif 0 < pages.cover.width < pages.cover.height:
        reference = (pages.cover.width, pages.cover.height)
    else:
        page = next(page for page in pages.pages if page.numbers is None)
        raise ValueError(
            f"Cannot infer logical page span for {page.path}: no portrait single-page "
            "reference is available. Supply explicit page-number mappings."
        )

    next_number = 1
    inferred = []
    for page in pages.pages:
        if page.numbers is not None:
            if page.numbers:
                next_number = max(page.numbers) + 1
        else:
            # Ordinary print-page margins are part of the page rectangle. Only
            # try trimming padding when the original dimensions do not fit.
            dimensions: tuple[int, int] | None = (page.width, page.height)
            span = _inferred_span((page.width, page.height), reference)
            if span is None:
                dimensions = _pagination_dimensions(page)
                if dimensions is not None:
                    span = _inferred_span(dimensions, reference)
            if span is None:
                measurement = (
                    f"{dimensions[0]} x {dimensions[1]} after black-border measurement"
                    if dimensions is not None
                    else "no nonblack content"
                )
                raise ValueError(
                    f"Cannot infer logical page span for {page.path}: "
                    f"original {page.width} x {page.height}, {measurement}, "
                    f"reference {reference[0]} x {reference[1]}; "
                    "no positive integer span within 1%. Supply an explicit page-number mapping."
                )
            page = replace(page, numbers=tuple(range(next_number, next_number + span)))
            next_number += span
        inferred.append(page)
    return replace(pages, pages=tuple(inferred))


class MarvelUnlimitedAdapter(InputAdapter):
    def __init__(
        self,
        client: httpx.Client | None = None,
        cookie_file: Path | None = None,
    ) -> None:
        self.work_dirs: list[Path] = []

        if client is not None:
            self.client = client
            return

        if cookie_file is not None:
            cookies = MozillaCookieJar(cookie_file)
            cookies.load(ignore_discard=True, ignore_expires=True)
        else:
            cookies = MozillaCookieJar()

        self.client = httpx.Client(
            cookies=cookies,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    def _build_clf(
        self,
        issue_data: _MarvelIssueData,
        metadata: dict[str, Any],
        pages: Pages,
    ) -> CLF:
        return CLF(
            service="marvelUnlimited",
            service_id=issue_data.digital_id,
            title=metadata["title"],
            source_url=BASE_URL + ISSUE_PATH + f"/{issue_data.catalog_id}",
            series=metadata["series_title"],
            issue_number=issue_data.issue_number,
            service_series_id=issue_data.series_id,
            publication_date=date.fromisoformat(metadata["release_date"]),
            publisher="Marvel",
            description=metadata["description"],
            age_rating=metadata["rating"],
            imprint=metadata.get("imprint"),
            thumbnail_url=f"{metadata['thumbnail']['path']}.{metadata['thumbnail']['extension']}",
            creators=tuple(
                Creator(
                    name=creator["full_name"],
                    role=creator["role"],
                )
                for creator in metadata["creators"]["extended_list"]
            ),
            pages=pages,
        )

    def matches_url(self, url: str) -> bool:
        parsed = urlparse(url)

        # fmt: off
        return (
            parsed.netloc.lower() in {"marvel.com", "www.marvel.com"}
            and parsed.path.startswith("/comics/issue/")
        )
        # fmt: on

    def get_catalog_id(self, source: str) -> str:
        match source:
            case _ if source.isdigit():
                return source
            case _ if m := re.search(r"/comics/issue/([0-9]+)(?:/.*)?$", source):
                return f"{m.group(1)}"
            case _:
                raise InvalidComicInputError(f"Unable to parse source: {source}")

    def get_issue_data(self, catalog_id: str) -> _MarvelIssueData:
        issue_url = BASE_URL + ISSUE_PATH + f"/{catalog_id}"

        response = self.client.get(issue_url)
        response.raise_for_status()

        html = response.text

        marker = "window['__marvel-fitt__']="
        start = html.find(marker)
        if start == -1:
            raise ServiceResponseError("Unable to find Marvel issue data in response")
        start += len(marker)

        decoder = json.JSONDecoder()
        page_data, _ = decoder.raw_decode(response.text[start:])

        try:
            issue_data = page_data["page"]["content"]["issueDetails"]
        except KeyError as exc:
            raise ServiceResponseError("Marvel response did not contain issueDetails") from exc

        if issue_data["id"] != catalog_id:
            raise ServiceResponseError("Requested Catalog ID does not match Marvel response")

        return _MarvelIssueData(
            catalog_id=str(issue_data["id"]),
            digital_id=str(issue_data["digitalComicID"]),
            issue_number=str(issue_data["issue"]),
            series_id=str(issue_data["seriesId"]),
        )

    def get_metadata(self, digital_id: str) -> dict[str, Any]:
        if not digital_id.isdigit():
            raise InvalidComicInputError("Invalid Digital ID")
        url = BIFROST_BASE_URL + METADATA_PATH + f"/{digital_id}"

        response = self.client.get(url)
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        # Marvel-specific data. This needs to be turned into a CLF():
        meta: dict[str, Any] = data["data"]["results"][0]["issue_meta"]

        return meta

    def get_page_sources(self, digital_id: str) -> _MarvelPageSources:
        if not digital_id.isdigit():
            raise InvalidComicInputError("Invalid Digital ID")

        url = BIFROST_BASE_URL + ASSETS_PATH + f"/{digital_id}"

        response = self.client.get(url)
        response.raise_for_status()

        try:
            result = response.json()["data"]["results"][0]
            subscriber = result["auth_state"]["subscriber"]
            pages = result["pages"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ServiceResponseError("Marvel response did not contain page assets") from exc

        if not subscriber:
            raise IneligibleError("Marvel Unlimited subscription required")

        try:
            # The first asset is treated as the cover. Asset sequence alone does
            # not establish logical pagination: an image may contain a spread.
            return _MarvelPageSources(
                cover=_MarvelPageSource(
                    numbers=(),
                    url=pages[0]["assets"]["source"],
                ),
                pages=list(
                    _MarvelPageSource(
                        numbers=None,
                        url=page["assets"]["source"],
                    )
                    for page in pages[1:]
                ),
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ServiceResponseError("Marvel response contained invalid page asset data") from exc

    def _download_image(self, url: str, filename: Path) -> DownloadedImageFile:
        response = self.client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";")[0]

        if not content_type.startswith("image/"):
            raise ServiceResponseError(
                f"Expected image from {url}; got {content_type or 'no Content-Type'}"
            )

        extension = mimetypes.guess_extension(content_type)

        if extension is None:
            raise ServiceResponseError(f"Unknown extension for image content type: {content_type}")

        with Image.open(BytesIO(response.content)) as img:
            width, height = img.size
            mode = img.mode

        filename = filename.with_suffix(extension)
        filename.write_bytes(response.content)

        return DownloadedImageFile(
            filename=filename,
            width=width,
            height=height,
            content_type=content_type,
            mode=mode,
        )

    def _source_hash(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def get_pages(
        self,
        digital_id: str,
        page_sources: _MarvelPageSources,
        progress: Callable[[int, int], None] | None = None,
        *,
        infer_pagination: bool = True,
    ) -> Pages:
        work_dir = Path(WORK_PATH) / digital_id
        work_dir.mkdir(parents=True, exist_ok=True)
        self.work_dirs.append(work_dir)

        total = len(page_sources.pages) + 1
        completed = 0
        if progress is not None:
            progress(completed, total)

        url = page_sources.cover.url
        filename = work_dir / f"UCD-cover-{self._source_hash(url)}"
        cover_image = self._download_image(url, filename)
        completed += 1
        if progress is not None:
            progress(completed, total)

        cover_page = Page(
            numbers=(),
            path=cover_image.filename,
            width=cover_image.width,
            height=cover_image.height,
            content_type=cover_image.content_type,
            mode=cover_image.mode,
        )

        page_list: list[Page] = []

        for asset_index, page_source in enumerate(page_sources.pages, start=1):
            filename = work_dir / f"UCD-{asset_index:05}-{self._source_hash(page_source.url)}"

            image = self._download_image(page_source.url, filename)
            page_list.append(
                Page(
                    numbers=page_source.numbers,
                    path=image.filename,
                    width=image.width,
                    height=image.height,
                    content_type=image.content_type,
                    mode=image.mode,
                )
            )
            completed += 1
            if progress is not None:
                progress(completed, total)

        pages = Pages(cover=cover_page, pages=tuple(page_list))
        return _infer_page_numbers(pages) if infer_pagination else pages

    def cleanup(self) -> None:
        return
        for path in self.work_dirs:
            shutil.rmtree(path, ignore_errors=True)

    def get_clf(
        self,
        source: str,
        progress: ProgressCallback | None = None,
        metadata_ready: MetadataCallback | None = None,
    ) -> CLF:
        if not (self.matches_url(source) or source.isdigit()):
            raise InvalidComicInputError(f"Invalid Marvel comic input: {source}")

        catalog_id = self.get_catalog_id(source)
        issue_data = self.get_issue_data(catalog_id)

        metadata = self.get_metadata(issue_data.digital_id)
        if metadata_ready is not None:
            metadata_ready(
                str(metadata["title"]),
                str(metadata["series_title"]),
                issue_data.issue_number,
            )

        page_sources = self.get_page_sources(issue_data.digital_id)

        page_progress: Callable[[int, int], None] | None = None
        if progress is not None:
            title = str(metadata["title"])

            def report_page_progress(completed: int, total: int) -> None:
                progress(title, completed, total)

            page_progress = report_page_progress

        pages = self.get_pages(
            issue_data.digital_id,
            page_sources,
            progress=page_progress,
            infer_pagination=metadata.get("digital_format", "print") == "print",
        )

        return self._build_clf(
            issue_data=issue_data,
            metadata=metadata,
            pages=pages,
        )
