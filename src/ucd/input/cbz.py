"""Local static-image CBZ input with deterministic metadata precedence."""

import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import date
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile, ZipInfo

from PIL import Image

from ucd.download.images import _atomic_write
from ucd.exceptions import InvalidComicInputError
from ucd.input.base import InputAdapter, MetadataCallback, ProgressCallback
from ucd.metadata import prepare_comic_metadata
from ucd.models import Creator, Page, PartialDate, Publication, SourceRepresentation

logger = logging.getLogger(__name__)
CACHE_PATH = Path.home() / ".local" / "share" / "ucd" / "cbz"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
IMAGE_FORMATS = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
    "GIF": (".gif", "image/gif"),
    "TIFF": (".tiff", "image/tiff"),
    "BMP": (".bmp", "image/bmp"),
}
TEXT_FIELDS = {
    "Title": "title",
    "Series": "series",
    "Number": "issue_number",
    "Summary": "description",
    "Publisher": "publisher",
    "Imprint": "imprint",
    "LanguageISO": "language",
    "AgeRating": "age_rating",
    "Web": "source_url",
}
LIST_FIELDS = {
    "Genre": "genres",
    "Tags": "tags",
    "StoryArc": "story_arcs",
    "Characters": "characters",
    "Teams": "teams",
    "Locations": "locations",
}
CREATOR_FIELDS = {
    "Writer": "writer",
    "Penciller": "penciller",
    "Inker": "inker",
    "Colorist": "colorist",
    "Letterer": "letterer",
    "CoverArtist": "cover artist",
    "Editor": "editor",
    "Translator": "translator",
}


@dataclass
class _Metadata:
    fields: dict[str, Any] = field(default_factory=dict)
    pages: dict[int, dict[str, str]] = field(default_factory=dict)


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _integer(value: object) -> int | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()) is not None:
        return int(value)
    if type(value) is int and value >= 0:
        return value
    return None


def _list(value: object) -> tuple[str, ...]:
    return (
        tuple(part.strip() for part in value.split(",") if part.strip())
        if isinstance(value, str)
        else ()
    )


def _date(year: object, month: object, day: object) -> date | PartialDate | None:
    parts = [_integer(value) for value in (year, month, day)]
    if all(value is None for value in parts):
        return None
    y, m, d = parts
    if y is not None and m is not None and d is not None:
        try:
            return date(y, m, d)
        except ValueError:
            pass
    if y is not None and d is None:
        try:
            return PartialDate(y, m)
        except ValueError:
            pass
    logger.warning("Invalid publication date preserved in source metadata; not normalized")
    return None


def _comicinfo(data: bytes, image_count: int) -> _Metadata | None:
    try:
        # Reject DTDs rather than permitting entity expansion, including
        # UTF-16 XML.
        if b"<!DOCTYPE" in data.replace(b"\x00", b"").upper():
            raise ValueError("DTDs are unsupported")
        root = ET.fromstring(data)
        if root.tag != "ComicInfo":
            raise ValueError("expected ComicInfo root")
    except (ET.ParseError, ValueError) as exc:
        logger.warning("Invalid ComicInfo.xml; trying ZIP-comment metadata: %s", exc)
        return None
    result = _Metadata()
    for tag, target in TEXT_FIELDS.items():
        if value := _text(root.findtext(tag)):
            result.fields[target] = value
    for tag, target in LIST_FIELDS.items():
        result.fields[target] = _list(root.findtext(tag))
    for tag, target in (("Volume", "volume"), ("Count", "series_count")):
        result.fields[target] = _integer(root.findtext(tag))
    result.fields["publication_date"] = _date(
        root.findtext("Year"), root.findtext("Month"), root.findtext("Day")
    )
    result.fields["creators"] = tuple(
        Creator(name, role)
        for tag, role in CREATOR_FIELDS.items()
        for name in _list(root.findtext(tag))
    )
    if root.findtext("Manga") == "YesAndRightToLeft":
        result.fields["reading_direction"] = "rtl"
    for entry in root.findall("Pages/Page"):
        index = _integer(entry.get("Image"))
        if index is None or index >= image_count or index in result.pages:
            logger.warning(
                "Ignoring invalid or duplicate ComicInfo page index: %s", entry.get("Image")
            )
            continue
        result.pages[index] = dict(entry.attrib)
    supported = (
        set(TEXT_FIELDS)
        | set(LIST_FIELDS)
        | set(CREATOR_FIELDS)
        | {"Volume", "Count", "Year", "Month", "Day", "Manga", "Pages", "PageCount"}
    )
    if any(child.tag not in supported for child in root):
        logger.warning("Unsupported ComicInfo fields preserved in source metadata; not normalized")
    if any(
        set(hints) - {"Image", "Type", "DoublePage"}
        or hints.get("Type", "Story") not in {"Story", "FrontCover"}
        for hints in result.pages.values()
    ):
        logger.warning(
            "Additional page roles/properties preserved in source metadata; not normalized"
        )
    return result


