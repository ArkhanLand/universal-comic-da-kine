import hashlib
import json
import mimetypes
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import partial
from http.cookiejar import MozillaCookieJar
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from ucd.download.cache import cache_root
from ucd.download.images import ImageCache
from ucd.download.pagination import (
    _infer_page_numbers as _infer_page_numbers,
)
from ucd.download.pagination import (
    _inferred_span as _inferred_span,
)
from ucd.download.pagination import (
    _pagination_dimensions as _pagination_dimensions,
)
from ucd.download.pagination import (
    _presentation_span as _presentation_span,
)
from ucd.exceptions import (
    IneligibleError,
    InvalidComicInputError,
    ServiceResponseError,
    UnavailableError,
)
from ucd.input.base import InputAdapter, MetadataCallback, ProgressCallback
from ucd.models import Creator, DownloadedImageFile, Page, Pages, Publication

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
CACHE_PATH = cache_root() / "marvel-unlimited"


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
    asset_id: str | None = None


@dataclass(frozen=True, slots=True)
class _MarvelPageSources:
    cover: _MarvelPageSource
    pages: list[_MarvelPageSource]


class MarvelUnlimitedAdapter(InputAdapter):
    def __init__(
        self,
        client: httpx.Client | None = None,
        cookie_file: Path | None = None,
        *,
        cache_dir: Path | None = None,
        refresh: bool = False,
    ) -> None:
        self.image_cache = ImageCache(cache_dir if cache_dir is not None else CACHE_PATH)
        self.refresh = refresh

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

    def _build_publication(
        self,
        issue_data: _MarvelIssueData,
        metadata: dict[str, Any],
        cover: Page,
        narrative: tuple[Page, ...],
    ) -> Publication:
        return Publication(
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
            age_rating=metadata.get("rating"),
            imprint=metadata.get("imprint"),
            thumbnail_url=f"{metadata['thumbnail']['path']}.{metadata['thumbnail']['extension']}",
            creators=tuple(
                Creator(
                    name=creator["full_name"],
                    role=creator["role"],
                )
                for creator in metadata["creators"]["extended_list"]
            ),
            cover=cover,
            narrative=narrative,
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
        if response.status_code == 404:
            raise UnavailableError(
                f"No downloadable digital edition is available for Marvel digital ID {digital_id}"
            )
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        # Marvel-specific data. This needs to be turned into a Publication():
        meta: dict[str, Any] = data["data"]["results"][0]["issue_meta"]

        return meta

    def get_page_sources(self, digital_id: str) -> _MarvelPageSources:
        if not digital_id.isdigit():
            raise InvalidComicInputError("Invalid Digital ID")

        url = BIFROST_BASE_URL + ASSETS_PATH + f"/{digital_id}"

        response = self.client.get(url)
        if response.status_code == 404:
            raise UnavailableError(
                f"No downloadable digital edition is available for Marvel digital ID {digital_id}"
            )
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
            if not isinstance(pages, list) or not all(isinstance(page, dict) for page in pages):
                raise ServiceResponseError("Marvel response contained invalid page asset data")
            # Observed stable across repeated manifests despite URL rotation.
            # Missing IDs fall back to URL-specific reuse, never sequence IDs.
            identifiers = [str(page["id"]) for page in pages if page.get("id") is not None]
            if len(set(identifiers)) != len(identifiers):
                raise ServiceResponseError("Marvel response contained duplicate page IDs")
            # The first asset is treated as the cover. Asset sequence alone does
            # not establish logical pagination: an image may contain a spread.
            return _MarvelPageSources(
                cover=_MarvelPageSource(
                    numbers=(),
                    url=pages[0]["assets"]["source"],
                    asset_id=str(pages[0]["id"]) if pages[0].get("id") is not None else None,
                ),
                pages=list(
                    _MarvelPageSource(
                        numbers=None,
                        url=page["assets"]["source"],
                        asset_id=str(page["id"]) if page.get("id") is not None else None,
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
            img.load()
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
        if not digital_id.isdigit():
            raise InvalidComicInputError("Invalid Digital ID")
        sources = [page_sources.cover, *page_sources.pages]
        total = len(sources)
        if progress is not None:
            progress(0, total)

        page_list: list[Page] = []
        Path(WORK_PATH).mkdir(parents=True, exist_ok=True)
        # Scratch downloads are private to this call. Publication paths point
        # to the persistent cache and survive cleanup or another instance.
        with TemporaryDirectory(dir=WORK_PATH) as temporary:
            for index, source in enumerate(sources):
                identity = (
                    ["asset", source.asset_id]
                    if source.asset_id is not None
                    else ["url-sha256", self._source_hash(source.url)]
                )
                key = json.dumps(["marvelUnlimited", digital_id, "source", identity])
                filename = Path(temporary) / str(index)
                image = self.image_cache.acquire(
                    key,
                    partial(self._download_image, source.url, filename),
                    refresh=self.refresh,
                )
                page_list.append(
                    Page(
                        numbers=() if index == 0 else source.numbers,
                        path=image.filename,
                        width=image.width,
                        height=image.height,
                        content_type=image.content_type,
                        mode=image.mode,
                    )
                )
                if progress is not None:
                    progress(index + 1, total)

        pages = Pages(cover=page_list[0], pages=tuple(page_list[1:]))
        return _infer_page_numbers(pages) if infer_pagination else pages

    def cleanup(self) -> None:
        # Original objects are persistent; temporary downloads clean themselves.
        pass

    def get_publication(
        self,
        source: str,
        progress: ProgressCallback | None = None,
        metadata_ready: MetadataCallback | None = None,
    ) -> Publication:
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

        acquired = self.get_pages(
            issue_data.digital_id,
            page_sources,
            progress=page_progress,
            infer_pagination=metadata.get("digital_format", "print") == "print",
        )

        return self._build_publication(
            issue_data=issue_data,
            metadata=metadata,
            cover=acquired.cover,
            narrative=acquired.pages,
        )
