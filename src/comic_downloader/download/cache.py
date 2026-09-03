from pathlib import Path

class Cache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def comic_dir(self, service: str, service_id: str) -> Path:
        return self.root / service / service_id

    def pages_dir(self, service: str, service_id: str) -> Path:
        return self.comic_dir(service, service_id) / "pages"

    def complete_marker(self, service: str, service_id: str) -> Path:
        return self.comic_dir(service, service_id) / "complete"

    def is_complete(self, service: str, service_id: str) -> bool:
        return self.complete_marker(service, service_id).is_file()

    def prepare(self, service: str, service_id: str) -> Path:
        path = self.pages_dir(service, service_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def mark_complete(self, service: str, service_id: str) -> None:
        marker = self.complete_marker(service, service_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
