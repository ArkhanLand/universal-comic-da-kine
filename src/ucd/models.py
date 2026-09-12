from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Creator:
    name: str
    role: str


@dataclass(frozen=True, slots=True)
class Page:
    number: int | None
    path: Path
    width: int
    height: int
    content_type: str
    mode: str


@dataclass(frozen=True, slots=True)
class Pages:
    cover: Page
    pages: tuple[Page, ...]


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
