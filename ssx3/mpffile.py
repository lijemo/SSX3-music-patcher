"""Patch the segment table inside an SSX 3 .mpf ("PFDx") music map.

The map ends with a table of one (offset, duration) pair per segment:

    u32 BE  segment start offset in the .mus, in units of 128 bytes
    u32 BE  segment duration in milliseconds (truncated)

The table is located by matching it against the real segment offsets parsed
out of the matching retail .mus, so we never have to guess where it lives.
"""
import struct

TABLE_STRIDE = 8
OFFSET_UNIT = 128


def locate_table(mpf, segment_offsets):
    """Find the byte offset of the segment table. Raises if not found."""
    want = [o // OFFSET_UNIT for o in segment_offsets]
    n = len(want)
    if n == 0:
        raise ValueError('no segments given')
    need = TABLE_STRIDE * (n - 1) + 4
    first = struct.pack('>I', want[0])
    p = 0
    while True:
        p = mpf.find(first, p)
        if p < 0 or p + need > len(mpf):
            break
        if p % 4 == 0:
            ok = True
            for i in range(1, n):
                got = struct.unpack_from('>I', mpf, p + i * TABLE_STRIDE)[0]
                if got != want[i]:
                    ok = False
                    break
            if ok:
                return p
        p += 1
    raise ValueError('segment table not found in .mpf')


def read_table(mpf, pos, count):
    return [struct.unpack_from('>II', mpf, pos + i * TABLE_STRIDE)
            for i in range(count)]


def point_all_at_single(mpf, segment_offsets, duration_ms):
    """Rewrite every table entry to (offset 0, duration_ms).

    Whatever route the map's event graph takes, every choice now resolves to
    the one long segment at the start of the new .mus.
    """
    pos = locate_table(mpf, segment_offsets)
    out = bytearray(mpf)
    for i in range(len(segment_offsets)):
        struct.pack_into('>II', out, pos + i * TABLE_STRIDE, 0, duration_ms)
    return bytes(out), pos


def retarget(mpf, segment_offsets, new_entries):
    """Rewrite the table with explicit (byte_offset, duration_ms) pairs."""
    pos = locate_table(mpf, segment_offsets)
    if len(new_entries) != len(segment_offsets):
        raise ValueError('entry count must match the original segment count')
    out = bytearray(mpf)
    for i, (off, dur) in enumerate(new_entries):
        if off % OFFSET_UNIT:
            raise ValueError(f'offset {off} is not {OFFSET_UNIT}-byte aligned')
        struct.pack_into('>II', out, pos + i * TABLE_STRIDE,
                         off // OFFSET_UNIT, dur)
    return bytes(out), pos
