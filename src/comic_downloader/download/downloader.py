from pathlib import Path
from urllib.parse import urlparse

import httpx

from comic_downloader.download.cache import Cache
from comic_downloader.exceptions import DownloadError
from comic_downloader.models import Comic, Page


class Downloader:
    def __init__(self, client: httpx.Client, cache: Cache) -> None:
        self.client = client
        self.cache = cache

    def download(self, comic: Comic, *, redownload: bool = False) -> list[Path]:
        pages_dir = self.cache.prepare(comic.service, comic.service_id)

        if self.cache.is_complete(comic.service, comic.service_id) and not redownload:
            return self._cached_pages(pages_dir)

        if redownload:
            for path in pages_dir.iterdir():
                if path.is_file():
                    path.unlink()

        for page in comic.pages:
            self._download_page(page, pages_dir)

        self.cache.mark_complete(comic.service, comic.service_id)
        return self._cached_pages(pages_dir)

    def _download_page(self, page: Page, pages_dir: Path) -> Path:
        ext = self._extension_for(page)
        destination = pages_dir / f"{page.number:05d}{ext}"
        try:
            response = self.client.get(page.url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DownloadError(f"Failed to download page {page.number}: {exc}") from exc
        destination.write_bytes(response.content)
        return destination

    @staticmethod
    def _extension_for(page: Page) -> str:
        if page.filename:
            suffix = Path(page.filename).suffix
            if suffix:
                return suffix.lower()
        suffix = Path(urlparse(page.url).path).suffix
        return suffix.lower() if suffix else ".jpg"

    @staticmethod
    def _cached_pages(pages_dir: Path) -> list[Path]:
        return sorted(p for p in pages_dir.iterdir() if p.is_file())
