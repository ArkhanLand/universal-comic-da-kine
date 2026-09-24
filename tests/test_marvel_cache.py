import json
from dataclasses import replace
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest
from PIL import Image
from typer.testing import CliRunner

from tests.helpers import make_test_clf
from ucd.cli import app
from ucd.exceptions import ServiceResponseError
from ucd.input.marvel_unlimited import MarvelUnlimitedAdapter
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

    def adapter(self, root, *, refresh=False):
        return MarvelUnlimitedAdapter(
            client=httpx.Client(transport=httpx.MockTransport(self.handle)),
            cache_dir=root,
            refresh=refresh,
        )


def acquire(adapter, digital_id="39895", progress=None):
    return adapter.get_pages(digital_id, adapter.get_page_sources(digital_id), progress=progress)


def records(root):
    return [json.loads(p.read_text()) for p in (root / "acquisitions").glob("*.json")]


def test_rotating_urls_reuse_across_instances_and_export(tmp_path):
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
    clf = make_test_clf(tmp_path)
    destination = tmp_path / "cached.cbz"
    write_cbz(replace(clf, pages=second), destination)
    with ZipFile(destination) as archive:
        assert archive.read("00001.png") == server.images["one"]
        assert archive.read("00002.png") == server.images["two"]


def test_reorder_insert_and_changed_identity(tmp_path):
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


def test_same_id_replacement_requires_refresh_and_preserves_original(tmp_path):
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
def test_bad_local_state_is_recovered(tmp_path, damage):
    server = MarvelServer()
    first = acquire(server.adapter(tmp_path))
    target = first.pages[0].path
    if damage == "missing":
        target.unlink()
    elif damage == "corrupt":
        target.write_bytes(b"broken")
    else:
        for path in (tmp_path / "acquisitions").glob("*.json"):
            if json.loads(path.read_text())["sha256"] == target.stem:
                path.write_text("invalid json")
    recovered = acquire(server.adapter(tmp_path))
    assert server.downloads == ["cover", "one", "two", "one"]
    assert recovered.pages[0].path.read_bytes() == server.images["one"]


def test_failed_refresh_keeps_previous_acquisition(tmp_path):
    server = MarvelServer()
    first = acquire(server.adapter(tmp_path))
    server.fail = "one"
    with pytest.raises(httpx.HTTPStatusError):
        acquire(server.adapter(tmp_path, refresh=True))
    downloads = list(server.downloads)
    assert acquire(server.adapter(tmp_path)) == first
    assert server.downloads == downloads


def test_asset_ids_are_scoped_to_issue(tmp_path):
    server = MarvelServer()
    acquire(server.adapter(tmp_path), "1")
    server.images["one"] = image_bytes("purple")
    second = acquire(server.adapter(tmp_path), "2")
    assert len(server.downloads) == 6
    assert second.pages[0].path.read_bytes() == server.images["one"]


def test_missing_ids_do_not_reuse_by_sequence(tmp_path):
    server = MarvelServer()
    server.omit_ids = True
    acquire(server.adapter(tmp_path))
    acquire(server.adapter(tmp_path))
    assert len(server.downloads) == 3
    server.generation += 1
    acquire(server.adapter(tmp_path))
    assert len(server.downloads) == 6


def test_duplicate_ids_rejected(tmp_path):
    server = MarvelServer()
    server.order = ["cover", "one", "one"]
    with pytest.raises(ServiceResponseError, match="duplicate page IDs"):
        acquire(server.adapter(tmp_path))
    assert not server.downloads


def test_cli_refresh_is_independent_of_overwrite(tmp_path, monkeypatch):
    observed = []
    clf = make_test_clf(tmp_path)

    class Adapter:
        def __init__(self, *, cookie_file, refresh):
            observed.append(refresh)

        def get_clf(self, source, progress, metadata_ready):
            metadata_ready(clf.title, clf.series, clf.issue_number)
            return clf

        def cleanup(self):
            pass

    monkeypatch.setattr("ucd.cli.MarvelUnlimitedAdapter", Adapter)
    runner = CliRunner()
    args = ["download", "1", "--output-dir", str(tmp_path / "output")]
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, args + ["--refresh"]).exit_code == 1
    assert runner.invoke(app, args + ["--refresh", "--overwrite"]).exit_code == 0
    assert observed == [False, True, True]


def test_interrupted_first_import_resumes_successful_assets(tmp_path):
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


def test_invalid_image_does_not_replace_cached_original(tmp_path):
    server = MarvelServer()
    first = acquire(server.adapter(tmp_path))
    server.images["cover"] = b"not a PNG"
    with pytest.raises(OSError):
        acquire(server.adapter(tmp_path, refresh=True))
    assert len(records(tmp_path)) == 3
    assert acquire(server.adapter(tmp_path)) == first
