class ComicDownloaderError(Exception):
    """Base project exception."""

class UnsupportedServiceError(ComicDownloaderError):
    """No registered service can handle the URL."""

class DownloadError(ComicDownloaderError):
    """A page download failed."""
