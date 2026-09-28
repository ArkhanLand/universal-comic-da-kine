from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class Creator:
    name: str
    role: str


@dataclass(frozen=True, slots=True)
class Page:
    """One image entry; logical numbers are independent of its sequence
    position.
    """

    # () = zero logical pages, None = unknown, (n, ...) = known logical
    # ordinals.
    numbers: tuple[int, ...] | None
    path: Path
    width: int
    height: int
    content_type: str
    mode: str

    def __post_init__(self) -> None:
        if self.numbers is not None:
            if not isinstance(self.numbers, tuple):
                raise ValueError("Logical page numbers must be a tuple or None")
            if any(type(number) is not int or number <= 0 for number in self.numbers):
                raise ValueError("Logical page numbers must be positive integers")
            if len(set(self.numbers)) != len(self.numbers):
                raise ValueError("Logical page numbers must be unique within an image")

    @property
    def is_spread(self) -> bool | None:
        """Whether the image represents multiple logical pages; None if
        unknown.
        """
        return None if self.numbers is None else len(self.numbers) > 1


@dataclass(frozen=True, slots=True)
class Pages:
    cover: Page
    pages: tuple[Page, ...]

    def __post_init__(self) -> None:
        if self.cover.numbers != ():
            raise ValueError("The cover must have no logical page numbers: ()")
        seen: set[int] = set()
        for page in self.pages:
            if page.numbers is not None:
                if seen.intersection(page.numbers):
                    raise ValueError("Logical page numbers must be unique across images")
                seen.update(page.numbers)

    @property
    def logical_page_count(self) -> int | None:
        """Count logical pages present in this edition, excluding the
        cover.

        Omitted print material is not counted. Unknown if any mapping is
        unknown.
        """
        total = 0
        for page in self.pages:
            if page.numbers is None:
                return None
            total += len(page.numbers)
        return total

    @property
    def interior_image_count(self) -> int:
        return len(self.pages)


@dataclass(frozen=True, slots=True)
class DownloadedImageFile:
    filename: Path
    width: int
    height: int
    content_type: str
    mode: str


@dataclass(frozen=True, slots=True)
class PartialDate:
    """A known year and optional month, without an invented day."""

    year: int
    month: int | None = None
    day: None = field(default=None, init=False)

    def __post_init__(self) -> None:
        date(self.year, 1 if self.month is None else self.month, 1)


@dataclass(frozen=True, slots=True)
class ComicMetadata:
    """Optional native documents, usable by any output supporting these
    formats.
    """

    comicinfo: bytes
    comicbookinfo: bytes
    normalized: "Publication"
    comicinfo_name: str = "ComicInfo.xml"
    comicinfo_origin: Literal["source", "generated"] = "generated"
    comicbookinfo_origin: Literal["source", "generated"] = "generated"


@dataclass(frozen=True, slots=True)
class SourceRepresentation:
    """An exact retained source and the normalized state derived from it.

    Outputs can preserve native metadata without consulting an input adapter.
    The snapshot guards against replaying stale metadata after model edits.
    """

    media_type: str
    path: Path
    sha256: str
    normalized: "Publication"
    assets: tuple[tuple[Path, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Publication:
    """Normalized in-memory publication, including pages and metadata."""

    service: str
    service_id: str
    service_series_id: str
    title: str

    # Cover is separate from the ordered narrative reading sequence.
    cover: Page
    narrative: tuple[Page, ...]

    comic_metadata: ComicMetadata | None = None
    source_representation: SourceRepresentation | None = None
    source_url: str | None = None

    # Optional facing-page presentation metadata; never inferred from image
    # shape.
    reading_direction: Literal["ltr", "rtl"] | None = None
    first_page_side: Literal["left", "right"] | None = None

    # Basic bibliographic metadata
    series: str | None = None
    issue_number: str | None = None
    volume: int | None = None
    series_count: int | None = None

    # Publication metadata
    publication_date: date | PartialDate | None = None
    publisher: str | None = None
    imprint: str | None = None
    language: str | None = None
    age_rating: str | None = None
    thumbnail_url: str | None = None

    # Descriptive metadata
    description: str | None = None
    genres: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)

    # Optional comic-specific descriptive metadata
    story_arcs: tuple[str, ...] = field(default_factory=tuple)
    characters: tuple[str, ...] = field(default_factory=tuple)
    teams: tuple[str, ...] = field(default_factory=tuple)
    locations: tuple[str, ...] = field(default_factory=tuple)

    # Credits
    creators: tuple[Creator, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.cover.numbers != ():
            raise ValueError("The cover must have no logical page numbers: ()")
        seen: set[int] = set()
        for page in self.narrative:
            if page.numbers is not None:
                if seen.intersection(page.numbers):
                    raise ValueError("Logical page numbers must be unique across images")
                seen.update(page.numbers)

    @property
    def logical_page_count(self) -> int | None:
        """Count logical pages present in this edition, excluding the
        cover.

        Omitted print material is not counted. Unknown if any mapping is
        unknown.
        """
        total = 0
        for page in self.narrative:
            if page.numbers is None:
                return None
            total += len(page.numbers)
        return total

    @property
    def interior_image_count(self) -> int:
        return len(self.narrative)

    def unchanged_source(self) -> SourceRepresentation | None:
        """Refuse stale source replay until explicit metadata-edit policies
        exist.
        """
        source = self.source_representation
        if source is not None and self.normalized_state() != source.normalized:
            raise ValueError(
                "Publication was edited after import; preserving source metadata requires "
                "an explicit reconciliation policy, which is not implemented yet"
            )
        return source

    def normalized_state(self) -> "Publication":
        return replace(self, comic_metadata=None, source_representation=None)

    def ready_comic_metadata(self) -> ComicMetadata:
        """Require explicit preparation and refuse stale metadata after
        edits.
        """
        self.unchanged_source()
        if self.comic_metadata is None:
            raise ValueError(
                "Publication metadata is not prepared; call prepare_comic_metadata first"
            )
        if self.normalized_state() != self.comic_metadata.normalized:
            raise ValueError("Publication metadata is stale; prepare it again before export")
        return self.comic_metadata
