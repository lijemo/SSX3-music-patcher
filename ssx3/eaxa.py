"""EA-XA (v2) ADPCM codec as used by SSX 3 on GameCube.

Each channel is a run of 15-byte frames (1 control byte + 14 nibble bytes =
28 samples), preceded by two big-endian int16 history samples.

    control byte:  high nibble = coefficient index, low nibble = shift - 8
    per sample:    v = sign_extend(nibble << 28) >> shift        (x256 domain)
                   s = clamp16((v + h1*c1 + h2*c2 + 128) >> 8)
"""
import struct

COEF1 = (0, 240, 460, 392)
COEF2 = (0, 0, -208, -220)
FRAME_SAMPLES = 28
FRAME_BYTES = 15


def clamp16(x):
    return -32768 if x < -32768 else (32767 if x > 32767 else x)


def channel_bytes(nsamples):
    """Encoded length of one channel (history + frames), before alignment."""
    frames = -(-nsamples // FRAME_SAMPLES)
    return 4 + frames * FRAME_BYTES


def decode_channel(data, nsamples):
    hist1, hist2 = struct.unpack('>hh', data[0:4])
    out = []
    p = 4
    while len(out) < nsamples:
        fi = data[p]
        p += 1
        ci = (fi >> 4) & 3
        shift = (fi & 0x0F) + 8
        c1, c2 = COEF1[ci], COEF2[ci]
        for i in range(FRAME_SAMPLES):
            b = data[p + (i >> 1)]
            nib = (b >> 4) if (i & 1) == 0 else (b & 0x0F)
            v = (nib << 28) & 0xFFFFFFFF
            if v & 0x80000000:
                v -= 0x100000000
            v >>= shift
            s = clamp16((v + hist1 * c1 + hist2 * c2 + 128) >> 8)
            out.append(s)
            hist2 = hist1
            hist1 = s
        p += 14
    return out[:nsamples]


def _try(blk, c1, c2, shift, hist1, hist2, limit=None):
    """Exact decoder maths for one (coef, shift) candidate."""
    sh = 28 - shift
    half = 1 << (sh - 1)
    h1, h2 = hist1, hist2
    err = 0
    nibs = []
    for s in blk:
        pred = h1 * c1 + h2 * c2
        q = ((s << 8) - pred - 128 + half) >> sh
        if q < -8:
            q = -8
        elif q > 7:
            q = 7
        rec = clamp16(((q << sh) + pred + 128) >> 8)
        e = s - rec
        err += e * e
        nibs.append(q & 0x0F)
        h2 = h1
        h1 = rec
        if limit is not None and err > limit:
            return None
    return err, nibs, h1, h2


def encode_channel(samples, hist1=0, hist2=0):
    """int16 iterable -> (bytes, hist1, hist2). Short final frame is held."""
    samples = list(samples)
    out = bytearray(struct.pack('>hh', hist1, hist2))
    for base in range(0, len(samples), FRAME_SAMPLES):
        blk = samples[base:base + FRAME_SAMPLES]
        if len(blk) < FRAME_SAMPLES:
            blk = blk + [blk[-1] if blk else 0] * (FRAME_SAMPLES - len(blk))
        best = None
        for ci in range(4):
            c1, c2 = COEF1[ci], COEF2[ci]
            h1, h2 = hist1, hist2
            worst = 0
            for s in blk:
                d = abs((s << 8) - (h1 * c1 + h2 * c2))
                if d > worst:
                    worst = d
                h2 = h1
                h1 = s
            need = 28 - max(worst // 7, 1).bit_length()
            for shift in range(max(8, need - 1), min(24, need + 3)):
                r = _try(blk, c1, c2, shift, hist1, hist2,
                         limit=best[0] if best else None)
                if r and (best is None or r[0] < best[0]):
                    best = (r[0], ci, shift, r[1], r[2], r[3])
        if best is None:
            r = _try(blk, COEF1[0], COEF2[0], 8, hist1, hist2)
            best = (r[0], 0, 8, r[1], r[2], r[3])
        _, ci, shift, nibs, hist1, hist2 = best
        out.append((ci << 4) | ((shift - 8) & 0x0F))
        for i in range(0, FRAME_SAMPLES, 2):
            out.append((nibs[i] << 4) | nibs[i + 1])
    return bytes(out), hist1, hist2
