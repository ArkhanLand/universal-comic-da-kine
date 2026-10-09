import base64
import json
import ssl
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from ucd.auth.libby import (
    GATEWAY,
    KEYRING_SERVICE,
    ConnectionStore,
    Credentials,
    CredentialStoreError,
    LibbyAuthenticationError,
    LibbyClient,
    Library,
    Session,
)
from ucd.cli import app
from ucd.exceptions import ServiceResponseError, UnavailableError

LIBRARY = Library("phoenix", "Phoenix Public Library", "123")
CHIP_ID = "a1b2c3d4-1234-1234-1234-123456789abc"


def identity(*, expiry=None, cards=None):
    claims = {
        "exp": expiry if expiry is not None else time.time() + 3600,
        "chip": {
            "id": CHIP_ID,
            "cards": cards
            if cards is not None
            else [["puid", "card-id", None, True, "123", "phoenix"]],
        },
    }
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def loan(**changes):
    return {
        "id": "11103570",
        "cardId": "card-id",
        "type": {"id": "ebook"},
        "overDriveFormat": {"id": "ebook-overdrive"},
        "expires": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        **changes,
    }


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[service, username] = password


@pytest.mark.parametrize(
    "cards",
    [
        [["puid", "card-id", None, True, "123", "phoenix"]],
        [["puid", "card-id"]],
        [["puid", "card-id", None, True, "123", "phoenix-phoenixpl"]],
    ],
    ids=["full", "minimal", "alias"],
)
def test_card_bootstrap(cards):
    requests = []
    token = identity(cards=cards)
    responses = [
        {"identity": "primary", "chip": CHIP_ID},
        {},
        {"identity": token, "chip": CHIP_ID},
        {"result": "synchronized", "loans": []},
    ]

    def handler(request):
        requests.append(request)
        assert request.url.host == "sentry.libbyapp.com"
        return httpx.Response(200, json=responses.pop(0))

    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(handler)))
    session = client.authenticate(LIBRARY, Credentials("card-number", "pin-secret"))
    assert session == Session(token, "card-id")
    assert [(r.method, r.url.path) for r in requests] == [
        ("POST", "/chip"),
        ("POST", "/auth/link/123"),
        ("POST", "/chip"),
        ("GET", "/chip/sync"),
    ]
    assert json.loads(requests[1].content) == {
        "ils": "phoenix",
        "username": "card-number",
        "password": "pin-secret",
    }
    assert requests[1].headers["Authorization"] == "Bearer primary"
    assert "Chrome/149.0.0.0" in requests[1].headers["User-Agent"]
    assert requests[1].headers["Origin"] == "https://libbyapp.com"
    assert requests[2].url.params["v"] == "a1b2c3d4"
    assert requests[2].headers["Authorization"] == "Bearer primary"
    assert requests[0].headers["Accept-Language"] == "bh"
    assert requests[2].headers["Accept-Language"] == "ir"
    assert "Accept-Language" not in requests[1].headers
    assert requests[3].headers["Authorization"] == f"Bearer {token}"
    assert "pin-secret" not in repr(Credentials("card-number", "pin-secret", session))
    assert token not in repr(session)


@pytest.mark.parametrize("status", [401, 403])
def test_cached_auth_rejected(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"result": "bad_credentials"})

    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(LibbyAuthenticationError):
        client.authenticate(LIBRARY, Credentials("number", "pin", Session(identity(), "card-id")))
    assert len(requests) == 1
    assert requests[0].url.path == "/chip/sync"


