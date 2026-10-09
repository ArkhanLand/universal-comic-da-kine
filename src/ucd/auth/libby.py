"""Browser-free Libby setup and active-loan opening.

Protocol reference: libby-archiver 0.4.0 (JavaGT, MIT); see THIRD_PARTY_NOTICES.md.
These observed private endpoints are not a supported public API.
"""

import base64
import json
import os
import re
import ssl
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol, cast
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

from ucd.exceptions import ComicDownloaderError, ServiceResponseError, UnavailableError

# The legacy sentry-read.svc.overdrive.com host presents a mismatched
# certificate. Use Libby's web gateway with normal hostname verification.
API = "https://sentry.libbyapp.com"
GATEWAY = "https://sentry.libbyapp.com"
THUNDER = "https://thunder.api.overdrive.com"
# Observed in the official browser request during live testing on 2026-10-07.
CLIENT_VERSION = "22.1.2"
KEYRING_SERVICE = "ucd.libby-overdrive"
# Match the reference client's desktop identity when bootstrapping chips.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)


class LibbyAuthenticationError(ComicDownloaderError):
    """Command-wide authentication failure; never a skippable title failure."""

    def __init__(self, message: str, *, result: str | None = None) -> None:
        super().__init__(message)
        self.result = result


class CredentialStoreError(ComicDownloaderError):
    """The system credential store is unavailable or unusable."""


