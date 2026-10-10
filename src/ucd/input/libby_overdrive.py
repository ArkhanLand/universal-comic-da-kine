"""Turn an existing fixed-layout Libby checkout into a Publication.

get_publication() shows the complete import flow. The Libby API verifies the
card and checkout and supplies catalog metadata. The separate reader client
opens the authorized rendition; libby_assets maps its spine to image URLs.

Each successful image fetch keeps its original bytes and receipt. Only after
all images arrive do we infer logical page spans and mark the capture complete.
The command layer prepares output metadata and writes the CBZ afterward.

Catalog bibliography and reader structure have separate authority. Retained
source projections exclude runtime authorization fields; raw reader objects
and signed resource URLs remain ephemeral. See docs/libby-overdrive-read.md
for the observed protocol and the decisions behind this restricted adapter.
"""

import hashlib
import json
import re
from collections.abc import Callable
from datetime import date
from functools import partial
from html import unescape
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from ucd.auth.libby import (
    ConnectionStore,
    Credentials,
    LibbyClient,
    Session,
    config_path,
    system_secret_store,
)
from ucd.download.cache import cache_root
from ucd.download.images import ImageCache, _atomic_write
from ucd.download.pagination import _infer_page_numbers
from ucd.exceptions import InvalidComicInputError, ServiceResponseError
from ucd.input.base import InputAdapter, MetadataCallback, ProgressCallback
from ucd.input.libby_assets import Asset, download_image, map_assets
from ucd.input.libby_overdrive_read import OverDriveReadClient, ReadRendition
from ucd.models import Creator, DownloadedImageFile, Page, Pages, PartialDate, Publication


def _text(value: Any) -> str | None:
    """Make source prose usable as plain text without retaining signed URLs."""
    if not isinstance(value, str) or not value.strip():
        return None

    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = unescape(re.sub(r"<[^>]*>", "", value)).strip()

    # Source metadata must not retain signed URLs, even in descriptive prose.
    value = re.sub(r"https?://[^\s<>]+", "[source link omitted]", value)
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)


def _date(value: Any) -> date | PartialDate | None:
    """Preserve the source's known date precision instead of inventing a day."""
    if not isinstance(value, str):
        return None

    try:
        if re.fullmatch(r"\d{4}", value):
            return PartialDate(int(value))
        if re.fullmatch(r"\d{4}-\d{2}", value):
            return PartialDate(int(value[:4]), int(value[5:7]))
        if re.match(r"\d{4}-\d{2}-\d{2}(?:$|T)", value):
            return date.fromisoformat(value[:10])
    except ValueError:
        pass

    return None


def _named(value: Any) -> str | None:
    """Catalog names may be plain strings or records containing a name."""
    if isinstance(value, dict):
        return _text(value.get("name"))

    return _text(value)


def bibliographic(title_id: str, catalog: dict[str, Any], book: dict[str, Any]) -> dict[str, Any]:
    """Prefer catalog bibliography; use reader values as documented fallbacks."""
    # Identity and title.
    title_record = book.get("title", {})
    title = _text(catalog.get("title")) or (
        _text(title_record.get("main")) if isinstance(title_record, dict) else _text(title_record)
    )
    if not title:
        raise ServiceResponseError("Publication metadata lacks a title.")

    # Series and description; series position is not an issue number.
    detailed = catalog.get("detailedSeries", {})
    detailed = detailed if isinstance(detailed, dict) else {}
    description = book.get("description", {})
    fallback = description.get("full") if isinstance(description, dict) else description

    # Credits use the catalog when available, otherwise reader creators.
    creators = catalog.get("creators") or book.get("creator") or []
    credits = []
    roles = {"author": "writer", "aut": "writer", "illustrator": "artist", "ill": "artist"}
    creator_records = creators if isinstance(creators, list) else []

    for creator in creator_records:
        if isinstance(creator, dict) and (name := _text(creator.get("name"))):
            role = (_text(creator.get("role")) or "unknown").lower()
            credits.append(Creator(name, roles.get(role, role)))

    # Language and age rating belong to catalog metadata.
    languages = catalog.get("languages") or book.get("language") or []
    first = languages[0] if isinstance(languages, list) and languages else languages
    language = _text(first.get("id")) if isinstance(first, dict) else _text(first)

    ratings = catalog.get("ratings", {})
    ratings = ratings if isinstance(ratings, dict) else {}

    # Reading direction belongs to the rendition, not the catalog.
    direction = book.get("i18n-page-progression-direction")

    return dict(
        service="libby-overdrive",
        service_id=title_id,
        service_series_id=str(detailed.get("seriesId") or ""),
        source_url=f"https://share.libbyapp.com/title/{title_id}",
        title=title,
        series=_text(catalog.get("series")) or _text(detailed.get("seriesName")),
        # Series readingOrder does not prove issue/volume numbering.
        publication_date=_date(catalog.get("publishDate")),
        publisher=_named(catalog.get("publisher")),
        imprint=_named(catalog.get("imprint")),
        language=language,
        description=_text(catalog.get("description")) or _text(fallback),
        age_rating=_named(ratings.get("maturityLevel")),
        reading_direction=direction if direction in ("ltr", "rtl") else None,
        creators=tuple(credits),
    )


