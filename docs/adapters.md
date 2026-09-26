# Input and output modules

This guide separates working extension points from the intended public
interface. There is no stable plugin API or automatic plugin discovery yet.

## Current implementation

`src/ucd/input/base.py` defines `InputAdapter` with `name`,
`matches_url(url)`, and `get_clf(source, progress=None, metadata_ready=None)
-> CLF`. The progress callback takes `(label, completed, total)`; the metadata
callback takes `(title, series, issue_number)`. Adapters must not depend on
the CLI UI.

`MarvelUnlimitedAdapter` is the sole concrete input. It acquires metadata and
images, returns a complete model with local `Page.path` references, and offers
`cleanup()` for its scratch state. Cleanup is not part of the base interface.
The CLI directly constructs this adapter; implementing a subclass alone does
not register it or route new source types to it.

`src/ucd/output/cbz.py` supplies `write_cbz(clf, destination,
overwrite=False)` (with `overwrite` keyword-only). Outputs are functions, not
subclasses of an output base class. ComicInfo and ComicBookInfo serializers
project supported model metadata; CBZ writes source image bytes without
re-encoding. Existing metadata mappings cannot round-trip all source fields,
logical mappings, first-page-side information, or arbitrary spread extents.

For an experimental module today, implement against these actual APIs, add
explicit routing if needed, and use mocked acquisition and local fixture
tests. See `tests/test_marvel.py`, `tests/test_marvel_cache.py`,
`tests/test_pages.py`, and `tests/test_cbz.py`. Do not assume planned
repository objects exist.

## Target contract

The immediate name migration is `CLF` to `Publication` and `get_clf()` to
`get_publication()`. [Migration work](roadmap.md) must update the abstract
method, concrete adapters, CLI, serializers, test factories, and mocks
together.

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
