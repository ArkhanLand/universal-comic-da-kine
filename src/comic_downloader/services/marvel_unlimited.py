from urllib.parse import urlparse

from comic_downloader.models import Comic
from comic_downloader.services.base import ComicService


class MarvelService(ComicService):
    def matches_url(self, url: str) -> bool:
        parsed = urlparse(url)

        # fmt: off
        return (
            parsed.netloc.lower() in {"marvel.com", "www.marvel.com"}
            and parsed.path.startswith("/comics/issue/")
        )
        # fmt: on

    def get_comic(self, url: str) -> Comic:
        raise NotImplementedError
