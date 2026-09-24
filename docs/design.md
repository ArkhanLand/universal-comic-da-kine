# UCD Core Design

This document records the current architectural decisions for Universal Comic
Da Kine (UCD). It is intended to describe durable invariants rather than
implementation details. The implementation may evolve, but persisted UCD data
should remain migratable and understandable as the project changes.

## Core pipeline

UCD normalizes every supported input into the same internal publication
model:

```text
Input Adapter -> Persistent normalized CLF -> Transformations -> Output Adapter
```

An input adapter owns all source-specific acquisition work. Once it returns a
CLF, downstream code must not need to know whether the publication came from
Marvel Unlimited, a CBZ archive, PDF, EPUB, an image directory, or another
source.

A returned CLF is complete and immediately usable for inspection,
transformation, or export. For an online source, this means that all page
images have already been acquired or derived and stored as persistent UCD
objects before the adapter returns.

Normalization does not require discarding the exact source container. When a
source arrives as a meaningful publication artifact such as a PDF, EPUB, CBZ,
ZIP, or other container, UCD may preserve that exact byte stream as immutable
provenance in addition to the normalized page objects.

## Publication identity and revisions

Each imported publication receives a stable UCD-generated publication ID,
expected to be a UUID. The publication ID represents the logical publication
across edits and revisions and is not derived from metadata or image
contents.

The state of a publication is represented by immutable CLF revisions. Any
change to normalized publication state creates a new revision rather than
overwriting the previous one. Examples include:

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
normalized CLF state while the publication UUID remains the stable logical
identity.

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

A `Page` represents one image entry in a publication. Its optional logical
page mapping is separate from its position in the image sequence:

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

These are positive logical interior ordinals, not printed page labels or
image indices. Numbers must be unique within and across image entries. Gaps
and reordering are allowed: count is the number of represented logical pages,
not the highest number. An interior page without a printed number still
represents a logical page. The mapping is publication metadata, not an
intrinsic property of the underlying image bytes.

A logical page represents one normal single-page extent in the supplied
digital edition. A spread image represents multiple such extents. This count
describes the content present in the CLF; it does not reconstruct original
print pagination or count omitted material such as advertisements. Removing
ads can also change which pages face each other, so sequential logical
numbers alone do not establish original print pairings.

Page order is represented exclusively by the enclosing sequence. Reordering
entries does not rewrite their logical mappings or image objects. Download
filenames and CBZ member names use image sequence indices, never logical
numbers.

The current `Pages` container keeps `cover` separate from interior `pages`.
`Pages.interior_image_count` counts interior assets.
`Pages.logical_page_count` sums their logical extents and excludes the cover;
it returns `None` if any interior mapping is unknown. A cover-only
publication has logical count zero. `Page.is_spread` is true for multiple
logical pages, false for zero or one, and unknown for `None`. The core model
never infers extent from dimensions; input adapters may apply a documented
heuristic.

`CLF.reading_direction` (`ltr`/`rtl`) and `CLF.first_page_side`
(`left`/`right`) retain optional facing-page presentation metadata. Missing
values remain unknown.

### Marvel pagination

The adapter treats the first asset as the cover, with `numbers=()`. Interior
assets start with `numbers=None`. For print-format imports, the most frequent
portrait (`0 < width < height`) interior image dimensions provide a
single-page reference (excluding explicit zero/multi-page mappings). A
portrait cover is the fallback if no such interior asset exists. Each image's
width is mathematically scaled to this reference height; a positive integer
width multiple within 1% is accepted as an estimated logical span, including
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
and raises an error. Uniform multi-page assets that remain portrait could
instead be mistaken for singles and undercounted. Explicit mappings are
needed to resolve that ambiguity; a tighter matching tolerance cannot resolve
it.

Unmatched ratios or a missing single-page reference raise `ValueError`,
identifying the asset and requesting an explicit page-number mapping. Imports
do not silently continue with incomplete inferred pagination. Explicit
mappings always win, including `()` for non-counting assets. Callers can
disable inference with `get_pages(..., infer_pagination=False)`. `get_clf()`
disables it for an explicit non-print `digital_format` (such as vertical
Infinity Comics); a missing format retains the print-import default. Progress
counts downloaded images, including the cover. Divisibility by four is not a
general publication invariant. An earlier version of the code assumed stapled
paper publications made from folded sheets, motivating that restriction. It
does not apply to the logical page count of the supplied digital edition.

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
and no implemented persisted CLF serializer to migrate. Existing archives are
untouched; new exports use the revised metadata semantics.

## Cover semantics

A cover is itself a `Page`, but it is structurally separate from the
narrative page sequence.

Conceptually:

```python
@dataclass(frozen=True, slots=True)
class CLF:
    cover: Page | None
    pages: tuple[Page, ...]
```

The invariants are:

```text
cover      = publication cover, if present
pages[0]   = first page of the reading experience
```

A cover is never implicitly part of `pages`. An output adapter may explicitly
include the cover when the requested output calls for it.

## Image metadata

