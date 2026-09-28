import os
from pathlib import Path
from tempfile import TemporaryDirectory

import typer

from ucd.exceptions import ComicDownloaderError, InvalidComicInputError
from ucd.input.cbz import CBZInputAdapter
from ucd.input.marvel_unlimited import MarvelUnlimitedAdapter
from ucd.metadata import prepare_comic_metadata
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
                publication = adapter.get_publication(
                    source,
                    progress=progress.update,
                    metadata_ready=check_destination,
                )
                publication = prepare_comic_metadata(publication)
                destination = output_dir / make_cbz_filename(publication)
                write_cbz(
                    publication,
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


@app.command()
def convert(
    sources: list[Path] = typer.Argument(..., metavar="SOURCE..."),
    output_dir: Path = typer.Option(
        ..., "--output-dir", "-o", file_okay=False, help="Directory for converted CBZ files."
    ),
    overwrite: bool = typer.Option(False, "--overwrite"),
    quit_on_error: bool = typer.Option(False, "--quit-on-error"),
) -> None:
    """Preserve local CBZs and add missing ZIP-comment metadata through
    Publication.
    """
    adapter = CBZInputAdapter()
    progress = _ProgressBar()
    failures = 0
    output_dir.mkdir(parents=True, exist_ok=True)

    def check_destination(title: str, series: str | None, issue_number: str | None) -> None:
        destination = output_dir / make_cbz_filename_from_metadata(title, series, issue_number)
        for source in sources:
            if destination.resolve() == source.resolve() or (
                destination.exists() and source.exists() and destination.samefile(source)
            ):
                raise InvalidComicInputError(
                    "Output would replace an input archive; choose another directory"
                )
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)

    for source in sources:
        progress.reset()
        try:
            publication = adapter.get_publication(
                str(source), progress=progress.update, metadata_ready=check_destination
            )
            destination = output_dir / make_cbz_filename(publication)
            # Publish only a complete archive; failure must not leave partial
            # output.
            with TemporaryDirectory(dir=output_dir) as scratch:
                temporary = Path(scratch) / "output.cbz"
                write_cbz(publication, temporary)
                check_destination(publication.title, publication.series, publication.issue_number)
                if overwrite:
                    temporary.replace(destination)
                else:
                    os.link(temporary, destination)
            typer.echo(f"Wrote {destination}")
        except (ComicDownloaderError, OSError, ValueError) as exc:
            failures += 1
            typer.echo(f"Error: {exc}", err=True)
            if quit_on_error:
                raise typer.Exit(code=1) from exc
    if failures:
        raise typer.Exit(code=1)
