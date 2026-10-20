import os
from dataclasses import replace
from io import BytesIO

import httpx
import pytest
from PIL import Image
from typer.testing import CliRunner

from tests.helpers import make_test_publication
from ucd.cli import _ProgressBar, app, wait_for_delay
from ucd.download.pagination import _infer_page_numbers
from ucd.exceptions import ServiceResponseError
from ucd.input.marvel_unlimited import (
    MarvelUnlimitedAdapter,
    _MarvelPageSource,
    _MarvelPageSources,
)
from ucd.metadata import prepare_comic_metadata
from ucd.models import Pages


@pytest.mark.parametrize("scenario", ["known", "unknown", "mixed", "failure"])
def test_pagination_notice(tmp_path, monkeypatch, scenario):
    base = make_test_publication(tmp_path)
    # Real synthetic images exercise the shared inference diagnostic, without
    # touching a provider. The wider page cannot receive an inferred span.
    single, wide = base.narrative
    Image.new("RGB", (100, 150), "red").save(single.path)
    Image.new("RGB", (267, 150), "blue").save(wide.path)
    pages = Pages(
        cover=base.cover,
        pages=(replace(single, numbers=None), replace(wide, numbers=None, width=267)),
    )

    class Adapter:
        def __init__(self, **kwargs):
            pass

        def get_publication(self, source, progress, metadata_ready):
            publication = replace(base, title=f"Example {source}")
            metadata_ready(publication.title, None, None)
            if scenario != "known" and source == "1":
                inferred = _infer_page_numbers(pages)
                publication = replace(publication, narrative=inferred.pages)
            if scenario == "failure":
                raise ServiceResponseError("Synthetic acquisition failure")
            return publication

        def cleanup(self):
            pass

    monkeypatch.setattr("ucd.cli.LibbyOverDriveReadAdapter", Adapter)
    sources = ["1", "2"] if scenario == "mixed" else ["1"]
    output = tmp_path / "output"
    result = CliRunner().invoke(
        app,
        [
            "download",
            *sources,
            "--service",
            "libby-overdrive",
            "--issue-delay",
            "0",
            "--output-dir",
            str(output),
        ],
    )
    expected_code = 1 if scenario == "failure" else 0
    assert result.exit_code == expected_code, "Pagination uncertainty must not fail a download"
    assert "Cannot infer logical page span" not in result.output, (
        "Detailed inference diagnostics must not appear in normal CLI output"
    )
    notice = "(Warning: logical pagination unknown) "
    expected_notices = 1 if scenario in {"unknown", "mixed"} else 0
    assert result.output.count(notice) == expected_notices, (
        "Report uncertainty once for each successfully written affected book"
    )
    written = [line for line in result.stdout.splitlines() if "Wrote " in line]
    if scenario == "failure":
        assert not written, "Failed acquisitions must not claim that an archive was written"
        assert "Error: Synthetic acquisition failure" in result.stderr, (
            "Real acquisition failures must retain their error message"
        )
    else:
        assert len(written) == len(sources), "Every successful download must report its output"
        prefix = notice if expected_notices else ""
        assert written[0].startswith(prefix + "Wrote "), (
            "The parenthesized warning must precede Wrote on the same line"
        )
        assert result.stderr == "", "Nonfatal pagination notices must not look like errors"
        if scenario == "mixed":
            assert written[1].startswith("Wrote "), "Pagination notices must not leak between books"


def test_wait_for_delay() -> None:
    waits: list[float] = []
    wait_for_delay(1.5, waits.append)
    assert waits == [1.5]


def test_waiting_output(capsys, monkeypatch) -> None:
    progress = _ProgressBar()
    progress.waiting(1.5)
    output = capsys.readouterr()
    expected = "Waiting 1.5 secs between issues\n"
    assert output.out == expected, "Test non-interactive stdout"
    assert output.err == "", "Test non-interactive stderr"

    monkeypatch.setattr(
        "ucd.cli.get_terminal_size",
        lambda: os.terminal_size((80, 24)),
    )
    progress = _ProgressBar()
    progress._interactive = True
    progress.waiting(1.5)

    output = capsys.readouterr()
    expected = "\r\x1b[2KWaiting [" + "-" * 30 + "] 1.5s"
    assert output.out == expected, "Test interactive 80col stdout"
    assert output.err == "", "Test interactive 80col stderr"

    monkeypatch.setattr(
        "ucd.cli.get_terminal_size",
        lambda: os.terminal_size((10, 24)),
    )
    progress = _ProgressBar()
    progress._interactive = True
    progress.waiting(1.5)

    output = capsys.readouterr()
    expected = "\r\x1b[2Kting 1.5s"
    assert output.out == expected, "Test interactive 10col stdout"
    assert output.err == "", "Test interactive 10col output"


@pytest.mark.parametrize(
    "sources, options, expected_waits",
    [
        (["1", "2", "3"], [], [1.0, 1.0]),
        (["1"], [], []),
        (["1", "2", "3"], ["--issue-delay", "0"], []),
        (["1", "2", "3"], ["--issue-delay", "0.25"], [0.25, 0.25]),
    ],
    ids=["default", "single", "disabled", "fractional"],
)
def test_issue_delay(tmp_path, monkeypatch, sources, options, expected_waits) -> None:
    publication = make_test_publication(tmp_path)

    class Adapter:
        def __init__(self, *, cookie_file, refresh, cache_dir, workers):
            pass

        def get_publication(self, source, progress, metadata_ready):
            metadata_ready(publication.title, publication.series, publication.issue_number)
            return prepare_comic_metadata(publication)

        def cleanup(self):
            pass

    monkeypatch.setattr("ucd.cli.MarvelUnlimitedAdapter", Adapter)

    runner = CliRunner()
    args = ["download"] + sources + options + ["--output-dir", str(tmp_path / "output")]
    waits: list[float] = []
    monkeypatch.setattr("ucd.cli.wait_for_delay", waits.append)
    result = runner.invoke(app, args)

    assert result.exit_code == 0, f"Exit code nonzero: {result.exit_code}"
    assert waits == expected_waits


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
