# Libby / OverDrive Read fixed-layout comic retrieval

## Status and scope

This document records observed retrieval behavior and the proposed acquisition
boundary for Libby titles delivered through OverDrive Read. It adds no adapter,
CLI routing, or stable capture schema. The existing
[core design](design.md) and [adapter contract](adapters.md) apply.

The investigated rendition was *Mecha-Ude: Mechanical Arms, Volume 1*,
OverDrive title ID `11103570`. Its capture contained one cover and 192 narrative
JPEGs. All 193 assets were mapped, fetched, written, and verified against their
browser-computed SHA-256 hashes. These are observations about this rendition,
not a guarantee that every Libby title has the same structure.

Libby is the discovery/loan application; OverDrive Read is the delivery and
reader mechanism considered here. MediaDo is a distinct Libby delivery
mechanism and is out of scope. Reflowable books and other delivery formats
require separate investigation.

Three terms must remain distinct:

- **Spine component:** a reader document, such as `html/page053.xhtml`,
  carrying source ordering and rendition properties.
- **Image asset:** the JPEG response body referenced by that component's CSS.
  Its filename is provenance, and its exact bytes define object identity.
- **UCD Page:** the normalized reading unit associated with an image and a
  logical mapping. A component index or image filename is not a logical page
  number. One image need not universally represent one logical page.

## Reader, openbook, and BIF structure

The authorized Libby loan opens an OverDrive Read reader on a
`dewey-…read.libbyapp.com` host, embedded beneath `libbyapp.com`. The
openbook/BIF metadata describes the rendition. In the observed live reader,
`BIF.map` exposes publication metadata, `spine`, and `nav`; `BIF.objects`
contains runtime reader objects rather than a second catalog record.

The observed runtime component sequence was accessible through
`BIF.objects.reader._.context.spine._.components`. Loaded components have
iframes whose documents expose the shared stylesheets. These private runtime
paths are discovery details, not an API promise. Component metadata is a
reduced projection of the BIF map; loaded/rendered DOM elements alone are not
a complete publication inventory.

`BIF.map.spine` establishes authoritative order. Preserve its array order and
associate each component with its source path and image mapping. The observed
`-odread-spine-position` values matched zero-based spine indices; retain them
as corroborating source ordinals, not a replacement ordering or side signal.
Do not sort image filenames, CSS rules, TOC entries, or current DOM nodes to
reconstruct reading order. Runtime visibility filters used during discovery
must not silently replace spine semantics.

## Cover, narrative, and presentation semantics

`nav.landmarks` explicitly identifies the cover component. In this rendition
that target is `html/cover.xhtml`, whose spine entry has `linear=false`.
The other 192 entries have `linear=true` and form the narrative in spine
order. Cover selection uses the landmark and spine semantics together;
being the first entry or having a familiar filename is only corroboration.

The cover becomes a separate `Publication.cover`, with `Page.numbers=()`.
The ordered `linear=true` entries supply `Publication.narrative`. Additional
non-linear resources, if present, should be preserved as source structure
without automatically becoming narrative or covers. Missing or conflicting
role information requires explicit handling rather than positional guessing.
Narrative membership does not by itself prove a logical single-page extent or
printed pagination.

`BIF.map["i18n-page-progression-direction"]` is explicitly `rtl` for this
title, so `Publication.reading_direction` is `rtl`. This does not reverse the
spine. Direction controls presentation; previous/next still follow source
sequence order.

`first_page_side` remains unknown (`None`). No explicit `rendition-position`
was found across the investigated spine. `rendition-spread="auto"`, RTL,
viewport dimensions, and odd/even spine positions do not establish left/right
placement. A future title with explicit position metadata should be evaluated
at its first narrative entry before projecting that field.

`firstPlace` is an observed numeric BIF field whose semantics remain unknown.
Preserve its raw value; do not interpret it as a page number, initial side,
cover designation, or ordering rule.

## Component-to-image mapping

The shared CSS supplies the component-to-image relationship through
`background-image` rules for `#cover` and `#pageNNN`:

```text
spine component        CSS element    direct source asset
html/cover.xhtml       #cover         /images/cover.jpg
html/page002.xhtml     #page002       /images/<opaque>.jpg
...
html/page193.xhtml     #page193       /images/<opaque>.jpg
```

