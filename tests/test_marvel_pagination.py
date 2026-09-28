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


@pytest.mark.parametrize("border_color", [(0, 0, 0), (5, 5, 5)], ids=["black", "near-black"])
def test_padded_spans(tmp_path, border_color):
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


def test_span_override(tmp_path, caplog):
    cover = replace(asset(tmp_path, "cover", (100, 150)), numbers=())
    single = asset(tmp_path, "single", (100, 150))
    ambiguous = asset(tmp_path, "wide", (267, 150))
    pages = Pages(cover=cover, pages=(single, ambiguous, single))
    inferred = _infer_page_numbers(pages)
    assert [p.numbers for p in inferred.pages] == [(1,), None, None]
    assert inferred.logical_page_count is None
    assert "wide.png: original 267 x 150" in caplog.text
    assert "within 2%" in caplog.text
    explicit = replace(ambiguous, numbers=(2, 3, 4))
    inferred = _infer_page_numbers(replace(pages, pages=(single, explicit, single)))
    assert [p.numbers for p in inferred.pages] == [(1,), (2, 3, 4), (5,)]


def test_explicit_spans(tmp_path):
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


def test_blank_retained(tmp_path, caplog):
    cover = replace(asset(tmp_path, "cover", (100, 150)), numbers=())
    blank = asset(tmp_path, "blank", (267, 150))
    Image.new("RGB", (267, 150), "black").save(blank.path)
    assert _pagination_dimensions(blank) is None
    pages = Pages(cover=cover, pages=(blank,))
    assert _infer_page_numbers(pages) == pages
    assert "no nonblack content" in caplog.text


def test_no_reference(tmp_path, caplog):
    cover = replace(asset(tmp_path, "cover", (200, 150)), numbers=())
    pages = Pages(cover=cover, pages=(asset(tmp_path, "wide", (400, 150)),))
    assert _infer_page_numbers(pages) == pages
    assert "no portrait single-page reference" in caplog.text


def test_retain_gutter(tmp_path):
    page = asset(tmp_path, "gutter", (200, 150))
    with Image.open(page.path) as image:
        ImageDraw.Draw(image).rectangle((95, 0, 105, 149), fill="black")
        image.save(page.path)
    assert _pagination_dimensions(page) == (200, 150)


@pytest.mark.parametrize("infer", [True, False])
def test_inference_toggle(tmp_path, monkeypatch, infer):
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


def test_jpeg_gatefold(tmp_path):
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
    "multiple, expected",
    [(1.95, None), (1.97, 2), (2.0, 2), (2.03, 2), (2.05, None), (3.89, None), (3.93, 4), (4.0, 4)],
)
def test_span_tolerance(multiple, expected):
    assert _inferred_span((round(1000 * multiple), 1500), (1000, 1500)) == expected


def test_prefer_interior(tmp_path):
    cover = replace(asset(tmp_path, "cover", (100, 180)), numbers=())
    single = asset(tmp_path, "single", (100, 150))
    spread = asset(tmp_path, "spread", (200, 150))
    pages = Pages(cover=cover, pages=(single, spread))
    inferred = _infer_page_numbers(pages)
    assert [p.numbers for p in inferred.pages] == [(1,), (2, 3)]


def test_retain_white(tmp_path, caplog):
    page = asset(tmp_path, "white-paper", (400, 150), border=30, border_color=(255, 255, 255))
    assert _pagination_dimensions(page) == (460, 210)
    cover = replace(asset(tmp_path, "cover", (100, 150)), numbers=())
    pages = Pages(cover=cover, pages=(page,))
    assert _infer_page_numbers(pages) == pages
    assert "within 2%" in caplog.text


def test_black_padding(tmp_path):
    page = asset(tmp_path, "black-and-white", (100, 150), border=10, border_color=(255, 255, 255))
    with Image.open(page.path) as white_page:
        padded = Image.new("RGB", (140, 190), "black")
        padded.paste(white_page, (10, 10))
        padded.save(page.path)
    assert _pagination_dimensions(page) == (120, 170)


def test_resume_at_anchor(tmp_path, caplog):
    cover = replace(asset(tmp_path, "cover", (100, 150)), numbers=())
    single = asset(tmp_path, "single", (100, 150))
    odd = asset(tmp_path, "odd", (267, 150))
    pages = Pages(
        cover=cover,
        pages=(
            single,
            odd,
            replace(odd, numbers=()),
            single,
            replace(single, numbers=(17,)),
            single,
        ),
    )
    inferred = _infer_page_numbers(pages)
    assert [p.numbers for p in inferred.pages] == [(1,), None, (), None, (17,), (18,)]
    assert len(caplog.records) == 1


def test_xmen_export(tmp_path, caplog):
    # Synthetic artwork with the observed dimensions and 36-image sequence.
    from zipfile import ZipFile

    from tests.helpers import make_test_publication
    from ucd.output.cbz import write_cbz

    cover = replace(asset(tmp_path, "cover", (6897, 2800)), numbers=())
    single = asset(tmp_path, "single", (1844, 2800))
    spread = asset(tmp_path, "spread", (3757, 2800))
    pinup = asset(tmp_path, "pinup", (2110, 2800))
    pages = Pages(cover=cover, pages=(*([single] * 31), spread, spread, spread, pinup))
    originals = [p.path.read_bytes() for p in (cover, *pages.pages)]
    inferred = _infer_page_numbers(pages)
    assert [p.numbers for p in inferred.pages[:31]] == [(n,) for n in range(1, 32)]
    assert [p.numbers for p in inferred.pages[31:]] == [(32, 33), (34, 35), (36, 37), None]
    assert len(caplog.records) == 1
    assert "pinup.png" in caplog.text
    publication = replace(make_test_publication(tmp_path), cover=cover, narrative=inferred.pages)
    destination = tmp_path / "xmen.cbz"
    write_cbz(publication, destination)
    with ZipFile(destination) as archive:
        assert archive.namelist() == ["ComicInfo.xml", *[f"{i:05}.png" for i in range(36)]]
        assert [archive.read(f"{i:05}.png") for i in range(36)] == originals
        assert b"<PageCount>" not in archive.read("ComicInfo.xml")
    assert [p.path.read_bytes() for p in (cover, *pages.pages)] == originals
    assert "Preserving assets" in caplog.text
