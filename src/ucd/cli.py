from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True)


@app.command()
def download(
    url: str,
    output: Path = typer.Option(Path("downloads"), "--output", "-d"),
    overwrite: bool = typer.Option(False, "--overwrite", "-o"),
    kill_cache: bool = typer.Option(False, "--kill-cache", "-k"),
) -> None:
    """Acquire and package a comic-like publication."""
    typer.echo(f"URL: {url}")
    typer.echo(f"Output directory: {output}")
    typer.echo(f"Overwrite archive: {overwrite}")
    typer.echo(f"Remove & re-fetch cached source pages: {kill_cache}")
    typer.echo("No services are registered yet.")
    raise typer.Exit(code=2)
