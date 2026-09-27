from abc import ABC, abstractmethod
from collections.abc import Callable

from ucd.models import Publication

ProgressCallback = Callable[[str, int, int], None]
MetadataCallback = Callable[[str, str | None, str | None], None]


class InputAdapter(ABC):
    name: str

    @abstractmethod
    def matches_url(self, url: str) -> bool:
        """Return True if this service handles the URL."""

    @abstractmethod
    def get_publication(
        self,
        source: str,
        progress: ProgressCallback | None = None,
        metadata_ready: MetadataCallback | None = None,
    ) -> Publication:
        """Return a fully ingested publication."""