def _comicbookinfo(data: bytes) -> _Metadata:
    result = _Metadata()
    if not data:
        return result
    try:
        payload = json.loads(data)
        info = payload.get("ComicBookInfo/1.0") if isinstance(payload, dict) else None
        if not isinstance(info, dict):
            raise ValueError("expected ComicBookInfo/1.0 object")
    except (ValueError, UnicodeError) as exc:
        logger.warning("Invalid ZIP-comment metadata; using filename and images: %s", exc)
        return result
    for source, target in {
        "title": "title",
        "series": "series",
        "issue": "issue_number",
        "publisher": "publisher",
        "comments": "description",
        "language": "language",
    }.items():
        if value := _text(info.get(source)):
            result.fields[target] = value
    if "language" not in result.fields and (language := _text(info.get("lang"))):
        result.fields["language"] = language
    if type(info.get("issue")) in (int, float):
        result.fields["issue_number"] = str(info["issue"])
    result.fields["series_count"] = _integer(info.get("numberOfIssues"))
    result.fields["genres"] = _list(info.get("genre"))
    tags = info.get("tags")
    result.fields["tags"] = (
        tuple(t for item in tags if (t := _text(item))) if isinstance(tags, list) else _list(tags)
    )
    # Preserve date precision: ComicBookInfo supplies year/month, not a day.
    result.fields["publication_date"] = _date(
        info.get("publicationYear"), info.get("publicationMonth"), None
    )
    credits = info.get("credits", [])
    result.fields["creators"] = (
        tuple(
            Creator(person, role)
            for credit in credits
            if isinstance(credit, dict)
            and (person := _text(credit.get("person")))
            and (role := _text(credit.get("role")))
        )
        if isinstance(credits, list)
        else ()
    )
    return result


def _order(entry: ZipInfo) -> tuple[tuple[tuple[int, int | str], ...], str]:
    # Numeric runs sort naturally: page2 precedes page10, independently of ZIP
    # order.
    return tuple(
        (1, int(part)) if re.fullmatch(r"[0-9]+", part) else (0, part.casefold())
        for part in re.split(r"([0-9]+)", entry.filename)
    ), entry.filename


