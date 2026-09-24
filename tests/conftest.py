import pytest


@pytest.fixture(autouse=True)
def isolate_image_cache(tmp_path, monkeypatch):
    """Tests must never read or populate the user's persistent image cache."""
    monkeypatch.setattr("ucd.input.marvel_unlimited.CACHE_PATH", tmp_path / "image-cache")
