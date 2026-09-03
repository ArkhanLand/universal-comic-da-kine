from pathlib import Path
from comic_downloader.download.cache import Cache

def test_cache_complete_marker(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    assert not cache.is_complete("example", "123")
    cache.prepare("example", "123")
    cache.mark_complete("example", "123")
    assert cache.is_complete("example", "123")
