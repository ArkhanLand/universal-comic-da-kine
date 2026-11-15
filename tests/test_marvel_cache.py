import json
from dataclasses import replace
from datetime import datetime
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event
from zipfile import ZipFile

import httpx
import pytest
from PIL import Image
from typer.testing import CliRunner

from tests.helpers import make_test_publication
from ucd.cli import app
from ucd.input.marvel_unlimited import MarvelUnlimitedAdapter
from ucd.metadata import prepare_comic_metadata
from ucd.output.cbz import write_cbz


def image_bytes(color):
    stream = BytesIO()
    Image.new("RGB", (100, 150), color).save(stream, format="PNG")
    return stream.getvalue()


class MarvelServer:
    def __init__(self):
        self.order = ["cover", "one", "two"]
        self.generation = 1
        self.images = {
            "cover": image_bytes("red"),
            "one": image_bytes("green"),
            "two": image_bytes("blue"),
        }
        self.downloads = []
        self.fail = None
        self.omit_ids = False

    def handle(self, request):
        if "/assets/" in request.url.path:
            pages = []
            for asset_id in self.order:
                page = {
                    "assets": {"source": f"https://example.com/{self.generation}/{asset_id}.png"}
                }
                if not self.omit_ids:
                    page["id"] = asset_id
                pages.append(page)
            return httpx.Response(
                200,
                json={"data": {"results": [{"auth_state": {"subscriber": True}, "pages": pages}]}},
            )
        asset_id = request.url.path.rsplit("/", 1)[1].removesuffix(".png")
        self.downloads.append(asset_id)
        if asset_id == self.fail:
            return httpx.Response(500)
        return httpx.Response(
            200, content=self.images[asset_id], headers={"Content-Type": "image/png"}
        )

    def adapter(self, root, *, refresh=False, workers=1):
        return MarvelUnlimitedAdapter(
            client=httpx.Client(transport=httpx.MockTransport(self.handle)),
            cache_dir=root,
            refresh=refresh,
            workers=workers,
        )


def acquire(adapter, digital_id="39895", progress=None):
    return adapter.get_pages(digital_id, adapter.get_page_sources(digital_id), progress=progress)


def record_paths(root):
    return (root / "shards" / "acquisitions").glob("*/*/*.json")


def records(root):
    return [json.loads(p.read_text()) for p in sorted(record_paths(root))]


def test_sharded_layout(tmp_path):
    result = acquire(MarvelServer().adapter(tmp_path))
    shards = tmp_path / "shards"
    objects = [result.cover.path, *(page.path for page in result.pages)]
    for directory, paths in [
        ("objects", objects),
        ("assets", list((shards / "assets").glob("*/*/*"))),
        ("acquisitions", list(record_paths(tmp_path))),
    ]:
        assert len(paths) == 3
        for path in paths:
            assert path == shards / directory / path.stem[:1] / path.stem[1:3] / path.name
        assert not (tmp_path / directory).exists()


@pytest.mark.parametrize(
    "legacy_directories",
    [
        ("assets",),
        ("acquisitions",),
        ("objects",),
        ("assets", "acquisitions", "objects"),
    ],
)
@pytest.mark.parametrize("layout", ["flat", "nested"])
def test_reuse_legacy_cache(tmp_path, monkeypatch, legacy_directories, layout):
    server = MarvelServer()
    acquire(server.adapter(tmp_path))
    legacy = {}
    for directory in legacy_directories:
        root = tmp_path / directory
        sharded_root = tmp_path / "shards" / directory
        for path in sharded_root.glob("*/*/*"):
            name = path.name if layout == "flat" else path.relative_to(sharded_root)
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            path.rename(destination)
            legacy[destination] = destination.read_bytes()
    assert len(legacy) == 3 * len(legacy_directories)

    def fail_on_scan(*args, **kwargs):
        pytest.fail("Cache lookup enumerated a directory")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "glob", fail_on_scan)
        patch.setattr(Path, "iterdir", fail_on_scan)
        result = acquire(server.adapter(tmp_path))
    assert server.downloads == ["cover", "one", "two"]
    assert result.pages[0].path.read_bytes() == server.images["one"]
    assert {path: path.read_bytes() for path in legacy} == legacy


def test_refresh_supersedes_flat_pointer(tmp_path):
    server = MarvelServer()
    server.order = ["cover", "one"]
    acquire(server.adapter(tmp_path))
    assets = tmp_path / "assets"
    assets.mkdir()
    flat_pointers = {}
    for path in (tmp_path / "shards" / "assets").glob("*/*/*"):
        flat_path = path.rename(assets / path.name)
        flat_pointers[flat_path] = flat_path.read_bytes()
    server.images["one"] = image_bytes("purple")
    refreshed = acquire(server.adapter(tmp_path, refresh=True))

    assert refreshed.pages[0].path.read_bytes() == server.images["one"]
    assert acquire(server.adapter(tmp_path)) == refreshed
    assert server.downloads == ["cover", "one", "cover", "one"]
    assert {path: path.read_bytes() for path in flat_pointers} == flat_pointers


