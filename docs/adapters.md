# Input and output modules

This guide separates working extension points from the intended public
interface. There is no stable plugin API or automatic plugin discovery yet.

## Current implementation

`src/ucd/input/base.py` defines `InputAdapter` with `name`,
`matches_url(url)`, and:

```python
get_publication(source, progress=None, metadata_ready=None) -> Publication
```

The progress callback takes `(label, completed, total)`; the metadata callback
takes `(title, series, issue_number)`. Adapters must not depend on the CLI UI.

`MarvelUnlimitedAdapter` acquires service metadata and
images, returns a complete model with local `Page.path` references, and offers
`cleanup()` for its scratch state. Cleanup is not part of the base interface.
The returned Publication has separate `cover: Page` and
`narrative: tuple[Page, ...]` fields. Iterate the narrative directly; the cover
is not one of its entries. Counts and logical-mapping validation belong to
Publication. The adapter's intermediate `get_pages()` result still uses
`Pages` during acquisition; it is unpacked when constructing Publication.

Marvel and Libby use `download.parallel.fetch_ordered()` for bounded network
concurrency, selected by `download --workers N` (positive integer, default 1).
The helper prefetches a limited window and delivers results in source order.
Marvel checks its verified cache before scheduling network work, then
publishes new receipts and assembles pages on the consumer thread. Refresh
bypasses reuse. Libby uses the same helper for component documents and images;
only Libby needs the Mapping phase. Authentication and metadata dependencies
remain sequential. Original image bytes, acquisition timestamps, and page
order retain their existing meanings. Synthetic tests cover overlap, cache
reuse, refresh, and failure recovery.

On 2026-10-09, fresh Marvel downloads of *Doctor Strange Annual (2016) #1*
(32 images) were measured at several worker counts. Each command used
`--refresh --overwrite`, so cache reuse did not replace network acquisition.

| Workers | Elapsed (s) | User CPU (s) | System CPU (s) |
| ---: | ---: | ---: | ---: |
| 1 | 13.882 | 1.282 | 0.270 |
| 4 | 4.152 | 0.934 | 0.178 |
| 8 | 1.191 | 0.629 | 0.147 |
| 16 | 1.058 | 0.675 | 0.211 |
| 32 | 1.220 | 0.731 | 0.308 |

The one-/32-worker archive pair and the later four-/eight-worker pair passed
ZIP integrity checks; all image bytes and archive member order matched
exactly within each pair. The sixteen-worker output was overwritten before
independent comparison. The test used an Apple
M1 Max MacBook Pro with 10 CPU cores (8 performance, 2 efficiency), 32 GiB RAM,
and macOS 26.7.1 (build `25G241`). Eight workers delivered nearly all of the
observed improvement for this issue, while sixteen was slightly quicker and
32 provided no additional gain. These are single live observations, not
repeated controlled benchmarks; differences around a tenth of a second do
not establish an optimal worker count. Renewed browser-exported Marvel cookies
were required before the test succeeded.

`CBZInputAdapter` implements the same contract for local archives. It returns
persistent cached paths, a `Publication.source_representation` carrying the
exact archive, and an optional `Publication.comic_metadata` attachment holding
prepared ComicInfo and ComicBookInfo bytes with provenance. No scratch
cleanup is needed. The legacy
`matches_url` hook recognizes local `.cbz` paths for this adapter. See
[CBZ input](cbz-input.md) for metadata precedence and supported projections.

The CLI routes `download` to Marvel or Libby and `convert` to CBZ input;
implementing a subclass alone does not register it or route new sources.

`src/ucd/output/cbz.py` supplies `write_cbz(publication, destination,
overwrite=False)` (with `overwrite` keyword-only). Outputs are functions, not
subclasses of an output base class. Shared helpers in `ucd.metadata` prepare
ComicInfo and ComicBookInfo documents before output. CBZ input calls
`prepare_comic_metadata()` to preserve supplied documents and generate missing
ones; the Marvel download pipeline calls it after acquisition. Metadata
preparation is separate from archive writing and reusable by other pipelines.
The optional `Publication.comic_metadata` payload is not universal metadata;
other outputs primarily consume normalized fields and can use attached native
documents where appropriate.

