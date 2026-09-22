"""Load a song from disk and hand back 22050 Hz stereo int16 channels.

WAV is read natively. Anything else goes through ffmpeg if it is on PATH;
if it isn't, the error says exactly how to install it.
"""
import os
import shutil
import struct
import subprocess
import wave

import numpy as np

TARGET_RATE = 22050
NATIVE = {'.wav'}


class AudioError(Exception):
    pass


def _lowpass_taps(cutoff_frac, ntaps=63):
    """Windowed-sinc low-pass. cutoff_frac is relative to the sample rate."""
    n = np.arange(ntaps) - (ntaps - 1) / 2.0
    h = 2 * cutoff_frac * np.sinc(2 * cutoff_frac * n)
    h *= np.hamming(ntaps)
    return h / h.sum()


def resample(x, src_rate, dst_rate):
    """Band-limited resample of a float32 array."""
    if src_rate == dst_rate:
        return x
    if dst_rate < src_rate:
        taps = _lowpass_taps(0.45 * dst_rate / src_rate)
        x = np.convolve(x, taps, mode='same')
    n_out = int(round(len(x) * dst_rate / src_rate))
    idx = np.arange(n_out, dtype=np.float64) * (src_rate / dst_rate)
    i0 = np.floor(idx).astype(np.int64)
    frac = (idx - i0).astype(np.float32)
    i0 = np.clip(i0, 0, len(x) - 1)
    i1 = np.clip(i0 + 1, 0, len(x) - 1)
    return x[i0] * (1.0 - frac) + x[i1] * frac


def _read_wav(path):
    with wave.open(path, 'rb') as w:
        nch, width, rate, nframes = (w.getnchannels(), w.getsampwidth(),
                                     w.getframerate(), w.getnframes())
        raw = w.readframes(nframes)
    if width == 2:
        a = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    elif width == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
        v = np.where(v & 0x800000, v - 0x1000000, v)
        a = v.astype(np.float32) / 8388608.0
    elif width == 4:
        a = np.frombuffer(raw, dtype='<i4').astype(np.float32) / 2147483648.0
    else:
        raise AudioError(f'{path}: unsupported WAV sample width {width}')
    return a.reshape(-1, nch), rate


def _read_ffmpeg(path):
    from .ffmpeg import find, install_hint
    exe = find('ffmpeg')
    if not exe:
        raise AudioError(
            f'{os.path.basename(path)} is not a WAV file and ffmpeg was not '
            f'found.\n'
            f'Either export the song to WAV, or install ffmpeg with:\n'
            f'    {install_hint()}\n'
            f'(then open a new terminal so PATH picks it up).')
    cmd = [exe, '-v', 'error', '-i', path, '-f', 's16le', '-acodec',
           'pcm_s16le', '-ac', '2', '-ar', str(TARGET_RATE), '-']
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise AudioError(f'ffmpeg failed on {path}:\n'
                         f'{p.stderr.decode("utf-8", "replace").strip()}')
    a = np.frombuffer(p.stdout, dtype='<i2').astype(np.float32) / 32768.0
    return a.reshape(-1, 2), TARGET_RATE


def load(path, rate=TARGET_RATE, peak_dbfs=-0.5):
    """Return [left, right] as lists of int16 at `rate`."""
    ext = os.path.splitext(path)[1].lower()
    if ext in NATIVE:
        data, src = _read_wav(path)
    else:
        data, src = _read_ffmpeg(path)
    if data.size == 0:
        raise AudioError(f'{path}: no audio samples')
    if data.shape[1] == 1:
        chans = [data[:, 0], data[:, 0]]
    elif data.shape[1] == 2:
        chans = [data[:, 0], data[:, 1]]
    else:                                   # fold anything else to stereo
        half = data.shape[1] // 2
        chans = [data[:, :half].mean(axis=1), data[:, half:].mean(axis=1)]
    chans = [resample(np.ascontiguousarray(c), src, rate) for c in chans]
    peak = max(float(np.abs(c).max()) for c in chans)
    if peak > 0:
        gain = (10 ** (peak_dbfs / 20.0)) / peak
        if gain < 1.0:                      # only ever attenuate
            chans = [c * gain for c in chans]
    out = []
    for c in chans:
        q = np.clip(np.rint(c * 32767.0), -32768, 32767).astype(np.int32)
        out.append(q.tolist())
    n = min(len(c) for c in out)
    return [c[:n] for c in out], rate
