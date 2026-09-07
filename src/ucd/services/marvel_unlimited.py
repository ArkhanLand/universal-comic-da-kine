import json
import re
from dataclasses import dataclass
from datetime import date
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ucd.exceptions import InvalidComicInputError, ServiceResponseError
from ucd.models import CLF, Creator
from ucd.services.base import CLFService

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)

BASE_URL = "https://www.marvel.com"
BIFROST_BASE_URL = "https://bifrost.marvel.com"
ISSUE_PATH = "/comics/issue/{catalog_id}"
METADATA_PATH = "/v1/catalog/digital-comics/metadata/{digital_id}"


@dataclass(frozen=True, slots=True)
class _MarvelIssueData:
    catalog_id: str
    digital_id: str
    issue_number: str
    series_id: str


class MarvelService(CLFService):
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

    def _metadata_to_clf(
        self,
        issue_data: _MarvelIssueData,
        meta: dict[str, Any],
    ) -> CLF:
        return CLF(
            service="marvelUnlimited",
            service_id=issue_data.digital_id,
            title=meta["title"],
            source_url=BASE_URL + ISSUE_PATH.format(issue_data.catalog_id),
            series=meta["series_title"],
            issue_number=issue_data.issue_number,
            service_series_id=issue_data.series_id,
            publication_date=date.fromisoformat(meta["release_date"]),
            publisher="Marvel",
            description=meta["description"],
            age_rating=meta["rating"],
            imprint=meta["imprint"],
            thumbnail_url=f"{meta['thumbnail']['path']}.{meta['thumbnail']['extension']}",
            creators=tuple(
                Creator(
                    name=creator["full_name"],
                    role=creator["role"],
                )
                for creator in meta["creators"]["extended_list"]
            ),
        )

    def matches_url(self, url: str) -> bool:
        parsed = urlparse(url)

        # fmt: off
        return (
            parsed.netloc.lower() in {"marvel.com", "www.marvel.com"}
            and parsed.path.startswith("/comics/issue/")
        )
        # fmt: on

    def get_catalog_id(self, source: str) -> str:
        match source:
            case _ if source.isdigit():
                return source
            case _ if m := re.search(r"/comics/issue/([0-9]+)(?:/.*)?$", source):
                return f"{m.group(1)}"
            case _:
                raise InvalidComicInputError(f"Unable to parse source: {source}")

    def get_issue_data(self, catalog_id: str) -> _MarvelIssueData:
        issue_url = BASE_URL + ISSUE_PATH.format(catalog_id=catalog_id)

        response = self.client.get(issue_url)
        response.raise_for_status()

        html = response.text

        marker = "window['__marvel-fitt__']="
        start = html.find(marker)
        if start == -1:
            raise ServiceResponseError("Unable to find Marvel issue data in response")
        start += len(marker)

        decoder = json.JSONDecoder()
        page_data, _ = decoder.raw_decode(response.text[start:])

        try:
            issue_data = page_data["page"]["content"]["issueDetails"]
        except KeyError as exc:
            raise ServiceResponseError("Marvel response did not contain issueDetails") from exc

        if issue_data["id"] != catalog_id:
            raise ServiceResponseError("Requested catalog ID does not match Marvel response")

        return _MarvelIssueData(
            catalog_id=str(issue_data["id"]),
            digital_id=str(issue_data["digitalComicID"]),
            issue_number=str(issue_data["issue"]),
            series_id=str(issue_data["seriesId"]),
        )

    def get_metadata(self, digital_id: str) -> dict[str, Any]:
        if not digital_id.isdigit():
            raise InvalidComicInputError("Invalid Digital ID")
        url = BIFROST_BASE_URL + METADATA_PATH.format(digital_id=digital_id)

        response = self.client.get(url)
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        # Marvel-specific data. This needs to be turned into a CLF():
        meta: dict[str, Any] = data["data"]["results"][0]["issue_meta"]

        return meta

    def get_clf(self, source: str) -> CLF:
        if not (self.matches_url(source) or source.isdigit()):
            raise InvalidComicInputError(f"Invalid Marvel comic input: {source}")

        catalog_id = self.get_catalog_id(source)
        issue_data = self.get_issue_data(catalog_id)
        digital_id = issue_data.digital_id
        metadata = self.get_metadata(digital_id)

        return self._metadata_to_clf(issue_data, metadata)
