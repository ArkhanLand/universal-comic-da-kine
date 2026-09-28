# UCD Core Design

This document records the current architectural decisions for Universal Comic
Da Kine (UCD). It is intended to describe durable invariants rather than
implementation details. The implementation may evolve, but persisted UCD data
should remain migratable and understandable as the project changes.

## Status and terminology

This is the agreed target architecture, not a list of implemented features.
The current code has a `Publication` dataclass,
`InputAdapter.get_publication()`, path-backed pages, Marvel acquisition, a
persistent Marvel image cache, local CBZ input with source retention, and CBZ
output. It does not yet implement the general UCD Repository or revision
manifests. See
[the migration roadmap](roadmap.md) for API migration notes and
[the adapter guide](adapters.md) for current extension points.

The project remains **Universal Comic Da Kine (UCD)**. **CLF** remains useful
as a theoretical term for the comic-like category/model; it does not name a
file on disk or the Python class. The normalized in-memory class is
called **Publication**. Code-shaped examples below describe that target unless
explicitly labeled as current behavior.

## Native reading model

A UCD-native publication is a readable work composed of an ordered sequence of
static visual reading units/pages. A reader may view units individually or
simultaneously in small groups, including N-page spreads. The reader controls
duration and progression; a presentation can remain indefinitely. There is no
intrinsic playback clock. If representing the work requires modeling the
passage of time, it is outside the native model.

Previous and next follow the primary reading order. Page selection, search,
and navigation structures may offer other access. Continuous-scroll static
works fit: scrolling changes the reader's viewport, not the work over time.
There is no required paper size, print origin, or legal book/periodical split.

A logical **Page** is a reading unit, not an object filename or a display
slot. A conceptual **View** groups pages/regions for presentation, such as
facing pages or a viewport on a long static page. It does not impose timing,
change reading order, or imply a new image object. View is not yet a Python
class. One source image can span several logical pages; several pages can
share a View. Existing `Page.numbers` mappings are described below and remain
valid during migration; this decision does not require splitting spread bytes.

See [source acceptance](source-acceptance.md) for native categories and the
explicit, semantically lossy conversion policy for non-native works.

## Core concepts and pipeline

- **Object:** immutable bytes identified by their SHA-256 hash, independent
  of media format, original filename, or physical storage location.
- **Source:** the origin/acquisition context, including provider identifiers
  and useful source metadata. A **Source Object** is an exact acquired source
  byte stream, such as a PDF or CBZ; a service may instead supply many
  objects.
- **Asset:** a recognized object participating in a publication, with media
  type, properties, role, and provenance. Source and derived assets are
  distinguished. Not every retained object is a page image.
- **Page:** a logical static reading unit associated with assets and explicit
  logical mappings; **View** is optional presentation grouping.
- **Publication:** the normalized, temporary in-memory representation used
  for inspection, transformation, and output. It references object IDs/hashes.
- **UCD Repository:** persistent historical knowledge: identities, metadata,
  revisions, acquisition/derivation records, object hashes, and availability.
  It knows more than any one in-memory Publication.

```text
Input Adapter -> Publication -> Transformations -> Output Adapter
                      |                |
                      +-- UCD Repository --+
                           |         |
                        history   object storage
```

Inputs and outputs communicate through Publication, with generic object
resolution; outputs never reopen source formats to recover missing semantics.
This avoids N x M pairwise converters. A returned Publication is complete and
usable, with required bytes available for the operation. Completeness does not
require permanent retention of every byte. Rehydrating a historical revision
whose objects are absent requires explicit reacquisition or a clear
unavailability error before export.

UCD targets loss-preserving normalization, provenance, transformation, and
universal translation for static readable/fixed-layout publications. It
complements collection/library organizers such as Calibre. Preserving an
original does not make every export or derivation lossless; losses must be
identified separately.

## Publication identity and revisions

Each imported publication receives a stable UCD-generated publication ID,
expected to be a UUID. The publication ID represents the logical publication
across edits and revisions and is not derived from metadata or image contents.

The state of a publication is represented by immutable publication revision
records in the repository. Any change to normalized publication state creates
a new revision rather than overwriting the previous one. Examples include:

- metadata edits;
- page reordering, insertion, or removal;
- cover changes;
- image transformations;
- lossless image optimization.

The publication record identifies the current revision. Earlier revisions
remain available until explicitly removed by a future history-management
operation.

