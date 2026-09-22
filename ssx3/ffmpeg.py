"""Finding ffmpeg/ffprobe, and reading song metadata with them.

PATH alone is not enough: a freshly installed ffmpeg is not visible to any
process that started before the install, which is a very easy way for this
to look broken when it is fine. So the known install locations are searched
as well.
"""
import glob
import json
import os
import re
import shutil
import subprocess

import sys

WINDOWS = sys.platform == 'win32'
MACOS = sys.platform == 'darwin'

HINTS = {
    'win32': [
        r'%LOCALAPPDATA%\Microsoft\WinGet\Links',
        r'%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin',
        r'%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*\**\bin',
        r'C:\ffmpeg\bin',
        r'%ProgramFiles%\ffmpeg\bin',
        r'%ProgramFiles(x86)%\ffmpeg\bin',
    ],
    'darwin': [
        '/opt/homebrew/bin',      # Apple silicon Homebrew
        '/usr/local/bin',         # Intel Homebrew
        '/opt/local/bin',         # MacPorts
        '$HOME/bin',
    ],
}


def install_hint():
    """How to get ffmpeg on this machine."""
    if WINDOWS:
        return 'winget install Gyan.FFmpeg'
    if MACOS:
        return 'brew install ffmpeg'
    return 'install ffmpeg and put it on your PATH'


_cache = {}


def find(name):
    """Locate ffmpeg or ffprobe. Returns a path, or None."""
    if name in _cache:
        return _cache[name]
    exe = shutil.which(name)
    if not exe:
        suffix = '.exe' if WINDOWS else ''
        for pat in HINTS.get(sys.platform, []):
            for d in glob.glob(os.path.expandvars(pat), recursive=True):
                cand = os.path.join(d, name + suffix)
                if os.path.isfile(cand) and os.access(cand, os.X_OK | os.R_OK):
                    exe = cand
                    break
            if exe:
                break
    _cache[name] = exe
    return exe


def read_tags(path):
    """{title, artist, album} from the file's own metadata. May be empty."""
    probe = find('ffprobe')
    if not probe:
        return {}
    try:
        p = subprocess.run(
            [probe, '-v', 'error', '-show_entries',
             'format_tags=title,artist,album,album_artist',
             '-of', 'json', path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace', timeout=30)
        tags = json.loads(p.stdout or '{}').get('format', {}).get('tags', {})
    except Exception:                                # noqa: BLE001
        return {}
    out = {}
    for k, v in tags.items():
        k = k.lower()
        if v and v.strip():
            out[k] = v.strip()
    if 'artist' not in out and 'album_artist' in out:
        out['artist'] = out['album_artist']
    return out


def metadata_for(path, max_len=40):
    """Best guess at (title, artist, album) for a song file.

    Embedded tags win. Otherwise a filename like "Artist - Title" is split,
    with any leading track number dropped. Otherwise the bare filename.
    """
    tags = read_tags(path)
    stem = os.path.splitext(os.path.basename(path))[0].strip()
    # drop a leading track number: "01 ", "01. ", "01 - "
    bare = re.sub(r'^\s*\d{1,3}\s*[-._)]?\s+', '', stem) or stem
    title = tags.get('title')
    artist = tags.get('artist')
    album = tags.get('album')
    if not title or not artist:
        if ' - ' in bare:
            left, right = bare.split(' - ', 1)
            artist = artist or left.strip()
            title = title or right.strip()
        else:
            title = title or bare
    return (title or bare)[:max_len], (artist or 'Custom')[:max_len], \
           (album or 'Custom')[:max_len]
