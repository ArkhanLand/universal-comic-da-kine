# Changes

This file records user-visible changes by release, newest first. Git history
records individual commits; pull requests contain their reviewed descriptions.
The unreleased section is a living draft for the first public alpha, not an
announcement that a release has shipped.

## Unreleased — first public alpha

Version and release date are not yet assigned. The package currently declares
`0.1.0`; that development value does not establish a public alpha release.
The feature list below describes what is implemented today. Add completed
features as work lands, and finalize this section against the release commit.

### Available in the current implementation

- Acquire individual Marvel Unlimited issues from supported issue URLs or
  catalog IDs, using an authorized session's Netscape-format cookie file.
- Export CBZ archives with original image bytes, an explicitly separate cover,
  ordered narrative images, ComicInfo XML, and ComicBookInfo ZIP-comment
  metadata. Exported metadata is a supported projection, not a complete
  round-trip representation of all source information.
- Convert local static-image CBZs through Publication, preparing missing
  ComicBookInfo ZIP comments from ComicInfo.xml and missing XML from comments.
  Preserve image bytes, use natural filename order for normalization, and
  honor cover/spread hints. Preserve original XML bytes, namespace
  declarations, unknown fields, existing ZIP comments, member names, order,
  and archive metadata in output. Shared metadata helpers attach prepared
  documents to Publication; the CBZ writer only packages them. Retain partial
  dates without inventing a day. Refuse stale or corrupt source replay. Retain
  source archives and images by hash. The `convert` command refuses to
  overwrite inputs and publishes only complete outputs.
- Retain original Marvel image bytes in a persistent cache with SHA-256
  verification and per-fetch acquisition records. Reuse known assets despite
  changing delivery URLs; retain older acquisitions when refreshing.
- Support explicit refresh independently of output overwrite. Recover missing
  or corrupt cache entries by downloading them again.
- Process multiple issue arguments sequentially with acquisition progress,
  configurable output and cookie paths, and a `--quit-on-error` option for
  handled source failures. Report unavailable Bifrost digital editions without
  exposing a traceback for metadata/assets 404 responses.
- Represent logical page mappings separately from image order, including
  zero, one, multiple, or unknown logical pages per image. Exclude the cover
  from logical interior counts. Marvel imports can estimate spread extents;
  spans within 2% are accepted. Ambiguous spans warn and remain unknown,
  allowing export without discarding images or guessing subsequent numbers.
- Preserve optional reading-direction and first-page-side metadata in the
  model, with supported projections into output metadata.
- Use the normalized in-memory `Publication` class and `get_publication()`
  adapter method, with separate `cover` and ordered `narrative` fields.
- Document the static, reader-controlled publication model, native source
  categories, intended preservation architecture, and current adapter APIs.

### Development and compatibility

- GitHub Actions runs formatting, lint, type checks, and offline tests for
  pull requests targeting `main` and pushes to `main`, using Python 3.12.
- Concise test names and parameter labels keep current verbose result rows
  within 80 columns. Live-service tests remain separate from routine checks.
- Python 3.12 or newer is required.
- The former `CLF` and `get_clf()` names have been removed without aliases.
  API compatibility is not promised before public beta. Existing cache bytes
  and hashes are unaffected by these naming changes.

### Known limitations

- Implemented inputs are Marvel Unlimited and local CBZ, with CBZ output. PDF,
  fixed-layout EPUB, directory, and other service inputs are not implemented.
  Native eligibility in the design is not a claim of adapter availability.
- CBZ normalization maps supported fields, while output preserves the original
  source representation. Explicit metadata editing/reconciliation is not yet
  implemented. See [CBZ input limits](docs/cbz-input.md).
- The general UCD Repository, publication revision history, independent
  retention/history policies, and transformation framework remain planned.
  The Marvel image cache does not implement all of these guarantees.
- Cover material is currently one required `Page`. Labeled cover components
  and complex folding structures remain design work in [issue #18][covers].
- `matches_url()` still reflects the current service adapter. General source
  recognition and validation, plugin discovery, and a stable extension
  interface remain unfinished.
- Some unexpected HTTP and other failures can still terminate an operation.
  The alpha does not promise crash-free operation or complete error handling.

### Work to settle before the first public alpha

This is a release-planning checklist, not additional implemented features or
an unconditional commitment to ship every native source category.

- [ ] Choose a useful initial input/output set and document exactly which
      adapters, content features, and options the alpha includes.
- [ ] Provide a documented, tested module interface and working extension
      examples suitable for outside experimenters.
- [ ] Establish representative input/output fixtures and explain metadata,
      preservation, and conversion limitations.
- [ ] Check installation and advertised commands from a clean environment;
      list incomplete paths and reproducible known failures explicitly.
- [ ] Select an alpha version and release commit, reconcile these notes with
      that commit, and publish the reviewed notes with the release.

Detailed implementation work belongs in [the roadmap](docs/roadmap.md) and
GitHub issues. Move completed items into the feature list above; do not
present unchecked plans as shipped functionality.

## Release stages

**Alpha:** experimental code for discovering problems and testing the design.
Incomplete paths and crashes are possible at any point. Describe the working
subset and known limitations honestly. Alpha status does not permit silently
overwriting original data or claiming unimplemented preservation guarantees.

**Beta:** every advertised feature, command, and option must function within
its documented scope. No knowingly unfinished or nonfunctional advertised
paths. Unexpected bugs may still be discovered; beta is not a guarantee of
zero defects. The broader roadmap need not be complete, but unsupported work
must remain explicitly outside the beta's advertised capabilities.

## Maintaining these notes

Use reviewed PR descriptions as the primary input for release notes. For a
change without a PR, use its commit description. Capture the user-visible
result, fixes, breaking changes, and limitations; avoid copying implementation
chatter or listing every intermediate commit as a separate feature.

Proposed automation: gather merged PR descriptions and otherwise unrepresented
commit descriptions since the previous release tag, produce a draft, and
retain links to the source changes. Deduplicate PRs and their commits, account
for reverted or superseded work, and require maintainer review before changing
release notes or publishing a release. No release-note generation automation
has been installed yet.

At release time, replace the unreleased heading with the chosen version and
date, preserve that section as the release record, and create a fresh
unreleased section for subsequent work. These notes should summarize each
release rather than duplicate Git's per-commit history.

[covers]: https://github.com/ArkhanLand/universal-comic-da-kine/issues/18