def safe_metadata(catalog: dict[str, Any], book: dict[str, Any]) -> dict[str, Any]:
    """A deliberately small source projection, never a runtime/token dump."""

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return _text(value)

        if isinstance(value, (bool, int, float)) or value is None:
            return value

        if isinstance(value, list):
            return [clean(item) for item in value]

        if isinstance(value, dict):
            allowed = {
                "main",
                "subtitle",
                "collection",
                "name",
                "role",
                "id",
                "seriesId",
                "seriesName",
                "readingOrder",
                "full",
                "short",
                "biography",
                "type",
                "value",
                "maturityLevel",
                "front",
                "width",
                "height",
                "bytes",
                "media-type",
                "lastModified",
                "isbn",
                "identifiers",
                "fulfillmentType",
                "onSaleDateUtc",
                "fileSize",
                "accessibilityStatements",
                "title",
                "landmarks",
                "toc",
                "linear",
                "rendition-layout",
                "rendition-orientation",
                "rendition-spread",
                "rendition-viewport",
                "rendition-position",
                "-odread-spine-position",
                "-odread-file-bytes",
                "-odread-cover-color",
                "-odread-cover-ratio",
            }

            result = {key: clean(item) for key, item in value.items() if key in allowed}

            # Only safe relative component paths are retained.
            for key in ("path", "-odread-original-path"):
                path = value.get(key)
                if isinstance(path, str) and re.fullmatch(
                    r"[A-Za-z0-9_./-]+(?:#[A-Za-z0-9_-]+)?", path
                ):
                    result[key] = path

            return result

        return None

    return {
        "catalog": {
            key: clean(catalog[key])
            for key in (
                "id",
                "title",
                "sortTitle",
                "series",
                "detailedSeries",
                "creators",
                "languages",
                "publishDate",
                "publishDateText",
                "publisher",
                "imprint",
                "description",
                "subjects",
                "ratings",
                "formats",
            )
            if key in catalog
        },
        "reader": {
            key: clean(book[key])
            for key in (
                "title",
                "creator",
                "description",
                "language",
                "rendition-format",
                "i18n-page-progression-direction",
                "firstPlace",
                "spine",
                "nav",
                "cover",
            )
            if key in book
        },
    }


