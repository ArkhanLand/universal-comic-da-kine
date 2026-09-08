# Universal Comic Da Kine

Universal Comic Da Kine (UCD) is a toolkit for working with
**comic-like files (CLFs)**: publications whose primary content consists
of images representing pages.

UCD aims to provide a format-independent model for ingesting,
manipulating, preserving, and exporting comic-style publications.

## Goals

UCD should be able to:

- Read CBZ, PDF, EPUB, image directories, and other image-page formats.
- Acquire publications from supported online reader services.
- Normalize these sources into a common internal CLF representation.
- Preserve imported image bytes exactly and persist imported publications.
- Inspect and edit publication metadata.
- Reorder, insert, remove, or truncate pages without rewriting page images.
- Crop, rotate, resize, optimize, or otherwise process page images while retaining originals.
- Split and combine publications.
- Export the resulting publication to supported formats such as CBZ,
  PDF, EPUB, or image sets.

## Architecture

UCD treats file formats and online services as adapters around a common
Comic-Like File model:

    Input Adapter -> Persistent CLF -> Transformations -> Output Adapter

An input adapter completely ingests its source before returning a CLF.
The resulting publication and its image objects are stored persistently,
so downstream transformations and output adapters do not need to know
where the publication originally came from.

Image data is kept in an immutable, content-addressed object store. A CLF
has a stable publication identity and immutable revisions; edits create new
revisions rather than destroying previous state.

See [docs/design.md](docs/design.md) for the current core design decisions
and storage invariants.

## Status

Early development.
