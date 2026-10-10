import os
from io import BytesIO

import httpx
import pytest
from PIL import Image

from ucd.cli import _ProgressBar
from ucd.input.marvel_unlimited import (
    MarvelUnlimitedAdapter,
    _MarvelPageSource,
    _MarvelPageSources,
)


def test_download_progress(tmp_path, monkeypatch) -> None:
    images: dict[str, bytes] = {}

    for number in range(1, 4):
        image = Image.new("RGB", (100, 200))
        buffer = BytesIO()
        image.save(buffer, format="JPEG")
        images[f"https://example.com/page{number}"] = buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=images[str(request.url)],
            headers={"Content-Type": "image/jpeg"},
        )

    monkeypatch.setattr("ucd.input.marvel_unlimited.WORK_PATH", tmp_path)

    adapter = MarvelUnlimitedAdapter(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    sources = _MarvelPageSources(
        cover=_MarvelPageSource(numbers=(), url="https://example.com/page1"),
        pages=[
            _MarvelPageSource(numbers=(1,), url="https://example.com/page2"),
            _MarvelPageSource(numbers=(2,), url="https://example.com/page3"),
        ],
    )
    updates: list[tuple[int, int]] = []

    adapter.get_pages(
        "51975",
        sources,
        progress=lambda completed, total: updates.append((completed, total)),
    )

    assert updates == [(0, 3), (1, 3), (2, 3), (3, 3)]


@pytest.mark.parametrize("columns", [87, 80, 60, 25, 10], ids=["87", "80", "60", "25", "10"])
def test_bar_width(columns, monkeypatch):
    monkeypatch.setattr("ucd.cli.get_terminal_size", lambda: os.terminal_size((columns, 24)))
    bar = _ProgressBar()
    for count in (0, 99, 100, 193):
        line = bar._line(count, 193, int(count / 193 * 100))
        assert len(line) < columns
        assert "%" in line


def test_log_progress(capsys):
    bar = _ProgressBar()
    title = "Mecha-Ude: Mechanical Arms, Volume 1"
    for count in range(194):
        bar.update(title, count, 193)
    output = capsys.readouterr().out
    assert output.count(title) == 1
    assert "\r" not in output
    assert "\x1b" not in output
    assert output.endswith("Images: 193/193 (100%)\n")
    assert len(output.splitlines()) == 3


def test_bar_error_line(capsys, monkeypatch):
    monkeypatch.setattr("ucd.cli.sys.stdout.isatty", lambda: True)
    bar = _ProgressBar()
    bar.update("Title", 0, 170)
    bar.update("Title", 1, 170)
    bar.finish()
    bar.finish()
    output = capsys.readouterr().out
    assert output.count("\r\x1b[2K") == 2
    assert output.endswith("%\n")
    assert not output.endswith("\n\n")


def test_bar_reset(capsys):
    bar = _ProgressBar()
    bar.update("First", 0, 2)
    bar.update("First", 2, 2)
    bar.reset()
    bar.update("Second", 0, 2)
    output = capsys.readouterr().out
    assert "Acquiring Second\nImages: 0/2 (0%)\n" in output


def test_mapping_progress(capsys, monkeypatch):
    monkeypatch.setattr("ucd.cli.sys.stdout.isatty", lambda: True)
    bar = _ProgressBar()
    bar.mapping("Title", 0, 170)
    bar.mapping("Title", 1, 170)
    bar.mapping("Title", 170, 170)
    bar.update("Title", 0, 170)
    bar.update("Title", 170, 170)
    output = capsys.readouterr().out
    assert output.count("Acquiring Title") == 1
    assert output.count("\r\x1b[2KMapping [") == 3
    assert output.count("\r\x1b[2K Images [") == 2
    assert output.endswith("100%\n")


def test_setup_row(capsys, monkeypatch):
    monkeypatch.setattr("ucd.cli.sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("ucd.cli.get_terminal_size", lambda: os.terminal_size((87, 24)))
    bar = _ProgressBar()
    for step in ("Checking checkout…", "Fetching metadata…", "Opening reader…"):
        bar.status(step)
    assert capsys.readouterr().out == ""
    bar.begin("Title")
    output = capsys.readouterr().out
    assert output.startswith("Acquiring Title\n")
    assert output.endswith("Gathering metadata...")
    bar.status("Decoding metadata…")
    assert capsys.readouterr().out == "\r\x1b[2KGathering metadata...."
    bar.mapping("Title", 0, 170)
    assert capsys.readouterr().out.startswith("\n\r\x1b[2KMapping [")


def test_setup_narrow(capsys, monkeypatch):
    monkeypatch.setattr("ucd.cli.sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("ucd.cli.get_terminal_size", lambda: os.terminal_size((25, 24)))
    bar = _ProgressBar()
    bar.begin("Title")
    capsys.readouterr()
    bar.status("Checking checkout…")
    bar.status("Fetching metadata…")
    rows = capsys.readouterr().out.split("\r\x1b[2K")[1:]
    assert all(len(row) < 25 for row in rows)
    assert rows[-1] == "Gathering metadata.."


def test_setup_log(capsys):
    bar = _ProgressBar()
    bar.status("Checking checkout…")
    bar.status("Fetching metadata…")
    assert capsys.readouterr().out == ""
    bar.finish()
    bar.finish()
    assert capsys.readouterr().out == "Gathering metadata..\n"
    bar.reset()
    bar.status("Opening reader…")
    bar.finish()
    assert capsys.readouterr().out == "Gathering metadata.\n"
