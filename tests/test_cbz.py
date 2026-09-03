from pathlib import Path
from zipfile import ZipFile
from comic_downloader.models import Comic
from comic_downloader.output.cbz import write_cbz

def test_cbz_contains_metadata_and_page(tmp_path: Path) -> None:
    page = tmp_path / "00001.jpg"
    page.write_bytes(b"not-a-real-jpeg")
    comic = Comic(service="example", service_id="1", title="Example #1")
    destination = tmp_path / "example.cbz"
    write_cbz(comic, [page], destination)
    with ZipFile(destination) as archive:
        assert archive.namelist() == ["ComicInfo.xml", "00001.jpg"]
