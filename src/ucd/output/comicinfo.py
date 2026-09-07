import xml.etree.ElementTree as ET

from ucd.models import CLF

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


# Makes the XML structure "ComicInfo.xml", as defined in the CBZ standard
# Returns a bytes object with the output from xml.etree.ElementTree.tostring()
def make_comicinfo(clf: CLF) -> bytes:
    root = ET.Element("ComicInfo")

    def add(tag: str, value: object | None) -> None:
        if value is not None and str(value).strip():
            ET.SubElement(root, tag).text = str(value)

    add("Title", clf.title)
    add("Series", clf.series)
    add("Number", clf.issue_number)
    add("Summary", clf.description)
    add("Publisher", clf.publisher)
    add("Imprint", clf.imprint)
    add("AgeRating", clf.age_rating)

    if clf.publication_date:
        add("Year", clf.publication_date.year)
        add("Month", clf.publication_date.month)
        add("Day", clf.publication_date.day)

    grouped: dict[str, list[str]] = {}
    for creator in clf.creators:
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
