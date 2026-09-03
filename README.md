# comic-downloader

A modular Python framework for downloading comics from supported services,
caching source pages, and packaging them as CBZ files with `ComicInfo.xml`.

This initial commit intentionally contains no service-specific implementation.
Service adapters are isolated from downloading, caching, and output generation.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

ruff check .
ruff format --check .
mypy src
pytest
```

## Architecture

```text
URL
 |
 v
Service adapter
 |
 v
Comic + Page models
 |
 +--> Downloader --> persistent page cache
 |
 +--> ComicInfo.xml
 |
 v
CBZ writer
```

A provider implementation derives from `ComicService`, resolves a URL, and
normalizes provider-specific data into the shared `Comic` and `Page` models.
It should not write files or build archives.

Cache keys are `service/service_id`. Archive overwrite and source-page
redownload are deliberately separate operations.

Status: project skeleton only; no remote service is implemented yet.