def test_parallel_order(tmp_path):
    server = MarvelServer()
    handler = server.handle
    barrier = Barrier(3)
    last_page = Event()

    def parallel(request):
        if request.url.host == "example.com":
            barrier.wait(timeout=5)
            if request.url.path.endswith("cover.png"):
                assert last_page.wait(timeout=5)
            elif request.url.path.endswith("two.png"):
                last_page.set()
        return handler(request)

    server.handle = parallel
    updates = []
    pages = acquire(
        server.adapter(tmp_path, workers=3), progress=lambda *args: updates.append(args)
    )
    assert pages.cover.path.read_bytes() == server.images["cover"]
    assert [page.path.read_bytes() for page in pages.pages] == [
        server.images["one"],
        server.images["two"],
    ]
    assert updates == [(count, 3) for count in range(4)]


def test_parallel_cache(tmp_path):
    server = MarvelServer()
    first = acquire(server.adapter(tmp_path, workers=3))
    before = records(tmp_path)
    server.downloads = []
    second = acquire(server.adapter(tmp_path, workers=3))
    assert first == second
    assert not server.downloads
    assert records(tmp_path) == before
    server.images["one"] = image_bytes("yellow")
    refreshed = acquire(server.adapter(tmp_path, workers=3, refresh=True))
    assert sorted(server.downloads) == ["cover", "one", "two"]
    assert refreshed.pages[0].path.read_bytes() == server.images["one"]
    assert len(records(tmp_path)) == len(before) + 3


def test_parallel_fail(tmp_path):
    server = MarvelServer()
    server.fail = "one"
    with pytest.raises(httpx.HTTPStatusError):
        acquire(server.adapter(tmp_path, workers=3))
    assert len(records(tmp_path)) == 1
    server.fail = None
    server.downloads = []
    acquire(server.adapter(tmp_path, workers=3))
    assert sorted(server.downloads) == ["one", "two"]


def test_reuse_and_export(tmp_path):
    server = MarvelServer()
    root = tmp_path / "cache"
    first = acquire(server.adapter(root))
    before = records(root)
    server.generation += 1
    progress = []
    second = acquire(server.adapter(root), progress=lambda n, total: progress.append((n, total)))
    assert server.downloads == ["cover", "one", "two"]
    assert second == first
    assert progress == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert records(root) == before
    for record in before:
        timestamp = datetime.fromisoformat(record["fetched_at"])
        assert timestamp.utcoffset().total_seconds() == 0
        assert "." in record["fetched_at"]
        assert "https://" not in json.dumps(record)
    publication = make_test_publication(tmp_path)
    destination = tmp_path / "cached.cbz"
    write_cbz(
        prepare_comic_metadata(replace(publication, cover=second.cover, narrative=second.pages)),
        destination,
    )
    with ZipFile(destination) as archive:
        assert archive.read("00001.png") == server.images["one"]
        assert archive.read("00002.png") == server.images["two"]


def test_manifest_changes(tmp_path):
    server = MarvelServer()
    first = acquire(server.adapter(tmp_path))
    server.images["new"] = image_bytes("yellow")
    server.order = ["cover", "two", "new", "one"]
    server.generation += 1
    result = acquire(server.adapter(tmp_path))
    assert server.downloads == ["cover", "one", "two", "new"]
    assert result.pages[0].path == first.pages[1].path
    assert result.pages[2].path == first.pages[0].path
    assert [p.numbers for p in result.pages] == [(1,), (2,), (3,)]
    server.order = ["cover", "new"]
    assert len(acquire(server.adapter(tmp_path)).pages) == 1


def test_refresh_original(tmp_path):
    server = MarvelServer()
    first = acquire(server.adapter(tmp_path))
    old_bytes = first.pages[0].path.read_bytes()
    server.images["one"] = image_bytes("purple")
    assert acquire(server.adapter(tmp_path)) == first
    refreshed = acquire(server.adapter(tmp_path, refresh=True))
    assert refreshed.pages[0].path.read_bytes() == server.images["one"]
    assert first.pages[0].path.read_bytes() == old_bytes
    assert len(records(tmp_path)) == 6
    assert len(server.downloads) == 6