class CBZInputAdapter(InputAdapter):
    name = "cbz"

    def __init__(self, cache_dir: Path | None = None, *, max_bytes: int = 2 * 1024**3) -> None:
        self.cache_dir = cache_dir if cache_dir is not None else CACHE_PATH
        self.max_bytes = max_bytes

    def matches_url(self, url: str) -> bool:
        """Legacy adapter hook: this input recognizes local .cbz paths, not
        URLs.
        """
        return "://" not in url and Path(url).suffix.casefold() == ".cbz"

    def get_publication(
        self,
        source: str,
        progress: ProgressCallback | None = None,
        metadata_ready: MetadataCallback | None = None,
    ) -> Publication:
        if not self.matches_url(source):
            raise InvalidComicInputError("CBZ input requires a local .cbz file")
        try:
            return self._read(Path(source), progress, metadata_ready)
        except FileExistsError:
            raise
        except (
            OSError,
            BadZipFile,
            RuntimeError,
            NotImplementedError,
            ValueError,
            Image.DecompressionBombError,
        ) as exc:
            raise InvalidComicInputError(f"Cannot read CBZ {source}: {exc}") from exc

    def _read(
        self,
        source: Path,
        progress: ProgressCallback | None,
        metadata_ready: MetadataCallback | None,
    ) -> Publication:
        if source.stat().st_size > self.max_bytes:
            raise InvalidComicInputError("CBZ exceeds the configured byte limit")
        original = source.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        with ZipFile(BytesIO(original)) as archive:
            entries = archive.infolist()
            if sum(entry.file_size for entry in entries) > self.max_bytes:
                raise InvalidComicInputError("Expanded CBZ exceeds the configured byte limit")
            images = []
            metadata = []
            names: set[str] = set()
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                    raise InvalidComicInputError(f"Unsafe archive path: {entry.filename}")
                if (
                    entry.is_dir()
                    or "__MACOSX" in path.parts
                    or path.name == ".DS_Store"
                    or path.name.startswith("._")
                ):
                    continue
                if entry.filename.casefold() in names:
                    raise InvalidComicInputError(f"Duplicate archive path: {entry.filename}")
                names.add(entry.filename.casefold())
                if entry.flag_bits & 1:
                    raise InvalidComicInputError("Encrypted CBZ entries are unsupported")
                if path.name.casefold() == "comicinfo.xml":
                    metadata.append(entry)
                elif path.suffix.casefold() in IMAGE_SUFFIXES:
                    images.append(entry)
                else:
                    raise InvalidComicInputError(f"Unsupported CBZ entry: {entry.filename}")
            if not images or len(metadata) > 1:
                raise InvalidComicInputError("CBZ needs images and at most one ComicInfo.xml")
            images.sort(key=_order)
            raw_xml = archive.read(metadata[0]) if metadata else None
            xml = _comicinfo(raw_xml, len(images)) if raw_xml is not None else None
            meta = xml if xml is not None else _comicbookinfo(archive.comment)
            title = meta.fields.setdefault("title", source.stem)
            if metadata_ready:
                metadata_ready(title, meta.fields.get("series"), meta.fields.get("issue_number"))
            covers = [
                index for index, hints in meta.pages.items() if hints.get("Type") == "FrontCover"
            ]
            if len(covers) > 1:
                raise InvalidComicInputError("Multiple front covers cannot yet be represented")
            cover_index = covers[0] if covers else 0
            pages = []
            next_number: int | None = 1
            if progress:
                progress(title, 0, len(images))
            # Retain the exact container, including unsupported/unknown
            # metadata.
            _atomic_write(self.cache_dir / "objects" / f"{digest}.cbz", original)
            for index, entry in enumerate(images):
                data = archive.read(entry)
                with Image.open(BytesIO(data)) as image:
                    if getattr(image, "n_frames", 1) != 1 or image.format not in IMAGE_FORMATS:
                        raise InvalidComicInputError(
                            f"Not a supported static image: {entry.filename}"
                        )
                    extension, content_type = IMAGE_FORMATS[image.format]
                    width, height = image.size
                    mode = image.mode
                    image.verify()
                object_id = hashlib.sha256(data).hexdigest()
                object_path = self.cache_dir / "objects" / f"{object_id}{extension}"
                _atomic_write(object_path, data)
                numbers: tuple[int, ...] | None = None
                if index == cover_index:
                    numbers = ()
                else:
                    hint = meta.pages.get(index, {}).get("DoublePage", "").casefold()
                    if hint not in {"true", "false", "1", "0"}:
                        next_number = None
                    elif next_number is not None:
                        span = 2 if hint in {"true", "1"} else 1
                        numbers = tuple(range(next_number, next_number + span))
                        next_number += span
                pages.append(Page(numbers, object_path, width, height, content_type, mode))
                if progress:
                    progress(title, index + 1, len(images))
            record = {
                "source_sha256": digest,
                "source_path": str(source.resolve()),
                "metadata_source": "ComicInfo.xml"
                if xml is not None
                else "ZIP comment or defaults",
                "images": [
                    {"name": entry.filename, "sha256": page.path.stem}
                    for entry, page in zip(images, pages, strict=True)
                ],
            }
            _atomic_write(
                self.cache_dir / "sources" / f"{digest}.json",
                (json.dumps(record, indent=2) + "\n").encode(),
            )
            publication = Publication(
                service=self.name,
                service_id=digest,
                service_series_id="",
                cover=pages[cover_index],
                narrative=tuple(p for i, p in enumerate(pages) if i != cover_index),
                **meta.fields,
            )
            publication = replace(
                publication,
                source_representation=SourceRepresentation(
                    media_type="application/vnd.comicbook+zip",
                    path=self.cache_dir / "objects" / f"{digest}.cbz",
                    sha256=digest,
                    normalized=publication,
                    assets=tuple((page.path, page.path.stem) for page in pages),
                ),
            )
            return prepare_comic_metadata(
                publication,
                comicinfo=raw_xml,
                comicbookinfo=archive.comment or None,
                comicinfo_name=metadata[0].filename if metadata else "ComicInfo.xml",
            )
