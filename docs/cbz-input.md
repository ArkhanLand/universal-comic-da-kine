# CBZ input and metadata completion

`ucd convert` reads local CBZs into Publication and preserves their native
representation when exporting back to CBZ. The input adapter prepares both
ComicInfo and ComicBookInfo documents before returning Publication, preserving
existing documents and generating missing ones from normalized metadata:

```sh
ucd convert '/collection/Example #1.cbz' --output-dir /collection/converted
```

Multiple input paths are processed sequentially. Errors produce a nonzero
exit status; `--quit-on-error` stops the batch. Existing outputs are refused
unless `--overwrite` is supplied. Inputs are never overwritten, including
other input files in the same batch. Output is published only after the new
archive is complete. Compare results before replacing files in a collection.

## Preservation contract

CBZ-to-CBZ export retains the original member bytes and ZIP records. To add a
missing ZIP comment alone, it changes only the end-of-central-directory
comment-length field and appends the prepared comment. To add missing XML, it
keeps all original bytes as a prefix, appends the prepared XML member, and
creates a new central directory using verbatim copies of existing records.
It does not reserialize existing member metadata.
Existing nonempty comments are preserved exactly, including unknown JSON,
conflicting values, non-JSON content, whitespace, and encoding. The operation
does not overwrite an existing comment with the normalized projection.

Original ComicInfo.xml bytes are unchanged: fields, attributes, namespace
declarations, schema hints, processing instructions, XML comments, CDATA,
whitespace, and unknown extensions survive. Filenames, member order, member
comments, timestamps, permissions, compression, ZIP extra fields, and resource
forks also survive. Missing XML is generated during input preparation;
malformed existing XML is preserved rather than silently repaired. The
existing source file remains untouched; preservation applies to metadata
within the archive, not external filesystem timestamps of the newly written
output file.

Publication carries a `SourceRepresentation` with a hash-verified retained
archive, image hashes, and a snapshot of the imported normalized state. Its
optional `ComicMetadata` attachment carries both prepared documents, their
source/generated origins, and the XML member name. Outputs consume this
representation through Publication; they do not consult the input adapter. An
edited normalized publication, a changed retained image/archive, or an
unrecognized ZIP trailer causes export to fail rather than replay stale
metadata or discard information. Adding XML to archives with ZIP64 central
directories, split volumes, signatures, or unrecognized directory layouts is
currently refused; ZIP64 local image headers with an ordinary central
directory are supported. Deliberate metadata corrections and transformations
need an explicit reconciliation policy; none is implemented by this command
yet.

The normalized model and generated metadata for new publications still have
format limitations. Preservation of imported CBZ metadata does not claim a
general lossless translation mechanism for unrelated formats.

## Acceptance and order

The first implementation requires an explicit local `.cbz` path. A random
`.zip` is not automatically accepted. The archive must contain at least one
supported static raster image and at most one ComicInfo.xml, optionally in a
subdirectory. JPEG, PNG, WebP, GIF, TIFF, and BMP are supported when Pillow
recognizes a single image/frame. Animated and multi-frame images are rejected
rather than flattened. SVG, encrypted entries, unsafe paths, duplicate names
(case-insensitive), and other payload files are rejected. Common macOS
resource-fork files and `.DS_Store` are ignored as reading content but
preserved in the output archive.

Normalization uses natural filename order over full member paths: `page2`
precedes `page10`. Case-insensitive text comparison has an exact-filename tie
breaker. ComicInfo `Image` indices refer to this sorted sequence. Other readers
may sort unusually named archives differently. Export preserves the original
names and member order rather than renaming or rearranging images.

A single valid `FrontCover` designation selects the model's cover; otherwise
the first image is the fallback. Multiple front-cover designations are rejected
pending the cover model in issue #18. The remaining model pages retain their
relative order. Export preserves the source's actual cover placement and XML
assertions. The adapter never crops, resizes, recompresses, or splits images.

## Normalized metadata

A parseable XML document with a `ComicInfo` root is authoritative for the
normalized fields. No fields are filled from the ZIP comment when that document
exists, even if it is empty. This is tolerant parsing, not full XSD validation:
unusable individual fields or page indices are ignored in normalization.
Broken XML, an incorrect root, or a DTD enables the comment fallback. A valid
`ComicBookInfo/1.0` JSON object is the second choice. Otherwise, the filename
stem supplies the title and normalized metadata remains minimal. These choices
do not delete or replace the original metadata. Image corruption rejects the
archive.

Supported bibliographic fields, creator credits, lists, and right-to-left
reading direction populate Publication. Explicit `DoublePage` values provide
one- or two-page spans. Missing/invalid span hints leave inferred pagination
unknown from that point onward; dimensions and `PageCount` do not invent
mappings. Source `PageCount`, `Manga`, and page properties remain unchanged in
output even when they cannot map exactly into the normalized model. Excluding
a cover or other image from logical counting does not establish its physical
span; newly generated XML omits a spread flag for zero-page mappings.

Publication supports complete dates and `PartialDate` values with a known
year and optional month. Preparation retains that precision in generated
documents without inventing a day. Invalid dates remain in raw metadata and
produce a warning. Unsupported XML fields and page properties also warn that
they were not normalized. A new ZIP
comment contains the supported projection only; it does not replace or remove
the fuller original XML. Ampersand-separated creator parsing remains issue
#25. ComicInfo interpretation follows the
[Anansi documentation](https://anansi-project.github.io/docs/comicinfo/documentation).

## Source retention and library use

Original archives and images are retained under
`~/.local/share/ucd/cbz/objects`, addressed by SHA-256 with a convenience
extension. Records under `sources` map full original member names to hashes.
Member names are provenance, never filesystem extraction destinations.

```python
from pathlib import Path
from ucd.input.cbz import CBZInputAdapter
from ucd.output.cbz import write_cbz

adapter = CBZInputAdapter(cache_dir=Path("./cbz-cache"))
publication = adapter.get_publication("Example.cbz")
write_cbz(publication, Path("converted/Example.cbz"))
```

Document generation lives in shared `ucd.metadata` helpers, not in the archive
writer. `get_publication()` from CBZ input calls `prepare_comic_metadata()`
with its original documents. Other pipelines can call the same helper before
export. The writer requires `Publication.ready_comic_metadata()` and writes
exactly those bytes; it does not choose sources, interpret fields, or generate
documents. Other output formats can use normalized fields or these optional
documents as appropriate. There is no requirement that every Publication carry
comic metadata.

The adapter implements InputAdapter and its callbacks; `matches_url` remains
the legacy recognition hook for local paths. Cached paths remain valid after
the adapter is discarded; no temporary extraction cleanup is needed. The
default compressed/expanded limit is 2 GiB, configurable via `max_bytes`.
Reading holds the archive and one decompressed image in memory. Export also
reads the retained archive and verifies image hashes.

This cache is not the general historical UCD Repository. Versioned metadata,
independent retention policies, and general structured loss reporting remain
future work. Offline tests cover exact source-metadata preservation, XML-only
comment completion, precedence, rejected sources, stale/corrupt source refusal,
and CLI output protection without copyrighted fixtures or live services.
