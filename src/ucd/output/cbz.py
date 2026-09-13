from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from ucd.models import CLF
from ucd.output.comicinfo import make_comicinfo


def _sanitize_filename(value: str) -> str:
    return value.replace(":", "_").replace("/", "_")


def make_cbz_filename_from_metadata(
    title: str,
    series: str | None,
    issue_number: str | None,
) -> str:
    if series is None:
        return _sanitize_filename(title) + ".cbz"

    name = series
    if issue_number is not None:
        name += f" #{issue_number}"

    return _sanitize_filename(name) + ".cbz"


def make_cbz_filename(clf: CLF) -> str:
    return make_cbz_filename_from_metadata(
        clf.title,
        clf.series,
        clf.issue_number,
    )


def write_cbz(
    clf: CLF,
    destination: Path,
    *,
    overwrite: bool = False,
) -> None:
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)

    with ZipFile(destination, "w", compression=ZIP_STORED) as archive:
        archive.writestr("ComicInfo.xml", make_comicinfo(clf))

        archive.write(
            clf.pages.cover.path,
            arcname=f"00000{clf.pages.cover.path.suffix}",
        )

        for page in clf.pages.pages:
            archive.write(
                page.path,
                arcname=f"{page.number:05}{page.path.suffix}",
            )
