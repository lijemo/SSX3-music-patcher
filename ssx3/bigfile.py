"""EA BIGF archive reader/writer (SSX 3 GameCube).

Layout:
    'BIGF'              4 bytes
    total_size          u32 LE   (whole file length)
    file_count          u32 BE
    header_len          u32 BE
    per entry:  offset u32 BE, size u32 BE, name (NUL-terminated latin-1)
    trailer ('L222'), padding
    file data, each entry aligned

The index region is preserved byte-for-byte on rebuild and only the
offset/size fields are patched, so the trailer, padding and name spellings
survive untouched.
"""
import math
import struct


class Entry:
    __slots__ = ('name', 'offset', 'size', 'data', 'field_pos')

    def __init__(self, name, offset, size, field_pos):
        self.name = name
        self.offset = offset
        self.size = size
        self.data = None          # set to override on rebuild
        self.field_pos = field_pos

    @property
    def base(self):
        return self.name.replace('/', '\\').split('\\')[-1]

    def __repr__(self):
        return f'<Entry {self.name} @{self.offset} +{self.size}>'


class BigFile:
    """An EA BIGF archive.

    `base` lets the archive live inside a larger file (a disc image), so it
    can be read and rebuilt without extracting anything first.
    """

    def __init__(self, path, base=0):
        self.path = path
        self.base = base
        self.entries = []
        with open(path, 'rb') as f:
            f.seek(base)
            head = f.read(4)
            if head != b'BIGF':
                raise ValueError(f'{path}: not a BIGF archive (got {head!r})')
            self.total_size = struct.unpack('<I', f.read(4))[0]
            count, self.header_len = struct.unpack('>II', f.read(8))
            pos = 16
            for _ in range(count):
                off, size = struct.unpack('>II', f.read(8))
                chars = bytearray()
                while True:
                    c = f.read(1)
                    if c in (b'\0', b''):
                        break
                    chars += c
                self.entries.append(
                    Entry(chars.decode('latin-1'), off, size, pos))
                pos += 8 + len(chars) + 1
            live = [e.offset for e in self.entries if e.size]
            self.data_start = min(live)
            f.seek(base)
            self.index_region = bytearray(f.read(self.data_start))
        self.align = 0
        for o in live:
            self.align = math.gcd(self.align, o)
        self.align = max(min(self.align, 128), 1)

    # ---- reading -------------------------------------------------------
    def get(self, base):
        e = self.find(base)
        if e is None:
            raise KeyError(base)
        if e.data is not None:
            return e.data
        with open(self.path, 'rb') as f:
            f.seek(self.base + e.offset)
            return f.read(e.size)

    def find(self, base):
        low = base.lower()
        for e in self.entries:
            if e.base.lower() == low:
                return e
        return None

    def replace(self, base, data):
        e = self.find(base)
        if e is None:
            raise KeyError(f'{base} not in {self.path}')
        e.data = data

    # ---- writing -------------------------------------------------------
    def build(self, pad_to=None):
        """Serialise to bytes. pad_to forces an exact output length."""
        src = open(self.path, 'rb')
        try:
            cur = self.data_start
            placed = []
            for e in self.entries:
                if e.data is None and e.size == 0:
                    placed.append((e, 0, b''))
                    continue
                if e.data is not None:
                    data = e.data
                else:
                    src.seek(self.base + e.offset)
                    data = src.read(e.size)
                placed.append((e, cur, data))
                cur += len(data)
                cur = (cur + self.align - 1) // self.align * self.align
        finally:
            src.close()
        end = max((o + len(d) for _, o, d in placed), default=self.data_start)
        self.last_used = end
        total = pad_to if pad_to is not None else end
        if end > total:
            raise ValueError(
                f'{self.path}: rebuilt archive needs {end:,} bytes but only '
                f'{total:,} are available (over by {end - total:,})')
        out = bytearray(total)
        out[0:self.data_start] = self.index_region
        out[4:8] = struct.pack('<I', total)
        for e, off, data in placed:
            struct.pack_into('>II', out, e.field_pos, off, len(data))
            if data:
                out[off:off + len(data)] = data
        return bytes(out)
