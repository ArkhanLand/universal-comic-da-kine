from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw

from ucd.input.marvel_unlimited import (
    MarvelUnlimitedAdapter,
    _infer_page_numbers,
    _inferred_span,
    _MarvelPageSource,
    _MarvelPageSources,
    _pagination_dimensions,
)
from ucd.models import Page, Pages


def asset(
    tmp_path: Path,
    name: str,
    size: tuple[int, int],
    border: int = 0,
    border_color: tuple[int, int, int] = (0, 0, 0),
) -> Page:
    image = Image.new("RGB", (size[0] + 2 * border, size[1] + 2 * border), border_color)
    image.paste(Image.new("RGB", size, (120, 80, 40)), (border, border))
    path = tmp_path / f"{name}.png"
    image.save(path)
    return Page(
        numbers=None,
        path=path,
        width=image.width,
        height=image.height,
        content_type="image/png",
        mode="RGB",
    )


@pytest.mark.parametrize("border_color", [(0, 0, 0), (5, 5, 5)])
def test_infer_single_double_and_gatefold_from_padded_assets(tmp_path, border_color):
    cover = replace(
        asset(tmp_path, "cover", (100, 150), border=10, border_color=border_color), numbers=()
    )
    pages = Pages(
        cover=cover,
        pages=(
            asset(tmp_path, "single", (200, 300)),
            asset(tmp_path, "double", (200, 150)),
            asset(tmp_path, "triple", (300, 150), border=10, border_color=border_color),
            asset(tmp_path, "quad", (400, 150), border=30, border_color=border_color),
        ),
    )
    originals = [p.path.read_bytes() for p in (cover, *pages.pages)]
    inferred = _infer_page_numbers(pages)
    assert [p.numbers for p in inferred.pages] == [(1,), (2, 3), (4, 5, 6), (7, 8, 9, 10)]
    assert inferred.logical_page_count == 10
    assert inferred.interior_image_count == 4
    assert [(p.width, p.height) for p in inferred.pages] == [
        (p.width, p.height) for p in pages.pages
    ]
    assert [p.path.read_bytes() for p in (cover, *pages.pages)] == originals


def test_unknown_span_raises_and_explicit_override_resolves_it(tmp_path):
    cover = replace(asset(tmp_path, "cover", (100, 150)), numbers=())
    single = asset(tmp_path, "single", (100, 150))
    ambiguous = asset(tmp_path, "wide", (267, 150))
    pages = Pages(cover=cover, pages=(single, ambiguous, single))
    with pytest.raises(ValueError, match=r"wide.png.*267 x 150.*within 1%.*explicit"):
        _infer_page_numbers(pages)
    explicit = replace(ambiguous, numbers=(2, 3, 4))
    inferred = _infer_page_numbers(replace(pages, pages=(single, explicit, single)))
    assert [p.numbers for p in inferred.pages] == [(1,), (2, 3, 4), (5,)]


def test_explicit_zero_and_landscape_single_override_inference(tmp_path):
    cover = replace(asset(tmp_path, "cover", (100, 150)), numbers=())
    wide = asset(tmp_path, "wide", (200, 150))
    pages = Pages(
        cover=cover,
        pages=(
            replace(wide, numbers=()),
            replace(wide, numbers=(17,)),
            wide,
        ),
    )
    inferred = _infer_page_numbers(pages)
    assert [p.numbers for p in inferred.pages] == [(), (17,), (18, 19)]


def test_blank_asset_with_unmatched_dimensions_raises(tmp_path):
    cover = replace(asset(tmp_path, "cover", (100, 150)), numbers=())
    blank = asset(tmp_path, "blank", (267, 150))
    Image.new("RGB", (267, 150), "black").save(blank.path)
    assert _pagination_dimensions(blank) is None
    with pytest.raises(ValueError, match="blank.png.*no nonblack content"):
        _infer_page_numbers(Pages(cover=cover, pages=(blank,)))


def test_landscape_cover_does_not_establish_reference(tmp_path):
    cover = replace(asset(tmp_path, "cover", (200, 150)), numbers=())
    pages = Pages(cover=cover, pages=(asset(tmp_path, "wide", (400, 150)),))
    with pytest.raises(ValueError, match="wide.png.*no portrait single-page reference"):
        _infer_page_numbers(pages)


