"""Rebuild a GameCube disc image from a Dolphin-extracted sys/ + files/ tree.

Because every file we touch is padded back to its original length, the FST
needs no changes at all: each file simply goes back at the offset the
original FST already records.

    0x0000  boot.bin      (0x440)   0x420 dol offset, 0x424 fst offset,
                                    0x428 fst size,   0x42C fst max size
    0x0440  bi2.bin       (0x2000)
    0x2440  apploader.img
            main.dol      @ dol offset
            fst.bin       @ fst offset
            files         @ offsets held in the FST
"""
import os
import struct

STANDARD_SIZE = 1459978240
ENTRY = 12

import sys

DOLPHIN_TOOL_HINTS = {
    'win32': [
        r'%LOCALAPPDATA%\Programs\Dolphin*\**\DolphinTool.exe',
        r'%USERPROFILE%\Downloads\dolphin*\**\DolphinTool.exe',
        r'%ProgramFiles%\Dolphin*\**\DolphinTool.exe',
        r'%ProgramFiles(x86)%\Dolphin*\**\DolphinTool.exe',
    ],
    'darwin': [
        '/Applications/Dolphin.app/Contents/MacOS/DolphinTool',
        '$HOME/Applications/Dolphin.app/Contents/MacOS/DolphinTool',
        '/Applications/Dolphin*.app/Contents/MacOS/DolphinTool',
        '/opt/homebrew/bin/DolphinTool',
        '/usr/local/bin/DolphinTool',
    ],
}


def find_dolphin_tool():
    """Locate DolphinTool, used to read/write compressed .rvz images.

    Optional: plain .iso in and .iso out never needs it.
    """
    import glob
    import shutil
    for name in ('DolphinTool', 'dolphin-tool'):
        found = shutil.which(name)
        if found:
            return found
    for pat in DOLPHIN_TOOL_HINTS.get(sys.platform, []):
        for hit in glob.glob(os.path.expandvars(pat), recursive=True):
            if os.path.isfile(hit):
                return hit
    return None


def compress_to_rvz(iso_path, rvz_path, tool=None, progress=None):
    """Convert an .iso to .rvz with DolphinTool. Returns the output path."""
    import subprocess
    tool = tool or find_dolphin_tool()
    if not tool:
        raise FileNotFoundError(
            'DolphinTool.exe not found. It ships beside Dolphin.exe - pass '
            'its path explicitly, or keep the .iso output.')
    cmd = [tool, 'convert', '-i', iso_path, '-o', rvz_path,
           '-f', 'rvz', '-c', 'zstd', '-l', '5', '-b', '131072']
    if progress:
        progress(f'compressing with {os.path.basename(tool)} ...')
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding='utf-8', errors='replace')
    if p.returncode != 0 or not os.path.exists(rvz_path):
        raise RuntimeError(f'DolphinTool failed:\n{p.stdout.strip()}')
    return rvz_path


def _u32(b, o):
    return struct.unpack_from('>I', b, o)[0]


class Disc:
    def __init__(self, root):
        self.root = root
        self.sys = os.path.join(root, 'sys')
        self.files = os.path.join(root, 'files')
        for p in (self.sys, self.files):
            if not os.path.isdir(p):
                raise FileNotFoundError(f'{p} not found - is {root} a Dolphin '
                                        f'"extracted disc" folder?')
        self.boot = open(os.path.join(self.sys, 'boot.bin'), 'rb').read()
        self.bi2 = open(os.path.join(self.sys, 'bi2.bin'), 'rb').read()
        self.apploader = open(os.path.join(self.sys, 'apploader.img'), 'rb').read()
        self.dol = open(os.path.join(self.sys, 'main.dol'), 'rb').read()
        self.fst = open(os.path.join(self.sys, 'fst.bin'), 'rb').read()
        self.dol_offset = _u32(self.boot, 0x420)
        self.fst_offset = _u32(self.boot, 0x424)
        self.fst_size = _u32(self.boot, 0x428)
        self.game_id = self.boot[:6].decode('latin-1')
        self.entries = self._walk()

    def _walk(self):
        count = _u32(self.fst, 8)
        strings = count * ENTRY
        out = []
        stack = [(count, '')]
        for i in range(1, count):
            o = i * ENTRY
            flag = self.fst[o]
            name_off = int.from_bytes(self.fst[o + 1:o + 4], 'big')
            a, b = _u32(self.fst, o + 4), _u32(self.fst, o + 8)
            end = self.fst.find(b'\0', strings + name_off)
            name = self.fst[strings + name_off:end].decode('latin-1')
            while stack and i >= stack[-1][0]:
                stack.pop()
            parent = stack[-1][1] if stack else ''
            path = f'{parent}/{name}' if parent else name
            if flag:
                stack.append((b, path))
            else:
                out.append({'path': path, 'offset': a, 'size': b, 'index': i})
        return out

    def source_for(self, entry, overrides=None):
        if overrides and entry['path'] in overrides:
            return overrides[entry['path']]
        return os.path.join(self.files, entry['path'].replace('/', os.sep))

    def check(self, overrides=None):
        """Verify every FST file exists on disk at exactly its recorded size."""
        problems = []
        for e in self.entries:
            p = self.source_for(e, overrides)
            if not os.path.isfile(p):
                problems.append(f"missing: {e['path']} ({p})")
            elif os.path.getsize(p) != e['size']:
                problems.append(
                    f"size changed: {e['path']} is {os.path.getsize(p):,} "
                    f"bytes, FST says {e['size']:,}")
        return problems

    def disc_size(self):
        end = max([e['offset'] + e['size'] for e in self.entries] +
                  [self.fst_offset + self.fst_size,
                   self.dol_offset + len(self.dol)])
        return STANDARD_SIZE if end <= STANDARD_SIZE else \
            (end + 0x7FFF) // 0x8000 * 0x8000

    def build(self, out_path, overrides=None, progress=None):
        """Write a disc image. `overrides` maps an FST path such as
        'data/audio/music.big' to a replacement file on disk, so the
        extracted tree itself is never modified."""
        problems = self.check(overrides)
        if problems:
            raise ValueError('cannot rebuild disc:\n  ' + '\n  '.join(problems))
        size = self.disc_size()
        with open(out_path, 'wb') as f:
            f.truncate(size)
            f.seek(0)
            f.write(self.boot)
            f.write(self.bi2)
            f.seek(0x2440)
            f.write(self.apploader)
            f.seek(self.dol_offset)
            f.write(self.dol)
            f.seek(self.fst_offset)
            f.write(self.fst)
            for n, e in enumerate(self.entries):
                src = self.source_for(e, overrides)
                f.seek(e['offset'])
                with open(src, 'rb') as g:
                    while True:
                        chunk = g.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                if progress:
                    progress(n + 1, len(self.entries), e['path'])
        return size
