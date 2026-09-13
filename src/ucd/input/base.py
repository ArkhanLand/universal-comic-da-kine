from abc import ABC, abstractmethod
from collections.abc import Callable

from ucd.models import CLF

ProgressCallback = Callable[[str, int, int], None]


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
    ) -> CLF:
        """Return a fully ingested comic-like file."""