A revision may be identified by a cryptographic hash of its canonical
serialized representation. This provides an exact identity for a particular
normalized Publication state while the publication UUID remains the stable
logical identity.

Conceptually:

```text
Publication UUID
    -> current revision hash
        -> immutable revision manifest
            -> immutable image objects
```

Import therefore creates the initial revision of a publication. Subsequent
work creates later revisions of the same publication.

## Page model

The current Python `Page` represents one image entry in a publication. This
implementation combines an asset reference with logical page mappings; it is
not yet a separate logical Page/Asset/View implementation. Its optional
logical page mapping is separate from its position in the image sequence:

```python
@dataclass(frozen=True, slots=True)
class Page:
    numbers: tuple[int, ...] | None
    # Current implementation: path, width, height, content_type, mode.
    # Future persistent storage: image reference and image information.
```

`numbers` is required explicitly:

- `()` means the asset represents zero logical publication pages, e.g. the
  cover image.
- `(17,)` means one logical interior page.
- `(18, 19)` means a two-page spread in one image.
- `(20, 21, 22, 23)` means a four-page spread in one image.
- `None` means logical pagination is unknown, not zero.

These are positive logical interior ordinals, not printed page labels or image
indices. Numbers must be unique within and across image entries. Gaps and
reordering are allowed: count is the number of represented logical pages, not
the highest number. An interior page without a printed number still represents
a logical page. The mapping is publication metadata, not an intrinsic property
of the underlying image bytes.

A logical page represents one normal single-page extent in the supplied
digital edition. A spread image represents multiple such extents. This count
describes the content present in the Publication; it does not reconstruct
original print pagination or count omitted material such as advertisements.
Removing ads can also change which pages face each other, so sequential
logical numbers alone do not establish original print pairings.

Page order is represented exclusively by the enclosing sequence. Reordering
entries does not rewrite their logical mappings or image objects. Download
filenames and CBZ member names use image sequence indices, never logical
numbers.

`Publication.cover` is a separate `Page`. `Publication.narrative` is an
ordered tuple of interior `Page` objects, not a container holding the cover.
Iterate directly with `for page in publication.narrative`.
`Publication.interior_image_count` counts interior assets.
`Publication.logical_page_count` sums their logical extents and excludes the
cover;
it returns `None` if any interior mapping is unknown. A cover-only publication
has logical count zero. `Page.is_spread` is true for multiple logical pages,
false for zero or one, and unknown for `None`. The core model never infers
extent from dimensions; input adapters may apply a documented heuristic.

`Publication.reading_direction` (`ltr`/`rtl`) and
`Publication.first_page_side`
(`left`/`right`) retain optional facing-page presentation metadata. Missing
values remain unknown.

### Marvel pagination

The adapter treats the first asset as the cover, with `numbers=()`. Interior
assets start with `numbers=None`. For print-format imports, the most frequent
portrait (`0 < width < height`) interior image dimensions provide a
single-page reference (excluding explicit zero/multi-page mappings). A
portrait cover is the fallback if no such interior asset exists. Each image's
width is mathematically scaled to this reference height; a positive integer
width multiple within 2% is accepted as an estimated logical span, including
3+ page spreads. No image resampling is needed.

If the original rectangle does not fit, the adapter tries a measurement
excluding solid black edge bands in memory. Each RGB channel may differ from
black by at most 8, allowing small JPEG artifacts; only complete outer rows
and columns are ignored, not interior gutters. White borders are treated as
paper and always retained in the measurement. Stored image bytes and
dimensions are unchanged. Already matching page rectangles retain their
ordinary margins. A blank image with unmatched dimensions cannot supply a
trimmed measurement.

This can still misread landscape singles, legitimate uniform margins, or
unusual reference proportions. It is an import heuristic, not authoritative
metadata. Dimensions alone cannot establish an absolute single-page size: if
every asset contains the same multi-page span, their relative sizes do not
reveal that span. An all-landscape collection provides no portrait reference
and leaves inferred pagination unknown with a warning. Uniform multi-page
assets that remain portrait could instead be mistaken for singles and
undercounted. Explicit mappings are needed to resolve that ambiguity; a
tighter matching tolerance cannot resolve it.

