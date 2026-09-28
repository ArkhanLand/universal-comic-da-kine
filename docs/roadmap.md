# Architecture migration and public-testing readiness

The project name remains Universal Comic Da Kine / UCD. This roadmap records
agreed architecture, not completed functionality or a release schedule.

## Model/API naming migration

The in-memory class is now `ucd.models.Publication`, and input adapters use
`InputAdapter.get_publication()`. The abstract contract, Marvel adapter, CLI,
output functions, fixture factories, and mocked adapters use the new names.

This is a deliberate pre-beta breaking API change. Update imports of `CLF`
to `Publication`, calls/overrides of `get_clf()` to `get_publication()`, and
output keyword arguments from `clf=` to `publication=`. The internal Marvel
builder is now `_build_publication()`. There are no compatibility aliases or
deprecation wrappers; API compatibility is not promised before public beta.

The old reading-content wrapper is unpacked into `Publication.cover: Page`
and `Publication.narrative: tuple[Page, ...]`. Update constructors and
`dataclasses.replace` calls accordingly. Iterate `publication.narrative`
directly; the cover remains separate. Counts (`logical_page_count` and
`interior_image_count`) and reading-content validation now belong to
Publication. There is no `Publication.pages` compatibility accessor.
The intermediate `Pages` acquisition result is not stored in Publication.

These changes preserve logical pagination, metadata projections,
callbacks, image bytes, and cache behavior. No persisted CLF
serializer exists to migrate; existing cache data and hashes are unchanged.
API compatibility policy does not relax preservation of existing stored data.

Object/Asset implementation, logical Page/View separation, hash-based model
references, and the general repository remain separate work. The current
`Page` still combines a local path, image properties, and logical mappings
that can be zero, one, many, or unknown.

## Repository and preservation work

Implement a service-neutral UCD Repository with versioned authoritative
records, stable publication IDs, immutable revisions, and generic object
resolution. Preserve raw/unknown metadata and source associations as well as
normalized fields. Record tools, versions, parameters, and loss for
derivations.

Add independent history and retention policies: default history for both
ingest and ephemeral conversion, explicit history-disable, retained ingest
originals, temporary-object cleanup, known-but-absent object records,
hash-based reconnection, and garbage collection that does not forget history.
The Marvel cache supplies acquisition records and verified hashes, not these
guarantees.

## Reconciliation with existing issues

The issue bodies were reviewed while preparing this design. Their historical
uses of CLF should be read as the normalized model, with Publication the
Python name. This file records scope corrections without rewriting
issue history or claiming their acceptance criteria have been implemented.

| Issue | Alignment or required follow-up |
| --- | --- |
| [#5][i5] transformations | Replace "persistent-CLF" framing with in-memory Publication plus repository history; record semantic losses and independent retention. |
| [#6][i6] PDF input | Native static pages; extraction only when complete appearance is preserved, otherwise render with provenance. |
| [#7][i7] EPUB input | Narrow native acceptance to static fixed-layout EPUB; reflowable/timed content requires explicit lossy conversion or rejection. |
| [#8][i8] logical pages | Preserve implemented mappings and cover-excluded count; distinguish logical Page from Asset and optional View. |
| [#13][i13] CBZ input | Initial local adapter implemented with source retention, ComicInfo-first precedence, and offline round trips; see [limits](cbz-input.md). |
| [#14][i14] Marvel reuse | Existing cache implements image reuse; general repository/history/retention remain separate work. |
| [#15][i15] unavailable editions | Implemented: Bifrost metadata/assets 404s report source unavailability; batches continue unless explicitly stopped. |

Issues #1–4 and #9–12 cover acquisition, HTTP policy, naming, progress, and
metadata projections. Those concerns remain compatible with this design;
filename projections and export metadata must not become object identity or
replace authoritative source knowledge. No remote issue bodies were edited as
part of this documentation change.

## Gate for broader public testing

- A useful tested input/output set: Marvel and local CBZ inputs now export
  CBZ, including fixture-based round trips and documented capability limits.
  Assess whether this set is sufficient for the first public alpha.
- A documented adapter/module interface with working examples, explicit
  integration/discovery, lifecycle, error, and option contracts.
- Clear source acceptance, metadata fidelity, preservation, and loss reports.
- Repository/history and retention behavior tested against the guarantees
  advertised for that release, plus migration guidance and local setup docs.
- Reliable failures for unavailable sources, missing objects, and unsupported
  content, without requiring live accounts for the offline test suite.

This is a readiness checklist, not a promise that every native category will
have an adapter at the first public test release.

[i5]: https://github.com/ArkhanLand/universal-comic-da-kine/issues/5
[i6]: https://github.com/ArkhanLand/universal-comic-da-kine/issues/6
[i7]: https://github.com/ArkhanLand/universal-comic-da-kine/issues/7
[i8]: https://github.com/ArkhanLand/universal-comic-da-kine/issues/8
[i13]: https://github.com/ArkhanLand/universal-comic-da-kine/issues/13
[i14]: https://github.com/ArkhanLand/universal-comic-da-kine/issues/14
[i15]: https://github.com/ArkhanLand/universal-comic-da-kine/issues/15
