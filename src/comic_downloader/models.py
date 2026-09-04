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
    filename: str | None = None


@dataclass(frozen=True, slots=True)
class Comic:
    service: str
    service_id: str
    title: str
    series: str | None = None
    issue_number: str | None = None
    publication_date: date | None = None
    description: str | None = None
    publisher: str | None = None
    imprint: str | None = None
    age_rating: str | None = None
    creators: tuple[Creator, ...] = field(default_factory=tuple)
    pages: tuple[Page, ...] = field(default_factory=tuple)