def test_interior_black_gutter_is_not_removed(tmp_path):
    page = asset(tmp_path, "gutter", (200, 150))
    with Image.open(page.path) as image:
        ImageDraw.Draw(image).rectangle((95, 0, 105, 149), fill="black")
        image.save(page.path)
    assert _pagination_dimensions(page) == (200, 150)


@pytest.mark.parametrize("infer", [True, False])
def test_download_inference_is_optional_and_progress_counts_assets(tmp_path, monkeypatch, infer):
    cover = asset(tmp_path, "cover", (100, 150))
    wide = asset(tmp_path, "wide", (400, 150), border=20)
    content = {"/cover": cover.path.read_bytes(), "/wide": wide.path.read_bytes()}

    def handler(request):
        return httpx.Response(
            200, content=content[request.url.path], headers={"Content-Type": "image/png"}
        )

    monkeypatch.setattr("ucd.input.marvel_unlimited.WORK_PATH", tmp_path / "downloads")
    adapter = MarvelUnlimitedAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    sources = _MarvelPageSources(
        cover=_MarvelPageSource(numbers=(), url="https://example.com/cover"),
        pages=[_MarvelPageSource(numbers=None, url="https://example.com/wide")],
    )
    progress = []
    pages = adapter.get_pages(
        "39895",
        sources,
        infer_pagination=infer,
        progress=lambda done, total: progress.append((done, total)),
    )
    assert pages.pages[0].numbers == ((1, 2, 3, 4) if infer else None)
    assert progress == [(0, 2), (1, 2), (2, 2)]
    assert pages.pages[0].path.read_bytes() == content["/wide"]


def test_jpeg_letterboxed_four_page_spread(tmp_path):
    # Reproduce the observed dimensions without including publisher artwork.
    cover = asset(tmp_path, "cover", (975, 1500))
    wide = asset(tmp_path, "wide", (2601, 1500))
    for page, box, background in (
        (cover, (0, 32, 974, 1499), "white"),
        (wide, (0, 248, 2600, 1255), "black"),
    ):
        image = Image.new("RGB", (page.width, page.height), background)
        ImageDraw.Draw(image).rectangle(box, fill=(120, 120, 120))
        image.save(page.path, format="JPEG", quality=75)
    pages = Pages(cover=replace(cover, numbers=()), pages=(wide,))
    before = wide.path.read_bytes()
    inferred = _infer_page_numbers(pages)
    assert inferred.pages[0].numbers == (1, 2, 3, 4)
    assert inferred.pages[0].width == 2601
    assert inferred.pages[0].height == 1500
    assert wide.path.read_bytes() == before


@pytest.mark.parametrize(
    "multiple, expected", [(2.0, 2), (2.01, 2), (2.03, None), (3.89, None), (3.97, 4), (4.0, 4)]
)
def test_span_tolerance_is_one_percent(multiple, expected):
    assert _inferred_span((round(1000 * multiple), 1500), (1000, 1500)) == expected


def test_interior_dimensions_take_precedence_over_cover(tmp_path):
    cover = replace(asset(tmp_path, "cover", (100, 180)), numbers=())
    single = asset(tmp_path, "single", (100, 150))
    spread = asset(tmp_path, "spread", (200, 150))
    pages = Pages(cover=cover, pages=(single, spread))
    inferred = _infer_page_numbers(pages)
    assert [p.numbers for p in inferred.pages] == [(1,), (2, 3)]


def test_white_margins_are_never_trimmed(tmp_path):
    page = asset(tmp_path, "white-paper", (400, 150), border=30, border_color=(255, 255, 255))
    assert _pagination_dimensions(page) == (460, 210)
    cover = replace(asset(tmp_path, "cover", (100, 150)), numbers=())
    with pytest.raises(ValueError, match="white-paper.png.*within 1%"):
        _infer_page_numbers(Pages(cover=cover, pages=(page,)))


def test_black_padding_outside_white_paper_preserves_white(tmp_path):
    page = asset(tmp_path, "black-and-white", (100, 150), border=10, border_color=(255, 255, 255))
    with Image.open(page.path) as white_page:
        padded = Image.new("RGB", (140, 190), "black")
        padded.paste(white_page, (10, 10))
        padded.save(page.path)
    assert _pagination_dimensions(page) == (120, 170)
