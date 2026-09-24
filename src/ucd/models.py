from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class Creator:
    name: str
    role: str


@dataclass(frozen=True, slots=True)
class Page:
    """One image entry; logical numbers are independent of its sequence position."""

    # () = zero logical pages, None = unknown, (n, ...) = known logical ordinals.
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
        """Whether the image represents multiple logical pages; None if unknown."""
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
        """Count logical pages present in this edition, excluding the cover.

        Omitted print material is not counted. Unknown if any mapping is unknown.
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


# Comic-Like File. The internal UCD representation of the entire publication, including metadata
@dataclass(frozen=True, slots=True)
class CLF:
    service: str
    service_id: str
    service_series_id: str
    title: str

    # Downloadable content
    pages: Pages

    source_url: str | None = None

    # Optional facing-page presentation metadata; never inferred from image shape.
    reading_direction: Literal["ltr", "rtl"] | None = None
    first_page_side: Literal["left", "right"] | None = None

    # Basic bibliographic metadata
    series: str | None = None
    issue_number: str | None = None
    volume: int | None = None
    series_count: int | None = None

    # Publication metadata
    publication_date: date | None = None
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
