from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class Creator:
    name: str
    role: str


@dataclass(frozen=True, slots=True)
class Page:
    number: int
    url: str


@dataclass(frozen=True, slots=True)
class Comic:
    service: str
    service_id: str
    service_series_id: str
    title: str

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

    # Comic-specific descriptive metadata
    story_arcs: tuple[str, ...] = field(default_factory=tuple)
    characters: tuple[str, ...] = field(default_factory=tuple)
    teams: tuple[str, ...] = field(default_factory=tuple)
    locations: tuple[str, ...] = field(default_factory=tuple)

    # Credits
    creators: tuple[Creator, ...] = field(default_factory=tuple)

    # Downloadable content
    pages: tuple[Page, ...] = field(default_factory=tuple)
