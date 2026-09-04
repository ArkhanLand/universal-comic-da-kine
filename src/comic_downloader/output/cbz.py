from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from comic_downloader.models import Comic
from comic_downloader.output.comicinfo import make_comicinfo


def write_cbz(
    comic: Comic,
    pages: list[Path],
    destination: Path,
    *,
    overwrite: bool = False,
) -> None:
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)

    with ZipFile(destination, "w", compression=ZIP_STORED) as archive:
        archive.writestr("ComicInfo.xml", make_comicinfo(comic))
        for page in sorted(pages):
            archive.write(page, arcname=page.name)
