import re
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from comic_downloader.models import Comic
from comic_downloader.services.base import ComicService

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)


class MarvelService(ComicService):
    def __init__(
        self,
        client: httpx.Client | None = None,
        cookie_file: Path | None = None,
    ) -> None:
        if client is not None:
            self.client = client
            return

        if cookie_file is not None:
            cookies = MozillaCookieJar(cookie_file)
            cookies.load(ignore_discard=True, ignore_expires=True)
        else:
            cookies = MozillaCookieJar()

        self.client = httpx.Client(
            cookies=cookies,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

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
        url = f"https://www.marvel.com/comics/issue/{catalog_id}"

        response = self.client.get(url)
        response.raise_for_status()

        match = re.search(
            r'"digitalComicID"\s*:\s*"([0-9]+)"',
            response.text,
        )

        if not match:
            raise ValueError("Unable to find digitalComicID")

        return match.group(1)

    def get_metadata(self, digital_id: str) -> dict[str, Any] | type[ValueError]:
        if not re.search(r"^[0-9]+$", digital_id):
            raise ValueError("Invalid Digital ID")
        url = f"https://bifrost.marvel.com/v1/catalog/digital-comics/metadata/{digital_id}"

        response = self.client.get(url)
        response.raise_for_status()

        data: dict[str, Any] = response.json()
        # Marvel-specific data. This needs to be turned into a Comic():
        meta: dict[str, Any] = data["data"]["results"][0]["issue_meta"]

        return meta
