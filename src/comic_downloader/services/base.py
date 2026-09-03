from abc import ABC, abstractmethod
from comic_downloader.models import Comic

class ComicService(ABC):
    name: str

    @abstractmethod
    def matches_url(self, url: str) -> bool:
        """Return True if this service handles the URL."""

    @abstractmethod
    def get_comic(self, url: str) -> Comic:
        """Return normalized metadata and page URLs."""
