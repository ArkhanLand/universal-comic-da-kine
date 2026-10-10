# Universal Comic Da Kine

Universal Comic Da Kine (UCD) is for technically inclined collectors who care
about what their electronic books and comics contain, where they came from,
and what changed along the way.

The goal is careful local data storage: immutable originals, useful metadata
with source attribution, reproducible transformations, and translation between
formats without forgetting provenance. UCD complements library organizers such
as Calibre by focusing on normalization, preservation, and conversion. It does
not aim to replace your collection catalog or reading application.

**Pre-1.0, early development.** Marvel Unlimited, fixed-layout Libby OverDrive
Read, and local CBZ inputs export to CBZ with ComicInfo XML and ComicBookInfo
ZIP-comment metadata. Original image bytes and per-fetch records are cached;
CBZ input retains source archives and image bytes by hash. The general
historical repository, transformation framework, and additional input/output
adapters described below are planned, not available features.

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

### Reprocessing existing CBZs

```sh
ucd convert 'Example #1.cbz' --output-dir ./converted
```

Existing ComicInfo.xml or ZIP-comment metadata fills the normalized model;
missing metadata documents are prepared before export. Existing
XML, comments, image bytes, names, and archive metadata are preserved exactly
in the output. Inputs remain untouched. Normalized metadata edits are refused
until an explicit reconciliation policy is available. See
[CBZ input](docs/cbz-input.md) for acceptance, ordering, and metadata limits.

### Libby downloads

Set up your saved library card, list its existing checkouts, then download a
supported fixed-layout OverDrive Read title:

```bash
ucd init --service libby-overdrive
ucd list-loans --service libby-overdrive
ucd download --service libby-overdrive TITLE_ID --output-dir ./comics
```

`--library-card NAME` selects one saved card. UCD never borrows
automatically.
Other Libby formats and unsupported page mappings fail explicitly. Images
retain their exact source bytes; cover and reading order come from the reader
spine and landmarks. RTL does not reverse that order. Libby filenames use the
full title when no explicit issue number is known, preventing different
volumes of the same series from sharing a filename.

Downloads stop on the first failure. `--continue-on-error` processes
subsequent titles and returns a failing exit status if any title failed.
Authentication failures always stop. Existing output files are successful
skips unless `--overwrite` is supplied. CBZs are published only after writing
completes. `--output-format cbz` is the only supported output format.

Downloads show the title once and a compact image progress bar. Libby also
shows measured component-mapping progress before fetching images. Interactive
bars fit the terminal width; redirected output records each phase's start and
completion. The title appears before Libby's preliminary status line,
`Gathering metadata`, which adds a dot per setup milestone. Redirected output
gets one setup summary. Setup commands still report their steps individually.

For network downloads, `--workers N` overlaps up to N image fetches at once
(any positive integer, default 1). Libby also uses that count when fetching
component documents.
Authentication stays sequential, and books are processed in input order. Page
order, original bytes, and the progress display stay the same. Marvel reuses
verified cached images unless `--refresh` is supplied. Use `--workers 1` for a
sequential comparison. Local CBZ imports retain their existing serial flow.

Choose a worker count for the size of the book and your connection:

- For a typical 27–36-page comic, start with `--workers 8`. Our 32-image
  Marvel issue took 13.882s with one worker and 1.191s with eight. Sixteen
  took 1.058s and 32 took 1.220s, so eight delivered nearly all the benefit.
- For a manga around 200 pages, try `--workers 16` or `--workers 32`. Our
  three-volume Libby batch (590 images total) took 116.149s with four,
  57.566s with eight, 37.134s with sixteen, and 24.087s with 32 workers.
  Larger pools substantially improved the whole download, including mapping.
- Compare elapsed time using `time` and keep `--refresh` enabled for network
  comparisons. Use `--overwrite` or separate output folders so existing CBZs
  do not cause a skip. If additional workers stop helping or requests fail,
  reduce the count.

These are starting points from single live runs on an Apple M1 Max MacBook
Pro with 32 GiB RAM. Results depend on network latency, bandwidth, and provider
behavior; CPU core count does not determine the number of concurrent requests.
The default remains one worker, and any positive count can be selected.

Keep concurrency reasonable and respect the provider's usage policies.
Excessive parallel requests to Marvel, Libby, or another service may be treated
as network abuse and lead to throttling, blocked access, or account suspension.
The absence of a worker ceiling is not permission to overwhelm a service.

`ucd cache where` displays the shared cache root. Use `--cache-dir PATH` or
`UCD_CACHE_DIR` to override it. Defaults are `~/Library/Caches/ucd` on macOS,
`%LOCALAPPDATA%/ucd` on Windows, and `${XDG_CACHE_HOME:-~/.cache}/ucd` on
Linux. Title IDs establish Libby cache identity; readable titles are recorded
in capture manifests. Credentials remain in the system credential store.

Libby presently fetches images on each acquisition until rendition/path
stability across sessions is verified. SHA-256 still deduplicates identical
bytes. Each fetch has its own timestamped receipt, and capture manifests
record associations and completeness. A failed acquisition retains verified
objects and receipts. `--refresh` never permits replacing an existing CBZ; use
`--overwrite` for that. The old `~/.local/share/ucd/marvel` cache is left
untouched; it is not migrated automatically to the shared root.

### Reusing Marvel downloads

Marvel images are retained under the shared cache root, in `marvel-unlimited`,
and reused by digital issue ID, page ID, and source rendition, even when
delivery URLs change. Metadata and the page manifest are fetched on each
import. Cached bytes are checked against their SHA-256 hash before reuse.

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

CLF remains a theoretical term for the comic-like category/model. The Python
class is `Publication`, and input adapters return it through
`get_publication()`. This is not a persistent "CLF file." See the
[roadmap](docs/roadmap.md) for migration notes and remaining architecture work.

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

## Development process

UCD is developed with assistance from AI coding tools, including ChatGPT and
Codex. They are used for tasks such as implementation, refactoring, testing,
documentation, research, and code review.

Architecture, requirements, data-model decisions, compatibility constraints,
and acceptance of changes remain the maintainer's responsibility. AI-assisted
changes are reviewed, tested, and revised as part of the normal development
process.

Wrap Markdown prose, comments, and docstrings at 78 columns, including comment
markers. Preserve code blocks, tables, and unbreakable URLs/identifiers when
wrapping would change meaning. Python code uses the configured 100 columns.

Keep verbose pytest result lines within 80 columns, counting the module path,
test name, parameter ID, status, and progress suffix. Use short, descriptive
parameter IDs rather than automatically generated source snippets or URLs.
