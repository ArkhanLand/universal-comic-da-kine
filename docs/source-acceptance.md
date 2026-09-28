# Source acceptance and native semantics

This is the target acceptance policy. Today Marvel Unlimited and local CBZ
inputs and CBZ output are implemented. Native eligibility is not a claim of
adapter availability or complete enforcement in the current code.

Acceptance depends on capabilities and presentation semantics, not a filename
extension. A candidate must supply static reading units, a meaningful primary
order, and enough information to normalize their appearance without silently
removing essential semantics. Adapters must inspect the actual content and
report unsupported features or ambiguous ordering.

| Source category | Native in principle? | Required interpretation |
| --- | --- | --- |
| CBZ/comic image archives | Yes | Validate comic structure, static images, and deterministic reading order; random ZIP is not CBZ. |
| PDF | Yes, for static pages | Preserve discrete page appearance through faithful extraction or explicit rendering policy. |
| Fixed-layout EPUB | Yes, for static content | Interpret package/spine, layout, resources, covers, and spread properties. |
| Static webcomics/services | Yes | Acquire static assets and reading order; no downstream service dependency. |
| Static image sequences/directories | Yes, when a readable work | Establish order and publication intent; do not infer these from image presence alone. |
| Single image | An asset, not necessarily a publication | Explicitly identifying a one-unit readable work can establish a publication. |
| Continuous-scroll static work | Yes | Preserve ordered static content; viewport movement is reader-controlled. |
| Reflowable EPUB | Not inherently | Selecting a viewport, fonts, and pagination fixes one rendering and loses reflow semantics. |
| Animated images, video, audio | No | Time, motion, or sound cannot be represented natively. |
| Timed or interactive works | No, when behavior is essential | A static snapshot cannot preserve the original interaction or clock. |

Container eligibility does not bless every feature inside it. An animated
image inside a fixed-layout EPUB is still non-native. A PDF's discrete static
pages fit, but required embedded media, scripts, or dynamic forms do not.
Ordinary reader navigation, search, and static links do not by themselves
introduce a playback clock or disqualify a static publication.

The current local archive subset and rejection rules are documented in
[CBZ input](cbz-input.md). Eligibility above remains broader than implementation.

## Explicit conversion and loss reporting

Default native normalization must reject unsupported essential semantics or
request an explicit conversion policy; it must not silently pick the first
frame. Static conversion can select frames, freeze a defined state, or render
reflowable text with declared layout settings. Record:

- Original source object hashes and acquisition context.
- The requested policy, selected state/frame, and relevant rendering settings.
- Derived object hashes, tools/libraries/versions, and ordered operations.
- The semantic losses: animation, synchronization, audio, interaction,
  reflowability, or other omitted behavior.

The result can be a native static Publication; the original remains
non-native. Retaining original bytes does not make this conversion
semantically lossless. History and byte retention follow the [repository
policy](design.md). No such general conversion or loss-reporting API is
implemented yet.

## PDF and EPUB details

PDF is native because its pages are discrete and static, not because it is an
image archive. Extract an embedded raster only when it faithfully represents
the complete page. Otherwise render text, vectors, masks, layers, and
composition under an explicit policy. Retain the source PDF during ingest and
record resolution, color, page boxes, annotations, and renderer versions.
Raster rendering can lose vector scalability, searchable text, or other
properties; preserve auxiliary information where possible and report loss. See
the detailed [PDF design](design.md#pdf-input-normalization).

Fixed-layout EPUB needs package/spine and rendering semantics, not ZIP image
sorting. Reflowable EPUB may only enter the static model through an explicit
conversion recording the chosen layout and semantic loss. A future adapter
must state which EPUB features it supports and reject unsupported essential
content rather than claim that every `.epub` is native.
