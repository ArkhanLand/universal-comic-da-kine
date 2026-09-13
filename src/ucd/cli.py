from pathlib import Path

import typer

from ucd.exceptions import ComicDownloaderError
from ucd.input.marvel_unlimited import MarvelUnlimitedAdapter
from ucd.output.cbz import write_cbz

app = typer.Typer(no_args_is_help=True)


class _ProgressBar:
    def __init__(self, label: str, width: int = 30) -> None:
        self.label = label
        self.width = width
        self._last_completed = -1

    def update(self, completed: int, total: int) -> None:
        if completed == self._last_completed:
            return

        self._last_completed = completed
        ratio = completed / total if total else 1.0
        filled = min(self.width, int(ratio * self.width))
        bar = "#" * filled + "-" * (self.width - filled)
        percent = int(ratio * 100)
        typer.echo(
            f"\r{self.label} [{bar}] {completed}/{total} {percent:3d}%",
            nl=False,
        )

        if completed >= total:
            typer.echo()


@app.command()
def download(
    source: str,
    output: Path = typer.Option(
        Path("output.cbz"),
        "--output",
        "-o",
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
) -> None:
    """Download a Marvel Unlimited issue and write it as a CBZ."""

    adapter = MarvelUnlimitedAdapter(cookie_file=cookie_file)
    progress = _ProgressBar("Downloading pages")

    try:
        clf = adapter.get_clf(source, progress=progress.update)
        write_cbz(
            clf,
            output,
            overwrite=overwrite,
        )
    except ComicDownloaderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        adapter.cleanup()

    typer.echo(f"Wrote {output}")
