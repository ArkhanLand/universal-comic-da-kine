import json

from ucd.models import CLF

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


def make_comicbookinfo(clf: CLF) -> bytes:
    info: dict[str, object] = {}

    if clf.series:
        info["series"] = clf.series
    if clf.issue_number:
        info["issue"] = clf.issue_number
    if clf.series_count is not None:
        info["numberOfIssues"] = clf.series_count
    if clf.publisher:
        info["publisher"] = clf.publisher
    if clf.language:
        info["language"] = clf.language
        # Calibre tests for presence of "language" then reads "lang"
        info["lang"] = clf.language
    if clf.description:
        info["comments"] = clf.description
    if clf.genres:
        info["genre"] = ", ".join(clf.genres)
    if clf.tags:
        info["tags"] = list(clf.tags)
    if clf.title:
        info["title"] = clf.title

    if clf.publication_date is not None:
        info["publicationMonth"] = clf.publication_date.month
        info["publicationYear"] = clf.publication_date.year

    credits = [
        {
            "person": creator.name,
            "role": ROLE_MAP.get(creator.role.casefold(), creator.role),
        }
        for creator in clf.creators
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
