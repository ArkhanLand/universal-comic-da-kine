from pathlib import Path

import typer

from ucd.exceptions import ComicDownloaderError
from ucd.input.marvel_unlimited import MarvelUnlimitedAdapter
from ucd.output.cbz import write_cbz

app = typer.Typer(no_args_is_help=True)


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

    try:
        clf = adapter.get_clf(source)
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
