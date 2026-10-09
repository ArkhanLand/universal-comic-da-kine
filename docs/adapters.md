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

`CBZInputAdapter` implements the same contract for local archives. It returns
persistent cached paths, a `Publication.source_representation` carrying the
exact archive, and an optional `Publication.comic_metadata` attachment holding
prepared ComicInfo and ComicBookInfo bytes with provenance. No scratch
cleanup is needed. The legacy
`matches_url` hook recognizes local `.cbz` paths for this adapter. See
[CBZ input](cbz-input.md) for metadata precedence and supported projections.

The CLI explicitly routes `download` to Marvel and `convert` to CBZ input;
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

This section describes the unmerged prototype in the
`feat/libby-authentication` working checkout as of 2026-10-09. These commands
are not yet available from the main checkout or a released UCD installation.
The live validation status of each path is noted below.

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

The working-branch prototype includes
`ucd list-loans --service libby-overdrive`, intended to list active checkouts
in saved-card preference order with title IDs, readable card names, and delivery
formats. Command registration/help and synthetic tests have been checked. A live check
on 2026-10-09 listed three active checkouts on the selected Phoenix connection;
this command is not yet merged.
It omits expired loans and credentials. `--library-card <name>` restricts the
lookup to one saved connection. Authentication failures stop the whole command.

`UCD_DEV_COMMANDS=1 ucd inspect-loan --service libby-overdrive <title-id>`
is a development/testing diagnostic, registered only when `UCD_DEV_COMMANDS`
is exactly `1` at process startup. Otherwise it is absent from both help and
command dispatch. Normal downloads will perform these checks internally;
users do not need a separate inspection step. In the prototype, `list-loans` is registered
without the development flag.

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

CSS image mapping, exact-byte image acquisition, catalog metadata integration,
cache reuse, normalization, and download routing still need implementation or
verification. The `download` command continues to use Marvel. Marvel setup is
also not implemented. See [the Libby retrieval design](libby-overdrive-read.md)
for the agreed target and the exact protocol reproduction steps.

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