def test_cached_session_reused():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"result": "synchronized", "loans": []})

    session = Session(identity(), "card-id")
    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.authenticate(LIBRARY, Credentials("number", "pin", session)) == session
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("token", "expected_language"),
    [(None, "bh"), ("AbCdeFGhiJKlmn.OPqrSTuv.WxyZ", "rq")],
    ids=["initial", "renewed"],
)
def test_chip_header_scope(token, expected_language):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    client = LibbyClient(
        httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"Accept-Language": "en-US,en"},
        )
    )
    client._request("POST", f"{GATEWAY}/chip", identity=token)
    client._request("POST", f"{GATEWAY}/auth/link/123", identity=token)
    client._request("GET", f"{GATEWAY}/chip/sync", identity=token)
    assert requests[0].headers["Accept-Language"] == expected_language
    assert all(r.headers["Accept-Language"] == "en-US,en" for r in requests[1:])


def test_resolve_library():
    def handler(request):
        assert str(request.url) == "https://thunder.api.overdrive.com/v2/libraries/phoenix"
        assert "Authorization" not in request.headers
        return httpx.Response(
            200, json={"preferredKey": "phoenix", "name": LIBRARY.name, "websiteId": 123}
        )

    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.resolve_library(" Phoenix ") == LIBRARY


@pytest.mark.parametrize(
    "key",
    ["", "https://example.com", "../phoenix", "phoenix?secret"],
    ids=["empty", "url", "parent", "query"],
)
def test_reject_bad_library(key):
    client = LibbyClient(
        httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("request")))
    )
    with pytest.raises(ValueError):
        client.resolve_library(key)


def test_reject_missing_loan():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"result": "synchronized", "loans": []})

    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(UnavailableError, match="not checked out"):
        client.open_loan(LIBRARY, Session(identity(), "card-id"), "11103570")
    assert [(r.method, r.url.path) for r in requests] == [("GET", "/chip/sync")]


@pytest.mark.parametrize(
    "changes",
    [
        {"cardId": "other-card"},
        {"expires": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
    ],
    ids=["card", "expired"],
)
def test_reject_inactive_loan(changes):
    client = LibbyClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json={"result": "synchronized", "loans": [loan(**changes)]}
                ),
            )
        )
    )
    assert client.find_loan(Session(identity(), "card-id"), "11103570") is None


@pytest.mark.parametrize("canonical_key", ["phoenix", "phoenix-phoenixpl"], ids=["slug", "alias"])
def test_open_loan_gateway(canonical_key):
    requests = []
    session = Session(
        identity(
            cards=[
                [
                    "puid",
                    "card-id",
                    None,
                    True,
                    "123",
                    canonical_key,
                ]
            ]
        ),
        "card-id",
    )

    def handler(request):
        requests.append(request)
        if request.url.path == "/chip/sync":
            return httpx.Response(200, json={"result": "synchronized", "loans": [loan()]})
        assert str(request.url).startswith(f"{GATEWAY}/open/book/card/card-id/title/11103570?")
        assert request.headers["Authorization"] == f"Bearer {session.identity}"
        codex = json.loads(base64.b64decode(request.url.params["t"]))
        assert codex["codex"]["library"]["key"] == canonical_key
        return httpx.Response(200, json={"message": "ephemeral-secret"})

    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.open_loan(LIBRARY, session, "11103570") == {"message": "ephemeral-secret"}
    assert len(requests) == 2
    assert all(r.method == "GET" for r in requests)


@pytest.mark.parametrize(
    "format_id",
    ["ebook-kindle", "ebook-mediado", "", None],
    ids=["kindle", "mediado", "empty", "none"],
)
def test_reject_other_delivery(format_id):
    client = LibbyClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json={
                        "result": "synchronized",
                        "loans": [loan(overDriveFormat={"id": format_id})],
                    },
                )
            )
        )
    )
    with pytest.raises(UnavailableError, match="OverDrive Read"):
        client.open_loan(LIBRARY, Session(identity(), "card-id"), "11103570")


