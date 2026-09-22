"""Read and write SSX 3 GameCube .mus streams.

A .mus is a chain of independent sub-streams ("segments"), each of which is

    SCHl  variable header  (tag/length/value, big-endian values)
    SCCl  u32 BE count of SCDl blocks that follow
    SCDl  xN   audio data
    SCEl  terminator

Every block is 'tag' + u32 LE block length. Segments are zero-padded so the
next one starts on a SEG_ALIGN boundary.
"""
import struct

from .eaxa import FRAME_BYTES, FRAME_SAMPLES, channel_bytes, encode_channel

SEG_ALIGN = 128
BLOCK_SAMPLES = 1484          # 53 frames, matches the retail encoder
MARKER_TAGS = {0xFC, 0xFD, 0xFE}
TAG_CHANNELS = 0x82
TAG_SAMPLE_RATE = 0x84
TAG_NUM_SAMPLES = 0x85


def parse_header(d, off):
    """Parse an SCHl block.

    Returns (items, platform, block_len) where items is the tag stream in
    file order: ('mark', tag) or ('tag', tag, byte_length, value).
    """
    if d[off:off + 4] != b'SCHl':
        raise ValueError(f'expected SCHl at 0x{off:X}, got {d[off:off+4]!r}')
    blocklen = struct.unpack('<I', d[off + 4:off + 8])[0]
    p = off + 8
    platform = None
    if d[p:p + 2] == b'PT':
        platform = d[p + 2]
        p += 4
    end = off + blocklen
    items = []
    while p < end:
        tag = d[p]
        p += 1
        if tag == 0xFF:
            break
        if tag in MARKER_TAGS:
            items.append(('mark', tag))
            continue
        if p >= end:
            break
        ln = d[p]
        p += 1
        items.append(('tag', tag, ln, int.from_bytes(d[p:p + ln], 'big')))
        p += ln
    return items, platform, blocklen


def header_fields(d, off=0):
    """Convenience: {tag: value} for one SCHl block."""
    items, platform, _ = parse_header(d, off)
    out = {t[1]: t[3] for t in items if t[0] == 'tag'}
    if platform is not None:
        out['_platform'] = platform
    return out


def segments(d):
    """Yield dicts describing each segment in a .mus."""
    p = 0
    out = []
    cur = None
    while p + 8 <= len(d):
        tag = d[p:p + 4]
        size = struct.unpack('<I', d[p + 4:p + 8])[0]
        if size == 0 or tag not in (b'SCHl', b'SCCl', b'SCDl', b'SCEl', b'SCLl'):
            nxt = d.find(b'SCHl', p)
            if nxt < 0:
                break
            p = nxt
            continue
        if tag == b'SCHl':
            items, platform, blen = parse_header(d, p)
            cur = {'start': p, 'items': items, 'platform': platform,
                   'fields': {t[1]: t[3] for t in items if t[0] == 'tag'},
                   'header_len': blen, 'nblocks': 0}
            out.append(cur)
        elif tag == b'SCDl' and cur is not None:
            cur['nblocks'] += 1
        elif tag == b'SCEl' and cur is not None:
            cur['end'] = p + size
        p += size
    return out


def segment_offsets(d):
    return [s['start'] for s in segments(d)]


HEADER_ALIGN = 8


def _build_header(template, num_samples, channels, sample_rate):
    """Rebuild a retail SCHl block with our own values.

    Fields are re-emitted at whatever width they now need - retail files use
    a 2-byte sample count for short segments and 3 bytes for long ones, and
    one long segment usually needs the wider field.
    """
    items, platform, _ = parse_header(bytes(template), 0)
    new = {TAG_NUM_SAMPLES: num_samples, TAG_CHANNELS: channels,
           TAG_SAMPLE_RATE: sample_rate, 0x0B: channels}
    body = bytearray()
    if platform is not None:
        body += b'PT' + bytes((platform, 0))
    for item in items:
        if item[0] == 'mark':
            body.append(item[1])
            continue
        _, tag, ln, val = item
        if tag in new:
            val = new[tag]
            ln = max(ln, max(1, (val.bit_length() + 7) // 8))
        body.append(tag)
        body.append(ln)
        body += val.to_bytes(ln, 'big')
    body.append(0xFF)
    size = len(body) + 8                          # + block tag and length
    size = (size + HEADER_ALIGN - 1) // HEADER_ALIGN * HEADER_ALIGN
    out = bytearray(b'SCHl' + struct.pack('<I', size))
    out += body
    out += b'\0' * (size - len(out))
    return bytes(out)


def _scdl(chunks, nsamples, channels):
    """Assemble one SCDl block from per-channel encoded bytes."""
    stride = max(len(c) for c in chunks)
    stride += stride & 1                      # channels start on even offsets
    head = 12 + 4 * channels
    body = bytearray(stride * channels)
    offs = []
    for i, c in enumerate(chunks):
        offs.append(i * stride)
        body[i * stride:i * stride + len(c)] = c
    size = head + len(body)
    out = bytearray()
    out += b'SCDl' + struct.pack('<I', size) + struct.pack('>I', nsamples)
    for o in offs:
        out += struct.pack('>I', o)
    out += body
    assert len(out) == size, (len(out), size)
    return bytes(out)


def build_segment(channels_pcm, sample_rate, template_header,
                  block_samples=BLOCK_SAMPLES, progress=None):
    """Build one complete segment (SCHl..SCEl) from int16 channel arrays."""
    nch = len(channels_pcm)
    total = len(channels_pcm[0])
    for c in channels_pcm:
        if len(c) != total:
            raise ValueError('channels differ in length')
    out = bytearray()
    out += _build_header(template_header, total, nch, sample_rate)
    blocks = []
    hist = [(0, 0)] * nch
    done = 0
    while done < total:
        n = min(block_samples, total - done)
        chunks = []
        for ch in range(nch):
            enc, h1, h2 = encode_channel(
                channels_pcm[ch][done:done + n], *hist[ch])
            hist[ch] = (h1, h2)
            chunks.append(enc)
        blocks.append(_scdl(chunks, n, nch))
        done += n
        if progress:
            progress(done, total)
    out += b'SCCl' + struct.pack('<I', 12) + struct.pack('>I', len(blocks))
    for b in blocks:
        out += b
    out += b'SCEl' + struct.pack('<I', 8)
    return bytes(out)


def pad_segment(seg, align=SEG_ALIGN):
    rem = len(seg) % align
    return seg + b'\0' * (align - rem) if rem else seg
