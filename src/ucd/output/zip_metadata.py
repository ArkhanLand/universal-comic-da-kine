"""Add prepared metadata without reserializing existing ZIP member records."""

import struct
from io import BytesIO
from zipfile import ZipFile, ZipInfo

_EOCD = struct.Struct("<4s4H2LH")


def _directory(data: bytes, comment: bytes) -> tuple[int, int, int]:
    end = len(data) - len(comment) - _EOCD.size
    if end < 0:
        raise ValueError("Cannot preserve an unrecognized ZIP trailer")
    signature, disk, start_disk, disk_count, count, size, offset, length = _EOCD.unpack_from(
        data, end
    )
    if signature != b"PK\x05\x06" or length != len(comment):
        raise ValueError("Cannot preserve unrecognized ZIP trailing data")
    if disk or start_disk or disk_count != count:
        raise ValueError("Split ZIP archives cannot be extended without a preservation policy")
    if count == 65535 or size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
        raise ValueError("Adding XML to ZIP64 archives is not yet supported")
    if offset + size != end:
        raise ValueError("Cannot extend a ZIP with unrecognized directory layout")
    # Unknown directory records (including signatures) cannot simply be carried
    # forward after changing the directory they describe.
    cursor = offset
    for _ in range(count):
        if data[cursor : cursor + 4] != b"PK\x01\x02" or cursor + 46 > end:
            raise ValueError("Cannot preserve an unrecognized ZIP directory record")
        name_len, extra_len, comment_len = struct.unpack_from("<3H", data, cursor + 28)
        cursor += 46 + name_len + extra_len + comment_len
    if cursor != end:
        raise ValueError("Cannot extend a ZIP with extra directory records")
    return count, offset, size


def complete_archive(data: bytes, xml_name: str, xml: bytes, comment: bytes) -> bytes:
    """Install prepared documents; never choose metadata sources or
    interpret them.
    """
    with ZipFile(BytesIO(data)) as archive:
        old_comment = archive.comment
        has_xml = xml_name in archive.namelist()
        if has_xml and archive.read(xml_name) != xml:
            raise ValueError("Replacing existing XML requires an explicit metadata-edit policy")
    if old_comment and old_comment != comment:
        raise ValueError(
            "Replacing an existing ZIP comment requires an explicit metadata-edit policy"
        )
    if has_xml:
        if old_comment == comment:
            return data
        if len(data) < 22 or data[-22:-18] != b"PK\x05\x06" or data[-2:] != b"\x00\x00":
            raise ValueError("Cannot add a ZIP comment without altering unrecognized trailing data")
        return data[:-2] + len(comment).to_bytes(2, "little") + comment

    count, offset, size = _directory(data, old_comment)
    if count >= 65534:
        raise ValueError("Adding XML would require ZIP64 output")
    entry_stream = BytesIO()
    with ZipFile(entry_stream, "w") as addition:
        addition.writestr(ZipInfo(xml_name), xml)
    entry = entry_stream.getvalue()
    _, entry_offset, entry_size = _directory(entry, b"")
    local = entry[:entry_offset]
    central = bytearray(entry[entry_offset : entry_offset + entry_size])
    # Leave all original bytes in place. Point the new XML record at its new
    # local header, then copy existing directory records verbatim to the end.
    new_offset = len(data) + len(local)
    new_size = size + len(central)
    if max(len(data), new_offset, new_size) >= 0xFFFFFFFF:
        raise ValueError("Adding XML would require ZIP64 output")
    struct.pack_into("<L", central, 42, len(data))
    end = _EOCD.pack(b"PK\x05\x06", 0, 0, count + 1, count + 1, new_size, new_offset, len(comment))
    return data + local + data[offset : offset + size] + central + end + comment