def test_config_privacy_and_order(tmp_path):
    secrets = MemorySecrets()
    store = ConnectionStore(tmp_path / "connections.json", secrets)
    creds = Credentials("number-secret", "pin-secret", Session(identity(), "card-id"))
    first = store.save("phoenix", LIBRARY, creds)
    store.save("county", replace(LIBRARY, key="county"), creds)
    updated = store.save("phoenix", LIBRARY, creds)
    assert updated.secret_id == first.secret_id
    assert [c.name for c in store.connections()] == ["phoenix", "county"]
    assert store.credentials(first) == creds
    config = store.path.read_text()
    for secret in ("number-secret", "pin-secret", creds.session.identity, "card-id"):
        assert secret not in config
    assert len(secrets.values) == 2
    assert (KEYRING_SERVICE, first.secret_id) in secrets.values


def test_keyring_failure(tmp_path):
    class BrokenSecrets(MemorySecrets):
        def set_password(self, service, username, password):
            raise RuntimeError("secret should never escape")

    store = ConnectionStore(tmp_path / "config.json", BrokenSecrets())
    with pytest.raises(CredentialStoreError, match="unlock") as error:
        store.save("phoenix", LIBRARY, Credentials("number", "pin"))
    assert "secret should never escape" not in str(error.value)
    assert not store.path.exists()


def test_card_selection(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "config.json", MemorySecrets())
    for name in ("phoenix", "county"):
        store.save(name, replace(LIBRARY, key=name), Credentials(name, "pin"))
    client = LibbyClient(httpx.Client())
    searched = []

    def authenticate(library, credentials):
        searched.append(library.key)
        return Session(identity(), library.key)

    monkeypatch.setattr(client, "authenticate", authenticate)
    monkeypatch.setattr(client, "find_loan", lambda session, title_id: loan())
    assert store.select_loan(client, "11103570")[0].name == "phoenix"
    assert searched == ["phoenix"]
    searched.clear()
    assert store.select_loan(client, "11103570", name="county")[0].name == "county"
    assert searched == ["county"]


def test_auth_stops_card_search(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "config.json", MemorySecrets())
    for name in ("phoenix", "county"):
        store.save(name, LIBRARY, Credentials(name, "pin"))
    client = LibbyClient(httpx.Client())
    called = []

    def fail(library, credentials):
        called.append(credentials.card_number)
        raise LibbyAuthenticationError("private response")

    monkeypatch.setattr(client, "authenticate", fail)
    with pytest.raises(LibbyAuthenticationError, match="--library-card phoenix"):
        store.select_loan(client, "11103570")
    assert called == ["phoenix"]


def test_request_error_privacy():
    def handler(request):
        raise httpx.ConnectError("https://host/?secret=do-not-print", request=request)

    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ServiceResponseError) as error:
        client.sync(Session(identity(), "card-id"))
    assert "do-not-print" not in str(error.value)


def test_init_saves_on_success(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "config.json", MemorySecrets())
    monkeypatch.setattr("ucd.auth.libby.system_secret_store", lambda: store.secrets)
    monkeypatch.setattr("ucd.auth.libby.config_path", lambda: store.path)
    monkeypatch.setattr(LibbyClient, "resolve_library", lambda self, key: LIBRARY)
    monkeypatch.setattr(
        LibbyClient,
        "authenticate",
        lambda self, library, credentials: Session(identity(), "card-id"),
    )
    result = CliRunner().invoke(
        app, ["init", "--service", "libby-overdrive"], input="phoenix\nnumber-secret\npin-secret\n"
    )
    assert result.exit_code == 0, result.output
    assert "Your library key is the slug in your Libby URL" in result.output
    assert "libbyapp.com/library/your-library" in result.output
    assert [c.name for c in store.connections()] == ["phoenix"]
    assert "number-secret" not in result.output
    assert "pin-secret" not in result.output