Unmatched ratios or a missing single-page reference emit a warning and retain
all assets unchanged in source order. An unmatched span leaves that asset and
subsequent inferred page numbers as `None` until a nonempty explicit mapping
anchors numbering again. Explicit mappings always win, including `()` for
non-counting assets; an empty mapping does not restore a lost numbering
anchor. Without a reference, all existing mappings are retained without
inference. The logical page count is unknown if any mapping is unknown; CBZ
export still includes every image and omits unknown pagination metadata.
Callers can disable inference with `get_pages(..., infer_pagination=False)`.
`get_publication()` disables it for an explicit non-print `digital_format`
(such as vertical Infinity Comics); a missing format retains the print-import
default. Progress counts downloaded images, including the cover. Divisibility
by four is not a general publication invariant. An earlier version of the code
assumed stapled paper publications made from folded sheets, motivating that
restriction. It does not apply to the logical page count of the supplied
digital edition.

### Output and migration

CBZ writes the cover as `00000.ext` and numbers interior image assets
sequentially from `00001.ext`. Each asset is written once, regardless of its
logical mapping. ComicInfo `PageCount` uses the logical interior count and is
omitted when unknown. Its per-image `Image` indices follow CBZ image order,
including cover index zero. The cover is marked `FrontCover`; known two-page
spans have `DoublePage=true`, and known zero/one-page spans have
`DoublePage=false`. Unknown and 3+ page spans omit this attribute because
ComicInfo cannot express their exact extent. Known RTL direction maps to
`Manga=YesAndRightToLeft`; no Manga value is inferred for LTR or unknown
direction.

ComicInfo cannot round-trip arbitrary logical mappings, first-page-side
metadata, or gatefold extent. This change does not introduce a custom archive
manifest; source-metadata preservation and guided-view interoperability are
separate work. ComicBookInfo output is unchanged.

Callers must migrate `Page(number=None, ...)` for covers to `numbers=()`, and
known `number=n` values to `numbers=(n,)`. Asset indices previously passed as
page numbers must become `None` unless logical pagination has been verified.
There is no compatibility accessor for the ambiguous singular `number` field,
and no implemented persisted Publication serializer to migrate. Existing
archives are untouched; new exports use the revised metadata semantics.

## Cover semantics

The current model stores cover and narrative as separate fields. It requires
a cover; making the cover optional remains future model work.

A cover is itself a `Page`, but it is structurally separate from the narrative
page sequence.

Conceptually:

```python
@dataclass(frozen=True, slots=True)
class Publication:
    cover: Page
    narrative: tuple[Page, ...]
```

The invariants are:

```text
cover          = publication cover
narrative[0]   = first interior page of the reading experience
```

A cover is never implicitly part of `narrative`. An output adapter may
explicitly include the cover when the requested output calls for it.

## Image metadata

Basic image characteristics are inspected when an image object is created and
stored with the asset (currently on `Page`) so ordinary UCD operations do not
need to reopen and parse image files merely to answer common questions.

The initial conceptual structure is:

```python
@dataclass(frozen=True, slots=True)
class ImageInfo:
    format: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    format_details: str | None = None
    color_mode: str | None = None
    bit_depth: int | None = None
```

`format_details` is intentionally descriptive rather than machine-parseable.
It may contain values such as:

```text
JFIF 1.01
Progressive JPEG
PNG, non-interlaced
WebP lossless with alpha
```

If UCD later needs to reason programmatically about a property that was
previously present only in `format_details`, that property should be promoted
to a dedicated `ImageInfo` field.

## Immutable objects and image assets

Every object stored by UCD is an immutable byte stream, including containers,
images, and auxiliary payloads. Imported source bytes are authoritative
original data and are never modified in place.

The object ID is the SHA-256 hash of the exact byte stream:

```text
Object ID = SHA-256(exact bytes)
```

Two files that decode to identical pixels but contain different bytes are
different image objects. This distinction is intentional: UCD preserves exact
provenance, not merely visual equivalence. Original bytes also preserve any
embedded metadata (such as creator credits, EXIF, IPTC, or XMP) and hidden
data, including potential alternate reality game (ARG) clues, without
requiring UCD to recognize or interpret them.

A page references an image object indirectly:

```python
@dataclass(frozen=True, slots=True)
class ImageReference:
    object_id: str
```

