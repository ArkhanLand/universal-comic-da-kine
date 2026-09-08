# UCD Core Design

This document records the current architectural decisions for Universal Comic Da Kine (UCD). It is intended to describe durable invariants rather than implementation details. The implementation may evolve, but persisted UCD data should remain migratable and understandable as the project changes.

## Core pipeline

UCD normalizes every supported input into the same internal publication model:

```text
Input Adapter -> Persistent normalized CLF -> Transformations -> Output Adapter
```

An input adapter owns all source-specific acquisition work. Once it returns a CLF, downstream code must not need to know whether the publication came from Marvel Unlimited, a CBZ archive, PDF, EPUB, an image directory, or another source.

A returned CLF is complete and immediately usable for inspection, transformation, or export. For an online source, this means that all page images have already been acquired and stored as persistent UCD objects before the adapter returns.

## Publication identity and revisions

Each imported publication receives a stable UCD-generated publication ID, expected to be a UUID. The publication ID represents the logical publication across edits and revisions and is not derived from metadata or image contents.

The state of a publication is represented by immutable CLF revisions. Any change to normalized publication state creates a new revision rather than overwriting the previous one. Examples include:

- metadata edits;
- page reordering, insertion, or removal;
- cover changes;
- image transformations;
- lossless image optimization.

The publication record identifies the current revision. Earlier revisions remain available until explicitly removed by a future history-management operation.

A revision may be identified by a cryptographic hash of its canonical serialized representation. This provides an exact identity for a particular normalized CLF state while the publication UUID remains the stable logical identity.

Conceptually:

```text
Publication UUID
    -> current revision hash
        -> immutable revision manifest
            -> immutable image objects
```

Import therefore creates the initial revision of a publication. Subsequent work creates later revisions of the same publication.

## Page model

A `Page` represents exactly one image. It has no filename and no page number.

Conceptually:

```python
@dataclass(frozen=True, slots=True)
class Page:
    image: ImageReference
    info: ImageInfo
```

Page order is represented exclusively by the enclosing CLF. Reordering pages changes the CLF revision but does not modify any `Page` object.

This is an important invariant: page position is publication state, not image state.

## Cover semantics

A cover is itself a `Page`, but it is structurally separate from the narrative page sequence.

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

A cover is never implicitly part of `pages`. An output adapter may explicitly include the cover when the requested output calls for it.

## Image metadata

Basic image characteristics are inspected when an image object is created and stored with the page so ordinary UCD operations do not need to reopen and parse image files merely to answer common questions.

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

`format_details` is intentionally descriptive rather than machine-parseable. It may contain values such as:

```text
JFIF 1.01
Progressive JPEG
PNG, non-interlaced
WebP lossless with alpha
```

If UCD later needs to reason programmatically about a property that was previously present only in `format_details`, that property should be promoted to a dedicated `ImageInfo` field.

## Immutable image objects

Every image stored by UCD is an immutable byte stream. Imported source bytes are authoritative original data and are never modified in place.

The object ID is the SHA-256 hash of the exact byte stream:

```text
Image Object ID = SHA-256(exact image bytes)
```

Two files that decode to identical pixels but contain different bytes are different image objects. This distinction is intentional: UCD preserves exact provenance, not merely visual equivalence.

A page references an image object indirectly:

```python
@dataclass(frozen=True, slots=True)
class ImageReference:
    object_id: str
```

The persistent object store resolves `object_id` to the physical file. Source filenames, archive member names, URLs, and storage paths are not part of the normalized CLF model.

## Persistent UCD store

UCD maintains durable storage across command invocations. Once a publication is imported, it remains managed by UCD until explicitly deleted.

Conceptually:

```text
UCD store/
├── objects/
│   └── immutable content-addressed image objects
└── publications/
    └── publication records and immutable revision manifests
```

The exact physical layout is an implementation detail and may evolve. The storage root should eventually be configurable, with an OS-appropriate default.

This store is more than a disposable cache: it is UCD's authoritative local representation of imported publications and their image resources.

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

Object A remains intact. Object B is a new immutable object with its own SHA-256 object ID. A new CLF revision references B where appropriate, while earlier revisions continue to reference A.

UCD does not automatically convert imported JPEG, PNG, WebP, or other raster images into a common format during ingestion. Preserving the original encoding avoids unnecessary CPU and storage use and, more importantly, preserves the exact publisher-provided byte stream.

A standard lossless working format such as PNG may be used when an operation actually changes decoded pixels.

## Lossless optimization is a transformation

Pixel-preserving optimization can still change the underlying bytes. For example, losslessly optimizing JPEG entropy coding creates a new byte stream even if the decoded pixels are identical.

Therefore:

```text
original object A
        |
        | lossless optimization
        v
optimized object B
```

This creates a new image object and a new CLF revision. The original object remains available.

UCD therefore distinguishes two guarantees:

```text
original  = exact source bytes
lossless  = decoded image content preserved, bytes may differ
```

## Input adapter contract

An `InputAdapter` completely ingests and normalizes its source before returning.

Conceptually:

```python
class InputAdapter(ABC):
    @abstractmethod
    def get_clf(self, source: str) -> CLF:
        ...
```

For an online reader service, the adapter may internally:

1. identify the publication;
2. fetch source metadata;
3. discover transient page resources;
4. download each image byte stream;
5. inspect the image;
6. hash and persist the immutable image object;
7. construct `Page` objects;
8. construct and persist the initial CLF revision;
9. return the complete normalized CLF.

Transient acquisition details do not need to survive normalization. A stable publication URL may be retained as provenance, but service-specific transient page URLs or internal acquisition identifiers should not be required by downstream code.

## Output adapter contract

Output adapters consume only normalized UCD state plus access to the object store. They must not require knowledge of the input adapter that originally acquired the publication.

Thus all of these converge on the same output path:

```text
Marvel Unlimited -> CLF -> CBZ
PDF              -> CLF -> CBZ
CBZ              -> CLF -> PDF
```

A simple conversion that does not require changing page pixels should use the exact stored image bytes wherever the destination format permits them.

## Persistence and schema evolution

Persistent structures must carry explicit schema versions. The exact representation is still to be designed, but a revision manifest will conceptually contain fields such as:

```json
{
  "schema_version": 1,
  "publication_id": "...",
  "revision_id": "...",
  "cover": "...",
  "pages": []
}
```

Serialization and deserialization should be explicit rather than treating Python dataclass serialization as the storage format. This allows the Python object model to evolve independently of persisted UCD data and provides a clean path for future migrations.

## Files are authoritative

The initial authoritative store should use ordinary files plus structured manifests rather than depending on a database for correctness.

If UCD later needs SQLite or another database for fast searching or indexing, that database should be a derived index that can be reconstructed from the authoritative store. A damaged or deleted index must not imply loss of the publication library.

## Design invariants

The core invariants are:

1. Original imported image bytes are immutable.
2. SHA-256 identifies the exact bytes of an image object.
3. A `Page` represents exactly one image and contains no filename or page number.
4. Reading order belongs to the CLF, not to `Page`.
5. The cover is a `Page` separate from the narrative page sequence.
6. Imported publications persist until explicitly deleted.
7. Publication identity is stable across edits.
8. CLF revisions are immutable; edits create new revisions.
9. Persisted structures are explicitly schema-versioned and migratable.
10. Input-specific acquisition details do not leak into downstream processing.
11. Imported image encoding is preserved unless an explicit transformation or output requirement changes it.
12. Transformations never destroy the original image object.
