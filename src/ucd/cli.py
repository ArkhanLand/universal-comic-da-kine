import os
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import typer

from ucd.auth.libby import CredentialStoreError, LibbyAuthenticationError
from ucd.download.cache import cache_root
from ucd.exceptions import ComicDownloaderError, InvalidComicInputError
from ucd.input.cbz import CBZInputAdapter
from ucd.input.libby_overdrive import LibbyOverDriveReadAdapter
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


@app.command("init")
def initialize(
    service: str = typer.Option(..., "--service"),
    library_card: str | None = typer.Option(None, "--library-card"),
) -> None:
    """Verify and save a library connection using the system credential store."""
    if service != "libby-overdrive":
        typer.echo(f"Error: setup is not yet supported for service {service}.", err=True)
        raise typer.Exit(code=1)

    import httpx

    from ucd.auth.libby import (
        ConnectionStore,
        Credentials,
        LibbyClient,
        config_path,
        system_secret_store,
    )

    try:
        store = ConnectionStore(config_path(), system_secret_store())
        existing = store.connections()
        previous = next((c for c in existing if c.name == library_card), None)
        typer.echo(
            'Your library key is the slug in your Libby URL, e.g. "your-library" in\n'
            "libbyapp.com/library/your-library (also shown in your library’s share links)."
        )
        key = typer.prompt(
            "Library key",
            default=previous.library.key if previous else None,
        )
        with httpx.Client(timeout=30, follow_redirects=False) as http_client:
            client = LibbyClient(http_client, progress=typer.echo)
            library = client.resolve_library(key)
            typer.echo(f"Library: {library.name} (websiteId {library.website_id})")
            name = library_card or key.strip().lower()
            if library_card is None and any(c.name == name for c in existing):
                name = typer.prompt("That card name is already saved; choose a distinct card name")
                if any(c.name == name for c in existing):
                    raise ValueError("Name is already saved; use --library-card to update it.")
            card_number = typer.prompt("Library card number", hide_input=True)
            pin = typer.prompt(
                "Card PIN/passcode (blank if none)",
                default="",
                hide_input=True,
                show_default=False,
            )
            credentials = Credentials(card_number, pin)
            # Do not reuse an old session when verifying replacement credentials.
            session = client.authenticate(library, credentials)
            store.save(name, library, Credentials(card_number, pin, session))
        typer.echo(f"Verified and saved library card {name}.")
    except (ComicDownloaderError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command("list-loans")
def list_loans(
    service: str = typer.Option(..., "--service"),
    library_card: str | None = typer.Option(None, "--library-card"),
) -> None:
    """List active checkouts on saved library cards without borrowing or downloading."""
    if service != "libby-overdrive":
        typer.echo("Error: loan listing supports only libby-overdrive.", err=True)
        raise typer.Exit(code=1)

    import re

    import httpx

    from ucd.auth.libby import (
        ConnectionStore,
        LibbyClient,
        config_path,
        system_secret_store,
    )

    try:
        store = ConnectionStore(config_path(), system_secret_store())
        typer.echo("Checking saved library connections and active loans…")
        with httpx.Client(timeout=30, follow_redirects=False) as http_client:
            loans = store.active_loans(
                LibbyClient(http_client, progress=typer.echo),
                name=library_card,
            )
        if not loans:
            typer.echo("No active checkouts found.")
        for connection, loan in loans:

            def text(value: object) -> str:
                return re.sub(r"[\x00-\x1f\x7f]", " ", str(value))

            kind = loan.get("type")
            format_record = loan.get("overDriveFormat")
            format_id = (
                format_record.get("id", "unknown") if isinstance(format_record, dict) else "unknown"
            )
            kind_id = kind.get("id", "unknown") if isinstance(kind, dict) else "unknown"
            typer.echo(
                f"{text(connection.name)}: {text(loan.get('id', 'unknown'))} "
                f"[{text(kind_id)} / {text(format_id)}] {text(loan.get('title', 'Untitled'))}"
            )
        typer.echo(f"Active loans: {len(loans)}")
    except (ComicDownloaderError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


def inspect_loan(
    title_id: str = typer.Argument(...),
    service: str = typer.Option(..., "--service"),
    library_card: str | None = typer.Option(None, "--library-card"),
) -> None:
    """Verify an active loan and inspect reading structure without downloading images."""
    if service != "libby-overdrive":
        typer.echo("Error: loan inspection supports only libby-overdrive.", err=True)
        raise typer.Exit(code=1)

    import httpx

    from ucd.auth.libby import (
        ConnectionStore,
        Credentials,
        LibbyClient,
        Session,
        config_path,
        system_secret_store,
    )
    from ucd.input.libby_overdrive_read import OverDriveReadClient

    try:
        store = ConnectionStore(config_path(), system_secret_store())
        typer.echo("Checking saved library connections and active loans…")
        with httpx.Client(timeout=30, follow_redirects=False) as api_client:
            client = LibbyClient(api_client, progress=typer.echo)
            connection, session, _ = store.select_loan(client, title_id, name=library_card)
            typer.echo(f"Active checkout found on {connection.name}.")
            typer.echo("Opening OverDrive Read loan…")

            def save_renewed(updated_session: Session) -> None:
                credentials = store.credentials(connection)
                store.save(
                    connection.name,
                    connection.library,
                    Credentials(
                        credentials.card_number,
                        credentials.pin,
                        updated_session,
                    ),
                )

            passport = client.open_loan(
                connection.library,
                session,
                title_id,
                session_renewed=save_renewed,
            )
        typer.echo("Establishing reader connection and decoding openbook…")
        # Separate cookie jar: no API credentials are forwarded to the reader.
        with httpx.Client(timeout=30, follow_redirects=False) as read_client:
            rendition = OverDriveReadClient(read_client).fetch_openbook(passport)
            summary = rendition.summary()
        typer.echo(f"Spine components: {summary['spine_components']}")
        typer.echo(f"Cover components: {summary['cover_components']}")
        typer.echo(f"Narrative components: {summary['narrative_components']}")
        typer.echo(f"Nonlinear components: {summary['nonlinear_components']}")
        typer.echo(f"Reading direction: {summary['reading_direction']}")
        typer.echo("Loan inspection complete. No image files were downloaded.")
    except (ComicDownloaderError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None


def register_development_commands(target: typer.Typer) -> None:
    """Developer commands are explicitly enabled when the process starts."""
    if os.environ.get("UCD_DEV_COMMANDS") == "1":
        target.command("inspect-loan")(inspect_loan)


register_development_commands(app)


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
    service: str | None = typer.Option(
        None, "--service", help="Input service; bare IDs default to Marvel."
    ),
    library_card: str | None = typer.Option(None, "--library-card"),
    cache_dir: Path | None = typer.Option(None, "--cache-dir", envvar="UCD_CACHE_DIR"),
    output_format: str = typer.Option("cbz", "--output-format"),
    continue_on_error: bool = typer.Option(False, "--continue-on-error"),
    quit_on_error: bool = typer.Option(False, "--quit-on-error", hidden=True),
) -> None:
    """Download checked-out Libby titles or Marvel issues as CBZ files."""
    if service not in {None, "marvel-unlimited", "libby-overdrive"}:
        raise typer.BadParameter("Supported services: marvel-unlimited, libby-overdrive.")
    if output_format != "cbz":
        raise typer.BadParameter("Only CBZ output is currently supported.")
    if continue_on_error and quit_on_error:
        raise typer.BadParameter("Choose either --continue-on-error or --quit-on-error.")

    output_dir.mkdir(parents=True, exist_ok=True)
    adapters: dict[str, MarvelUnlimitedAdapter | LibbyOverDriveReadAdapter] = {}
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
                selected = service or (
                    "libby-overdrive"
                    if source.startswith("https://share.libbyapp.com/title/")
                    else "marvel-unlimited"
                )
                if library_card and selected != "libby-overdrive":
                    raise InvalidComicInputError("--library-card requires a Libby source.")
                if selected not in adapters:
                    if selected == "libby-overdrive":
                        adapters[selected] = LibbyOverDriveReadAdapter(
                            library_card=library_card,
                            cache_dir=cache_dir,
                            refresh=refresh,
                            status=typer.echo,
                        )
                    else:
                        adapters[selected] = MarvelUnlimitedAdapter(
                            cookie_file=cookie_file,
                            refresh=refresh,
                            cache_dir=cache_dir / selected if cache_dir is not None else None,
                        )
                publication = adapters[selected].get_publication(
                    source,
                    progress=progress.update,
                    metadata_ready=check_destination,
                )
                publication = prepare_comic_metadata(publication)
                filename = (
                    make_cbz_filename_from_metadata(publication.title, None, None)
                    if publication.service == "libby-overdrive" and publication.issue_number is None
                    else make_cbz_filename(publication)
                )
                destination = output_dir / filename
                # Publish only a complete archive, including failures during writing.
                with TemporaryDirectory(dir=output_dir) as scratch:
                    temporary = Path(scratch) / "output.cbz"
                    write_cbz(publication, temporary)
                    if overwrite:
                        temporary.replace(destination)
                    else:
                        os.link(temporary, destination)
                typer.echo(f"Wrote {destination}")
            except FileExistsError as exc:
                typer.echo(f"Skipped existing output: {exc}")
            except (LibbyAuthenticationError, CredentialStoreError) as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(code=1) from None
            except (ComicDownloaderError, OSError, ValueError, httpx.HTTPError) as exc:
                failures += 1
                message = (
                    "Service connection failed." if isinstance(exc, httpx.HTTPError) else str(exc)
                )
                typer.echo(f"Error: {message}", err=True)
                if not continue_on_error:
                    raise typer.Exit(code=1) from None

        if failures:
            raise typer.Exit(code=1)
    finally:
        for adapter in adapters.values():
            adapter.cleanup()


cache_app = typer.Typer(help="Locate the original image cache.")
app.add_typer(cache_app, name="cache")


@cache_app.command("where")
def cache_where(
    cache_dir: Path | None = typer.Option(None, "--cache-dir", envvar="UCD_CACHE_DIR"),
) -> None:
    """Print the shared cache root without opening credentials or fetching data."""
    typer.echo(str(cache_dir if cache_dir is not None else cache_root()))


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
