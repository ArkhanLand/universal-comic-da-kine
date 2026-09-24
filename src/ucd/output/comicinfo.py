import xml.etree.ElementTree as ET

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

# ComicInfo.xml uses a controlled AgeRating vocabulary. Keep source-native values in
# the CLF and normalize only when serializing to this target format.
AGE_RATING_MAP = {
    "rated t": "Teen",
    "rated t+": "Teen",
    "t": "Teen",
    "t+": "Teen",
    "teen": "Teen",
}


def _join(values: tuple[str, ...]) -> str | None:
    return ", ".join(values) if values else None


def _comicinfo_age_rating(value: str | None) -> str | None:
    if value is None:
        return None
    return AGE_RATING_MAP.get(value.strip().casefold(), value)


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
    add("Count", clf.series_count)
    add("Volume", clf.volume)
    add("Summary", clf.description)
    add("Publisher", clf.publisher)
    add("Imprint", clf.imprint)
    add("Genre", _join(clf.genres))
    add("Tags", _join(clf.tags))
    add("Web", clf.source_url)
    add("PageCount", clf.pages.logical_page_count)
    add("LanguageISO", clf.language)
    add("AgeRating", _comicinfo_age_rating(clf.age_rating))
    add("StoryArc", _join(clf.story_arcs))
    add("Characters", _join(clf.characters))
    add("Teams", _join(clf.teams))
    add("Locations", _join(clf.locations))

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

    if clf.reading_direction == "rtl":
        add("Manga", "YesAndRightToLeft")

    # Image indices address archive assets, including the cover at index zero.
    page_info = ET.SubElement(root, "Pages")
    for index, page in enumerate((clf.pages.cover, *clf.pages.pages)):
        attributes = {"Image": str(index)}
        if index == 0:
            attributes["Type"] = "FrontCover"
        # ComicInfo has no exact representation for unknown or 3+ page spans.
        if page.numbers is not None and len(page.numbers) <= 2:
            attributes["DoublePage"] = "true" if len(page.numbers) == 2 else "false"
        ET.SubElement(page_info, "Page", attributes)

    ET.indent(root, space="  ")
    xml: bytes = ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
    )
    return xml
