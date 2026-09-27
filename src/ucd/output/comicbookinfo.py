import json

from ucd.models import Publication

ROLE_MAP = {
    "writer": "Writer",
    "penciler": "Penciller",
    "penciller": "Penciller",
    "inker": "Inker",
    "colorist": "Colorist",
    "colourist": "Colorist",
    "letterer": "Letterer",
    "editor": "Editor",
    "cover artist": "CoverArtist",
    "coverartist": "CoverArtist",
}


def make_comicbookinfo(publication: Publication) -> bytes:
    info: dict[str, object] = {}

    if publication.series:
        info["series"] = publication.series
    if publication.issue_number:
        info["issue"] = publication.issue_number
    if publication.series_count is not None:
        info["numberOfIssues"] = publication.series_count
    if publication.publisher:
        info["publisher"] = publication.publisher
    if publication.language:
        info["language"] = publication.language
        # Calibre tests for presence of "language" then reads "lang"
        info["lang"] = publication.language
    if publication.description:
        info["comments"] = publication.description
    if publication.genres:
        info["genre"] = ", ".join(publication.genres)
    if publication.tags:
        info["tags"] = list(publication.tags)
    if publication.title:
        info["title"] = publication.title

    if publication.publication_date is not None:
        info["publicationMonth"] = publication.publication_date.month
        info["publicationYear"] = publication.publication_date.year

    credits = [
        {
            "person": creator.name,
            "role": ROLE_MAP.get(creator.role.casefold(), creator.role),
        }
        for creator in publication.creators
    ]

    if credits:
        info["credits"] = credits

    payload = {
        "appID": "Universal Comic Da Kine",
        "ComicBookInfo/1.0": info,
    }

    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
