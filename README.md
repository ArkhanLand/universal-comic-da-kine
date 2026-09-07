# Universal Comic Da Kine

Universal Comic Da Kine (UCD) is a toolkit for working with
**comic-like files (CLFs)**: publications whose primary content consists
of images representing pages.

UCD aims to provide a format-independent model for ingesting,
manipulating, and exporting comic-style publications.

## Goals

UCD should be able to:

- Read CBZ, PDF, EPUB, image directories, and other image-page formats.
- Acquire publications from supported online reader services.
- Normalize these sources into a common internal CLF representation.
- Inspect and edit publication metadata.
- Reorder, insert, remove, or truncate pages.
- Crop, rotate, resize, optimize, or otherwise process page images.
- Split and combine publications.
- Export the resulting publication to supported formats such as CBZ,
  PDF, EPUB, or image sets.

## Architecture

UCD treats file formats and online services as adapters around a common
Comic-Like File model:

    Input Adapter -> CLF -> Transformations -> Output Adapter

This keeps format- and service-specific code separate from the core
publication-processing logic.

## Status

Early development.