Basic image characteristics are inspected when an image object is created and
stored with the page so ordinary UCD operations do not need to reopen and
parse image files merely to answer common questions.

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

## Immutable image objects

Every image stored by UCD is an immutable byte stream. Imported source bytes
are authoritative original data and are never modified in place.

The object ID is the SHA-256 hash of the exact byte stream:

```text
Image Object ID = SHA-256(exact image bytes)
```

Two files that decode to identical pixels but contain different bytes are
different image objects. This distinction is intentional: UCD preserves exact
provenance, not merely visual equivalence. Original bytes also preserve any
embedded metadata (such as creator credits, EXIF, IPTC, or XMP) and hidden data,
including potential alternate reality game (ARG) clues, without requiring UCD
to recognize or interpret them.

A page references an image object indirectly:

```python
@dataclass(frozen=True, slots=True)
class ImageReference:
    object_id: str
```

The persistent object store resolves `object_id` to the physical file. Input
adapters may retain source filenames, archive member names, and stable source
URLs as provenance when meaningful for that source. These values do not define
image object identity or replace the CLF's explicit reading order. Temporary
download paths and physical object-store locations are storage details, not
source provenance.

## Immutable source artifacts

UCD may also preserve the exact publication-level artifact from which
normalized pages were acquired or derived. Examples include a
publisher-delivered PDF, EPUB, CBZ, downloaded ZIP, or other source
container.

A source artifact is an immutable byte stream identified by the SHA-256 hash
of its exact bytes, using the same content-addressed principle as image
objects:

```text
Source Artifact ID = SHA-256(exact source bytes)
```

Source artifacts are provenance, not pages. They do not replace normalization
and are not exposed to output adapters as a substitute for the CLF.
`get_clf()` must still return a complete normalized publication whose pages
are persistent image objects.

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
immutable CLF revision
```

A publication may therefore retain both exact source provenance and one or
more normalized derivations without conflating the two.

## Persistent UCD store

UCD maintains durable storage across command invocations. Once a publication
is imported, it remains managed by UCD until explicitly deleted.

Conceptually:

```text
UCD store/
├── objects/
│   └── immutable content-addressed objects
│       ├── image objects
│       └── source artifacts
└── publications/
    └── publication records and immutable revision manifests
```

The exact physical layout is an implementation detail and may evolve. The
storage root should eventually be configurable, with an OS-appropriate
default.

This store is more than a disposable cache: it is UCD's authoritative local
representation of imported publications, their image resources, and retained
source artifacts.

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
SHA-256 object ID. A new CLF revision references B where appropriate, while
earlier revisions continue to reference A.

Every derived image must carry provenance identifying its source object(s),
the ordered transformation steps and their parameters, and the name and
version of every program used to generate it. Record relevant image-processing
libraries and codec versions as well, including when UCD invokes them directly
rather than through a standalone program. This applies to compression and
lossless optimization as well as pixel-changing operations; a multi-program
pipeline must retain the tools and versions for each step.

UCD does not automatically convert imported JPEG, PNG, WebP, or other raster
images into a common format during ingestion. Preserving the original
encoding avoids unnecessary CPU and storage use and, more importantly,
preserves the exact publisher-provided byte stream.

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

This creates a new image object and a new CLF revision. The original object
remains available.

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
    def get_clf(self, source: str) -> CLF: ...
```

For an online reader service, the adapter may internally:

1. identify the publication;
2. fetch source metadata;
3. discover transient page resources or a source publication artifact;
4. download the source bytes;
5. preserve a meaningful source artifact when appropriate;
6. acquire or derive normalized page images according to the adapter's
   normalization policy;
7. inspect, hash, and persist each immutable image object;
8. construct `Page` objects;
9. construct and persist the initial CLF revision;
10. return the complete normalized CLF.

Transient acquisition details do not need to survive normalization. A stable
publication URL may be retained as provenance, but service-specific transient
page URLs or internal acquisition identifiers should not be required by
downstream code.

Normalization parameters that materially affect the resulting CLF should
survive as provenance. This allows the exact source artifact plus the
recorded normalization policy to explain how a particular initial CLF
revision was derived.

### Metadata preservation during ingestion

The preservation goal is all available publication and asset metadata,
including fields UCD does not yet understand. Normalize recognized values into
CLF fields while retaining original representations, unknown fields, and their
source associations as versioned, namespaced metadata or references to
immutable auxiliary objects. Normalization must not silently discard metadata
merely because ComicInfo or ComicBookInfo cannot express it. Preserve
conflicting source values with their provenance rather than silently replacing
them.

This includes publication and asset identifiers, credits, reading order,
logical page mappings, guided-view regions and transitions, source names,
embedded image metadata, and auxiliary files. Original image bytes remain
authoritative for embedded metadata and hidden payloads; extracted fields
supplement rather than replace them. Authentication credentials, cookies, and
transient authorization tokens are acquisition state, not publication
metadata.

### Future CBZ input

