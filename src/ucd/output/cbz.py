import hashlib
import re
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from ucd.models import Publication
from ucd.output.zip_metadata import complete_archive

ZIP_COMMENT_MAX = 65535
_YEAR_RANGE_RE = re.compile(r"(\d{4}) ?[-\N{EN DASH}\N{EM DASH}] ?(\d{4})")


def _normalize_year_ranges(value: str) -> str:
    return _YEAR_RANGE_RE.sub(
        lambda match: f"{match[1]}\N{EN DASH}{match[2]}",
        value,
    )


def _sanitize_filename(value: str) -> str:
    value = _normalize_year_ranges(value)
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


def make_cbz_filename(publication: Publication) -> str:
    return make_cbz_filename_from_metadata(
        publication.title,
        publication.series,
        publication.issue_number,
    )


def write_cbz(
    publication: Publication,
    destination: Path,
    *,
    overwrite: bool = False,
) -> None:
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)

    metadata = publication.ready_comic_metadata()
    comment = metadata.comicbookinfo

    if len(comment) > ZIP_COMMENT_MAX:
        raise ValueError(
            f"ComicBookInfo comment is {len(comment)} bytes; "
            f"ZIP comments are limited to {ZIP_COMMENT_MAX} bytes"
        )

    source = publication.unchanged_source()
    if source is not None:
        if source.media_type != "application/vnd.comicbook+zip":
            raise ValueError("Cannot preserve this source representation in CBZ output")
        for asset_path, digest in source.assets:
            if hashlib.sha256(asset_path.read_bytes()).hexdigest() != digest:
                raise ValueError("Retained image changed; refusing to replay stale source metadata")
        data = source.path.read_bytes()
        if hashlib.sha256(data).hexdigest() != source.sha256:
            raise ValueError("Retained source archive failed its SHA-256 check")
        data = complete_archive(data, metadata.comicinfo_name, metadata.comicinfo, comment)
        if destination.resolve() == source.path.resolve() or (
            destination.exists() and destination.samefile(source.path)
        ):
            raise ValueError("Output must not replace the retained source archive")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb" if overwrite else "xb") as output:
            output.write(data)
        return

    destination.parent.mkdir(parents=True, exist_ok=True)

    with ZipFile(destination, "w", compression=ZIP_STORED) as archive:
        archive.comment = comment
        archive.writestr(metadata.comicinfo_name, metadata.comicinfo)

        archive.write(
            publication.cover.path,
            arcname=f"00000{publication.cover.path.suffix}",
        )

        for asset_index, page in enumerate(publication.narrative, start=1):
            archive.write(
                page.path,
                arcname=f"{asset_index:05}{page.path.suffix}",
            )