def test_init_failure_not_saved(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "config.json", MemorySecrets())
    monkeypatch.setattr("ucd.auth.libby.system_secret_store", lambda: store.secrets)
    monkeypatch.setattr("ucd.auth.libby.config_path", lambda: store.path)
    monkeypatch.setattr(LibbyClient, "resolve_library", lambda self, key: LIBRARY)

    def fail(*args):
        raise LibbyAuthenticationError("Authentication failed.")

    monkeypatch.setattr(LibbyClient, "authenticate", fail)
    result = CliRunner().invoke(
        app, ["init", "--service", "libby-overdrive"], input="phoenix\nnumber\npin\n"
    )
    assert result.exit_code == 1
    assert not store.path.exists()
    assert not store.secrets.values


def test_expired_session_renewal(monkeypatch):
    calls = []
    responses = [
        {"identity": "primary", "chip": CHIP_ID},
        {},
        {"identity": identity(), "chip": CHIP_ID},
        {"result": "synchronized", "loans": []},
    ]

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=responses.pop(0))

    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(handler)))
    old = Session(identity(expiry=0), "card-id")
    assert client.authenticate(LIBRARY, Credentials("number", "pin", old)) != old
    assert calls[0] == "/chip"


def test_client_upgrade_error():
    client = LibbyClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(403, json={"result": "client_upgrade_required"}),
            )
        )
    )
    with pytest.raises(ServiceResponseError, match="update UCD"):
        client.sync(Session(identity(), "card-id"))


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[]),
        httpx.Response(302, headers={"Location": "https://example.com/secret"}),
    ],
    ids=["text", "list", "redirect"],
)
def test_reject_bad_response(response):
    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(lambda r: response)))
    with pytest.raises(ServiceResponseError):
        client.sync(Session(identity(), "card-id"))


def test_search_next_card(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "config.json", MemorySecrets())
    for name in ("phoenix", "county"):
        store.save(name, LIBRARY, Credentials(name, "pin"))
    client = LibbyClient(httpx.Client())
    monkeypatch.setattr(
        client, "authenticate", lambda library, creds: Session(identity(), creds.card_number)
    )
    searched = []

    def find(session, title_id):
        searched.append(session.card_id)
        return loan() if session.card_id == "county" else None

    monkeypatch.setattr(client, "find_loan", find)
    assert store.select_loan(client, "11103570")[0].name == "county"
    assert searched == ["phoenix", "county"]


def test_duplicate_card_name(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "config.json", MemorySecrets())
    original = store.save("phoenix", LIBRARY, Credentials("original", "pin"))
    monkeypatch.setattr("ucd.auth.libby.system_secret_store", lambda: store.secrets)
    monkeypatch.setattr("ucd.auth.libby.config_path", lambda: store.path)
    monkeypatch.setattr(LibbyClient, "resolve_library", lambda self, key: LIBRARY)
    monkeypatch.setattr(
        LibbyClient,
        "authenticate",
        lambda self, library, credentials: Session(identity(), "card-id"),
    )
    result = CliRunner().invoke(
        app,
        ["init", "--service", "libby-overdrive"],
        input="phoenix\nphoenix-second\nnew-number\nnew-pin\n",
    )
    assert result.exit_code == 0, result.output
    assert [c.name for c in store.connections()] == ["phoenix", "phoenix-second"]
    assert store.credentials(original).card_number == "original"


def test_reinit_new_credentials(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "config.json", MemorySecrets())
    original = store.save(
        "phoenix", LIBRARY, Credentials("old", "old-pin", Session(identity(), "card-id"))
    )
    monkeypatch.setattr("ucd.auth.libby.system_secret_store", lambda: store.secrets)
    monkeypatch.setattr("ucd.auth.libby.config_path", lambda: store.path)
    monkeypatch.setattr(LibbyClient, "resolve_library", lambda self, key: LIBRARY)

    def authenticate(self, library, credentials):
        assert credentials.session is None
        assert credentials.card_number == "replacement"
        return Session(identity(), "card-id")

    monkeypatch.setattr(LibbyClient, "authenticate", authenticate)
    result = CliRunner().invoke(
        app,
        ["init", "--service", "libby-overdrive", "--library-card", "phoenix"],
        input="phoenix\nreplacement\nnew-pin\n",
    )
    assert result.exit_code == 0, result.output
    assert len(store.connections()) == 1
    assert store.credentials(original).card_number == "replacement"