Resolve relative CSS URLs against the applicable stylesheet/document base.
Opaque image names need not encode order and must not be treated as content
hashes without verification. The XHTML suffixes are source identifiers;
`page002` is the first narrative component here, not UCD logical page 2.
Validate the complete spine-to-asset association before export, and reject
missing or ambiguous mappings rather than delivering a partial publication.

The observed source JPEG for one component was 810 × 1215 while its displayed
extent was 720 × 1080. Reader scaling therefore does not describe source
dimensions. Direct image retrieval did not require reconstructing screenshots
or decoding the page-content obfuscation used by the reader.

`BIF.map.cover.front` is a separate small cover manifest/thumbnail. Its media
type, byte count, dimensions, aspect ratio, representative color, and
last-modified information describe that representation. They must not be
assumed to describe the full cover JPEG selected through the landmark, spine,
and CSS. Inspect the extracted full cover independently.

## Exact bytes and verification

Fetch the mapped image resources inside the authorized reader context and
retain the exact response-body bytes available to the browser. Compute
SHA-256 over those bytes before writing them to disk or a capture envelope.
No canvas export, screenshot, resizing, JPEG re-encoding, or metadata rewrite
belongs in this acquisition path. Image decoding for inspection must not
replace the stored bytes.

Record each asset's component association, spine index, role, sanitized source
asset path/name, local capture member, content type, byte count, and SHA-256.
Record successful acquisition times in UTC with the available precision,
separately from publication dates and source last-modified times. The receiver
recomputes hashes from the stored bytes and checks completeness, associations,
and sizes before normalization. Hash agreement verifies byte preservation
through capture; it is not independent proof of publisher authenticity.

The investigated ZIP contained `cover.jpg`, 192 `pages/*.jpg` files, and
`libby-manifest.json`. That generated ZIP is an acquisition envelope, not a
publisher-supplied original EPUB or other source container. The JPEG response
bodies are the acquired source objects. Capture member names describe storage;
they do not define reading order or logical pagination. Envelope names and a
versioned manifest schema remain implementation decisions.

## Browser capture and Python adapter boundary

The browser-side **Libby OverDrive Read capture** owns the live reader context:
discovering BIF/spine metadata, resolving CSS mappings, fetching authorized
image bytes and catalog metadata, sanitizing provenance, and exporting a
complete capture. Browser authentication and runtime state stop at this
boundary. A cross-origin reader frame could not use the directory picker in
the investigation; a generated ZIP provided a working transport.

A proposed `LibbyOverDriveReadAdapter` would consume that local capture,
validate its version and structure, verify every asset hash, retain/cache
exact source objects, inspect image properties, normalize supported metadata,
and return a complete `Publication` through `get_publication()`. It should
not require replaying a loan session or carrying browser credentials into
Python. Downstream output consumes Publication and available bytes through
the existing adapter contract.

This source supplies multiple objects and already fits the architectural
boundary described in the design. General repository persistence and raw
metadata attachments remain planned capabilities; this document does not
claim they are implemented or introduce new model fields.

## Sanitized capture metadata

Retain reader metadata and catalog metadata separately so their original
meaning and source precedence remain inspectable. Use an allowlist for BIF
publication fields rather than serializing `BIF.map` or the runtime wholesale:

- `title` (including main, subtitle, collection), `creator` (including roles
  and biographies), `description.short/full`, and `language`;
- `i18n-page-progression-direction`, `rendition-format`, and raw `firstPlace`;
- ordered `spine` entries with `id`, sanitized `path`, `linear`, `media-type`,
  `-odread-original-path`, `-odread-spine-position`, `-odread-file-bytes`, and
  observed rendition layout, orientation, spread, viewport, and position;
- `nav`, including TOC and landmarks with their titles, types, and sanitized
  component targets;
- `cover.front` technical fields and `-odread-cover-color` /
  `-odread-cover-ratio`, associated with the representation they describe.

Allowlisting includes inspecting nested URLs and values. Preserve the Thunder
media record as raw source metadata after removing any fulfillment/session
material. Retain useful catalog fields without forcing all of them into
Publication. Availability, holds, and visitor-specific nearby-library results
are contextual service data, not normalized publication identity.

