"""OverDrive Read fulfillment and metadata inspection (download routing pending).

The eData decoding algorithm follows libby-archiver 0.4.0 (MIT).
See THIRD_PARTY_NOTICES.md. No JavaScript from the service is executed.
"""

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ucd.auth.libby import USER_AGENT, LibbyAuthenticationError
from ucd.exceptions import ServiceResponseError, UnavailableError

# Restricted string-array reader: accepts quoted JS strings, never expressions.
_STRING = re.compile(r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' """, re.X | re.S)
_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
# Observed reader wrapper: the function parameter is bound directly to eData.
# Recognize this fixed structure and read its literal argument without eval.
_WRAPPED_EDATA = re.compile(
    r"""
    \(\s*function\s*\(\s*(?P<argument>[A-Za-z_$][\w$]*)\s*\)\s*\{
    \s*try\s*\{\s*Object\.defineProperty\s*\(\s*window\s*,
    \s*(?P<quote>['"])eData(?P=quote)\s*,\s*\{
    \s*value\s*:\s*(?P=argument)\s*,
    \s*writable\s*:\s*true\s*,
    \s*enumerable\s*:\s*true\s*,
    \s*configurable\s*:\s*true\s*\}\s*\)\s*;?\s*\}
    \s*catch\s*\(\s*[A-Za-z_$][\w$]*\s*\)\s*\{
    \s*window\.eData\s*=\s*(?P=argument)\s*;?\s*\}
    \s*\}\s*\)\s*\(\s*(?P<array>\[)
    """,
    re.X,
)


def _js_string(literal: str) -> str:
    body = literal[1:-1]
    output: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        index += 1
        if char != "\\":
            if char in "\r\n":
                raise ValueError
            output.append(char)
            continue
        if index >= len(body):
            raise ValueError
        escaped = body[index]
        index += 1
        if escaped in "xu":
            count = 2 if escaped == "x" else 4
            digits = body[index : index + count]
            if len(digits) != count or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                raise ValueError
            output.append(chr(int(digits, 16)))
            index += count
        elif escaped in "\\/'\"":
            output.append(escaped)
        elif escaped in _ESCAPES:
            output.append(_ESCAPES[escaped])
        else:
            raise ValueError
    return "".join(output)


def _string_array(literal: str) -> list[str]:
    body = literal[1:-1].strip()
    if not body:
        return []
    values: list[str] = []
    while body:
        match = _STRING.match(body)
        if match is None:
            raise ValueError
        values.append(_js_string(match.group()))
        body = body[match.end() :].lstrip()
        if not body:
            break
        if not body.startswith(","):
            raise ValueError
        body = body[1:].lstrip()
    return values


def _embedded_array(html: str, start: int) -> str:
    """Read the literal array, ignoring brackets inside quoted strings."""
    index = start + 1
    while index < len(html):
        while index < len(html) and html[index].isspace():
            index += 1
        if index < len(html) and html[index] == "]":
            return html[start : index + 1]
        match = _STRING.match(html, index)
        if match is None:
            raise ValueError
        index = match.end()
        while index < len(html) and html[index].isspace():
            index += 1
        if index < len(html) and html[index] == "]":
            return html[start : index + 1]
        if index >= len(html) or html[index] != ",":
            raise ValueError
        index += 1
    raise ValueError


def decode_openbook(html: str, buid: str) -> dict[str, Any]:
    """Decode the embedded eData envelope without eval or reader execution."""
    match = re.search(r"window\.eData\s*=\s*(?P<array>\[)", html)
    if match is None:
        match = _WRAPPED_EDATA.search(html)
    if match is None:
        raise ServiceResponseError("Reader page has no recognized openbook envelope.")
    try:
        if not re.fullmatch(r"[A-Za-z0-9-]+", buid):
            raise ValueError
        key = buid[::-1]
        data = '"'.join(_string_array(_embedded_array(html, match.start("array"))))
        if not data:
            raise ValueError
        output: list[str] = []
        for index, char in enumerate(data):
            digit = key[index % len(key)]
            code = ord(char)
            if digit in "123456789":
                code += (index + int(digit)) % 94
                if code > 126:
                    code = code % 126 + 32
            output.append(chr(code))
        payload = base64.b64decode("".join(output), validate=True).decode("utf-8")
        envelope = json.loads(payload)
        openbook = envelope.get("b") if isinstance(envelope, dict) else None
        if not isinstance(openbook, dict):
            raise ValueError
        return openbook
    except (ValueError, UnicodeError, OverflowError):
        raise ServiceResponseError("Cannot decode the reader's openbook envelope.") from None


def _read_url(url: str, *, origin: str | None = None) -> str:
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme == "https"
            and parts.username is None
            and parts.password is None
            and re.fullmatch(r"dewey-[a-z0-9-]+\.read\.libbyapp\.com", parts.hostname or "")
            and parts.port in (None, 443)
            and not parts.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise ServiceResponseError("Fulfillment did not provide a recognized HTTPS read host.")
    current_origin = f"https://{parts.hostname}"
    if origin is not None and current_origin != origin:
        raise ServiceResponseError("Reader handshake redirected outside its read host.")
    return current_origin


@dataclass(frozen=True)
class ReadRendition:
    """Ephemeral authorized rendition; never serialize this object wholesale."""

    web_url: str = field(repr=False)
    openbook: dict[str, Any] = field(repr=False)

    def summary(self) -> dict[str, str | int]:
        spine = self.openbook.get("spine")
        if not isinstance(spine, list) or not spine:
            raise ServiceResponseError("Openbook has no spine components.")
        if any(not isinstance(entry, dict) for entry in spine):
            raise ServiceResponseError("Openbook has a malformed spine.")
        if any(entry.get("rendition-layout") != "pre-paginated" for entry in spine):
            raise UnavailableError("This rendition is not supported fixed-layout OverDrive Read.")
        if any(not isinstance(entry.get("linear"), bool) for entry in spine):
            raise ServiceResponseError("Openbook lacks explicit spine linearity.")
        nav = self.openbook.get("nav")
        landmarks = nav.get("landmarks") if isinstance(nav, dict) else None
        if not isinstance(landmarks, list):
            raise ServiceResponseError("Openbook has no cover landmarks.")
        covers = [
            item for item in landmarks if isinstance(item, dict) and item.get("type") == "cover"
        ]
        if len(covers) != 1:
            raise ServiceResponseError("Openbook does not identify one unambiguous cover.")
        target = str(covers[0].get("path", "")).split("#", 1)[0]
        matched = [
            entry
            for entry in spine
            if target in (entry.get("path"), entry.get("-odread-original-path"), entry.get("id"))
        ]
        if not target or len(matched) != 1 or matched[0]["linear"] is not False:
            raise ServiceResponseError("Cover landmark and spine semantics disagree.")
        direction = self.openbook.get("i18n-page-progression-direction")
        return {
            "spine_components": len(spine),
            "cover_components": 1,
            "narrative_components": sum(entry["linear"] for entry in spine),
            "nonlinear_components": sum(not entry["linear"] for entry in spine),
            "reading_direction": direction if direction in ("ltr", "rtl") else "unknown",
        }


class OverDriveReadClient:
    """Own transient read-host cookies; never forward the API bearer token."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def _get(self, url: str, origin: str) -> httpx.Response:
        _read_url(url, origin=origin)
        request = self.client.build_request(
            "GET",
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Origin": "https://libbyapp.com",
                "Accept": "*/*",
            },
        )
        request.headers.pop("Authorization", None)
        try:
            response = self.client.send(request, follow_redirects=False)
        except httpx.HTTPError:
            raise ServiceResponseError(
                "Unable to establish a secure read-host connection."
            ) from None
        if response.status_code in (401, 403):
            raise LibbyAuthenticationError(
                "Read-host authorization failed; rerun ucd init --service libby-overdrive."
            )
        if response.is_error:
            raise ServiceResponseError(f"Read-host request failed (HTTP {response.status_code}).")
        return response

    @staticmethod
    def validate_resource(url: str, origin: str) -> None:
        _read_url(url, origin=origin)

    def resource(self, url: str, origin: str) -> httpx.Response:
        """Fetch a reader resource with bounded same-origin redirects."""
        for _ in range(8):
            response = self._get(url, origin)
            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise ServiceResponseError("Reader redirect has no target.")
                url = urljoin(url, location)
            elif response.status_code == 200:
                return response
            else:
                raise ServiceResponseError("Reader resource was not available.")
        raise ServiceResponseError("Too many redirects during reader acquisition.")

    def fetch_openbook(self, passport: dict[str, Any]) -> ReadRendition:
        urls = passport.get("urls")
        web = urls.get("web") if isinstance(urls, dict) else None
        message = passport.get("message")
        if not isinstance(web, str) or not isinstance(message, str) or not message:
            raise ServiceResponseError("Loan passport lacks read-host fulfillment information.")
        origin = _read_url(web)
        parsed = urlsplit(web)
        if parsed.query:
            raise ServiceResponseError("Loan passport has an unexpected reader URL.")
        url = web + "?" + message
        for _ in range(8):
            response = self._get(url, origin)
            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise ServiceResponseError("Reader handshake redirect has no target.")
                url = urljoin(url, location)
            else:
                break
        else:
            raise ServiceResponseError("Too many redirects during reader handshake.")
        response = self._get(web, origin)
        if response.status_code != 200:
            raise ServiceResponseError("Reader page was not available after fulfillment.")
        buid = (parsed.hostname or "").split(".")[0].removeprefix("dewey-")
        rendition = ReadRendition(web, decode_openbook(response.text, buid))
        rendition.summary()  # Refuse unsupported or ambiguous structure immediately.
        return rendition