def test_locked_store(monkeypatch):
    def unavailable():
        raise CredentialStoreError("System store unavailable.")

    monkeypatch.setattr("ucd.auth.libby.system_secret_store", unavailable)
    result = CliRunner().invoke(app, ["init", "--service", "libby-overdrive"])
    assert result.exit_code == 1
    assert "Library key" not in result.output


def test_reject_unsupported_store(monkeypatch):
    from ucd.auth.libby import system_secret_store

    class Plaintext:
        pass

    class Keyring:
        @staticmethod
        def get_keyring():
            return Plaintext()

    monkeypatch.setattr("ucd.auth.libby.import_module", lambda name: Keyring)
    with pytest.raises(CredentialStoreError, match="supported system"):
        system_secret_store()


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("certificate", "TLS certificate verification failed"),
        ("timeout", "timed out"),
        ("connection", "network/DNS"),
    ],
    ids=["tls", "timeout", "dns"],
)
def test_transport_errors(failure, expected):
    def handler(request):
        if failure == "certificate":
            try:
                raise ssl.SSLCertVerificationError("private certificate details")
            except ssl.SSLCertVerificationError as cause:
                raise httpx.ConnectError("secret request URL", request=request) from cause
        if failure == "timeout":
            raise httpx.ConnectTimeout("secret request URL", request=request)
        raise httpx.ConnectError("secret request URL", request=request)

    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ServiceResponseError, match=expected) as error:
        client.sync(Session(identity(), "card-id"))
    assert "secret request URL" not in str(error.value)
    assert "private certificate details" not in str(error.value)


@pytest.mark.parametrize(
    ("path", "step"),
    [
        ("/chip", "session creation/renewal"),
        ("/auth/link/123", "library-card verification"),
        ("/chip/sync", "saved-session verification"),
    ],
    ids=["create", "link", "sync"],
)
def test_auth_error_context(path, step):
    client = LibbyClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(403, json={"result": "missing_chip", "token": "private"}),
            )
        )
    )
    with pytest.raises(LibbyAuthenticationError) as error:
        client._request("POST", "https://sentry.libbyapp.com" + path)
    assert step in str(error.value)
    assert "HTTP 403" in str(error.value)
    assert "missing_chip" in str(error.value)
    assert "private" not in str(error.value)


def test_unknown_auth_result():
    client = LibbyClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(403, json={"result": "private-account-value"}),
            )
        )
    )
    with pytest.raises(LibbyAuthenticationError) as error:
        client.sync(Session(identity(), "card-id"))
    assert "private-account-value" not in str(error.value)


@pytest.mark.parametrize(
    "cards", [[], [["puid"]], [["a", "card-1"], ["b", "card-2"]]], ids=["none", "short", "many"]
)
def test_reject_ambiguous_cards(cards):
    responses = [
        {"identity": "primary", "chip": CHIP_ID},
        {},
        {"identity": identity(cards=cards), "chip": CHIP_ID},
    ]
    client = LibbyClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=responses.pop(0)),
            )
        )
    )
    with pytest.raises(LibbyAuthenticationError, match="unambiguous linked card"):
        client.authenticate(LIBRARY, Credentials("number", "pin"))


def test_active_loans(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "config.json", MemorySecrets())
    store.save("phoenix", LIBRARY, Credentials("number", "pin"))
    client = LibbyClient(httpx.Client())
    monkeypatch.setattr(client, "authenticate", lambda *args: Session(identity(), "card-id"))
    monkeypatch.setattr(
        client,
        "sync",
        lambda session: {
            "result": "synchronized",
            "loans": [
                loan(title="Synthetic manga"),
                loan(cardId="other-card"),
                loan(expires=(datetime.now(UTC) - timedelta(days=1)).isoformat()),
            ],
        },
    )
    listed = store.active_loans(client)
    assert len(listed) == 1
    assert listed[0][0].name == "phoenix"
    assert listed[0][1]["title"] == "Synthetic manga"


