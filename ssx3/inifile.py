"""Minimal editor for SSX 3's music.inf.

The file is plain text: '#' comments, '[Section]' headers and 'KEY = value'
lines. Edits are applied in place so unrelated lines keep their exact
spelling, and the result can be padded back to a fixed byte length.
"""
import re

SECTION_RE = re.compile(r'^\s*\[(.+?)\]\s*$')
KV_RE = re.compile(r'^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*?)(\s*)$')


class MusicInf:
    def __init__(self, text):
        self.lines = text.split('\n')

    @classmethod
    def load(cls, path):
        with open(path, 'r', encoding='latin-1', newline='') as f:
            return cls(f.read())

    def sections(self):
        out = {}
        name = None
        for i, line in enumerate(self.lines):
            m = SECTION_RE.match(line.split('#')[0])
            if m:
                name = m.group(1)
                out[name] = [i, len(self.lines)]
            elif name and out[name][1] == len(self.lines) and line.strip().startswith('['):
                pass
        keys = list(out)
        for a, b in zip(keys, keys[1:]):
            out[a][1] = out[b][0]
        return {k: tuple(v) for k, v in out.items()}

    def get(self, section, key):
        lo, hi = self.sections()[section]
        for line in self.lines[lo:hi]:
            m = KV_RE.match(line.split('#')[0])
            if m and m.group(2).lower() == key.lower():
                return m.group(4).strip().strip('"')
        return None

    def set(self, section, key, value):
        """Update the key in place, or append it at the end of the section."""
        lo, hi = self.sections()[section]
        text = f'{value}'
        for i in range(lo, hi):
            m = KV_RE.match(self.lines[i].split('#')[0])
            if m and m.group(2).lower() == key.lower():
                self.lines[i] = f'{m.group(1)}{m.group(2)}{m.group(3)}{text}'
                return
        j = hi - 1
        while j > lo and not self.lines[j].strip():
            j -= 1
        self.lines.insert(j + 1, f'    {key} = {text}')

    def text(self):
        return '\n'.join(self.lines)

    def to_bytes(self, pad_to=None):
        """Serialise, optionally padded with comment filler to an exact size."""
        body = self.text().encode('latin-1')
        if pad_to is None:
            return body
        if len(body) > pad_to:
            stripped = [l for l in self.lines
                        if not l.lstrip().startswith('#')]
            body = '\n'.join(stripped).encode('latin-1')
            if len(body) > pad_to:
                raise ValueError(
                    f'music.inf is {len(body)} bytes even without comments, '
                    f'but only {pad_to} are available')
        gap = pad_to - len(body)
        if gap == 0:
            return body
        filler = b'\n# pad'
        if gap < len(filler):
            return body + b' ' * gap
        pad = bytearray(filler)
        pad += b'.' * (gap - len(filler))
        return body + bytes(pad)
