# Architecture migration and public-testing readiness

The project name remains Universal Comic Da Kine / UCD. This roadmap records
agreed architecture, not completed functionality or a release schedule.

## Immediate model/API migration

Rename the in-memory `ucd.models.CLF` class to `Publication`, and coordinate
`InputAdapter.get_clf()` with a `get_publication()` API. The current name
spans the abstract contract, Marvel adapter, CLI, output
annotations/serializers, fixture factories, and mocked adapters. A blind
documentation-driven code replacement could break library callers and
third-party subclasses.

This documentation change deliberately leaves executable names unchanged. The
immediate follow-up should choose and document either a compatibility
alias/wrapper period or a pre-1.0 breaking migration, update all internal
callers together, and run `make check`. Verify metadata, image-byte output,
callbacks, and cache behavior remain unchanged. If aliases are chosen, test
both entry points and subclass behavior. There is no persisted CLF serializer
to migrate today; do not rename existing cache data or hashes for this change.

Keep this bounded name migration separate from implementing Object/Asset,
logical Page/View separation, hash-based model references, and the repository.
The current `Page` combines a local path, image properties, and zero/one/many/
unknown logical mappings; retain those established semantics during migration.

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
target Python name. This file records scope corrections without rewriting
issue history or claiming their acceptance criteria have been implemented.

| Issue | Alignment or required follow-up |
| --- | --- |
| [#5][i5] transformations | Replace "persistent-CLF" framing with in-memory Publication plus repository history; record semantic losses and independent retention. |
| [#6][i6] PDF input | Native static pages; extraction only when complete appearance is preserved, otherwise render with provenance. |
| [#7][i7] EPUB input | Narrow native acceptance to static fixed-layout EPUB; reflowable/timed content requires explicit lossy conversion or rejection. |
| [#8][i8] logical pages | Preserve implemented mappings and cover-excluded count; distinguish logical Page from Asset and optional View. |
| [#13][i13] CBZ input | Preserve source container and assets; random ZIP is not CBZ. Keep the specified ComicInfo-first metadata precedence. |
| [#14][i14] Marvel reuse | Existing cache implements image reuse; general repository/history/retention remain separate work. |
| [#15][i15] unavailable editions | Implemented: Bifrost metadata/assets 404s report source unavailability; batches continue unless explicitly stopped. |

Issues #1–4 and #9–12 cover acquisition, HTTP policy, naming, progress, and
metadata projections. Those concerns remain compatible with this design;
filename projections and export metadata must not become object identity or
replace authoritative source knowledge. No remote issue bodies were edited as
part of this documentation change.

## Gate for broader public testing

- A useful tested input/output set beyond the current Marvel-to-CBZ path,
  including fixture-based round trips and documented capability limits.
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