class SecretStore(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...


def system_secret_store() -> SecretStore:
    try:
        keyring = import_module("keyring")
        backend = keyring.get_keyring()
        # Reject plaintext/file/null backends; no headless fallback is supported.
        if type(backend).__module__ not in {
            "keyring.backends.macOS",
            "keyring.backends.Windows",
            "keyring.backends.SecretService",
            "keyring.backends.kwallet",
            "keyring.backends.libsecret",
        }:
            raise CredentialStoreError("No supported system credential store is available.")
        return cast(SecretStore, backend)
    except CredentialStoreError:
        raise
    except Exception:
        raise CredentialStoreError(
            "Cannot open the system credential store; install UCD dependencies and unlock it."
        ) from None


def config_path() -> Path:
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "ucd" / "libby-connections.json"


@dataclass(frozen=True)
class Library:
    key: str
    name: str
    website_id: str


@dataclass(frozen=True)
class Connection:
    name: str
    library: Library
    secret_id: str


@dataclass(frozen=True)
class Session:
    identity: str = field(repr=False)
    card_id: str = field(repr=False)
    client_version: str | None = CLIENT_VERSION


@dataclass(frozen=True)
class Credentials:
    card_number: str = field(repr=False)
    pin: str = field(repr=False)
    session: Session | None = field(default=None, repr=False)


def _claims(identity: str) -> dict[str, Any]:
    """Read expiry/card hints only; decoding a JWT does not verify its signature."""
    try:
        payload = identity.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return value if isinstance(value, dict) else {}
    except (ValueError, IndexError):
        return {}


def _identifier(value: Any) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ServiceResponseError("Libby returned an invalid identifier.")
    text = str(value)
    if not text:
        raise ServiceResponseError("Libby returned an empty identifier.")
    return text


class LibbyClient:
    def __init__(
        self,
        client: httpx.Client,
        *,
        client_version: str = CLIENT_VERSION,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.client = client
        self.client_version = client_version
        self.progress = progress or (lambda message: None)

    def _request(
        self,
        method: str,
        url: str,
        *,
        identity: str | None = None,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Origin": "https://libbyapp.com",
            "User-Agent": USER_AGENT,
        }
        if method == "POST" and urlsplit(url).path == "/chip":
            # Official Sentry transform: select two characters at path length
            # (4) from the reversed lowercase letters of the current identity.
            # A fresh device uses the fixed seed from the official client.
            seed = identity or "cudlkahllcnsjxhbmddl"
            headers["Accept-Language"] = re.sub(r"[^a-z]", "", seed)[::-1][4:6]
        if urlsplit(url).path.startswith("/open/"):
            headers.update({"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors"})
        if identity:
            headers["Authorization"] = f"Bearer {identity}"
        try:
            request = self.client.build_request(
                method, url, headers=headers, json=body, params=params
            )
            # The reference API client is bearer-only. Gateway cookies can
            # select a different chip from the reminted identity we send.
            request.headers.pop("Cookie", None)
            response = self.client.send(request, follow_redirects=False)
        except httpx.HTTPError as exc:
            # Exception strings can expose signed URLs; classify causes instead.
            cause: BaseException | None = exc
            seen: set[int] = set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause, ssl.SSLCertVerificationError):
                    raise ServiceResponseError(
                        "Libby authentication connection failed: TLS certificate "
                        "verification failed. Certificate checks remain enabled."
                    ) from None
                cause = cause.__cause__ or cause.__context__
            if isinstance(exc, httpx.TimeoutException):
                message = "Libby connection timed out; check your connection and retry."
            elif isinstance(exc, httpx.ConnectError):
                message = "Cannot connect to the Libby service; check network/DNS access and retry."
            else:
                message = "Libby connection failed before a response was received."
            raise ServiceResponseError(message) from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("result") == "client_upgrade_required":
            raise ServiceResponseError("Libby requires a newer client version; update UCD.")
        if response.status_code in (401, 403):
            path = urlsplit(url).path
            if path == "/chip":
                step = "session creation/renewal"
            elif path.startswith("/auth/link/"):
                step = "library-card verification"
            elif path == "/chip/sync":
                step = "saved-session verification"
            elif path.startswith("/open/"):
                step = "loan opening"
            else:
                step = "service request"
            result = data.get("result") if isinstance(data, dict) else None
            # Only known protocol constants may appear; arbitrary response text
            # and response fields can contain credentials or account details.
            safe_results = {
                "missing_chip",
                "whoa",
                "bad_credentials",
                "invalid_pin",
                "invalid_card",
                "unauthorized",
                "forbidden",
            }
            detail = (
                f", result={result}" if isinstance(result, str) and result in safe_results else ""
            )
            raise LibbyAuthenticationError(
                f"Libby rejected {step} (HTTP {response.status_code}{detail}).",
                result=result if result == "missing_chip" else None,
            )
        if response.is_error or response.is_redirect:
            raise ServiceResponseError(f"Libby request failed (HTTP {response.status_code}).")
        if not isinstance(data, dict):
            raise ServiceResponseError("Libby returned an unexpected JSON response structure.")
        return data

    def resolve_library(self, key: str) -> Library:
        key = key.strip().lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", key):
            raise ValueError("Use the library key from libbyapp.com/library/<key>.")
        data = self._request("GET", f"{THUNDER}/v2/libraries/{quote(key, safe='')}")
        return Library(
            key=str(data.get("preferredKey") or key),
            name=str(data.get("name") or key),
            website_id=_identifier(data.get("websiteId")),
        )

    def sync(self, session: Session) -> dict[str, Any]:
        data = self._request("GET", f"{API}/chip/sync", identity=session.identity)
        if data.get("result") != "synchronized" or not isinstance(data.get("loans"), list):
            raise LibbyAuthenticationError("Libby could not verify the saved session.")
        return data

    def authenticate(self, library: Library, credentials: Credentials) -> Session:
        cached = credentials.session
        if cached:
            exp = _claims(cached.identity).get("exp")
            if (
                cached.client_version == self.client_version
                and isinstance(exp, (int, float))
                and exp > time.time() + 300
            ):
                self.sync(cached)  # Rejected credentials are a hard failure, not a silent retry.
                return cached
        params = {"c": f"d:{self.client_version}", "s": "0"}
        self.progress("Creating Libby session…")
        primary = self._request("POST", f"{API}/chip", params=params)
        primary_identity = _identifier(primary.get("identity"))
        self.progress("Verifying library card…")
        self._request(
            "POST",
            f"{API}/auth/link/{quote(library.website_id, safe='')}",
            identity=primary_identity,
            body={
                "ils": library.key,
                "username": credentials.card_number,
                "password": credentials.pin,
            },
        )
        chip = _identifier(primary.get("chip"))
        self.progress("Renewing linked session…")
        minted = self._request(
            "POST",
            f"{API}/chip",
            identity=primary_identity,
            params={**params, "v": chip.split("-")[0]},
        )
        identity = _identifier(minted.get("identity"))
        chip_claim = _claims(identity).get("chip")
        if (
            minted.get("chip") != chip
            or not isinstance(chip_claim, dict)
            or chip_claim.get("id") != chip
        ):
            raise LibbyAuthenticationError(
                "Libby returned an identity for a different linked device."
            )
        cards = chip_claim.get("cards") if isinstance(chip_claim, dict) else None
        if not isinstance(cards, list):
            raise LibbyAuthenticationError("Libby did not return linked cards.")
        # This bootstrap creates a fresh device and links exactly one card.
        # The reference reads card[1]; other tuple positions are undocumented
        # and may contain library aliases rather than the catalog lookup key.
        if len(cards) != 1 or not isinstance(cards[0], list) or len(cards[0]) < 2:
            raise LibbyAuthenticationError("Libby did not return one unambiguous linked card.")
        session = Session(identity, _identifier(cards[0][1]), self.client_version)
        self.progress("Checking verified session…")
        self.sync(session)
        return session

    def find_loan(self, session: Session, title_id: str) -> dict[str, Any] | None:
        if not title_id.isascii() or not title_id.isdigit():
            raise ValueError("An OverDrive title ID must contain only digits.")
        for loan in self.sync(session)["loans"]:
            if not isinstance(loan, dict):
                raise ServiceResponseError("Libby returned a malformed loan.")
            if str(loan.get("id")) != title_id or str(loan.get("cardId")) != session.card_id:
                continue
            try:
                expires = datetime.fromisoformat(loan["expires"].replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    raise ValueError
            except (KeyError, AttributeError, TypeError, ValueError):
                raise ServiceResponseError(
                    "Libby returned a loan without a valid expiry."
                ) from None
            if expires > datetime.now(UTC):
                return loan
        return None

    def renew_session(self, session: Session) -> Session:
        """Refresh the existing chip as the official missing_chip handler does."""
        chip = _claims(session.identity).get("chip")
        device_id = chip.get("id") if isinstance(chip, dict) else None
        if not isinstance(device_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", device_id):
            raise LibbyAuthenticationError(
                "Saved device identity is invalid; rerun ucd init --service libby-overdrive."
            )
        self.progress("Refreshing existing Libby device identity…")
        data = self._request(
            "POST",
            f"{API}/chip",
            identity=session.identity,
            params={
                "c": f"d:{self.client_version}",
                "s": "0",
                "v": device_id.split("-")[0],
            },
        )
        identity = _identifier(data.get("identity"))
        updated_chip = _claims(identity).get("chip")
        if data.get("chip") != device_id or not isinstance(updated_chip, dict):
            raise LibbyAuthenticationError("Libby replaced the device during identity renewal.")
        cards = updated_chip.get("cards")
        if (
            updated_chip.get("id") != device_id
            or not isinstance(cards, list)
            or not any(
                isinstance(card, list) and len(card) >= 2 and str(card[1]) == session.card_id
                for card in cards
            )
        ):
            raise LibbyAuthenticationError("Renewed identity lost the selected library card.")
        return Session(identity, session.card_id, self.client_version)

    def open_loan(
        self,
        library: Library,
        session: Session,
        title_id: str,
        *,
        session_renewed: Callable[[Session], None] | None = None,
    ) -> dict[str, Any]:
        loan = self.find_loan(session, title_id)
        if loan is None:
            raise UnavailableError(f"Title {title_id} is not checked out on this library card.")
        kind_record = loan.get("type")
        format_record = loan.get("overDriveFormat")
        kind = kind_record.get("id") if isinstance(kind_record, dict) else None
        format_id = format_record.get("id") if isinstance(format_record, dict) else None
        if kind not in {"ebook", "magazine"} or format_id != "ebook-overdrive":
            raise UnavailableError("This loan is not an OverDrive Read ebook rendition.")
        # Layout is checked after decoding openbook; a passport alone cannot prove it.
        # Setup lookup keys can differ from the linked card's canonical key.
        # The official browser uses the linked card's key in the open codex.
        chip = _claims(session.identity).get("chip")
        cards = chip.get("cards") if isinstance(chip, dict) else None
        linked = [
            card
            for card in cards or []
            if isinstance(card, list)
            and len(card) >= 6
            and str(card[1]) == session.card_id
            and str(card[4]) == library.website_id
        ]
        if len(linked) != 1 or not isinstance(linked[0][5], str):
            raise LibbyAuthenticationError(
                "Saved identity lacks the selected card's library association; "
                "rerun ucd init --service libby-overdrive."
            )
        library_key = linked[0][5]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", library_key):
            raise ServiceResponseError("Linked card has an invalid library key.")
        slug = f"{session.card_id}-{title_id}"
        codex = {
            "codex": {
                "title": {"titleId": title_id, "slug": title_id},
                "loan": {"psnKey": slug, "slug": slug},
                "library": {"key": library_key, "name": library.name},
            },
            "dewey-url": "https://libbyapp.com",
            "spec": "V31",
        }
        encoded = base64.b64encode(json.dumps(codex).encode()).decode()
        open_kind = "magazine" if kind == "magazine" else "book"
        url = f"{GATEWAY}/open/{open_kind}/card/{quote(session.card_id, safe='')}/title/{title_id}"
        params = {"t": encoded, "website_id": library.website_id}
        try:
            return self._request("GET", url, identity=session.identity, params=params)
        except LibbyAuthenticationError as error:
            if error.result != "missing_chip":
                raise
        renewed = self.renew_session(session)
        if session_renewed:
            session_renewed(renewed)
        self.progress("Retrying loan opening with refreshed identity…")
        return self._request("GET", url, identity=renewed.identity, params=params)


class ConnectionStore:
    """Ordered non-secret connection configuration; secrets live only in keyring."""

    def __init__(self, path: Path, secrets: SecretStore) -> None:
        self.path = path
        self.secrets = secrets

    def connections(self) -> list[Connection]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text())
            if data["schema_version"] != 1:
                raise ValueError
            connections = [
                Connection(item["name"], Library(**item["library"]), item["secret_id"])
                for item in data["connections"]
            ]
            for connection in connections:
                if not all(
                    isinstance(value, str) and value
                    for value in (
                        connection.name,
                        connection.secret_id,
                        connection.library.key,
                        connection.library.name,
                        connection.library.website_id,
                    )
                ):
                    raise ValueError
            if len({c.name for c in connections}) != len(connections):
                raise ValueError
            return connections
        except (ValueError, KeyError, TypeError, OSError):
            raise CredentialStoreError("Invalid Libby connection configuration.") from None

    def credentials(self, connection: Connection) -> Credentials:
        try:
            raw = self.secrets.get_password(KEYRING_SERVICE, connection.secret_id)
            data = json.loads(raw) if raw else {}
            session = (
                Session(**{"client_version": None, **data["session"]})
                if data.get("session")
                else None
            )
            if not isinstance(data.get("card_number"), str) or not data["card_number"]:
                raise ValueError
            if not isinstance(data.get("pin"), str):
                raise ValueError
            if session and not all(
                isinstance(v, str) and v
                for v in (
                    session.identity,
                    session.card_id,
                )
            ):
                raise ValueError
            return Credentials(data["card_number"], data["pin"], session)
        except Exception:
            raise CredentialStoreError(
                f"Cannot read credentials for {connection.name}; "
                "unlock the credential store "
                "or rerun ucd init --service libby-overdrive "
                f"--library-card {connection.name}."
            ) from None

    def save(self, name: str, library: Library, credentials: Credentials) -> Connection:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            raise ValueError("Card names must use letters, digits, hyphens, or underscores.")
        connections = self.connections()
        previous = next((c for c in connections if c.name == name), None)
        connection = Connection(name, library, previous.secret_id if previous else str(uuid4()))
        try:
            self.secrets.set_password(
                KEYRING_SERVICE,
                connection.secret_id,
                json.dumps(asdict(credentials)),
            )
        except Exception:
            raise CredentialStoreError(
                "Cannot save credentials; unlock the system store."
            ) from None
        updated = [connection if c.name == name else c for c in connections]
        if previous is None:
            updated.append(connection)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Same-directory replacement prevents partially written configuration.
        with NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                json.dump(
                    {"schema_version": 1, "connections": [asdict(c) for c in updated]},
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return connection

    def select_loan(
        self,
        client: LibbyClient,
        title_id: str,
        *,
        name: str | None = None,
    ) -> tuple[Connection, Session, dict[str, Any]]:
        connections = self.connections()
        if name:
            connections = [c for c in connections if c.name == name]
        if not connections:
            raise LibbyAuthenticationError(
                "No matching saved card; run ucd init --service libby-overdrive."
            )
        for connection in connections:
            session = self.authenticated_session(client, connection)
            try:
                loan = client.find_loan(session, title_id)
            except LibbyAuthenticationError:
                raise self._repair_error(connection) from None
            if loan:
                return connection, session, loan
        raise UnavailableError(
            f"Title {title_id} is not checked out on the selected library cards."
        )

    @staticmethod
    def _repair_error(connection: Connection) -> LibbyAuthenticationError:
        return LibbyAuthenticationError(
            f"Authentication failed for {connection.name}; rerun "
            "ucd init --service libby-overdrive "
            f"--library-card {connection.name}."
        )

    def authenticated_session(
        self,
        client: LibbyClient,
        connection: Connection,
    ) -> Session:
        credentials = self.credentials(connection)
        try:
            session = client.authenticate(connection.library, credentials)
        except LibbyAuthenticationError:
            raise self._repair_error(connection) from None
        if session != credentials.session:
            self.save(
                connection.name,
                connection.library,
                Credentials(
                    credentials.card_number,
                    credentials.pin,
                    session,
                ),
            )
        return session

    def active_loans(
        self,
        client: LibbyClient,
        *,
        name: str | None = None,
    ) -> list[tuple[Connection, dict[str, Any]]]:
        connections = self.connections()
        if name:
            connections = [connection for connection in connections if connection.name == name]
        if not connections:
            raise LibbyAuthenticationError(
                "No matching saved card; run ucd init --service libby-overdrive."
            )
        active = []
        for connection in connections:
            session = self.authenticated_session(client, connection)
            try:
                synced = client.sync(session)
            except LibbyAuthenticationError:
                raise self._repair_error(connection) from None
            for loan in synced["loans"]:
                if not isinstance(loan, dict):
                    raise ServiceResponseError("Libby returned a malformed loan.")
                if str(loan.get("cardId")) != session.card_id:
                    continue
                try:
                    expires = datetime.fromisoformat(loan["expires"].replace("Z", "+00:00"))
                    if expires.tzinfo is None:
                        raise ValueError
                except (KeyError, TypeError, ValueError, AttributeError):
                    raise ServiceResponseError("Libby returned an invalid loan expiry.") from None
                if expires > datetime.now(UTC):
                    active.append((connection, loan))
        return active
