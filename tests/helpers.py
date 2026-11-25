"""Shared helpers for building synthetic COOLNESS sessions in tests."""

import struct


def u32(value):
    return struct.pack("<I", value)


def make_chunk(chunk_id, body):
    """Return one 4-byte-id chunk: id + u32 length + body."""
    assert len(chunk_id) == 4
    return chunk_id + u32(len(body)) + body


def make_wav_record(pointer, path):
    """Return one 'wav ' media record as stored inside LISTFILE.

    Layout: b"wav " + u32(record length) + u32(pointer) + u32(0x0e)
    + NUL-terminated path. Path starts 8 bytes into the record body.
    """
    raw = path.encode("latin1") + b"\x00"
    body = u32(pointer) + u32(0x0E) + raw
    return b"wav " + u32(len(body)) + body


def make_listfile(records):
    """Return a LISTFILE container holding the given (pointer, path) records."""
    body = b"".join(make_wav_record(pointer, path) for pointer, path in records)
    return b"LISTFILE" + u32(len(body)) + body


def make_ses(*chunks):
    """Return a COOLNESS file built from already-encoded chunk byte strings."""
    payload = b"".join(chunks)
    return b"COOLNESS" + u32(len(payload)) + payload
