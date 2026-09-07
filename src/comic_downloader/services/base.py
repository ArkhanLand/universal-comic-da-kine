from abc import ABC, abstractmethod

from comic_downloader.models import CLF


class CLFService(ABC):
    name: str

    @abstractmethod
    def matches_url(self, url: str) -> bool:
        """Return True if this service handles the URL."""

    @abstractmethod
    def get_clf(self, source: str) -> CLF:
        """Return normalized metadata and page URLs."""
