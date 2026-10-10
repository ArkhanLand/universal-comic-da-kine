"""Shared measured page spans; explicit logical mappings always win."""

import logging
from collections import Counter
from dataclasses import replace
from math import isclose

from PIL import Image, ImageChops

from ucd.models import Page, Pages

logger = logging.getLogger(__name__)


def _pagination_dimensions(page: Page) -> tuple[int, int] | None:
    """Derive image width and height excluding uniform black edge bands.

    A channel tolerance of 8 accommodates near-solid JPEG padding. Only complete
    outer rows/columns are excluded; interior gutters and white paper margins are
    left alone. Stored bytes and recorded dimensions are unchanged.
    """
    with Image.open(page.path) as original:
        image = original.convert("RGB")
    # Each channel is a grayscale image, not a scalar: its pixels hold that
    # color component's intensity (0-255) at the original image dimensions.
    red, green, blue = image.split()
    # lighter() takes the larger value at each pixel. Combining all three
    # channels gives max(R, G, B), so a pixel is near-black only if every
    # component is at most 8. This is not perceptual brightness.
    brightness = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    # point() applies a 256-entry lookup table: intensities 0-8 become zero,
    # and 9-255 become 255. getbbox() encloses all nonzero mask pixels,
    # returning (left, top, right, bottom), or None for an all-black mask.
    # The rectangle excludes black outer bands without removing dark areas
    # inside the artwork; right and bottom are exclusive coordinates.
    content = brightness.point([0] * 9 + [255] * 247).getbbox()
    if content is None:
        return None
    left, top, right, bottom = content
    return right - left, bottom - top


def _inferred_span(dimensions: tuple[int, int], reference: tuple[int, int]) -> int | None:
    """Scale the measurement to the reference height; accept integer spans within 2%."""
    width, height = dimensions
    reference_width, reference_height = reference
    if min(width, height, reference_width, reference_height) <= 0:
        return None
    scaled_width = width * reference_height / height
    relative_span = scaled_width / reference_width
    span = round(relative_span)
    return span if span >= 1 and isclose(relative_span, span, rel_tol=0.02) else None


def _presentation_span(dimensions: tuple[int, int], reference: tuple[int, int]) -> int | None:
    """Recognize ordinary spans and spreads presented a quarter-turn around.

    Matching reference heights rule out a quarter-turn check. Conflicting
    orientation matches remain unknown. The narrow-single fallback
    covers same-height promotional templates at the reference pixel height, up to
    6% narrower. It does not identify advertisements or change their role.
    """
    width, height = dimensions
    reference_width, reference_height = reference
    if min(width, height, reference_width, reference_height) <= 0:
        return None
    direct = _inferred_span(dimensions, reference)
    # Only try a quarter-turn when the height differs from the reference.
    # Also require the expected pixel scale: the width must match the
    # reference height exactly, so a pin-up cannot pass through rescaling alone.
    turned = (
        _inferred_span((height, width), reference)
        if height != reference_height and width == reference_height
        else None
    )
    # A quarter-turn is evidence for a spread only, not for a single page.
    matches = {span for span in (direct, turned) if span is not None and span >= 2}
    if direct == 1:
        matches.add(1)
    if matches:
        return matches.pop() if len(matches) == 1 else None
    if height == reference_height and 0.94 * reference_width <= width <= reference_width:
        return 1
    return None


def _infer_page_numbers(pages: Pages) -> Pages:
    """Estimate spans against the dominant portrait interior dimensions.

    This is an import heuristic, not a model invariant or source assertion.
    Explicit mappings win. An ambiguous span warns and leaves pagination unknown
    until a nonempty explicit mapping anchors subsequent numbering. Counts
    describe the supplied digital edition, not omitted print pages. Uniform
    multi-page assets can defeat the assumed single-page reference; dimensions
    alone cannot resolve that ambiguity.
    """
    if all(page.numbers is not None for page in pages.pages):
        return pages
    candidates = Counter(
        (page.width, page.height)
        for page in pages.pages
        if 0 < page.width < page.height and (page.numbers is None or len(page.numbers) == 1)
    )
    if candidates:
        reference = candidates.most_common(1)[0][0]
    elif 0 < pages.cover.width < pages.cover.height:
        reference = (pages.cover.width, pages.cover.height)
    else:
        page = next(page for page in pages.pages if page.numbers is None)
        logger.warning(
            "Cannot infer logical page span for %s: no portrait single-page "
            "reference is available. Preserving assets with unknown pagination; "
            "explicit page-number mappings can resolve it.",
            page.path,
        )
        return pages

    next_number: int | None = 1
    inferred = []
    for page in pages.pages:
        if page.numbers is not None:
            if page.numbers:
                next_number = max(page.numbers) + 1
        elif next_number is not None:
            # Ordinary print-page margins are part of the page rectangle. Only
            # try trimming padding when the original dimensions do not fit.
            dimensions: tuple[int, int] | None = (page.width, page.height)
            span = _presentation_span((page.width, page.height), reference)
            if span is None:
                dimensions = _pagination_dimensions(page)
                if dimensions is not None:
                    span = _presentation_span(dimensions, reference)
            if span is None:
                measurement = (
                    f"{dimensions[0]} x {dimensions[1]} after black-border measurement"
                    if dimensions is not None
                    else "no nonblack content"
                )
                logger.warning(
                    f"Cannot infer logical page span for {page.path}: "
                    f"original {page.width} x {page.height}, {measurement}, "
                    f"reference {reference[0]} x {reference[1]}; "
                    "no unambiguous span within 2% in either spread orientation "
                    "or the same-height narrow-single fallback. Preserving assets; "
                    "pagination remains "
                    "unknown until a nonempty explicit page-number mapping anchors it."
                )
                next_number = None
            else:
                page = replace(page, numbers=tuple(range(next_number, next_number + span)))
                next_number += span
        inferred.append(page)
    return replace(pages, pages=tuple(inferred))
