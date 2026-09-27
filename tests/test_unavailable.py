import httpx
import pytest
from typer.testing import CliRunner

from tests.helpers import make_test_publication
from ucd.cli import app
from ucd.exceptions import ComicDownloaderError, UnavailableError
from ucd.input.marvel_unlimited import MarvelUnlimitedAdapter


@pytest.mark.parametrize(
    ("method", "endpoint"),
    [("get_metadata", "metadata"), ("get_page_sources", "assets")],
)
@pytest.mark.parametrize("status", [404, 401, 403, 429, 500])
def test_bifrost_unavailability(method, endpoint, status):
    def handler(request):
        assert str(request.url) == (
            f"https://bifrost.marvel.com/v1/catalog/digital-comics/{endpoint}/51975"
        )
        # Error bodies need not be valid JSON.
        return httpx.Response(status, text="Unavailable")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = MarvelUnlimitedAdapter(client=client)
        if status == 404:
            with pytest.raises(
                UnavailableError, match="No downloadable digital edition.*51975"
            ) as exc:
                getattr(adapter, method)("51975")
            assert isinstance(exc.value, ComicDownloaderError)
        else:
            with pytest.raises(httpx.HTTPStatusError) as exc:
                getattr(adapter, method)("51975")
            assert exc.value.response.status_code == status


def test_catalog_404_is_not_digital_edition_unavailability():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
        adapter = MarvelUnlimitedAdapter(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            adapter.get_issue_data("72984")


@pytest.mark.parametrize("quit_on_error", [False, True])
def test_cli_unavailable_batch(tmp_path, monkeypatch, quit_on_error):
    calls = []
    cleaned = []
    publication = make_test_publication(tmp_path)

    class Adapter:
        def __init__(self, **kwargs):
            pass

        def get_publication(self, source, progress, metadata_ready):
            calls.append(source)
            if source == "1":
                raise UnavailableError("No downloadable digital edition is available for ID 1")
            metadata_ready(publication.title, publication.series, publication.issue_number)
            return publication

        def cleanup(self):
            cleaned.append(True)

    monkeypatch.setattr("ucd.cli.MarvelUnlimitedAdapter", Adapter)
    output = tmp_path / "output"
    args = ["download", "1", "2", "--output-dir", str(output)]
    if quit_on_error:
        args.append("--quit-on-error")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Error: No downloadable digital edition" in result.output
    assert "Traceback" not in result.output
    assert calls == (["1"] if quit_on_error else ["1", "2"])
    assert bool(list(output.glob("*.cbz"))) is not quit_on_error
    assert cleaned == [True]