@pytest.mark.parametrize("damage", ["missing", "corrupt", "record"])
def test_recover_cache(tmp_path, damage):
    server = MarvelServer()
    first = acquire(server.adapter(tmp_path))
    target = first.pages[0].path
    if damage == "missing":
        target.unlink()
    elif damage == "corrupt":
        target.write_bytes(b"broken")
    else:
        for path in record_paths(tmp_path):
            if json.loads(path.read_text())["sha256"] == target.stem:
                path.write_text("invalid json")
    recovered = acquire(server.adapter(tmp_path))
    assert server.downloads == ["cover", "one", "two", "one"]
    assert recovered.pages[0].path.read_bytes() == server.images["one"]


def test_failed_refresh(tmp_path):
    server = MarvelServer()
    first = acquire(server.adapter(tmp_path))
    server.fail = "one"
    with pytest.raises(httpx.HTTPStatusError):
        acquire(server.adapter(tmp_path, refresh=True))
    downloads = list(server.downloads)
    assert acquire(server.adapter(tmp_path)) == first
    assert server.downloads == downloads


def test_issue_scoping(tmp_path):
    server = MarvelServer()
    acquire(server.adapter(tmp_path), "1")
    server.images["one"] = image_bytes("purple")
    second = acquire(server.adapter(tmp_path), "2")
    assert len(server.downloads) == 6
    assert second.pages[0].path.read_bytes() == server.images["one"]


def test_missing_ids(tmp_path):
    server = MarvelServer()
    server.omit_ids = True
    acquire(server.adapter(tmp_path))
    acquire(server.adapter(tmp_path))
    assert len(server.downloads) == 3
    server.generation += 1
    acquire(server.adapter(tmp_path))
    assert len(server.downloads) == 6


def test_duplicate_ids(tmp_path):
    server = MarvelServer()
    server.order = ["cover", "one", "one"]
    result = acquire(server.adapter(tmp_path))
    assert len(result.pages) == 2
    assert result.pages[0].path == result.pages[1].path
    assert server.downloads == ["cover", "one", "one"]


def test_duplicate_ids_with_different_images(tmp_path):
    server = MarvelServer()
    acquire(server.adapter(tmp_path))

    def handle(request):
        response = server.handle(request)
        if "/assets/" in request.url.path:
            data = response.json()
            for page in data["data"]["results"][0]["pages"]:
                page["id"] = "one"
            return httpx.Response(200, json=data)
        return response

    adapter = MarvelUnlimitedAdapter(
        client=httpx.Client(transport=httpx.MockTransport(handle)), cache_dir=tmp_path
    )
    result = acquire(adapter)
    assert result.cover.path.read_bytes() == server.images["cover"]
    assert [page.path.read_bytes() for page in result.pages] == [
        server.images["one"],
        server.images["two"],
    ]
    downloads = list(server.downloads)
    acquire(adapter)
    assert server.downloads == downloads
    server.generation += 1
    acquire(adapter)
    assert server.downloads == downloads + ["cover", "one", "two"]


def test_refresh_overwrite(tmp_path, monkeypatch):
    observed = []
    publication = make_test_publication(tmp_path)

    class Adapter:
        def __init__(self, *, cookie_file, refresh, cache_dir, workers):
            observed.append((refresh, workers))

        def get_publication(self, source, progress, metadata_ready):
            metadata_ready(publication.title, publication.series, publication.issue_number)
            return prepare_comic_metadata(publication)

        def cleanup(self):
            pass

    monkeypatch.setattr("ucd.cli.MarvelUnlimitedAdapter", Adapter)
    runner = CliRunner()
    args = ["download", "1", "--output-dir", str(tmp_path / "output")]
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, args + ["--refresh"]).exit_code == 0
    assert runner.invoke(app, args + ["--refresh", "--overwrite"]).exit_code == 0
    assert runner.invoke(app, args + ["--refresh", "--overwrite", "--workers", "16"]).exit_code == 0
    assert observed == [(False, 1), (True, 1), (True, 1), (True, 16)]


def test_resume_import(tmp_path):
    server = MarvelServer()
    server.fail = "two"
    with pytest.raises(httpx.HTTPStatusError):
        acquire(server.adapter(tmp_path))
    assert len(records(tmp_path)) == 2
    server.fail = None
    server.generation += 1
    pages = acquire(server.adapter(tmp_path))
    assert server.downloads == ["cover", "one", "two", "two"]
    assert pages.logical_page_count == 2
    assert len(records(tmp_path)) == 3


def test_invalid_image(tmp_path):
    server = MarvelServer()
    first = acquire(server.adapter(tmp_path))
    server.images["cover"] = b"not a PNG"
    with pytest.raises(OSError):
        acquire(server.adapter(tmp_path, refresh=True))
    assert len(records(tmp_path)) == 3
    assert acquire(server.adapter(tmp_path)) == first
