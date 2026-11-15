"""Persistent original image bytes and per-fetch acquisition records."""

import hashlib
import json
import mimetypes
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

from PIL import Image

from ucd.download.cache import sharded_path
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


def _read_entry(root: Path, category: str, identifier: str, suffix: str = "") -> tuple[Path, bytes]:
    path = sharded_path(root, category, identifier, suffix)
    # The first sharded layout nested its prefixes inside the flat directories.
    nested_path = root / path.relative_to(root / "shards")
    for candidate in (path, nested_path):
        try:
            return candidate, candidate.read_bytes()
        except FileNotFoundError:
            continue
    flat_path = root / category / path.name
    return flat_path, flat_path.read_bytes()


class ImageCache:
    """Map provider asset identities to verified, content-addressed originals.

    Identity is supplied by the adapter, not derived from a delivery URL.
    Each successful fetch gets its own record; hits retain the original time.
    Refresh changes the lookup pointer but retains older records and objects.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def lookup(self, key: str) -> DownloadedImageFile | None:
        """Verify a reusable original without changing any cache state."""
        asset_id = hashlib.sha256(key.encode()).hexdigest()
        return self._lookup(asset_id, key)

    def acquisition_path(self, key: str) -> Path:
        """Return the receipt referenced by the asset's current cache pointer."""
        asset_id = hashlib.sha256(key.encode()).hexdigest()
        path, _ = self._read_acquisition(asset_id)
        return path

    def _read_acquisition(self, asset_id: str) -> tuple[Path, bytes]:
        _, pointer = _read_entry(self.root, "assets", asset_id)
        record_id = pointer.decode().strip()
        if re.fullmatch(r"[0-9a-f]{32}", record_id) is None:
            raise ValueError("Invalid cache acquisition ID")
        return _read_entry(self.root, "acquisitions", record_id, ".json")

    def _lookup(self, asset_id: str, key: str) -> DownloadedImageFile | None:
        try:
            _, record_data = self._read_acquisition(asset_id)
            record = json.loads(record_data)
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
            path, data = _read_entry(self.root, "objects", digest, extension)
            if hashlib.sha256(data).hexdigest() != digest:
                return None
            # Do not trust stale dimensions or a filename from the record.
            # Read dimensions from the actual verified image bytes.
            with Image.open(BytesIO(data)) as image:
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
        fetched_at: str | None = None,
    ) -> DownloadedImageFile:
        asset_id = hashlib.sha256(key.encode()).hexdigest()
        if not refresh:
            cached = self._lookup(asset_id, key)
            if cached is not None:
                return cached

        downloaded = download()
        # The callback returns only after a successful complete download.
        # Prefetched images supply their actual completion time, rather than
        # the later time when ordered consumption reaches this cache write.
        fetched_at = fetched_at or datetime.now(UTC).isoformat(timespec="microseconds")
        data = downloaded.filename.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        extension = downloaded.filename.suffix
        path = sharded_path(self.root, "objects", digest, extension)
        _atomic_write(path, data)
        record: dict[str, Any] = asdict(downloaded)
        record.pop("filename")
        record.update(schema_version=1, key=key, sha256=digest, fetched_at=fetched_at)
        record_id = uuid4().hex
        _atomic_write(
            sharded_path(self.root, "acquisitions", record_id, ".json"),
            (json.dumps(record, indent=2) + "\n").encode(),
        )
        # Publish the pointer last. A failed refresh leaves the prior record.
        _atomic_write(sharded_path(self.root, "assets", asset_id), record_id.encode())
        return DownloadedImageFile(
            path, downloaded.width, downloaded.height, downloaded.content_type, downloaded.mode
        )