The persistent object store resolves `object_id` to the physical file. Input
adapters may retain source filenames, archive member names, and stable source
URLs as provenance when meaningful for that source. These values do not define
image object identity or replace the Publication's explicit reading order.
Temporary download paths and physical object-store locations are storage
details, not source provenance.

## Immutable source artifacts

Ingest retains the exact publication-level source object from which normalized
pages were acquired or derived, when such an object is supplied. Examples
include a publisher-delivered PDF, EPUB, CBZ, downloaded ZIP, or other source
container.

A source artifact is an immutable byte stream identified by the SHA-256 hash
of its exact bytes, using the same content-addressed principle as image
objects:

```text
Source Artifact ID = SHA-256(exact source bytes)
```

Source artifacts are provenance, not pages. They do not replace normalization
and are not exposed to output adapters as a substitute for the Publication.
`get_publication()` must still return a complete normalized publication whose
required page assets are resolvable for the operation under the chosen
retention policy.

Preserving a source artifact is especially valuable when normalization
necessarily derives page images from a richer container. A PDF page, for
example, may be a composition of raster images, vector graphics, live text,
masks, transparency, optional-content groups, annotations, and other PDF
objects. UCD should retain the exact PDF while separately recording how
normalized page images were rendered from it.

Conceptually:

```text
immutable source artifact
        |
        | normalization policy
        v
normalized image objects
        |
        v
immutable Publication revision
```

A publication may therefore retain both exact source provenance and one or
more normalized derivations without conflating the two.

## UCD Repository: history and object retention

The repository persists knowledge by default, including for an ephemeral
conversion. Only an explicit history-disabled policy suppresses recording that
operation; it does not erase prior knowledge. Asset retention is a separate
policy. These policies and their CLI/API controls are not implemented.

| Operation | Metadata/history default | Object retention |
| --- | --- | --- |
| Ingest | Record identity, metadata, acquisitions, initial revision | Retain source objects and acquired assets |
| Ephemeral conversion | Record source/output hashes and derivation history | Temporary objects may be discarded after use |
| Explicit history-disabled operation | Do not add operation history | Independently selected retention policy |

Original source objects and acquired source assets are sacred: retained bytes
are never rewritten by transformations or silently replaced by derivatives.
Ephemeral cleanup is an explicit retention choice, not permission to mutate
originals or remove previously retained ingest assets. Derived assets carry
provenance even when their bytes are temporary.

The repository may know an object's hash, media properties, and provenance
without retaining its bytes locally. Availability is distinct from identity.
Later acquisition of the same hash reconnects those bytes to existing
knowledge. Garbage collection may remove eligible, unpinned bytes under an
explicit retention policy without deleting object records or revision history.
History references alone therefore do not promise local exportability.

Conceptually the repository stores authoritative versioned manifests and
records alongside content-addressed objects. Object paths, extensions, and
physical layout are implementation details. Publication references hashes, not
filenames; retained source/member names are provenance. Storage is format
independent, rather than separate identity schemes for PDFs and images.

The current Marvel cache is only a partial implementation: it retains image
bytes and acquisition records, uses hash-plus-extension filenames, and passes
paths through `Page.path`. It is not the general historical repository, and
there is no current history-disable, retention, or garbage-collection command.

## Original and transformed images

UCD distinguishes exact original bytes from derived image data.

If a source image is transformed:

```text
original image object A
        |
        | transform
        v
 derived image object B
```

Object A remains intact. Object B is a new immutable object with its own
SHA-256 object ID. A new Publication revision references B where appropriate,
while earlier revisions continue to reference A.

Every derived asset must carry provenance identifying its source object(s),
the ordered transformation steps and their parameters, and the name and
version of every program used to generate it. Record relevant image-processing
libraries and codec versions as well, including when UCD invokes them directly
rather than through a standalone program. This applies to compression and
lossless optimization as well as pixel-changing operations; a multi-program
pipeline must retain the tools and versions for each step.

UCD does not automatically convert imported JPEG, PNG, WebP, or other raster
images into a common format during ingestion. Preserving the original encoding
avoids unnecessary CPU and storage use and, more importantly, preserves the
exact publisher-provided byte stream.

A standard lossless working format such as PNG may be used when an operation
actually changes decoded pixels.

## Lossless optimization is a transformation

Pixel-preserving optimization can still change the underlying bytes. For
example, losslessly optimizing JPEG entropy coding creates a new byte stream
even if the decoded pixels are identical. Pixel preservation alone does not
guarantee preservation of embedded metadata or hidden payloads; retaining the
original byte stream preserves both when present.