A CBZ input adapter has not yet been implemented. Its design should preserve
the complete original ZIP member name for every imported asset, including
directory components, rather than retaining only the basename. For example,
`chapter-01/pages/003.jpg` and `chapter-02/pages/003.jpg` must remain
distinguishable. Associate each original member with its CLF asset
independently of generated storage names, logical page numbers, and reading
order. Member names are provenance and must not be blindly used as filesystem
extraction paths.

Preserve archive and member comments, available ZIP entry metadata, original
ComicInfo and ComicBookInfo payloads, and other metadata or auxiliary members,
including unrecognized content. Preserve member occurrence/order where needed
to distinguish duplicate ZIP member names. Retaining the exact source CBZ
preserves details that parsed ZIP metadata may not reproduce; the CLF should
also expose useful normalized metadata and references without requiring output
adapters to reopen that source archive.

### Acquisition timestamps

Acquisition provenance should record a per-asset `fetched_at` timestamp
marking successful download completion, expressed in UTC with fractional
seconds (preserving the clock precision available). This is an acquisition
timestamp, not the publication date or a source server modification time. It
belongs to the acquisition record rather than immutable image identity:
identical bytes fetched on different occasions share an image object but have
distinct acquisition records. This timestamp is a future requirement and is
not recorded by the current adapter.

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
one persistent raster image per logical page
    |
    v
normalized CLF
```

The PDF adapter may accept PDF-specific import options while preserving the
strong `get_clf()` contract. Conceptually:

```python
get_clf(
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
produce different normalized CLFs from the same immutable PDF source
artifact. For example:

```text
source artifact SHA-256 = X
        |
        +-- PDF normalization @ 150 dpi --> CLF revision A
        |
        +-- PDF normalization @ 300 dpi --> CLF revision B
```

Neither derivation changes the source PDF. Each resulting image is an
ordinary immutable UCD image object, and each normalized state is represented
by an immutable CLF revision.

A future implementation may optimize special cases where a PDF page is
provably equivalent to a single embedded raster image, but such optimization
must preserve page rendering semantics and must not make embedded-image
extraction the general PDF normalization strategy.

## Warhammer Vault as a PDF source adapter

Warhammer Vault is a concrete example of why acquisition and normalization
should remain separate concerns. The authenticated web application currently
delivers a publisher-provided PDF through a temporary signed object URL and
then renders that PDF in the browser.

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
normalized CLF
```

The Vault adapter should own Vault-specific authentication, publication
discovery, metadata acquisition, and retrieval of a fresh authorized PDF URL.
Once the PDF has been acquired and persisted, generic PDF normalization
should take over. The adapter should not scrape browser-rendered page images
when the publisher-provided PDF is available as the actual upstream source
asset.

This differs from services whose upstream representation is genuinely a
sequence of page assets. In those cases the source adapter should normalize
those page assets directly rather than manufacturing an intermediate PDF.

## Output adapter contract

Output adapters consume only normalized UCD state plus access to the object
store. They must not require knowledge of the input adapter that originally
acquired the publication.

Thus all of these converge on the same output path:

```text
Marvel Unlimited -> CLF -> CBZ
PDF              -> CLF -> CBZ
CBZ              -> CLF -> PDF
```

A simple conversion that does not require changing page pixels should use the
exact stored image bytes wherever the destination format permits them.

### Output metadata mappings and extensions

Each output adapter should document which CLF metadata it writes into native
fields, which it preserves in extensions or sidecars, and which it cannot
retain. Document encoding, schema version, asset associations, and round-trip
limitations, including conflicts between standard fields and preserved source
values. Any unavoidable metadata loss should be reported explicitly. These are
future preservation requirements, not claims about the current exporters.

For CBZ, continue emitting interoperable ComicInfo and ComicBookInfo fields
where supported. Plan a versioned UCD metadata manifest for values those
formats cannot represent, with references to preserved auxiliary payloads and
an explicit mapping from output ZIP members to CLF assets and original source
members. The manifest name, schema, and precedence rules remain to be
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

The core invariants are:

1. Original imported image bytes are immutable.
2. SHA-256 identifies the exact bytes of an image object.
3. A `Page` represents one image entry with an explicit
   zero/one/multiple/unknown logical page mapping; storage filenames and
   asset indices are not logical page numbers.
4. Reading order belongs to the CLF, not to `Page`.
5. The cover is a `Page` separate from the narrative page sequence.
6. Imported publications persist until explicitly deleted.
7. Publication identity is stable across edits.
8. CLF revisions are immutable; edits create new revisions.
9. Persisted structures are explicitly schema-versioned and migratable.
10. Input-specific acquisition details do not leak into downstream
    processing.
11. Imported image encoding is preserved unless an explicit transformation or
    output requirement changes it.
12. Transformations never destroy the original image object.
13. Meaningful source publication artifacts may be preserved byte-for-byte as
    immutable content-addressed provenance.
14. Source artifacts do not substitute for normalization; `get_clf()` still
    returns a complete persistent normalized CLF.
15. Normalization parameters that materially affect derived page images are
    recorded as provenance.
16. PDF pages are normalized according to rendering semantics, not by
    assuming embedded images are pages.
