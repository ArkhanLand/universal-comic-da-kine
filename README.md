# Universal Comic Da Kine

Universal Comic Da Kine (UCD) is for technically inclined collectors who care
about what their electronic books and comics contain, where they came from,
and what changed along the way.

The goal is careful local data storage: immutable originals, useful metadata
with source attribution, reproducible transformations, and translation between
formats without forgetting provenance. UCD complements library organizers such
as Calibre by focusing on normalization, preservation, and conversion. It does
not aim to replace your collection catalog or reading application.

**Pre-1.0, early development.** The implemented path is Marvel Unlimited
acquisition to CBZ, with ComicInfo XML and ComicBookInfo ZIP-comment metadata.
Marvel image bytes and per-fetch records are retained in a persistent cache.
The general historical repository, transformation framework, and additional
input/output adapters described below are planned, not available features.

## What counts as a publication?

A UCD-native publication is an ordered sequence of static visual reading
units/pages. Read one at a time, in small groups/spreads, or by scrolling a
static work: the reader controls duration and progression. There is no
intrinsic playback clock. A work requiring time to be modeled is outside the
native model.

Comic image archives, discrete static PDF pages, fixed-layout EPUB, and static
webcomics fit in principle. Reflowable EPUB is not inherently native; neither
are animation, video, audio, or timed/interactive media. Converting these to
static form must be explicit and recorded as semantically lossy. An arbitrary
ZIP is not automatically a comic, and an image is an asset before it is a
publication. See [source acceptance](docs/source-acceptance.md) for details
and the distinction between native categories and implemented adapters.

## Try the current implementation

Requires Python 3.12 or newer. From a checkout:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
ucd download --help
ucd download 'https://www.marvel.com/comics/issue/72984' --cookies cookies.txt
```

The Marvel adapter reads a Netscape-format cookie file for an authorized
Marvel session; access depends on the account and source availability.
`--output-dir` selects the destination. Multiple source arguments run in
sequence; `--quit-on-error` stops on handled failures. Bifrost metadata/assets
404 responses report an unavailable digital edition
without a traceback; other unexpected HTTP failures retain their existing
behavior.

### Reusing Marvel downloads

Marvel images are retained under `~/.local/share/ucd/marvel` and reused by
digital issue ID, page ID, and source rendition, even when delivery URLs
change. Metadata and the page manifest are fetched on each import. Cached
bytes are checked against their SHA-256 hash before reuse.

Use `ucd download SOURCE --refresh` to fetch fresh images. Add `--overwrite`
if the destination CBZ already exists; `--overwrite` alone still reuses cached
images. Refresh is needed for replacements or rendition changes that retain
the same page ID, because observed asset responses provide no revision marker.
Older original bytes and acquisition records remain stored after refresh.

Library callers can pass `cache_dir=Path(...)` and `refresh=True` to
`MarvelUnlimitedAdapter`. Existing `/tmp/ucd/marvel` downloads are not
migrated automatically because they lack verified identity mappings and fetch
records.

## Architecture and direction

The agreed architecture uses one normalized in-memory **Publication**:

```text
Input adapters -> Publication -> Transformations -> Output adapters
                       |
                  UCD Repository
               history + object storage
```

CLF remains a theoretical term for the comic-like category/model. The current
Python class and method are still `CLF` and `get_clf()`; their immediate
migration to `Publication` and `get_publication()` is documented in the
[roadmap](docs/roadmap.md). This is not a persistent "CLF file."

In the target design, immutable objects are identified by hashes, independent
of filenames and formats. Source bytes remain intact; derived assets record
how they were made. The UCD Repository keeps metadata and history by default,
even for ephemeral conversions. Keeping bytes is a separate policy: a known
hash and its history can survive after temporary local bytes are discarded.
These general repository guarantees are not yet implemented by the cache.

Input and output modules communicate through Publication, avoiding a separate
converter for every pair of formats. The [adapter guide](docs/adapters.md)
explains today's extension points and the intended contract. The
[core design](docs/design.md) covers objects, assets, pages, presentation,
provenance, retention, PDF normalization, and metadata limitations.

## Toward public testing

Before inviting broader test use, we want a useful set of inputs and outputs,
a documented and tested module interface, representative fixtures, and clear
preservation/loss reports. There is no promise of all-format support or stable
pre-1.0 APIs. The [roadmap](docs/roadmap.md) connects this work to existing
issues and identifies the architecture migrations still needed.

Contributions can help establish fixture-based round trips, source acceptance
rules, and metadata fidelity as well as add adapters. Run `make check` with
the development dependencies installed; live-service tests are separate (`make
test-network`) and require suitable access.

Wrap Markdown prose, comments, and docstrings at 78 columns, including comment
markers. Preserve code blocks, tables, and unbreakable URLs/identifiers when
wrapping would change meaning. Python code uses the configured 100 columns.