Therefore:

```text
original object A
        |
        | lossless optimization
        v
optimized object B
```

This creates a new image object and a new Publication revision. The original
remains unchanged; byte availability follows the explicit retention policy
above.

UCD therefore distinguishes two guarantees:

```text
original  = exact source bytes
lossless  = decoded image content preserved, bytes may differ
```

## Input adapter contract

An `InputAdapter` completely ingests and normalizes its source before
returning.

Conceptually:

```python
class InputAdapter(ABC):
    @abstractmethod
    def get_publication(self, source: str) -> Publication: ...
```

For an online reader service, the adapter may internally:

1. identify the publication;
2. fetch source metadata;
3. discover transient page resources or a source publication artifact;
4. download the source bytes;
5. retain supplied source objects under the ingest retention policy;
6. acquire or derive normalized page images according to the adapter's
   normalization policy;
7. inspect, hash, and store required objects under the retention policy;
8. construct `Page` objects;
9. record the initial revision/history unless explicitly disabled;
10. return the complete normalized Publication.

Transient acquisition details do not need to survive normalization. A stable
publication URL may be retained as provenance, but service-specific transient
page URLs or internal acquisition identifiers should not be required by
downstream code.

Normalization parameters that materially affect the resulting Publication
should survive as provenance. This allows the exact source artifact plus the
recorded normalization policy to explain how a particular initial Publication
revision was derived.

### Metadata preservation during ingestion

The preservation goal is all available publication and asset metadata,
including fields UCD does not yet understand. Normalize recognized values into
Publication fields while retaining original representations, unknown fields,
and their source associations as versioned, namespaced metadata or references
to immutable auxiliary objects. Normalization must not silently discard
metadata merely because ComicInfo or ComicBookInfo cannot express it. Preserve
conflicting source values with their provenance rather than silently replacing
them.

This includes publication and asset identifiers, credits, reading order,
logical page mappings, static guided-view regions, source names, embedded
image metadata, and auxiliary files. Original image bytes remain authoritative
for embedded metadata and hidden payloads; extracted fields supplement rather
than replace them. Authentication credentials, cookies, and transient
authorization tokens are acquisition state, not publication metadata. Timed
transitions may be retained as source metadata but are not native View
semantics; flattening them requires explicit loss reporting.

### CBZ input

The first CBZ input adapter retains the exact source archive and a member-name
to object-hash record. Publication carries a source representation and a
normalized-state snapshot so CBZ output can preserve native metadata without
consulting the input adapter. Optional `ComicMetadata` carries prepared native
documents and their provenance. Shared preparation preserves existing
documents and generates missing ones before export. The CBZ writer packages
those bytes, retaining original XML, comments, and member metadata. Other
output formats consume normalized fields and may use attached documents where
appropriate. Changed normalized state is refused until an explicit
reconciliation policy exists; retaining originals in a cache is not permission
to drop metadata from converted books. See the
[supported subset](cbz-input.md). Duplicate
member names and unsupported payloads are rejected by this implementation.
The broader preservation design below still applies to future extensions.

Preserve the complete original ZIP member name for every imported asset,
including directory components, rather than retaining only the basename. For
example, `chapter-01/pages/003.jpg` and `chapter-02/pages/003.jpg` must remain
distinguishable. Associate each original member with its Publication asset
independently of generated storage names, logical page numbers, and reading
order. Member names are provenance and must not be blindly used as filesystem
extraction paths.

Preserve archive and member comments, available ZIP entry metadata, original
ComicInfo and ComicBookInfo payloads, and other metadata or auxiliary members,
including unrecognized content. Preserve member occurrence/order where needed
to distinguish duplicate ZIP member names. Retaining the exact source CBZ
preserves details that parsed ZIP metadata may not reproduce; the Publication
should also expose useful normalized metadata and references without requiring
output adapters to reopen that source archive.

### Acquisition timestamps

Acquisition provenance records a per-asset `fetched_at` timestamp marking
successful download completion, expressed in UTC with fractional seconds
(preserving the clock precision available). This is an acquisition timestamp,
not the publication date or a source server modification time. It belongs to
the acquisition record rather than immutable image identity: identical bytes
fetched on different occasions share an image object but have distinct
acquisition records. The Marvel image cache now records this information;
general Publication provenance and export of these records remain future work.