`write_cbz()` requires prepared documents and writes them without parsing
their fields or choosing their sources. CBZ sources retain original member
records, XML, and comments; only missing prepared documents are added.
Imported model edits require explicit reconciliation and currently fail. For a
new Publication, prepare metadata after constructing the final normalized
state. Changes after preparation are rejected as stale; explicitly clear
`comic_metadata` before preparing an edited new publication. No compatibility
aliases remain at the old `ucd.output.comicinfo` and
`ucd.output.comicbookinfo` module paths.

Raw source metadata is preserved independently of what the normalized model
can represent. Generated projections cannot encode every logical mapping,
first-page-side value, or arbitrary spread extent. Full and partial publication
dates retain their known precision; no day is invented for a year/month date.

For an experimental module today, implement against these actual APIs, add
explicit routing if needed, and use mocked acquisition and local fixture
tests. See `tests/test_marvel.py`, `tests/test_marvel_cache.py`,
`tests/test_pages.py`, and `tests/test_cbz.py`. Do not assume planned
repository objects exist.

## Initial Libby connection support

Authentication, loan listing, and inspection were merged in PR #41. The
`feat/libby-download` branch adds fixed-layout image acquisition and CBZ
routing; a live download of title `11103570` also passed on 2026-10-09.

`ucd init --service libby-overdrive` now provides the initial setup path:
resolve the library key, prompt privately for a card number and PIN, verify
service authentication, and save credentials/session tokens in a supported
system credential store through `keyring`. `--library-card <name>` names or
updates a connection; without it, the library key is the default name and a
duplicate requires a distinct name. Ordered, versioned non-secret connection
configuration is separate from the image cache.

`ucd.auth.libby` implements direct device creation, card linking,
session verification/renewal, active-loan lookup across saved cards, and
OverDrive Read passport requests. Missing or expired loans are rejected;
credential rejection stops card lookup and instructs the user to rerun setup.
The observed client version is explicit and service upgrades require review.
The web gateway `sentry.libbyapp.com` is used instead of the legacy
`sentry-read.svc.overdrive.com` host, whose certificate failed hostname
verification during live setup. TLS verification is retained, and raw service
errors or credential-bearing URLs are not exposed in diagnostic messages.
See [third-party notices](../THIRD_PARTY_NOTICES.md).

