from pathlib import Path

import typer

from ucd.exceptions import ComicDownloaderError
from ucd.input.marvel_unlimited import MarvelUnlimitedAdapter
from ucd.output.cbz import (
    make_cbz_filename,
    make_cbz_filename_from_metadata,
    write_cbz,
)

app = typer.Typer(no_args_is_help=True)


class _ProgressBar:
    def __init__(self, width: int = 30, label_width: int = 32) -> None:
        self.width = width
        self.label_width = label_width
        self._last_completed = -1

    def reset(self) -> None:
        self._last_completed = -1

    def _display_label(self, label: str) -> str:
        if len(label) <= self.label_width:
            return label
        return "…" + label[-(self.label_width - 1) :]

    def update(self, label: str, completed: int, total: int) -> None:
        if completed == self._last_completed:
            return

        self._last_completed = completed
        ratio = completed / total if total else 1.0
        filled = min(self.width, int(ratio * self.width))
        bar = "#" * filled + "-" * (self.width - filled)
        percent = int(ratio * 100)
        typer.echo(
            f"\rAcquiring {self._display_label(label)} [{bar}] {completed}/{total} {percent:3d}%",
            nl=False,
        )

        if completed >= total:
            typer.echo()


@app.callback()
def main() -> None:
    """Universal Comic Da Kine."""


@app.command()
def download(
    sources: list[str] = typer.Argument(..., metavar="SOURCE..."),
    output_dir: Path = typer.Option(
        Path("."),
        "--output-dir",
        "-o",
        envvar="UCD_OUTPUT_DIR",
        file_okay=False,
        help="Directory for generated comic files.",
    ),
    cookie_file: Path = typer.Option(
        Path("cookies.txt"),
        "--cookies",
        "-c",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Fetch fresh image bytes even when locally cached; use --overwrite for existing CBZs.",
    ),
    quit_on_error: bool = typer.Option(
        False,
        "--quit-on-error",
        help="Stop after the first failed source.",
    ),
) -> None:
    """Download one or more Marvel Unlimited issues as CBZ files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    adapter = MarvelUnlimitedAdapter(cookie_file=cookie_file, refresh=refresh)
    progress = _ProgressBar()
    failures = 0

    def check_destination(
        title: str,
        series: str | None,
        issue_number: str | None,
    ) -> None:
        destination = output_dir / make_cbz_filename_from_metadata(
            title,
            series,
            issue_number,
        )
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)

    try:
        for source in sources:
            progress.reset()
            try:
                clf = adapter.get_clf(
                    source,
                    progress=progress.update,
                    metadata_ready=check_destination,
                )
                destination = output_dir / make_cbz_filename(clf)
                write_cbz(
                    clf,
                    destination,
                    overwrite=overwrite,
                )
                typer.echo(f"Wrote {destination}")
            except FileExistsError as exc:
                failures += 1
                typer.echo(f"Error: output file already exists: {exc}", err=True)
                if quit_on_error:
                    raise typer.Exit(code=1) from exc
            except ComicDownloaderError as exc:
                failures += 1
                typer.echo(f"Error: {exc}", err=True)
                if quit_on_error:
                    raise typer.Exit(code=1) from exc

        if failures:
            raise typer.Exit(code=1)
    finally:
        adapter.cleanup()