Never persist fulfillment/session secrets in manifests, source metadata,
logs, fixtures, documentation, or committed files. Excluded material includes
cookies, authorization headers, card/loan context, signed query strings,
loan-opening URLs, and values of `-odread-bank-verification-token`,
`-odread-bonafides-*`, `-odread-msg-access`, `-odread-msg-sync`, and
`-odread-cmpt-params`. Other runtime access/message/fulfillment fields require
the same treatment. Read-host URLs are ephemeral acquisition locations, not
publication identity; keep only safe asset paths/names as durable provenance.
Do not export a full network log or credential-bearing openbook payload.

## Thunder catalog metadata and public identity

The reader was observed requesting this endpoint on
`https://thunder.api.overdrive.com`:

```text
/v2/libraries/<library>/media/<title-id>?x-client-id=...
```

The investigated path was
`/v2/libraries/phoenix-phoenixpl/media/11103570`; its observed request had
only the `x-client-id` query parameter and returned HTTP 200. This records an
observed endpoint, not a supported public API or a promise that future
requests need no authorization. Persist the title/library provenance when
useful, not the request's session state or query values.

Useful fields include `id`, `title`, `sortTitle`, `series`, `detailedSeries`,
`creators`, `languages`, `publishDate`, `publishDateText`,
`estimatedReleaseDate`, `publisher`, `imprint`, `description`, `ratings`,
`subjects`, BISAC fields, and `formats`. Format records can supply `id`,
`name`, `isbn`, `identifiers`, `fulfillmentType`, `onSaleDateUtc`, `fileSize`,
and `accessibilityStatements`. Preserve distinctions between catalog dates
and format release dates, and retain their precision.

Use the stable OverDrive title ID as `service_id`. Construct the canonical
public `source_url` deterministically from that ID:

```text
https://share.libbyapp.com/title/<title-id>
https://share.libbyapp.com/title/11103570
```

The latter was confirmed to show this title and library availability. Prefer
this title-level provenance over a Libby shelf route, loan-opening URL, or
read-host fulfillment URL. Nearby-library suggestions are visitor-specific
and do not become publication metadata. Reader BUIDs and checkout identifiers
must not replace the title ID.

## Source precedence and deferred normalization

BIF is authoritative for rendition structure and reading semantics; Thunder
is preferred for catalog/bibliographic metadata. Preserve both source values
where they overlap so disagreements can be inspected rather than erased.

| UCD concept | Source and rule |
|---|---|
| Service | Proposed adapter identity `libbyOverdriveRead` |
| Service title ID | Thunder `id`, matched to the captured title |
| Public source URL | Share URL constructed from the title ID |
| Service series ID | Thunder `detailedSeries.seriesId`, when present |
| Title and series | Thunder title and structured series; BIF corroborates or supplies missing values |
| Creators | Thunder creator records; BIF corroboration/fallback |
| Publication date | Thunder `publishDate`, preserving known precision |
| Publisher / imprint | Thunder `publisher.name` / `imprint.name` |
| Language | Thunder language records, corroborated by BIF |
| Description | Thunder description; fallback BIF `description.full` |
| Age rating | Thunder `ratings.maturityLevel.name`, when applicable |
| Reading direction | Explicit BIF `i18n-page-progression-direction` |
| First narrative side | Explicit rendition position if established; otherwise unknown |
| Cover / narrative membership | BIF landmarks and spine `linear` semantics |
| Reading order / navigation | BIF spine order / BIF nav respectively |
| Image objects | Exact read-host image response bodies |

The investigated Thunder record supplied publisher `Scholastic Inc.`, imprint
`Graphix`, publication date `2025-04-15`, language `en`, and maturity level
`Juvenile`. `publisherAccount` describes a supplier/account relationship and
does not override `publisher.name`. `reserveId` is an alternate internal
identifier worth retaining raw; it does not supersede title `id`.

Preserve these source fields without normalizing them yet:

- TOC/nav structures: navigation over the spine, not alternate reading order.
- ISBN and other identifiers, including their format association. The
  observed OverDrive Read ISBN was `9781546142577`; Kindle, OverDrive Read,
  and Kobo format records do not imply identical bytes or manifestations.
- Subjects and BISAC values: do not collapse them into genres or tags.
- `detailedSeries.readingOrder`: a series position, not automatically a
  volume number. The observed value `"1"` agrees with the title's Volume 1
  but does not establish a general conversion rule.
- Accessibility statements and other accessibility metadata: retain their
  source association without inventing a normalized capability claim.

Retaining raw safe metadata allows later model extensions without corrupting
source meaning. Unknown presentation fields remain unknown, and future
normalization policy must be explicit about inference and loss.