def test_list_loans(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "config.json", MemorySecrets())
    connection = store.save("phoenix", LIBRARY, Credentials("private-number", "private-pin"))
    monkeypatch.setattr("ucd.auth.libby.system_secret_store", lambda: store.secrets)
    monkeypatch.setattr("ucd.auth.libby.config_path", lambda: store.path)
    monkeypatch.setattr(
        ConnectionStore,
        "active_loans",
        lambda *args, **kwargs: [
            (connection, loan(title="Synthetic manga", token="private-token"))
        ],
    )
    result = CliRunner().invoke(app, ["list-loans", "--service", "libby-overdrive"])
    assert result.exit_code == 0, result.output
    assert "11103570" in result.output
    assert "Synthetic manga" in result.output
    assert "Active loans: 1" in result.output
    for secret in ("private-number", "private-pin", "private-token", "card-id"):
        assert secret not in result.output


def test_api_cookie_isolation():
    requests = []

    def handler(request):
        requests.append(request)
        assert "Cookie" not in request.headers
        return httpx.Response(
            200,
            json={"result": "synchronized", "loans": []},
            headers={"Set-Cookie": "chip=wrong-device; Path=/; Secure"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    http_client.cookies.set("old-device", "private", domain="sentry.libbyapp.com")
    client = LibbyClient(http_client)
    session = Session(identity(), "card-id")
    client.sync(session)
    client.sync(session)
    assert len(requests) == 2
    assert all(r.headers["Authorization"] == f"Bearer {session.identity}" for r in requests)


def test_version_renewal():
    responses = [
        {"identity": "primary", "chip": CHIP_ID},
        {},
        {"identity": identity(), "chip": CHIP_ID},
        {"result": "synchronized", "loans": []},
    ]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=responses.pop(0))

    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(handler)))
    old = Session(identity(), "card-id", "22.0.2")
    renewed = client.authenticate(LIBRARY, Credentials("number", "pin", old))
    assert requests[0].url.path == "/chip"
    assert requests[0].url.params["c"] == "d:22.1.2"
    assert renewed.client_version == "22.1.2"


@pytest.mark.parametrize("second_status", [200, 403])
def test_open_retry(second_status):
    old = Session(identity(), "card-id")
    updated = identity(expiry=time.time() + 7200)
    device = "a1b2c3d4-1234-1234-1234-123456789abc"
    calls = []
    opens = 0
    saved = []

    def handler(request):
        nonlocal opens
        calls.append(request)
        if request.url.path == "/chip/sync":
            return httpx.Response(200, json={"result": "synchronized", "loans": [loan()]})
        if request.url.path == "/chip":
            assert request.headers["Authorization"] == f"Bearer {old.identity}"
            assert request.url.params["v"] == "a1b2c3d4"
            return httpx.Response(200, json={"chip": device, "identity": updated})
        opens += 1
        if opens == 1 or second_status == 403:
            return httpx.Response(403, json={"result": "missing_chip"})
        assert request.headers["Authorization"] == f"Bearer {updated}"
        return httpx.Response(200, json={"message": "ephemeral"})

    client = LibbyClient(httpx.Client(transport=httpx.MockTransport(handler)))
    if second_status == 403:
        with pytest.raises(LibbyAuthenticationError):
            client.open_loan(LIBRARY, old, "11103570", session_renewed=saved.append)
    else:
        assert client.open_loan(LIBRARY, old, "11103570", session_renewed=saved.append) == {
            "message": "ephemeral",
        }
    assert opens == 2
    assert len(saved) == 1 and saved[0].identity == updated
    assert [r.url.path for r in calls].count("/chip") == 1
    assert not any("auth/link" in r.url.path or "clone" in r.url.path for r in calls)
