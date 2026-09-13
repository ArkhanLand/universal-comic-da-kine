from abc import ABC, abstractmethod
from collections.abc import Callable

from ucd.models import CLF

ProgressCallback = Callable[[str, int, int], None]
MetadataCallback = Callable[[str, str | None, str | None], None]


class InputAdapter(ABC):
    name: str

    @abstractmethod
    def matches_url(self, url: str) -> bool:
        """Return True if this service handles the URL."""

    @abstractmethod
    def get_clf(
        self,
        source: str,
        progress: ProgressCallback | None = None,
        metadata_ready: MetadataCallback | None = None,
    ) -> CLF:
        """Return a fully ingested comic-like file."""
