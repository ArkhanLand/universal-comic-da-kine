"""Prepare optional comic metadata documents independently of archive
writers.
"""

from dataclasses import replace

from ucd.metadata.comicbookinfo import make_comicbookinfo
from ucd.metadata.comicinfo import make_comicinfo
from ucd.models import ComicMetadata, Publication


def prepare_comic_metadata(
    publication: Publication,
    *,
    comicinfo: bytes | None = None,
    comicbookinfo: bytes | None = None,
    comicinfo_name: str = "ComicInfo.xml",
) -> Publication:
    """Preserve supplied documents and generate missing ones from
    normalized data.

    Adapters decide source precedence and supply raw documents. Imported
    source edits need a reconciliation policy rather than silent
    rewriting. New model edits must explicitly clear stale CBZ metadata
    before preparing again.
    """
    publication.unchanged_source()
    if publication.comic_metadata is not None:
        publication.ready_comic_metadata()
        if comicinfo is not None or comicbookinfo is not None or comicinfo_name != "ComicInfo.xml":
            raise ValueError("Cannot replace already prepared metadata without an edit policy")
        return publication
    return replace(
        publication,
        comic_metadata=ComicMetadata(
            comicinfo=make_comicinfo(publication) if comicinfo is None else comicinfo,
            comicbookinfo=make_comicbookinfo(publication)
            if comicbookinfo is None
            else comicbookinfo,
            normalized=publication.normalized_state(),
            comicinfo_name=comicinfo_name,
            comicinfo_origin="generated" if comicinfo is None else "source",
            comicbookinfo_origin="generated" if comicbookinfo is None else "source",
        ),
    )