class LibbyOverDriveReadAdapter(InputAdapter):
    name = "libby-overdrive"

    def __init__(
        self,
        *,
        store: ConnectionStore | None = None,
        api_client: httpx.Client | None = None,
        read_client: httpx.Client | None = None,
        library_card: str | None = None,
        cache_dir: Path | None = None,
        refresh: bool = False,
        status: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store or ConnectionStore(config_path(), system_secret_store())

        # API credentials and reader cookies stay in separate clients.
        self.api = api_client or httpx.Client(timeout=30, follow_redirects=False)
        self.read = read_client or httpx.Client(timeout=30, follow_redirects=False)
        self._owned_api = api_client is None
        self._owned_read = read_client is None

        self.library_card = library_card
        self.root = (cache_dir if cache_dir is not None else cache_root()) / self.name
        self.refresh = refresh
        self.status = status or (lambda message: None)

    def matches_url(self, url: str) -> bool:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.netloc == "share.libbyapp.com"
            and bool(re.fullmatch(r"/title/\d+", parts.path))
            and not parts.query
            and not parts.fragment
        )

    def get_publication(
        self,
        source: str,
        progress: ProgressCallback | None = None,
        metadata_ready: MetadataCallback | None = None,
    ) -> Publication:
        """Open the checkout, acquire its images, then build a Publication."""
        title_id = self._title_id(source)
        reader = OverDriveReadClient(self.read)

        # 1. Verify the active checkout and open its authorized reader.
        catalog, rendition = self._open_rendition(title_id, reader)
        metadata = bibliographic(title_id, catalog, rendition.openbook)

        # Let the caller skip an existing output before acquiring images.
        if metadata_ready:
            metadata_ready(metadata["title"], None, None)

        # 2. Resolve the complete spine before fetching any image bytes.
        self.status("Mapping spine components to original images…")
        assets = map_assets(reader, rendition)

        # 3. Keep exact originals and a receipt for each successful fetch.
        pages, capture, records = self._acquire_images(
            title_id, metadata["title"], reader, rendition, assets, progress
        )

        # 4. Apply the shared pagination policy and explicit reader direction.
        inferred = _infer_page_numbers(pages)
        first = next(part for part in rendition.openbook["spine"] if part["linear"])
        side = first.get("rendition-position")
        first_side: Literal["left", "right"] | None = side if side in {"left", "right"} else None

        publication = Publication(
            **metadata,
            cover=inferred.cover,
            narrative=inferred.pages,
            first_page_side=first_side,
        )

        # Only a fully constructed publication makes this capture complete.
        self._write_capture(
            title_id,
            metadata["title"],
            capture,
            records,
            complete=True,
            sources=safe_metadata(catalog, rendition.openbook),
        )

        return publication

    def _title_id(self, source: str) -> str:
        """Accept a title ID or the canonical public title URL."""
        if re.fullmatch(r"[0-9]+", source):
            return source

        if self.matches_url(source):
            return urlsplit(source).path.rsplit("/", 1)[1]

        raise InvalidComicInputError("Use an OverDrive title ID or a Libby title share URL.")

    def _open_rendition(
        self, title_id: str, reader: OverDriveReadClient
    ) -> tuple[dict[str, Any], ReadRendition]:
        """Verify the saved card, fetch catalog data, and open the reader."""
        self.status("Checking saved library connections and active loans…")
        client = LibbyClient(self.api, progress=self.status)
        connection, session, _ = self.store.select_loan(client, title_id, name=self.library_card)

        self.status("Fetching current catalog metadata…")
        catalog = client.catalog_media(connection.library, session, title_id)

        # The loan opener can renew an existing identity once. Keep the
        # replacement in the credential store, separate from image records.
        def save_renewed(updated: Session) -> None:
            credentials = self.store.credentials(connection)
            self.store.save(
                connection.name,
                connection.library,
                Credentials(credentials.card_number, credentials.pin, updated),
            )

        self.status("Opening OverDrive Read loan…")
        passport = client.open_loan(
            connection.library, session, title_id, session_renewed=save_renewed
        )

        self.status("Establishing reader connection and decoding openbook…")
        rendition = reader.fetch_openbook(passport)

        return catalog, rendition

    def _acquire_images(
        self,
        title_id: str,
        title: str,
        reader: OverDriveReadClient,
        rendition: ReadRendition,
        assets: list[Asset],
        progress: ProgressCallback | None,
    ) -> tuple[Pages, str, list[dict[str, Any]]]:
        """Acquire images in spine order; keep partial receipts on failure."""
        title_dir = self.root / title_id
        image_cache = ImageCache(title_dir)
        origin = f"https://{urlsplit(rendition.web_url).hostname}"
        capture = uuid4().hex

        records: list[dict[str, Any]] = []
        narrative: list[Page] = []
        cover: Page | None = None

        total = len(assets)
        if progress:
            progress(title, 0, total)

        with TemporaryDirectory() as scratch:
            for number, asset in enumerate(assets):
                # No network-saving reuse until rendition/path stability is
                # verified across sessions. SHA-256 still deduplicates
                # objects.
                key = json.dumps([self.name, title_id, capture, asset.path])
                filename = Path(scratch) / str(number)
                image = image_cache.acquire(
                    key,
                    partial(download_image, reader, asset, origin, filename),
                    refresh=self.refresh,
                )

                # Cover and narrative roles come from the reader, not
                # position.
                page = Page(
                    numbers=() if asset.role == "cover" else None,
                    path=image.filename,
                    width=image.width,
                    height=image.height,
                    content_type=image.content_type,
                    mode=image.mode,
                )

                if asset.role == "cover":
                    cover = page
                elif asset.role == "narrative":
                    narrative.append(page)

                records.append(self._asset_record(title_dir, key, asset, image))

                # Persist each verified association. A later failure leaves
                # valid originals and their per-fetch receipts intact.
                self._write_capture(title_id, title, capture, records, complete=False)

                if progress:
                    progress(title, number + 1, total)

        if cover is None:
            raise ServiceResponseError("Acquisition did not produce the required full cover.")

        return Pages(cover, tuple(narrative)), capture, records

    @staticmethod
    def _asset_record(
        title_dir: Path, key: str, asset: Asset, image: DownloadedImageFile
    ) -> dict[str, Any]:
        """Associate one verified object with its source component and
        receipt.
        """
        pointer = title_dir / "assets" / hashlib.sha256(key.encode()).hexdigest()
        receipt = pointer.read_text().strip()

        return dict(
            component=asset.component,
            spine_index=asset.index,
            role=asset.role,
            source_path=asset.path,
            object=image.filename.relative_to(title_dir).as_posix(),
            sha256=hashlib.sha256(image.filename.read_bytes()).hexdigest(),
            acquisition=f"acquisitions/{receipt}.json",
            byte_count=image.filename.stat().st_size,
            width=image.width,
            height=image.height,
            content_type=image.content_type,
            mode=image.mode,
        )

    def _write_capture(
        self,
        title_id: str,
        title: str,
        capture: str,
        records: list[dict[str, Any]],
        *,
        complete: bool,
        sources: dict[str, Any] | None = None,
    ) -> None:
        """Atomically replace the manifest as verified images accumulate."""
        manifest = dict(
            schema_version=1,
            complete=complete,
            title_id=title_id,
            title=title,
            assets=records,
        )

        if sources is not None:
            manifest["sources"] = sources

        destination = self.root / title_id / "captures" / f"{capture}.json"
        data = json.dumps(manifest, indent=2).encode()
        _atomic_write(destination, data)

    def cleanup(self) -> None:
        if self._owned_api:
            self.api.close()

        if self._owned_read:
            self.read.close()
