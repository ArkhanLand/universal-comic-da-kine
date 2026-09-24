# Universal Comic Da Kine

Universal Comic Da Kine (UCD) is a toolkit for working with
**comic-like files (CLFs)**: publications whose primary content consists
of images representing pages.

UCD aims to provide a format-independent model for ingesting,
manipulating, preserving, and exporting comic-style publications.

## Goals

UCD should be able to:

- Read CBZ, PDF, EPUB, image directories, and other image-page formats.
- Acquire publications from supported online reader services.
- Normalize these sources into a common internal CLF representation.
- Preserve imported image bytes exactly and persist imported publications.
- Inspect and edit publication metadata.
- Reorder, insert, remove, or truncate pages without rewriting page images.
- Crop, rotate, resize, optimize, or otherwise process page images while retaining originals.
- Split and combine publications.
- Export the resulting publication to supported formats such as CBZ,
  PDF, EPUB, or image sets.

## Architecture

UCD treats file formats and online services as adapters around a common
Comic-Like File model:

    Input Adapter -> Persistent CLF -> Transformations -> Output Adapter

An input adapter completely ingests its source before returning a CLF.
The resulting publication and its image objects are stored persistently,
so downstream transformations and output adapters do not need to know
where the publication originally came from.

Image data is kept in an immutable, content-addressed object store. A CLF
has a stable publication identity and immutable revisions; edits create new
revisions rather than destroying previous state.

See [docs/design.md](docs/design.md) for the current core design decisions
and storage invariants.

## Reusing Marvel downloads

Marvel image downloads are retained under `~/.local/share/ucd/marvel` and
reused by digital issue ID, page ID, and source rendition, even when delivery
URLs change. Metadata and the page manifest are still fetched on each import.
Cached image bytes are checked against their SHA-256 hash before reuse.

Use `ucd download SOURCE --refresh` to fetch fresh images. Add `--overwrite`
if the destination CBZ already exists; `--overwrite` alone still reuses cached
images. Refresh is needed for replacements or rendition changes that retain
the same page ID, because Marvel's observed asset responses provide no
revision marker. Older original bytes and acquisition records remain stored
after refresh.

Library callers can pass `cache_dir=Path(...)` and `refresh=True` to
`MarvelUnlimitedAdapter`. Existing `/tmp/ucd/marvel` downloads are not
migrated automatically because they lack verified identity mappings and fetch
records.

## Style conventions

Wrap prose comments, docstrings, and Markdown prose at 78 columns, including
indentation and comment markers. Preserve code blocks, tables, and unbreakable
URLs or identifiers when wrapping would change their meaning. Python code
continues to use the 100-column limit configured in `pyproject.toml`.

## Status

Early development.
