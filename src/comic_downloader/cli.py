from pathlib import Path
import typer

app = typer.Typer(no_args_is_help=True)

@app.command()
def download(
    url: str,
    output: Path = typer.Option(Path("downloads"), "--output", "-d"),
    overwrite: bool = typer.Option(False, "--overwrite", "-o"),
    redownload: bool = typer.Option(False, "--redownload"),
) -> None:
    """Download and package a comic from a supported service."""
    typer.echo(f"URL: {url}")
    typer.echo(f"Output directory: {output}")
    typer.echo(f"Overwrite archive: {overwrite}")
    typer.echo(f"Redownload source pages: {redownload}")
    typer.echo("No services are registered yet.")
    raise typer.Exit(code=2)
