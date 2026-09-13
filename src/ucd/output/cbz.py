import re
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from ucd.models import CLF
from ucd.output.comicinfo import make_comicinfo


def _sanitize_filename(value: str) -> str:
    return value.replace(":", "_").replace("/", "_")


def make_cbz_filename(clf: CLF) -> str:
    series = clf.series or clf.title
    name = series

    if clf.publication_date is not None and not re.search(r"\(\d{4}\)$", series):
        name += f" ({clf.publication_date.year})"

    if clf.issue_number is not None:
        name += f" #{clf.issue_number}"

    return _sanitize_filename(name) + ".cbz"


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
