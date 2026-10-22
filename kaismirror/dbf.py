"""Streaming reader for dBase III attribute tables (.dbf) of the KAIS shapefiles.

The files are large (Burgas units: ~730 MB) and are read straight out of the zip,
record by record, without unpacking to disk.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterator


@dataclass
class Field:
    name: str
    type: str  # C (text), N/F (number), D (date), L (logical)
    length: int
    decimals: int


def _read_exact(f: BinaryIO, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def read_header(f: BinaryIO) -> tuple[int, int, list[Field]]:
    """Return (record_count, record_length, fields) and leave `f` at the first record."""
    head = _read_exact(f, 32)
    count, header_len, record_len = struct.unpack("<IHH", head[4:12])
    fields = []
    raw = _read_exact(f, header_len - 32)
    for i in range(0, len(raw) - 1, 32):
        desc = raw[i : i + 32]
        if desc[0] == 0x0D:
            break
        name = desc[:11].split(b"\0", 1)[0].decode("ascii")
        fields.append(Field(name, chr(desc[11]), desc[16], desc[17]))
    return count, record_len, fields


def _convert(field: Field, raw: bytes, encoding: str):
    if field.type == "C":
        return raw.decode(encoding, errors="replace").strip() or None
    text = raw.strip().decode("ascii", errors="replace")
    if not text or set(text) <= {"*", "?"}:
        return None
    if field.type in "NF":
        try:
            return float(text) if (field.decimals or "." in text or "e" in text.lower()) else int(text)
        except ValueError:
            return None
    if field.type == "L":
        return text[:1] in "YyTt"
    return text  # D (YYYYMMDD) and anything else as text


def iter_records(f: BinaryIO, encoding: str = "utf-8") -> tuple[list[Field], Iterator[dict]]:
    count, record_len, fields = read_header(f)
    offsets = []
    pos = 1  # byte 0 is the deletion flag
    for fld in fields:
        offsets.append((fld, pos, pos + fld.length))
        pos += fld.length

    def gen():
        for _ in range(count):
            rec = _read_exact(f, record_len)
            if len(rec) < record_len:
                return
            if rec[0:1] == b"*":  # deleted
                continue
            yield {fld.name: _convert(fld, rec[a:b], encoding) for fld, a, b in offsets}

    return fields, gen()
