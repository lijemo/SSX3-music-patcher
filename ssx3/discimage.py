"""Read and patch files inside a GameCube disc image, without extracting it.

Every file this toolkit rewrites is padded back to its original length, so
the disc's file table never changes and a patched file can be written
straight over the original bytes in the image.

    0x0000  boot.bin   0x420 dol offset, 0x424 fst offset, 0x428 fst size
    0x0440  bi2.bin
    0x2440  apploader
            main.dol, fst.bin and the files, at offsets held in the FST
"""
import os
import shutil
import struct

ENTRY = 12


def walk_fst(fst):
    """Parse an FST blob into [{path, offset, size}] for every file."""
    count = struct.unpack_from('>I', fst, 8)[0]
    strings = count * ENTRY
    out = []
    stack = [(count, '')]
    for i in range(1, count):
        o = i * ENTRY
        flag = fst[o]
        name_off = int.from_bytes(fst[o + 1:o + 4], 'big')
        a, b = struct.unpack_from('>II', fst, o + 4)
        end = fst.find(b'\0', strings + name_off)
        name = fst[strings + name_off:end].decode('latin-1')
        while stack and i >= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else ''
        path = f'{parent}/{name}' if parent else name
        if flag:
            stack.append((b, path))
        else:
            out.append({'path': path, 'offset': a, 'size': b})
    return out


class DiscImage:
    """An uncompressed .iso, opened in place."""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.boot = f.read(0x440)
            if len(self.boot) < 0x440:
                raise ValueError(f'{path}: too small to be a disc image')
            self.game_id = self.boot[:6].decode('latin-1', 'replace')
            self.title = self.boot[0x20:0x40].split(b'\0')[0].decode(
                'latin-1', 'replace')
            if self.boot[0x1C:0x20] != b'\xc2\x33\x9f\x3d':
                raise ValueError(
                    f'{os.path.basename(path)} is not a GameCube disc image '
                    f'(bad magic word).')
            self.fst_offset, self.fst_size = struct.unpack_from(
                '>II', self.boot, 0x424)
            f.seek(self.fst_offset)
            fst = f.read(self.fst_size)
        self.entries = walk_fst(fst)
        self.by_path = {e['path'].lower(): e for e in self.entries}

    def find(self, path):
        return self.by_path.get(path.lower().replace('\\', '/'))

    def read(self, path):
        e = self.find(path)
        if e is None:
            raise KeyError(f'{path} not in {os.path.basename(self.path)}')
        with open(self.path, 'rb') as f:
            f.seek(e['offset'])
            return f.read(e['size'])

    def write(self, path, data):
        """Overwrite a file in place. Must not grow."""
        e = self.find(path)
        if e is None:
            raise KeyError(f'{path} not in {os.path.basename(self.path)}')
        if len(data) > e['size']:
            raise ValueError(
                f'{path}: patched data is {len(data):,} bytes but only '
                f'{e["size"]:,} are available in the image')
        with open(self.path, 'r+b') as f:
            f.seek(e['offset'])
            f.write(data)
            if len(data) < e['size']:
                f.write(b'\0' * (e['size'] - len(data)))
        return e['offset']


def prepare_output(src, dst, tool=None, progress=None):
    """Make `dst` an .iso copy of `src`, converting from .rvz etc. if needed.

    Returns the path to the working .iso.
    """
    from .gcdisc import compress_to_rvz, find_dolphin_tool  # noqa: F401
    import subprocess
    ext = os.path.splitext(src)[1].lower()
    if ext == '.iso':
        if progress:
            progress(f'copying {os.path.basename(src)} ...')
        shutil.copyfile(src, dst)
        return dst
    tool = tool or find_dolphin_tool()
    if not tool:
        raise FileNotFoundError(
            f'{ext} images need DolphinTool.exe to convert. It ships beside '
            f'Dolphin.exe.\nEither pass its path, or give this tool an .iso.')
    if progress:
        progress(f'decompressing {os.path.basename(src)} ...')
    cmd = [tool, 'convert', '-i', src, '-o', dst, '-f', 'iso']
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding='utf-8', errors='replace')
    if p.returncode != 0 or not os.path.exists(dst):
        raise RuntimeError(f'DolphinTool could not read that image:\n'
                           f'{p.stdout.strip()}')
    return dst