### Marvel image reuse

The adapter retains exact image bytes in a persistent cache, separate from
scratch downloads. The default location is `~/.local/share/ucd/marvel`;
library callers can override it with `cache_dir`. Object filenames use SHA-256
plus an image extension. Each fetch creates a versioned acquisition record
with the object hash, provider asset identity, image attributes, and UTC fetch
time. An asset lookup pointer selects the latest successful acquisition.
Refresh retains older records and original objects; cleanup does not delete
this persistent data.

Two consecutive live asset requests for digital issue 39895 returned 18 unique
page IDs unchanged while all 18 source URLs changed. This supports using the
provider, digital issue ID, page ID, and source rendition as the reuse key; it
does not establish a permanent provider guarantee. The observed response
exposes no asset revision marker. Same-ID replacements or rendition changes
therefore require explicit refresh. New IDs trigger downloads; reordering and
removal follow the fresh manifest. Without an asset ID, reuse is restricted to
the exact URL hash rather than guessing an identity from sequence position.
Duplicate page IDs in a manifest are rejected as ambiguous.

Each import still requests metadata and an authorized asset manifest. Cache
hits verify the local object hash before reusing bytes, leave `fetched_at`
unchanged, and count toward acquisition progress. Missing or corrupt objects
or records trigger a download. Atomic writes publish objects and acquisition
records before lookup pointers, so an interrupted fetch cannot publish an
incomplete cache entry. Refresh operates per asset, not as an issue-wide
transaction; successful fetches before a later failure remain available.

`--refresh` bypasses image reuse. `--overwrite` separately controls
replacement of an existing output CBZ; refresh does not bypass that
protection. Existing URL-named scratch files lack verified identity mappings
and acquisition times and are not automatically adopted. General repository
revision records, metadata preservation, cache garbage collection, and
exported provenance remain separate work.

## PDF input normalization

PDF deserves an explicit normalization policy because a PDF page is a
rendering program rather than necessarily a single raster image. Embedded
images are page components and must not generally be treated as page images
merely because they can be extracted from the PDF.

The default PDF input path is therefore:

```text
PDF source
    |
    +--> preserve exact PDF as immutable source artifact
    |
    v
PDF renderer + normalization policy
    |
    v
static page assets available under the retention policy
    |
    v
normalized Publication
```

The PDF adapter may accept PDF-specific import options while preserving the
strong `get_publication()` contract. Conceptually:

```python
get_publication(
    source,
    options=PDFInputOptions(
        render_dpi=300,
        color_mode="preserve",
        alpha="preserve",
        annotations="exclude",
        page_box="media",
    ),
)
```

These field names and defaults are illustrative rather than final API
commitments. The durable requirement is that PDF normalization policy be
explicit enough to reproduce and explain the derived page images.

Potential PDF-specific policy includes:

- render resolution;
- MediaBox, CropBox, TrimBox, or other page-box selection;
- ICC profile and color-space handling;
- transparency and alpha handling;
- whether annotations are rendered;
- optional-content-group/layer visibility;
- overprint behavior;
- interactive form appearance;
- preservation of an existing text or OCR layer as auxiliary metadata.

Rendering parameters are provenance because different valid policies can
produce different normalized Publications from the same immutable PDF source
artifact. For example:

```text
source artifact SHA-256 = X
        |
        +-- PDF normalization @ 150 dpi --> Publication revision A
        |
        +-- PDF normalization @ 300 dpi --> Publication revision B
```

Neither derivation changes the source PDF. Each resulting image is an ordinary
immutable UCD image object, and each normalized state is represented by an
immutable Publication revision.

A future implementation may optimize special cases where a PDF page is
provably equivalent to a single embedded raster image, but such optimization
must preserve page rendering semantics and must not make embedded-image
extraction the general PDF normalization strategy.

## Warhammer Vault as a PDF source adapter

Warhammer Vault is a concrete example of why acquisition and normalization
should remain separate concerns. Earlier acquisition research observed the
authenticated web application delivering a publisher-provided PDF through a
temporary signed object URL for browser rendering. This is a proposed adapter,
not current support; validate the service behavior and access requirements
before implementing it.

For UCD, the appropriate source path is therefore:

```text
Warhammer Vault publication URL
        |
        v
authenticated Vault acquisition
        |
        v
publisher-delivered PDF
        |
        +--> immutable source artifact
        |
        v
generic PDF input normalization
        |
        v
normalized Publication
```

The Vault adapter should own Vault-specific authentication, publication
discovery, metadata acquisition, and retrieval of a fresh authorized PDF URL.
Once the PDF has been acquired and persisted, generic PDF normalization should
take over. The adapter should not scrape browser-rendered page images when the
publisher-provided PDF is available as the actual upstream source asset.

This differs from services whose upstream representation is genuinely a
sequence of page assets. In those cases the source adapter should normalize
those page assets directly rather than manufacturing an intermediate PDF.

## Output adapter contract

Output adapters consume only normalized UCD state plus access to the object
store. They must not require knowledge of the input adapter that originally
acquired the publication.

Thus all of these converge on the same output path:

```text
Marvel Unlimited -> Publication -> CBZ
PDF (planned)    -> Publication -> CBZ
CBZ (planned)    -> Publication -> PDF (planned)
```

A simple conversion that does not require changing page pixels should use the
exact stored image bytes wherever the destination format permits them.

### Output metadata mappings and extensions

Each output adapter should document which Publication metadata it writes into
native fields, which it preserves in extensions or sidecars, and which it
cannot retain. Document encoding, schema version, asset associations, and
round-trip limitations, including conflicts between standard fields and
preserved source values. Any unavoidable metadata loss should be reported
explicitly. These are future preservation requirements, not claims about the
current exporters.

For CBZ, continue emitting interoperable ComicInfo and ComicBookInfo fields
where supported. Plan a versioned UCD metadata manifest for values those
formats cannot represent, with references to preserved auxiliary payloads and
an explicit mapping from output ZIP members to Publication assets and original
source members. The manifest name, schema, and precedence rules remain to be
designed; adding it is outside the current logical-pagination change.

An optional README inside the CBZ could explain the metadata files, their
schema versions, how to interpret them, and known limitations. It would
supplement the machine-readable manifest rather than replace it. Before
adopting this convention, verify that target readers ignore these non-image
members and that generated metadata names cannot collide with retained source
members. Each output adapter should publish equivalent documentation even when
its format cannot embed a README.

## Persistence and schema evolution

Persistent structures must carry explicit schema versions. The exact
representation is still to be designed, but a revision manifest will
conceptually contain fields such as:

```json
{
  "schema_version": 1,
  "publication_id": "...",
  "revision_id": "...",
  "cover": "...",
  "pages": []
}
```

Publication provenance should also be able to identify retained source
artifacts and normalization parameters without requiring downstream consumers
to understand the originating service.

Serialization and deserialization should be explicit rather than treating
Python dataclass serialization as the storage format. This allows the Python
object model to evolve independently of persisted UCD data and provides a
clean path for future migrations.

## Files are authoritative

The initial authoritative store should use ordinary files plus structured
manifests rather than depending on a database for correctness.

If UCD later needs SQLite or another database for fast searching or indexing,
that database should be a derived index that can be reconstructed from the
authoritative store. A damaged or deleted index must not imply loss of the
publication library.

## Design invariants

1. Native works are static and reader-paced, with no intrinsic playback clock.
2. Publication is in memory; the UCD Repository retains historical knowledge.
3. Objects are immutable exact bytes identified by SHA-256, not filenames.
4. Source objects/acquired originals remain intact; derivatives are new
   objects.
5. Assets, logical pages, and presentation groupings are distinct concepts.
6. Reading order belongs to Publication; logical mappings are not file
   indices.
7. Cover designation is separate from the narrative sequence and interior
   count.
8. Publication identity is stable; normalized changes create immutable
   revisions.
9. History persists by default even for ephemeral conversion; retention is a
   separate policy, and absent bytes do not erase known hashes or provenance.
10. Persisted structures are explicitly versioned and migratable.
11. Inputs and outputs share Publication and generic object access only.
12. Derivations record source hashes, parameters, tools/versions, and losses.
13. Original encodings are preserved unless an explicit transformation or
    output requirement changes them; preservation of pixels is not byte
    identity.
14. Source objects do not substitute for complete normalized reading
    semantics.
15. PDF normalization respects complete page appearance, not merely embedded
    images; non-native timed/reflowable semantics require explicit conversion.
