from dataclasses import replace
from pathlib import Path

import pytest

from tests.helpers import make_test_publication


def test_logical_count_and_image_count_are_independent(tmp_path: Path) -> None:
    base = make_test_publication(tmp_path)
    single = base.narrative[0]
    publication = replace(
        base,
        narrative=(
            replace(single, numbers=()),
            replace(single, numbers=(17,)),
            replace(single, numbers=(18, 19)),
            replace(single, numbers=(20, 21, 22, 23)),
        ),
    )
    assert publication.interior_image_count == 4
    assert publication.logical_page_count == 7
    assert replace(base, narrative=()).logical_page_count == 0


def test_unknown_mapping_makes_total_unknown(tmp_path: Path) -> None:
    base = make_test_publication(tmp_path)
    publication = replace(
        base, narrative=(base.narrative[0], replace(base.narrative[1], numbers=None))
    )
    assert publication.logical_page_count is None
    assert publication.interior_image_count == 2
    assert publication.narrative[1].is_spread is None


def test_spread_semantics_ignore_image_dimensions(tmp_path: Path) -> None:
    single = make_test_publication(tmp_path).narrative[0]
    assert replace(single, width=9000, height=100).is_spread is False
    assert replace(single, numbers=(18, 19), width=100, height=9000).is_spread is True
    assert replace(single, numbers=(20, 21, 22, 23)).is_spread is True
    assert replace(single, numbers=()).is_spread is False


@pytest.mark.parametrize("numbers", [(0,), (-1,), (1, 1), (True,), (1.5,), [1]])
def test_reject_invalid_numbers(tmp_path: Path, numbers: object) -> None:
    page = make_test_publication(tmp_path).narrative[0]
    with pytest.raises(ValueError):
        replace(page, numbers=numbers)


@pytest.mark.parametrize("numbers", [None, (1,)])
def test_cover_must_explicitly_have_zero_logical_pages(
    tmp_path: Path,
    numbers: tuple[int, ...] | None,
) -> None:
    publication = make_test_publication(tmp_path)
    with pytest.raises(ValueError, match="cover"):
        replace(publication, cover=replace(publication.cover, numbers=numbers))


def test_reject_overlapping_logical_pages(tmp_path: Path) -> None:
    publication = make_test_publication(tmp_path)
    with pytest.raises(ValueError, match="across images"):
        replace(
            publication,
            narrative=(replace(publication.narrative[0], numbers=(1, 2)), publication.narrative[1]),
        )


def test_reordering_does_not_renumber_or_change_count(tmp_path: Path) -> None:
    publication = make_test_publication(tmp_path)
    reordered = replace(publication, narrative=publication.narrative[::-1])
    assert reordered.narrative[0].numbers == (2,)
    assert reordered.narrative[1].numbers == (1,)
    assert reordered.logical_page_count == publication.logical_page_count == 2


def test_32_logical_pages_remain_32_with_cover_and_spread(tmp_path: Path) -> None:
    base = make_test_publication(tmp_path)
    single = base.narrative[0]
    publication = replace(
        base,
        narrative=(
            *[replace(single, numbers=(n,)) for n in range(1, 31)],
            replace(single, numbers=(31, 32)),
        ),
    )
    assert publication.logical_page_count == 32
    assert publication.interior_image_count == 31
