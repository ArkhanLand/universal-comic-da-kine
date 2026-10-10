# Libby / OverDrive Read fixed-layout comic retrieval

## Status and scope

This document records observed retrieval behavior, the acquisition boundary,
and the agreed CLI workflow for Libby titles delivered through OverDrive Read.
Browser-free setup, renewal, loan opening, and inspection passed live checks
on 2026-10-09 and were merged in PR #41. The `feat/libby-download` branch adds
CSS mapping, image acquisition, catalog normalization, acquisition records,
and CBZ routing. These additions passed offline tests and a live CBZ download
on 2026-10-09. The existing [core design](design.md) and [adapter
contract](adapters.md) apply.

The investigated rendition was *Mecha-Ude: Mechanical Arms, Volume 1*,
OverDrive title ID `11103570`. Its capture contained one cover and 192 narrative
JPEGs. All 193 assets were mapped, fetched, written, and verified against their
browser-computed SHA-256 hashes. These are observations about this rendition,
not a guarantee that every Libby title has the same structure.

Libby is the discovery/loan application; OverDrive Read is the delivery and
reader mechanism considered here. MediaDo is a distinct Libby delivery
mechanism and is out of scope. Reflowable books and other delivery formats
require separate investigation.

Three terms must remain distinct:

- **Spine component:** a reader document, such as `html/page053.xhtml`,
  carrying source ordering and rendition properties.
- **Image asset:** the JPEG response body referenced by that component's CSS.
  Its filename is provenance, and its exact bytes define object identity.
- **UCD Page:** the normalized reading unit associated with an image and a
  logical mapping. A component index or image filename is not a logical page
  number. One image need not universally represent one logical page.

## Reader, openbook, and BIF structure

The authorized Libby loan opens an OverDrive Read reader on a
`dewey-…read.libbyapp.com` host, embedded beneath `libbyapp.com`. The
openbook/BIF metadata describes the rendition. In the observed live reader,
`BIF.map` exposes publication metadata, `spine`, and `nav`; `BIF.objects`
contains runtime reader objects rather than a second catalog record.

The observed runtime component sequence was accessible through
`BIF.objects.reader._.context.spine._.components`. Loaded components have
iframes whose documents expose the shared stylesheets. These private runtime
paths are discovery details, not an API promise. Component metadata is a
reduced projection of the BIF map; loaded/rendered DOM elements alone are not
a complete publication inventory.

`BIF.map.spine` establishes authoritative order. Preserve its array order and
associate each component with its source path and image mapping. The observed
`-odread-spine-position` values matched zero-based spine indices; retain them
as corroborating source ordinals, not a replacement ordering or side signal.
Do not sort image filenames, CSS rules, TOC entries, or current DOM nodes to
reconstruct reading order. Runtime visibility filters used during discovery
must not silently replace spine semantics.

## Cover, narrative, and presentation semantics

`nav.landmarks` explicitly identifies the cover component. In this rendition
that target is `html/cover.xhtml`, whose spine entry has `linear=false`.
The other 192 entries have `linear=true` and form the narrative in spine
order. Cover selection uses the landmark and spine semantics together;
being the first entry or having a familiar filename is only corroboration.

The cover becomes a separate `Publication.cover`, with `Page.numbers=()`.
The ordered `linear=true` entries supply `Publication.narrative`. Additional
non-linear resources, if present, should be preserved as source structure
without automatically becoming narrative or covers. Missing or conflicting
role information requires explicit handling rather than positional guessing.
Narrative membership does not by itself prove a logical single-page extent or
printed pagination. The user confirmed that this particular manga has no
spreads: its narrative images represent logical pages 1 through 192, each
with a singleton `numbers` mapping, and its logical page count is 192.

