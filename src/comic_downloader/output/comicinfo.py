import xml.etree.ElementTree as ET

from comic_downloader.models import Comic

ROLE_MAP = {
    "writer": "Writer",
    "penciler": "Penciller",
    "penciller": "Penciller",
    "inker": "Inker",
    "colorist": "Colorist",
    "letterer": "Letterer",
    "editor": "Editor",
    "cover artist": "CoverArtist",
}


def make_comicinfo(comic: Comic) -> bytes:
    root = ET.Element("ComicInfo")

    def add(tag: str, value: object | None) -> None:
        if value is not None and str(value).strip():
            ET.SubElement(root, tag).text = str(value)

    add("Title", comic.title)
    add("Series", comic.series)
    add("Number", comic.issue_number)
    add("Summary", comic.description)
    add("Publisher", comic.publisher)
    add("Imprint", comic.imprint)
    add("AgeRating", comic.age_rating)

    if comic.publication_date:
        add("Year", comic.publication_date.year)
        add("Month", comic.publication_date.month)
        add("Day", comic.publication_date.day)

    grouped: dict[str, list[str]] = {}
    for creator in comic.creators:
        tag = ROLE_MAP.get(creator.role.casefold())
        if tag:
            names = grouped.setdefault(tag, [])
            if creator.name not in names:
                names.append(creator.name)

    for tag, names in grouped.items():
        add(tag, ", ".join(names))

    ET.indent(root, space="  ")
    xml: bytes = ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
    )
    return xml
