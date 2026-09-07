class ComicDownloaderError(Exception):
    """Base project exception."""


class UnsupportedServiceError(ComicDownloaderError):
    """No registered service can handle the URL."""


class DownloadError(ComicDownloaderError):
    """A page download failed."""


class InvalidComicInputError(ComicDownloaderError):
    """A comic identifier couldn't be parsed"""


class ServiceResponseError(ComicDownloaderError):
    """A service returned an unexpected or malformed response."""