For general imports, reuse Marvel's documented pagination policy rather than
assuming every component is a single page. This includes the 2% tolerance for
integer page-width multiples, the narrow single-page range of 94–100% at the
exact reference height, and the existing reference selection, orientation,
edge-band measurement, and unknown-mapping rules described in
[Marvel pagination](design.md#marvel-pagination). Explicit mappings take
precedence. Measurements must not modify the retained image bytes.

`BIF.map["i18n-page-progression-direction"]` is explicitly `rtl` for this
title, so `Publication.reading_direction` is `rtl`. This does not reverse the
spine. Direction controls presentation; previous/next still follow source
sequence order.

`first_page_side` remains unknown (`None`). No explicit `rendition-position`
was found across the investigated spine. `rendition-spread="auto"`, RTL,
viewport dimensions, and odd/even spine positions do not establish left/right
placement. A future title with explicit position metadata should be evaluated
at its first narrative entry before projecting that field.

`firstPlace` is an observed numeric BIF field whose semantics remain unknown.
Preserve its raw value; do not interpret it as a page number, initial side,
cover designation, or ordering rule.

## Component-to-image mapping

The shared CSS supplies the component-to-image relationship through
`background-image` rules for `#cover` and `#pageNNN`:

```text
spine component        CSS element    direct source asset
html/cover.xhtml       #cover         /images/cover.jpg
html/page002.xhtml     #page002       /images/<opaque>.jpg
...
html/page193.xhtml     #page193       /images/<opaque>.jpg
```

Resolve relative CSS URLs against the applicable stylesheet/document base.
Opaque image names need not encode order and must not be treated as content
hashes without verification. The XHTML suffixes are source identifiers;
`page002` is the first narrative component here, not UCD logical page 2.
Validate the complete spine-to-asset association before export, and reject
missing or ambiguous mappings rather than delivering a partial publication.

The observed source JPEG for one component was 810 × 1215 while its displayed
extent was 720 × 1080. Reader scaling therefore does not describe source
dimensions. Direct image retrieval did not require reconstructing screenshots
or decoding the page-content obfuscation used by the reader.

`BIF.map.cover.front` is a separate small cover manifest/thumbnail. Its media
type, byte count, dimensions, aspect ratio, representative color, and
last-modified information describe that representation. They must not be
assumed to describe the full cover JPEG selected through the landmark, spine,
and CSS. Inspect the extracted full cover independently.

### Publisher-named XHTML with inline images

A later live inspection of *Berserk, Volume 1* (title ID `3368897`) found
227 fixed-layout spine components: one cover and 226 narrative components,
with explicit RTL direction. The first two source documents used paths like
`OEBPS/miur_9781630089993_epub3_001_r1.xhtml`. Their decoded bodies contained
one ordinary `<img>` each, no element IDs, and no CSS background image rules.
The cover reference was relative to the document's `images/` directory;
the narrative image had an opaque filename unrelated to the XHTML name.

The initial Mecha-Ude-specific path check rejected this rendition before
fetching images. The corrected mapper accepts safe relative XHTML paths and
uses each document's single direct image reference as its association. Cover
and narrative roles still come from landmarks and spine linearity; source
spine order remains authoritative. The existing CSS-background binding is
retained for documents that use it.

Multiple direct images, SVG image compositions, or simultaneous inline and
background page images fail explicitly. They require a rendering/composition
policy before UCD can represent the whole component as one unchanged image.
The inline-image path has synthetic coverage, including encoded bodies and
relative bases. A full live download was verified on 2026-10-09: the CBZ
contained 227 JPEGs, and every image matched its cached original bytes,
recorded SHA256, and byte count. ZIP integrity checks passed. ComicInfo
reported 226 narrative pages, English, and `Manga=YesAndRightToLeft`.

I checked the resulting archive in Simple Comic and confirmed that
the images rendered correctly, page turning worked with RTL mode selected,
and a two-page spread aligned correctly. RTL mode was selected manually;
this check does not establish automatic detection of the ComicInfo flag.

## Exact bytes and verification

Libby fetching optionally overlaps requests through `--workers N` (any
positive integer, default 1). Device authentication and the reader handshake
finish before any worker starts. Workers share the established HTTPX reader
client, and each image has its own temporary filename. The bounded queue holds
at most N pending results and consumes them in source order. Component
documents can overlap; shared stylesheet resolution remains serial and fetches
each sheet once. All components are mapped before image acquisition starts.

Cache publication, capture updates, page assembly, and progress callbacks run
on the consumer thread. Workers attach the image's fetch-completion timestamp
so an acquisition receipt records that time rather than a later ordered cache
write. Bars count validated results consumed in source order; a slow earlier
request can briefly hold up visible progress even if later requests finished.
On failure, queued work is cancelled and running requests finish before their
session or temporary directory closes. A partial capture stays incomplete,
and no partial CBZ is published. Tests exercise overlapping requests that
finish out of order, bounded scheduling, and cancellation. Live speed and
output comparisons were completed on 2026-10-09.

### Live concurrency measurements

The test machine was an Apple M1 Max MacBook Pro with 10 physical and 10
logical CPU cores: 8 performance cores and 2 efficiency cores. It had 32 GiB
of RAM (`34359738368` bytes) and ran macOS 26.7.1, build `25G241`. These values
came from my `sysctl` and `sw_vers` output. Worker counts describe
concurrent network fetches and are independent of CPU core count.

Each run freshly fetched the same three titles in input order: Berserk
(`3368897`, 227 images), Dorohedoro (`4247083`, 170 images), and Mecha-Ude
(`11103570`, 193 images), for 590 images total. The command included
`--refresh --overwrite`; authentication, mapping, acquisition, and CBZ writing
are all included in the shell's elapsed time.

| Workers | Elapsed (s) | User CPU (s) | System CPU (s) |
| ---: | ---: | ---: | ---: |
| 4 | 116.149 | 19.184 | 3.173 |
| 8 | 57.566 | 15.287 | 2.561 |
| 16 | 37.134 | 12.619 | 2.373 |
| 32 | 24.087 | 12.154 | 3.288 |

Reproduce a run from an environment with a verified saved Phoenix card and
these active checkouts, changing the worker count for each comparison:

```bash
time ucd download --service libby-overdrive --library-card phoenix \
  3368897 4247083 11103570 --workers 32 \
  --output-dir ~/E-Books/Parallel-Test/ --refresh --overwrite
```

Capture the machine details with:

```bash
sysctl -n machdep.cpu.brand_string
sysctl hw.physicalcpu hw.logicalcpu hw.memsize
sysctl hw.perflevel0.name hw.perflevel0.physicalcpu \
       hw.perflevel1.name hw.perflevel1.physicalcpu
sw_vers
```

All four- through sixteen-worker output archives passed ZIP integrity checks;
every image's bytes and archive member order matched the preceding sequential
copies. After the 32-worker run, the available Berserk and Dorohedoro outputs
also matched; Mecha-Ude was available under a sync-conflict filename and that
copy matched as well. The original 32-worker Mecha-Ude filename was no longer
present at verification time, so that copy's association with the timed run
is not independently established.

I saw a noticeable improvement during mapping. These are single
live observations rather than controlled benchmarks with repeated trials;
network conditions were not measured. There is no recorded sequential elapsed
time, so no speedup relative to one worker can be calculated. Going from 16
workers to 32 reduced the observed elapsed time by about 35%.

An initial live Dorohedoro download (title ID `4247083`) mapped 170 images
but failed when acquiring the cover. A header-only probe found an HTTPS 302
redirect from the reader host to `odrresources.cachefly.net`, with a different
path and no query. Image acquisition now accepts redirects to that exact
HTTPS hostname on the default port or port 443. Each image must start on the
authorized reader origin; documents and stylesheets retain the same-origin
rule. Redirects remain bounded, and CDN requests carry neither Authorization
nor Cookie headers. Synthetic tests verify original-byte preservation through
this redirect and reject other hosts, HTTP, userinfo, and fragments. The next
live batch completed Dorohedoro acquisition at 170/170 images and wrote its
CBZ, then completed Mecha-Ude at 193/193 images and wrote its CBZ. The existing
Berserk archive was skipped. I subsequently visually checked all three manga
volumes: Berserk, Dorohedoro, and Mecha-Ude. All three rendered correctly.
The earlier Berserk check also confirmed RTL page turning with RTL mode
selected and correct two-page spread alignment in Simple Comic.

Fetch mapped image resources through authorized fulfillment and retain the
exact response-body bytes. The research did this inside the browser reader;
the adapter fetches them directly. Compute SHA-256 over acquired bytes before
publishing them to the cache or a capture envelope. No canvas export,
screenshot, resizing, JPEG re-encoding, or metadata rewrite belongs in this
acquisition path. Image decoding for inspection must not replace the stored
bytes.

Record each asset's component association, spine index, role, sanitized source
asset path/name, local capture member, content type, byte count, and SHA-256.
Record successful acquisition times in UTC with the available precision,
separately from publication dates and source last-modified times. The receiver
recomputes hashes from the stored bytes and checks completeness, associations,
and sizes before normalization. Hash agreement verifies byte preservation
through capture; it is not independent proof of publisher authenticity.

The investigated ZIP contained `cover.jpg`, 192 `pages/*.jpg` files, and
`libby-manifest.json`. That generated ZIP is an acquisition envelope, not a
publisher-supplied original EPUB or other source container. The JPEG response
bodies are the acquired source objects. Capture member names describe storage;
they do not define reading order or logical pagination. Envelope names and a
versioned manifest schema remain implementation decisions.

## Direct acquisition and normalization boundary

The agreed user workflow is direct, browser-free acquisition:

```text
ucd init --service libby-overdrive
ucd download --service libby-overdrive 11103570
```

`LibbyOverDriveReadAdapter` uses saved authentication to verify the active
loan, obtain authorized OverDrive Read fulfillment, retrieve openbook/BIF and
CSS resources, resolve the spine-to-image associations, and fetch source
assets. Acquisition then verifies/caches exact objects, inspects image
properties, normalizes supported metadata, and returns a complete
`Publication` through `get_publication()`. Output consumes Publication and
available bytes through the existing adapter contract.

Manual browser capture is a research/reference-fixture path, not a required
user step or the normal adapter input. In the investigation, a cross-origin
reader frame could not use the directory picker; a generated ZIP supplied a
working transport. That successful capture established mapping and exact-byte
retrieval inside an authorized session. The later standalone UCD check verified
loan opening and reader metadata retrieval, as detailed below. That check did
not download images or produce a Publication/CBZ; full browser-free download
support must still be implemented and validated.

The locally installed npm package `libby-archiver` 0.4.0 provides a concrete
research starting point. Its setup resolves a library key, links a card using
its number and PIN, attempts to bootstrap a reusable identity through chip and
sync-code operations, and caches a session token. These are observations from its source,
not a supported service API or a commitment to copy its implementation. Its
automatic insecure-TLS fallback must not be copied; certificate verification
remains enabled. The package's extraction stalled at 0/193 for the investigated
rendition, which is why browser JavaScript was used for the successful capture.

This source supplies multiple objects and fits the architectural boundary
in the design. General repository persistence and raw metadata attachments
remain planned capabilities; this document introduces no new model fields.

## Setup, saved cards, and authentication failures

Setup prompts for the library key (the slug in
`libbyapp.com/library/<key>`), card number, and PIN/passcode, then verifies
authentication. The investigated setup used `phoenix`; this lookup key is
distinct from the observed Thunder library identifier `phoenix-phoenixpl`.
Resolve the library rather than assuming these identifiers are interchangeable.

Support multiple saved card connections. Default a connection's readable name
to its library key; ask for a distinct name when adding a second card at that
library. `--library-card <name>` selects a saved connection during setup or
download. Without an explicit selection, search cards in saved preference
order for an active checkout and use the first match.

Store card numbers, PINs, and reusable authentication tokens in the system
credential store. Ordinary UCD configuration holds connection names, library
identifiers, and preference order. Target terminal use on macOS, Windows, and
desktop Linux with a usable system credential store. Headless/SSH usage and
file-based credential fallbacks are out of scope for now; unavailable or locked
credential storage must produce a clear error.

Normal session expiry may trigger automatic renewal using stored credentials.
Outside `ucd init`, any authentication/credential failure is a hard failure
for the whole command, even with `--continue-on-error`. Do not prompt for
replacement credentials during download. Identify the affected saved connection
and ask the user to repair it, for example:

```text
ucd init --service libby-overdrive --library-card phoenix
```

The shared setup pattern should also accommodate
`ucd init --service marvel-unlimited`, with saved authentication replacing the
need for a manually supplied cookies file. Marvel login/authentication research
and implementation are separate work; this is an agreed interface direction,
not a claim of current support.

## Verified browser-free protocol

The following path was verified on 2026-10-09 against title `11103570` using
Libby's client version `22.1.2`. UCD renewed a saved identity, opened the active
loan, established reader access, decoded openbook, and reported 193 spine
components: one non-linear cover, 192 linear narrative components, and explicit
`rtl` progression. No image assets were downloaded by this check. Fresh
card-number/PIN setup also passed after the header fix: `ucd init` created,
linked, renewed, verified, and saved a new session, and a following inspection
returned the same counts and direction without a renewal retry.

These are private, observed endpoints and formats. The implementation locations
are `src/ucd/auth/libby.py` and `src/ucd/input/libby_overdrive_read.py`; their
synthetic tests are `tests/test_libby_auth.py` and `tests/test_libby_read.py`.
The public official-client source examined was
[`dewey-22.1.2/src/main.js`](https://libbyapp.com/dewey-22.1.2/src/main.js).
Recheck the protocol when the service changes rather than assuming these
observations apply to every version or delivery mechanism.

### Gateway, library lookup, and card linking

Use `https://sentry.libbyapp.com` for the API with normal TLS and hostname
verification. The legacy `sentry-read.svc.overdrive.com` resolved, but presented
a certificate for `*.odrsre.overdrive.com` that did not cover that hostname.
Disabling verification is not the remedy. The speculative replacement
`sentry-read.odrsre.overdrive.com` did not resolve and is not an established
endpoint.

Resolve the setup slug through Thunder's
`GET https://thunder.api.overdrive.com/v2/libraries/<key>`. The observed
`phoenix` lookup identifies Greater Phoenix Digital Library, `websiteId=34`.
The official client obtains authentication choices through
`GET /auth/forms/<website-id>`. This consortium returned several local ILS
choices; `ilsName=phoenix` specifically means Phoenix Public Library. A catalog
lookup slug is not a general substitute for the selected form's `ilsName`.
Current UCD submits the lookup key; that shortcut was confirmed for this card,
not for arbitrary consortium members.

The direct setup sequence is:

1. `POST /chip?c=d:22.1.2&s=0`, with no bearer token, creates a device. Keep
   the response's `chip` and `identity` in memory. `chip` is a device identifier;
   `identity` is the bearer JWT, not a TCP connection.
2. `POST /auth/link/<website-id>` with `Authorization: Bearer <identity>`
   links the card. Send JSON with `ils` set to the chosen ILS name, `username`
   set to the card number, and `password` set to the PIN/passcode. The observed
   successful browser request used `ils=phoenix` and no `captcha` field.
   Additional authentication requirements must be handled explicitly if a
   different library returns them.
3. Renew the same device with `POST /chip?c=d:22.1.2&s=0&v=<device-prefix>`,
   authenticated with the current identity and the special header below.
   `v` is the first hyphen-delimited segment of the full device ID; retain the
   full ID for consistency checks.
4. Require the response's `chip` and the replacement JWT's `chip.id` to match
   the existing full device ID. Require the expected card association, then
   verify `GET /chip/sync` with the replacement bearer token. The response must
   have `result=synchronized` and a loan list before saving the session.

UCD's fresh bootstrap currently requires one unambiguous linked-card tuple.
Observed tuple positions used by the implementation are card ID at index 1,
website ID at index 4, and the canonical linked-card library key at index 5.
The last key can differ from the setup slug: this card used
`phoenix-phoenixpl`. Other tuple positions remain undocumented; do not invent
normalized fields from them. Existing-device renewal checks that the selected
card remains present rather than requiring every device to have only one card.

API requests use `Accept: application/json`, `Origin: https://libbyapp.com`,
and a desktop user agent; JSON card-link requests have the corresponding
content type. Normal browser language headers and user-agent imitation alone
did not fix renewal. No Cookie header was present on the observed successful
browser renewal; UCD keeps the API requests bearer-only and uses a separate
cookie jar for reader fulfillment. There was no Authorization response header:
the replacement identity is in the `/chip` JSON response.

### Required `/chip` request header

The decisive difference is the official Sentry request transformation in
`obf/shib.js`, called by `app/base/services/service-sentry`. Chip acquisition
sets the internal request marker `path="chip"`. The transformation consumes
that marker and overwrites the outgoing **`Accept-Language`** header with two
characters derived from the current identity. The marker is not an additional
HTTP query parameter.

For a fresh device with neither identity nor chip, use the public fixed seed
`cudlkahllcnsjxhbmddl`; its derived header is `bh`. For renewal, use the entire
current JWT string, including its encoded segments and signature, without the
`Bearer ` prefix. This documentation example reproduces the transformation:

```python
import re

seed = current_identity or "cudlkahllcnsjxhbmddl"
accept_language = re.sub(r"[^a-z]", "", seed)[::-1][4:6]
```

Keep only characters already in ASCII `a` through `z`; do not lowercase the
input or derive the value from decoded JWT claims. Reverse the filtered string
and take indices 4 and 5. Four is the length of the internal `chip` path marker.
Recompute for each chip request using the identity sent in that request. Apply
this transformation to chip creation/renewal, not indiscriminately to card
linking, loan opening, Thunder, or read-host resource requests. The current
Python implementation does this for `POST /chip`.

The official source also has a chip-without-identity seed branch and an
automation-dependent alternative. Those branches were not needed to establish
UCD's working path and are not interchangeable with normal identity renewal.
In particular, the source's speculative `r=<full-chip>` recovery path returned
HTTP 400 in this investigation and is not implemented as UCD's renewal fallback.

A controlled live comparison renewed the same browser-created identity with
ordinary headers and with only this header changed. Ordinary headers returned
one card and `prbn=v`; the token-derived header returned the same device and
card with no `prbn` field. Correcting the header also removed `prbn` from the
saved UCD identity. UCD subsequently opened the loan successfully. This
establishes the request requirement. We call the transformation **client
validation**: reproducing the official request behavior in addition to holding
a bearer token. It introduces no independent secret. Discouraging alternate
clients is a plausible purpose, but the service's intent is not confirmed.
The meaning of `prbn`, `pri`, or `ag` remains unknown. An absent `prbn` field
is not an invalid identity. Temporary token-claim summaries used during this
investigation have been removed from CLI output; normal progress messages and
rendition inspection results remain.


### Loan lookup, opening, and bounded renewal

Use fresh `/chip/sync` data to find the requested title on the selected card
and require a valid, future loan expiry. Missing or expired checkouts must
fail without borrowing. Require the supported OverDrive Read delivery format
before opening; fixed layout is checked after decoding the rendition.

Build the opening request as
`GET /open/book/card/<card-id>/title/<title-id>` with the saved bearer token
and query parameters `t` and `website_id`. `t` is standard Base64 of UTF-8 JSON
with this structure; angle-bracket values are placeholders, not literal data:

```json
{
  "codex": {
    "title": {"titleId": "<title-id>", "slug": "<title-id>"},
    "loan": {
      "psnKey": "<card-id>-<title-id>",
      "slug": "<card-id>-<title-id>"
    },
    "library": {"key": "<linked-card-library-key>", "name": "<library-name>"}
  },
  "dewey-url": "https://libbyapp.com",
  "spec": "V31"
}
```

Use the canonical key from the tuple matched to this card and website, not an
unrelated card or the catalog lookup slug. The browser codex also contained
cover/logo/color fields; UCD's smaller structure above succeeded without them.
The implementation adds `Sec-Fetch-Site: same-site` and `Sec-Fetch-Mode: cors`
when opening the loan. All URLs, encoded loan context, and bearer tokens remain
transient.

Linking a card does not mutate a JWT already held by the caller. The fresh
browser's first opening request used a token with no card claims and `prbn=i`:
HTTP 403, `result=missing_chip`. It renewed the same device, received a token
with the card and no `prbn`, then retried successfully with HTTP 200. The
initial failure and subsequent success did not identify different devices.

UCD mirrors the official `_requestWithChip` handler: on `missing_chip`, renew
that existing identity with the correct header, validate the device and
selected card, persist the replacement identity in the credential store, and
retry the same opening request once. Repeated rejection is a hard failure;
other credential failures do not trigger this retry. This is token renewal,
not a reason to clone devices, relink the card, prompt for credentials, or
borrow a title. A successful loan list alone does not prove the identity can
open a loan.

Cloning was a false lead from the npm reference. Both direct linking and
cloning could list loans while opening failed. A clone-code experiment returned
`result=cloned` and the original chip, rather than the blessing expected by a
different official UI transfer flow. Neither switching device tokens nor
copying ordinary browser headers solved the failure. UCD removed the cloning,
blessing, and `UCD_LIBBY_AUTH_MODE` experiments after the header was verified.
Do not reintroduce them as prerequisites for card-number/PIN authentication.

### Reader handshake and embedded metadata

The successful opening response is a transient passport with `urls.web` and
`message`. `urls.web` identifies the authorized
`https://dewey-<buid>.read.libbyapp.com/` reader location; `message` is the
fulfillment query string. Keep both private. With a separate HTTP client and
cookie jar, request `urls.web + "?" + message`, retain any reader cookies, and
follow the allowed read-host redirects. The current client permits at most
eight handshake responses, checks every redirect before requesting it, and
rejects a different origin, non-HTTPS URL, embedded credentials, or a different
delivery host. An HTTP 200 handshake is valid; a redirect is not required.

After the handshake, request the clean `urls.web` URL using that cookie jar
and require HTTP 200. API bearer Authorization must never be forwarded to the
reader host. Current reader requests use a desktop user agent,
`Origin: https://libbyapp.com`, and `Accept: text/html`. The page can contain
encoded openbook even if the literal word `openbook` does not appear in HTML.

The fixed-layout reader's live page did **not** assign a literal array directly
to `window.eData`. It used the following wrapper, with the actual encoded
strings replaced here by placeholders:

```javascript
(function (d) {
  try {
    Object.defineProperty(window, 'eData', {
      value: d,
      writable: true,
      enumerable: true,
      configurable: true
    });
  } catch (e) {
    window.eData = d;
  }
})(["<encoded string>", "<encoded string>"]);
```

`d` is the function parameter bound to the literal invocation argument. It is
not an ordinary `d=[...]` assignment elsewhere in the script. Searching the
whole HTML for `d=` found unrelated loop counters and accidental matches in
encoded strings. Do not use those matches to reconstruct metadata.

Recognize either the direct `window.eData=[...]` form or this observed wrapper.
For the wrapper, verify that the function parameter, `value` property, and
fallback assignment reference the same identifier, then extract the literal
array passed to the invocation. Parse quoted strings and escapes only; reject
expressions, function calls, malformed literals, and inconsistent bindings.
Never evaluate the reader script. Brackets inside quoted strings do not end
the array. Do not require `SPARK.bifocalPath` to immediately follow the array;
other assignments or the wrapper can intervene. The npm parser's direct-array
regular expression did not recognize this live comic-reader form.

### Decoding the extracted string array

Derive `buid` from the read-host hostname by removing `dewey-` and the remaining
host suffix. Preserve the entire intervening value, including any hyphens;
it is transient rendition context, not a publication or asset ID.

Decode as follows, matching `decode_openbook()`:

1. Decode the quoted JavaScript string literals, including supported standard,
   hexadecimal (`\xHH`), and Unicode (`\uHHHH`) escapes. Concatenate the
   resulting array elements using one double-quote character (`"`) between
   elements. Do not simply concatenate without a separator.
2. Reverse `buid` to form the repeating key. For each character at zero-based
   index `i`, take key character `key[i % len(key)]` and the character code.
3. If the key character is `1` through `9`, add `(i + int(key_character)) % 94`.
   If the resulting code exceeds 126, replace it with `(code % 126) + 32`.
   Key character `0` and nonnumeric characters leave the code unchanged.
4. Strictly Base64-decode the transformed string, decode UTF-8, and parse JSON.
   Require the outer value to be an object and its `b` member to be an object.
   That `b` object is the decoded openbook/BIF map.
5. Validate the rendition before interpreting it: a nonempty spine, supported
   fixed layout (`rendition-layout=pre-paginated`), explicit boolean `linear`
   values, and one unambiguous cover landmark matching a non-linear component.
   Reading direction comes from the explicit openbook property, not filename
   order or the direction of the decoding key.

The decoded object can contain fulfillment secrets alongside safe metadata.
Apply the allowlist below before retaining anything; successful decoding does
not make wholesale serialization safe. No decryption or re-encoding of image
JPEGs was needed in the earlier browser capture. CSS-to-image mapping and
exact image fetching are separate from decoding this metadata envelope.

### Reproduction and validation

With UCD installed from this checkout and a supported credential store:

```bash
ucd init --service libby-overdrive --library-card phoenix
UCD_DEV_COMMANDS=1 ucd inspect-loan --service libby-overdrive 11103570
```

The merged CLI contains `list-loans` with synthetic tests and verified command
registration. A live check on 2026-10-09 listed three active checkouts on the
selected Phoenix connection, including title IDs, titles, and delivery
formats, without borrowing or downloading. Listing is optional when
reproducing inspection:

```bash
ucd list-loans --service libby-overdrive --library-card phoenix
```

Enter credentials only in the setup command's hidden prompts. An existing
valid connection can start at inspection; a saved token may first need the
bounded renewal described above. The successful inspection reported:

```text
Spine components: 193
Cover components: 1
Narrative components: 192
Nonlinear components: 1
Reading direction: rtl
Loan inspection complete. No image files were downloaded.
```

The synthetic tests cover initial/renewed chip headers, unchanged device/card
associations, bounded retry, credential handling, reader-cookie isolation,
direct and wrapped arrays, quoted brackets and escapes, rejected executable
expressions, layout/cover validation, and explicit direction. Run them with:

```bash
python -m pytest tests/test_libby_auth.py tests/test_libby_read.py
```

Use live checks to verify service behavior; mocked tests alone did not expose
the missing header or the wrapper. For comparison diagnostics, keep tokens and
signed URLs in hidden local prompts and report only status, structural names,
counts, and equality checks. Local source viewing helped identify the wrapper,
but raw reader HTML, encoded arrays, and full network logs must remain outside
Git and persisted acquisition metadata.

## Download, output, and failure policy

Only download titles already checked out on the selected/matching card. UCD
must not borrow automatically. A missing active checkout is a title failure.
Only the supported OverDrive Read fixed-layout image rendition is accepted;
MediaDo, reflowable books, and other delivery formats fail explicitly.

Process multiple title IDs sequentially in command-line order. This is an
agreed command-layer rule for all input adapters, not Libby-only scheduling.
By default a title failure stops processing. `--continue-on-error` reports
that failure and moves to the next title, preserving successful downloads;
the final exit status is nonzero if any title failed. Authentication failures
always stop the entire command regardless of that option.

`download` defaults to CBZ output. `--output-format cbz` selects it explicitly;
other values fail as unsupported until another output format is implemented.
Use Marvel's existing output filename convention and `--output-dir` option.
Mirror its handling of existing output: without `--overwrite`, report a
successful skip and proceed without acquiring image assets. `--refresh`
controls image acquisition independently and does not authorize overwriting
an output file.

For titles being acquired, fetch current loan, BIF, and Thunder metadata on
every run, even when all image assets can be reused. Metadata establishes the
current checkout, rendition structure/order, and bibliographic values.
Retain each successfully verified image if a later acquisition fails so that
a rerun can reuse it. Publish the final CBZ only after all required assets and
metadata are complete; do not leave a partial destination CBZ on failure.

## Shared asset cache and reuse policy

Use one discoverable cache root for all services, with readable service and
title directories, for example:

```text
ucd/
  libby-overdrive/
    11103570 — Mecha-Ude Mechanical Arms Volume 1/
  marvel-unlimited/
    39895 — Title/
```

Use the platform's standard cache location, allow an explicit location override,
and provide `ucd cache where` to display the root. Exact platform paths and
option spelling for the override remain implementation decisions. Sanitize
readable title labels for the filesystem; service/title identifiers establish
identity, so title changes must not orphan acquisitions or make a second
logical cache identity.

Within a title directory, keep exact image objects identified by SHA-256 and
versioned acquisition manifests recording roles, component associations,
original safe asset paths, reading order, properties, and fetch times. This
layout is a usability decision, not a general Repository schema or a change
to the distinction between source objects and logical Pages. Credentials never
belong in the asset cache.

SHA-256 identifies the exact stored bytes independently of the acquisition
URL, card, or session. A provider lookup is a separate association used to
avoid refetching bytes. The proposed lookup comprises title ID, a reliable
rendition identity, and original image asset path; do not use spine position.
Before enabling reuse, compare rendition/asset identifiers across at least two
sessions. If stability or rendition identity cannot be established, fetch
again and deduplicate against existing objects by SHA-256 afterward.

When identifiers are validated, follow Marvel's reuse policy: use fresh
metadata to locate acquisitions, verify local object hashes before reuse,
and refetch missing/corrupt objects. `--refresh` bypasses reuse. A replacement
served under unchanged provider identifiers may remain undetected until
refresh if the service supplies no trustworthy hash or revision marker.
Locally calculated SHA-256 alone cannot establish the freshness of an asset
that has not been fetched.

## Download implementation and remaining verification

The download branch supports `ucd download --service libby-overdrive TITLE_ID`
and canonical title share URLs. It requests current loan, Thunder, and reader
metadata, then fetches each signed component document with the reader cookie
jar. A same-origin document base establishes stylesheet resolution. The
observed component-body transform swaps characters one and four in each
four-character group and then decodes base64 and UTF-8; no JavaScript
executes.

Two fixed-layout image mappings are supported. The observed Mecha-Ude
`html/cover.xhtml` and `html/pageNNN.xhtml` documents bind to corresponding
page element IDs; their stylesheets supply one background image URL. Other
publisher-named XHTML documents can supply a single direct `<img src>` without
an element ID. Resolve that reference against the document URL or its
validated same-origin `<base href>`. Never derive an image name or page
identity from a publisher filename. Plain ID selectors and a single background
URL are accepted; conflicting mappings, conditional image rules, unsafe
document paths, foreign origins, and invalid image bytes fail before
publication. Stylesheets are fetched once per distinct URL during an
acquisition. All associations are validated before image retrieval starts.

Images are inspected with Pillow and saved without re-encoding. JPEG/PNG MIME
and decoded format must agree. Libby and Marvel share the existing measured
pagination policy. The complete normalized publication goes through existing
metadata preparation and CBZ output. A temporary archive is published only
when writing succeeds. Missing explicit issue numbering uses the full Libby
title for the output filename, retaining series metadata without making a
series position into a volume number.

The platform cache root is displayed by `ucd cache where`, overridden by
`--cache-dir` or `UCD_CACHE_DIR`. Libby title directories use stable numeric
IDs; capture manifests contain the readable title. ImageCache objects retain
exact byte hashes and per-fetch UTC receipts. Version-one capture manifests
associate component, spine index, role, safe image path, object path, receipt,
byte count, dimensions, media type, and mode. Each verified image updates an
incomplete manifest. Only a complete acquisition publishes `complete=true` and
the allowlisted catalog/reader source projection. Failed acquisition keeps
verified originals and their receipts; it never writes a partial destination
CBZ. This is an internal schema, not an original publisher container or the
future general historical repository.

Network-saving Libby reuse remains disabled until rendition/asset identifiers
are checked across sessions. Every fetch currently receives a distinct lookup
key; identical verified bytes still share the same SHA-256 object. No delivery
URL, card context, cookie, or bearer value is persisted. This conservative
choice does not yet provide network-saving resume after a partial download.

Live checks confirmed the first two components share a stylesheet with 193
background image rules (cover plus 192 pages), and use encoded component
bodies with array-indexed authorization parameters. Live parser checks of the
first two components found their expected elements and all 193 image mappings
unambiguously. A full live download of title `11103570` then completed. Local
verification found one full cover and 192 narrative JPEG entries, a valid ZIP
CRC check, and exact agreement between every archived image, its cached
original, and the acquisition SHA-256/byte-count record. ComicInfo reported
PageCount 192, RTL manga, language `en`, publisher `Scholastic Inc.`, and
imprint `Graphix`; the ZIP comment contained generated metadata. This
establishes successful acquisition/output for that rendition, not support for
every Libby title. The full manga and private source payloads must stay out of
deterministic fixtures and Git.

## Sanitized acquisition and capture metadata

Retain reader metadata and catalog metadata separately so their original
meaning and source precedence remain inspectable. Use an allowlist for BIF
publication fields rather than serializing `BIF.map` or the runtime wholesale:

- `title` (including main, subtitle, collection), `creator` (including roles
  and biographies), `description.short/full`, and `language`;
- `i18n-page-progression-direction`, `rendition-format`, and raw `firstPlace`;
- ordered `spine` entries with `id`, sanitized `path`, `linear`, `media-type`,
  `-odread-original-path`, `-odread-spine-position`, `-odread-file-bytes`, and
  observed rendition layout, orientation, spread, viewport, and position;
- `nav`, including TOC and landmarks with their titles, types, and sanitized
  component targets;
- `cover.front` technical fields and `-odread-cover-color` /
  `-odread-cover-ratio`, associated with the representation they describe.

Allowlisting includes inspecting nested URLs and values. Preserve the Thunder
media record as raw source metadata after removing any fulfillment/session
material. Retain useful catalog fields without forcing all of them into
Publication. Availability, holds, and visitor-specific nearby-library results
are contextual service data, not normalized publication identity.

Never persist fulfillment/session secrets in manifests, source metadata,
logs, fixtures, documentation, or committed files. Excluded material includes
cookies, authorization headers, card/loan context, signed query strings,
loan-opening URLs, and values of `-odread-bank-verification-token`,
`-odread-bonafides-*`, `-odread-msg-access`, `-odread-msg-sync`, and
`-odread-cmpt-params`. Other runtime access/message/fulfillment fields require
the same treatment. Read-host URLs are ephemeral acquisition locations, not
publication identity; keep only safe asset paths/names as durable provenance.
Do not export a full network log or credential-bearing openbook payload.

## Thunder catalog metadata and public identity

The reader was observed requesting this endpoint on
`https://thunder.api.overdrive.com`:

```text
/v2/libraries/<library>/media/<title-id>?x-client-id=...
```

The investigated path was
`/v2/libraries/phoenix-phoenixpl/media/11103570`; its observed request had
only the `x-client-id` query parameter and returned HTTP 200. This records an
observed endpoint, not a supported public API or a promise that future
requests need no authorization. Persist the title/library provenance when
useful, not the request's session state or query values.

Useful fields include `id`, `title`, `sortTitle`, `series`, `detailedSeries`,
`creators`, `languages`, `publishDate`, `publishDateText`,
`estimatedReleaseDate`, `publisher`, `imprint`, `description`, `ratings`,
`subjects`, BISAC fields, and `formats`. Format records can supply `id`,
`name`, `isbn`, `identifiers`, `fulfillmentType`, `onSaleDateUtc`, `fileSize`,
and `accessibilityStatements`. Preserve distinctions between catalog dates
and format release dates, and retain their precision.

Use the stable OverDrive title ID as `service_id`. Construct the canonical
public `source_url` deterministically from that ID:

```text
https://share.libbyapp.com/title/<title-id>
https://share.libbyapp.com/title/11103570
```

The latter was confirmed to show this title and library availability. Prefer
this title-level provenance over a Libby shelf route, loan-opening URL, or
read-host fulfillment URL. Nearby-library suggestions are visitor-specific
and do not become publication metadata. Reader BUIDs and checkout identifiers
must not replace the title ID.

## Source precedence and deferred normalization

BIF is authoritative for rendition structure and reading semantics; Thunder
is preferred for catalog/bibliographic metadata. Preserve both source values
where they overlap so disagreements can be inspected rather than erased.

| UCD concept | Source and rule |
|---|---|
| Service | CLI service selector `libby-overdrive`; Python adapter `LibbyOverDriveReadAdapter` (internal service-field spelling remains to be settled) |
| Service title ID | Thunder `id`, matched to the captured title |
| Public source URL | Share URL constructed from the title ID |
| Service series ID | Thunder `detailedSeries.seriesId`, when present |
| Title and series | Thunder title and structured series; BIF corroborates or supplies missing values |
| Creators | Thunder creator records; BIF corroboration/fallback |
| Publication date | Thunder `publishDate`, preserving known precision |
| Publisher / imprint | Thunder `publisher.name` / `imprint.name` |
| Language | Thunder language records, corroborated by BIF |
| Description | Thunder description; fallback BIF `description.full` |
| Age rating | Thunder `ratings.maturityLevel.name`, when applicable |
| Reading direction | Explicit BIF `i18n-page-progression-direction` |
| First narrative side | Explicit rendition position if established; otherwise unknown |
| Cover / narrative membership | BIF landmarks and spine `linear` semantics |
| Reading order / navigation | BIF spine order / BIF nav respectively |
| Image objects | Exact read-host image response bodies |

The investigated Thunder record supplied publisher `Scholastic Inc.`, imprint
`Graphix`, publication date `2025-04-15`, language `en`, and maturity level
`Juvenile`. `publisherAccount` describes a supplier/account relationship and
does not override `publisher.name`. `reserveId` is an alternate internal
identifier worth retaining raw; it does not supersede title `id`.

Preserve these source fields without normalizing them yet:

- TOC/nav structures: navigation over the spine, not alternate reading order.
- ISBN and other identifiers, including their format association. The
  observed OverDrive Read ISBN was `9781546142577`; Kindle, OverDrive Read,
  and Kobo format records do not imply identical bytes or manifestations.
- Subjects and BISAC values: do not collapse them into genres or tags.
- `detailedSeries.readingOrder`: a series position, not automatically a
  volume number. The observed value `"1"` agrees with the title's Volume 1
  but does not establish a general conversion rule.
- Accessibility statements and other accessibility metadata: retain their
  source association without inventing a normalized capability claim.

Retaining raw safe metadata allows later model extensions without corrupting
source meaning. Unknown presentation fields remain unknown, and future
normalization policy must be explicit about inference and loss.
