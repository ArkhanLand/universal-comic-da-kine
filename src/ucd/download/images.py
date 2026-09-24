"""Persistent original image bytes and per-fetch acquisition records."""

import hashlib
import json
import mimetypes
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

from PIL import Image

from ucd.models import DownloadedImageFile


def _atomic_write(path: Path, data: bytes) -> None:
    """Publish a complete file so interrupted writes never look complete."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


class ImageCache:
    """Map provider asset identities to verified, content-addressed originals.

    Identity is supplied by the adapter, not derived from a delivery URL.
    Each successful fetch gets its own record; hits retain the original time.
    Refresh changes the lookup pointer but retains older records and objects.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _lookup(self, pointer: Path, key: str) -> DownloadedImageFile | None:
        try:
            record_id = pointer.read_text().strip()
            if re.fullmatch(r"[0-9a-f]{32}", record_id) is None:
                return None
            record = json.loads((self.root / "acquisitions" / f"{record_id}.json").read_text())
            if record["schema_version"] != 1 or record["key"] != key:
                return None
            digest = record["sha256"]
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                return None
            content_type = record["content_type"]
            if not isinstance(content_type, str) or not content_type.startswith("image/"):
                return None
            extension = mimetypes.guess_extension(content_type)
            if extension is None:
                return None
            path = self.root / "objects" / f"{digest}{extension}"
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return None
            # Do not trust stale dimensions or a filename from the record.
            # Read dimensions from the actual verified image bytes.
            with Image.open(path) as image:
                width, height = image.size
                mode = image.mode
            return DownloadedImageFile(path, width, height, content_type, mode)
        except (OSError, ValueError, KeyError, TypeError):
            # Incomplete/corrupt local state is a miss, never proof of reuse.
            return None

    def acquire(
        self,
        key: str,
        download: Callable[[], DownloadedImageFile],
        *,
        refresh: bool = False,
    ) -> DownloadedImageFile:
        pointer = self.root / "assets" / hashlib.sha256(key.encode()).hexdigest()
        if not refresh:
            cached = self._lookup(pointer, key)
            if cached is not None:
                return cached

        downloaded = download()
        # The callback returns only after a successful complete download.
        fetched_at = datetime.now(UTC).isoformat(timespec="microseconds")
        data = downloaded.filename.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        extension = downloaded.filename.suffix
        path = self.root / "objects" / f"{digest}{extension}"
        _atomic_write(path, data)
        record: dict[str, Any] = asdict(downloaded)
        record.pop("filename")
        record.update(schema_version=1, key=key, sha256=digest, fetched_at=fetched_at)
        record_id = uuid4().hex
        _atomic_write(
            self.root / "acquisitions" / f"{record_id}.json",
            (json.dumps(record, indent=2) + "\n").encode(),
        )
        # Publish the pointer last. A failed refresh leaves the prior record.
        _atomic_write(pointer, record_id.encode())
        return DownloadedImageFile(
            path, downloaded.width, downloaded.height, downloaded.content_type, downloaded.mode
        )
