import httpx
import re
from urllib.parse import urlparse

from comic_downloader.models import Comic
from comic_downloader.services.base import ComicService


class MarvelService(ComicService):
    def __init__(self, client:httpx.Client | None = None) -> None:
        self.client = client or httpx.Client()

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

    def get_catalog_id(self, comic_input: str) -> str:
        match comic_input:
            # case _ if re.search(r"^[0-9]+$", comic_input):
            #     return comic_input
            case _ if m := re.search(r"/comics/issue/([0-9]+)(?:/.*)?$", comic_input):
                return f"{m.group(1)}"
            case _:
                raise ValueError(f"Unable to parse comic_input: {comic_input}")

    def get_digital_id(self, catalog_id: str) -> str:
        raise NotImplementedError