The official client's Sentry request transform supplies a required,
token-derived `Accept-Language` header for `POST /chip`. Ordinary language
headers could produce a token that listed loans but could not open them.
The implemented transform and the complete observed sequence are documented
in [the verified browser-free protocol](libby-overdrive-read.md#verified-browser-free-protocol).
Device cloning, blessing transfers, and the experimental
`UCD_LIBBY_AUTH_MODE` setup switch were removed; they are not part of the
current card-number/PIN setup path.

The CLI includes `ucd list-loans --service libby-overdrive`, intended to list
active checkouts in saved-card preference order with title IDs, readable card
names, and delivery formats. Command registration/help and synthetic tests
have been checked. A live check on 2026-10-09 listed three active checkouts on
the selected Phoenix connection; this command was merged in PR #41. It omits
expired loans and credentials. `--library-card <name>` restricts the lookup to
one saved connection. Authentication failures stop the whole command.

`UCD_DEV_COMMANDS=1 ucd inspect-loan --service libby-overdrive <title-id>` is
a development/testing diagnostic, registered only when `UCD_DEV_COMMANDS` is
exactly `1` at process startup. Otherwise it is absent from both help and
command dispatch. Normal downloads will perform these checks internally; users
do not need a separate inspection step. `list-loans` is registered without the
development flag.

The inspection command exercises saved-card lookup and passport acquisition,
establishes a separate read-host cookie jar, and decodes the embedded openbook
without executing service JavaScript. The live comic reader passed its encoded
string array as an argument to a function that assigns `window.eData`, rather
than assigning the array directly. The parser recognizes that observed wrapper
as well as direct arrays; it rejects expressions and inconsistent bindings.
It validates fixed layout, cover landmarks, and explicit linearity, then
reports only counts and reading direction. It prints no tokens, signed URLs,
or raw loan/openbook payloads and downloads no images.
`--library-card <name>` selects a saved connection explicitly.

The official `dewey-22.1.2/src/main.js` Sentry `_requestWithChip` handler
responds to `missing_chip` by renewing the existing device and retrying once.
UCD mirrors this bounded recovery at loan opening, verifies that renewal
retains the device and selected card, and persists the replacement identity
in the credential store. A repeated rejection remains a hard failure. This
does not relink the card, create another device, or prompt for credentials.
The corrected chip header is required on this renewal too. The private `prbn`
claim's meaning remains unknown; its absence is not an invalid identity.

A live UCD inspection passed on 2026-10-09 for title `11103570`, reporting
193 spine components, one non-linear cover, 192 linear narrative components,
and `rtl` progression. The check verified saved-session renewal, loan opening,
reader access, envelope decoding, and rendition validation. A subsequent fresh
`ucd init --service libby-overdrive --library-card phoenix` followed by
inspection also passed, verifying device creation, card/PIN linking, renewal,
credential storage, and reader inspection without reusing the repaired session. These results supplement the
synthetic authentication and reader tests; they do not prove complete Libby
download support.

`LibbyOverDriveReadAdapter` verifies current checkouts, fetches catalog and
reader metadata, maps the supported page elements through their stylesheets,
and returns a complete Publication with original image bytes. API and reader
clients have separate cookie jars. Documents, stylesheets, and component bases
stay on the validated HTTPS reader origin. Images start there but may redirect
to the exact HTTPS host `odrresources.cachefly.net`; CDN requests carry neither
Authorization nor Cookie headers. Other destinations remain rejected.

The mapper accepts a single direct XHTML image, including publisher-named
components without element IDs. The CSS-background path uses a restricted
parser accepting plain ID selectors and single background URLs. Ambiguous
mappings and conditional page-image rules fail explicitly; it does not
implement a browser cascade. The component body decoder accepts the observed
`__bif_cfc1` string transformation without executing JavaScript.

Libby and Marvel share the existing measured pagination heuristic. Explicit
reading direction and first-page position come from reader metadata; unknown
positions remain unknown. Catalog series positions do not become issue or
volume numbers. Catalog/BIF source projections and versioned acquisition
manifests are kept separately from normalized Publication fields. Reader
secrets and signed URLs are excluded. Per-fetch receipts retain UTC
timestamps.

The shared platform cache is discoverable with `ucd cache where`. Libby does
not yet save network requests through identifier-based reuse: originals are
fetched again and deduplicated by their verified SHA-256 bytes until stability
is demonstrated. Marvel retains its verified lookup reuse policy. Marvel setup
is not implemented. See [the retrieval design](libby-overdrive-read.md) for
protocol details and the remaining live-validation work.

## Target contract

The Python API uses `Publication` and `get_publication()` throughout the
abstract adapter, Marvel implementation, CLI, and output functions. The former
names have been removed without compatibility aliases; see the
[migration notes](roadmap.md). The broader repository contract remains planned.

The eventual input contract must:

1. Identify source capabilities and apply the [acceptance policy][acceptance].
2. Acquire all required source objects/assets and useful metadata.
3. Preserve exact originals; explicitly derive static assets when needed.
4. Record source associations, hashes, properties, provenance, and losses.
5. Construct normalized reading order, logical mappings, cover designation,
   and known reading direction without inventing missing information.
6. Return a complete Publication with generic object references/resolution.
7. Record history by default and apply the independently chosen byte-retention
   policy, including for ephemeral conversions.

Outputs consume only Publication plus generic object access, never input
adapters or source-specific parsing. Transformations operate at the same
boundary, creating new assets/revisions and provenance. Report what an output
can preserve, encode through extensions, or cannot represent. Hash identity
must not depend on generated output names or file suffixes.

The signatures for repository injection, options, capability discovery,
retention, cleanup, error reporting, and loss reporting are still to be
designed. A registry, versioned module contract, and public examples must be
implemented and tested before this becomes a public extension promise.

## Validation expected before public testing

Exercise acceptance/rejection, deterministic order, cover/spread mappings,
exact source bytes, unknown/conflicting metadata, and explicit loss behavior.
Round trips should compare supported semantics and asset hashes rather than
require byte-identical output containers. Include missing objects, failed
acquisition, history-disabled operations, ephemeral cleanup, and reconnection
when those repository capabilities are implemented. Keep live-service tests
separate from deterministic offline checks.

[acceptance]: source-acceptance.md
