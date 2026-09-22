#!/usr/bin/env python3
"""Point-and-click front end for the SSX 3 custom music patcher.

Pick a folder of songs, pick where the .iso should go, press Build.
Everything else - decoding, EA-XA encoding, patching the music maps,
rebuilding the archives and writing the disc image - happens underneath.
"""
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Keep the tool folder clean - no __pycache__ directories appearing next to
# the source, so the folder stays exactly as shipped.
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLI = os.path.join(HERE, 'ssx3_music.py')
sys.path.insert(0, HERE)

AUDIO_EXT = {'.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus',
             '.wma', '.aiff', '.aif', '.mp4', '.webm'}
WINDOWS = sys.platform == 'win32'
MACOS = sys.platform == 'darwin'


def have_ffmpeg():
    from ssx3.ffmpeg import find
    return find('ffmpeg') is not None


def ffmpeg_cmd():
    from ssx3.ffmpeg import install_hint
    return install_hint()


# SSX 3's "SSX Mix" playlist order. Used only to preview which slots your
# songs will land on; the build reads the real list out of the game image.
DEFAULT_SLOTS = [
    'Deep', 'Night', 'Go', 'Jerk', 'Rock Star', 'Like This', 'Mas', 'Action',
    'Patrol', 'Wobble', 'Care', 'Witness', 'Silver', 'Ride', 'Knows', 'Way',
    'Screw Up', 'Emerge', 'Higher', 'Poor Leno', 'Labor', 'Hypersonic',
    'Play', 'Who', 'Thing', 'Leave', 'Man', 'Freeze', 'Danse', 'Stare',
    'Clockworks', 'Bitter', 'Avalanche', 'Buffet', 'Good',
]


def playlist_slots():
    """Prefer the real playlist if this tool happens to sit in an extracted
    disc; otherwise fall back to the known retail order."""
    try:
        from ssx3.inifile import MusicInf
        inf = MusicInf.load(os.path.join(ROOT, 'files', 'data', 'config',
                                         'music.inf'))
        have = set(inf.sections())
        out = []
        pl = os.path.join(ROOT, 'files', 'data', 'config', 'playlist.inf')
        for line in open(pl, encoding='latin-1'):
            line = line.split('#')[0].strip()
            if line.upper().startswith('SONG'):
                name = line.split('=', 1)[1].strip().strip('"')
                if name in have:
                    out.append(name)
        if out:
            return out
    except Exception:
        pass
    return list(DEFAULT_SLOTS)


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.q = queue.Queue()
        self.slots = playlist_slots()
        root.title('SSX 3 Custom Music')
        root.minsize(780, 560)

        pad = dict(padx=10, pady=6)
        frm = ttk.Frame(root)
        frm.pack(fill='both', expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text='SSX 3 game image').grid(row=0, column=0,
                                                     sticky='w', **pad)
        self.rom = tk.StringVar()
        ttk.Entry(frm, textvariable=self.rom).grid(row=0, column=1,
                                                   sticky='ew', **pad)
        ttk.Button(frm, text='Browse...', command=self.pick_rom).grid(
            row=0, column=2, **pad)

        ttk.Label(frm, text='Songs folder').grid(row=1, column=0, sticky='w',
                                                 **pad)
        self.songs = tk.StringVar()
        ttk.Entry(frm, textvariable=self.songs).grid(row=1, column=1,
                                                     sticky='ew', **pad)
        ttk.Button(frm, text='Browse...', command=self.pick_songs).grid(
            row=1, column=2, **pad)

        ttk.Label(frm, text='Save disc image as').grid(row=2, column=0,
                                                       sticky='w', **pad)
        self.iso = tk.StringVar(
            value=os.path.join(os.path.expanduser('~'), 'Downloads',
                               'SSX3 Custom Music.iso'))
        ttk.Entry(frm, textvariable=self.iso).grid(row=2, column=1,
                                                   sticky='ew', **pad)
        ttk.Button(frm, text='Browse...', command=self.pick_iso).grid(
            row=2, column=2, **pad)

        self.romstat = ttk.Label(frm, text='', foreground='#444',
                                 wraplength=700, justify='left')
        self.romstat.grid(row=3, column=0, columnspan=3, sticky='w', padx=10)
        self.status = ttk.Label(frm, text='', foreground='#444')
        self.status.grid(row=4, column=0, columnspan=3, sticky='w', padx=10,
                         pady=(2, 0))

        opts = ttk.LabelFrame(frm, text='Options')
        opts.grid(row=5, column=0, columnspan=3, sticky='ew', padx=10, pady=8)
        self.fill = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts, variable=self.fill, command=self.rescan,
            text='Fill all 35 slots, repeating my songs as needed  '
                 '(so every track in the game is yours)'
        ).pack(anchor='w', padx=8, pady=4)

        self.ffbar = ttk.Frame(frm)
        self.ffbar.grid(row=6, column=0, columnspan=3, sticky='ew', padx=10)
        self.fflabel = ttk.Label(self.ffbar, foreground='#a33', wraplength=600,
                                 justify='left')
        self.fflabel.pack(side='left')
        self.ffbtn = ttk.Button(self.ffbar, text='Install ffmpeg',
                                command=self.install_ffmpeg)

        bar = ttk.Frame(frm)
        bar.grid(row=7, column=0, columnspan=3, sticky='ew', padx=10, pady=4)
        self.build_btn = ttk.Button(bar, text='Build disc image',
                                    command=self.start)
        self.build_btn.pack(side='left')
        self.prog = ttk.Progressbar(bar, mode='indeterminate')
        self.prog.pack(side='left', fill='x', expand=True, padx=10)

        mono = ('Consolas', 9) if WINDOWS else (
            ('Menlo', 11) if MACOS else ('DejaVu Sans Mono', 9))
        self.log = tk.Text(frm, height=18, wrap='word', bg='#111', fg='#ddd',
                           insertbackground='#ddd', font=mono)
        self.log.grid(row=8, column=0, columnspan=3, sticky='nsew',
                      padx=10, pady=(4, 10))
        frm.rowconfigure(8, weight=1)
        sb = ttk.Scrollbar(frm, command=self.log.yview)
        self.log['yscrollcommand'] = sb.set

        guess = os.path.join(os.path.expanduser('~'), 'Desktop', 'My Songs')
        if os.path.isdir(guess):
            self.songs.set(guess)
        self.rom_ok = None
        self.songs.trace_add('write', lambda *_: self.rescan())
        self.rom.trace_add('write', lambda *_: self.rescan())
        self.rescan()
        self.root.after(100, self.drain)

    # ---------------------------------------------------------------- helpers
    def check_rom(self):
        """Identify the chosen image. .iso is read directly; anything else is
        only identified once DolphinTool decompresses it at build time."""
        p = self.rom.get().strip()
        if not p:
            return None, 'Choose your SSX 3 disc image (.iso or .rvz).'
        if not os.path.isfile(p):
            return False, f'Not found: {p}'
        ext = os.path.splitext(p)[1].lower()
        if ext == '.iso':
            try:
                from ssx3.discimage import DiscImage
                d = DiscImage(p)
                ok = d.game_id.startswith('GXB')
                return ok, (f'{d.title}  ({d.game_id})   '
                            f'{len(d.entries)} files'
                            + ('' if ok else
                               '   - this does not look like SSX 3'))
            except Exception as e:                   # noqa: BLE001
                return False, f'Could not read that image: {e}'
        if ext in ('.rvz', '.gcz', '.ciso', '.wia', '.gcm'):
            from ssx3.gcdisc import find_dolphin_tool
            if not find_dolphin_tool():
                return False, (f'{ext} images need DolphinTool.exe (it ships '
                               f'beside Dolphin.exe) and it was not found. '
                               f'Use an .iso instead.')
            return True, (f'{os.path.basename(p)} - will be decompressed with '
                          f'DolphinTool, then checked.')
        return False, f'Unsupported file type "{ext}". Use .iso or .rvz.'

    def pick_rom(self):
        f = filedialog.askopenfilename(
            title='Your SSX 3 disc image',
            filetypes=[('GameCube disc image',
                        '*.iso *.rvz *.gcz *.ciso *.gcm *.wia'),
                       ('All files', '*.*')])
        if f:
            self.rom.set(f)

    def say(self, text):
        self.log.insert('end', text)
        self.log.see('end')

    def pick_songs(self):
        d = filedialog.askdirectory(title='Folder containing your songs')
        if d:
            self.songs.set(d)

    def pick_iso(self):
        f = filedialog.asksaveasfilename(
            title='Save the patched disc image as',
            defaultextension='.iso',
            filetypes=[('Disc image', '*.iso'),
                       ('Compressed disc image', '*.rvz')],
            initialfile='SSX3 Custom Music.iso')
        if f:
            self.iso.set(f)

    def find_songs(self):
        d = self.songs.get().strip()
        if not d or not os.path.isdir(d):
            return []
        return [os.path.join(d, f) for f in sorted(os.listdir(d))
                if os.path.splitext(f)[1].lower() in AUDIO_EXT]

    def rescan(self):
        ok, detail = self.check_rom()
        self.rom_ok = ok
        self.romstat['text'] = detail
        self.romstat['foreground'] = ('#161' if ok else
                                      '#444' if ok is None else '#a33')
        songs = self.find_songs()
        n = len(songs)
        if not self.songs.get().strip():
            self.status['text'] = 'Choose a folder of songs to begin.'
        elif n == 0:
            self.status['text'] = 'No audio files found in that folder.'
        else:
            targets = len(self.slots) if self.fill.get() else min(
                n, len(self.slots))
            names = ', '.join(self.slots[:targets][:6])
            more = '...' if targets > 6 else ''
            self.status['text'] = (
                f'{n} song(s) -> {targets} slot(s): {names}{more}')
        need_ff = any(os.path.splitext(s)[1].lower() != '.wav' for s in songs)
        if need_ff and not have_ffmpeg():
            self.fflabel['text'] = (
                'These songs are not WAV files, and ffmpeg is not installed - '
                'the build will fail without it.')
            self.fflabel.pack(side='left')
            self.ffbtn.pack(side='left', padx=8)
        else:
            self.fflabel.pack_forget()
            self.ffbtn.pack_forget()

    def install_ffmpeg(self):
        cmd = ffmpeg_cmd()
        if not messagebox.askokcancel(
                'Install ffmpeg',
                f'This will open a terminal and run:\n\n    {cmd}\n\n'
                f'ffmpeg is what decodes MP3/FLAC/M4A into audio the patcher '
                f'can use.\nWhen it finishes, come back and press Re-check.'):
            return
        try:
            if WINDOWS:
                subprocess.Popen(['cmd', '/c', 'start', 'cmd', '/k', cmd])
            elif MACOS:
                subprocess.Popen(
                    ['osascript', '-e',
                     f'tell application "Terminal" to do script "{cmd}"'])
            else:
                messagebox.showinfo('Run this yourself',
                                    f'Run this in a terminal:\n\n    {cmd}')
        except Exception as e:                       # noqa: BLE001
            messagebox.showinfo('Run this yourself',
                                f'Could not launch it ({e}).\n\nRun:\n\n'
                                f'    {cmd}')
        self.ffbtn['text'] = 'Re-check'
        self.ffbtn['command'] = self.rescan

    # ------------------------------------------------------------------ build
    def start(self):
        rom = self.rom.get().strip()
        if not rom or not self.rom_ok:
            messagebox.showerror(
                'Pick a game image',
                'Choose your SSX 3 disc image (.iso or .rvz) at the top of '
                'the window.\n\nIt is only ever read - a separate patched '
                'copy is written.')
            return
        songs = self.find_songs()
        if not songs:
            messagebox.showerror('No songs', 'Pick a folder with audio files.')
            return
        out = self.iso.get().strip()
        if not out:
            messagebox.showerror('No output', 'Choose where to save the .iso.')
            return
        ext = os.path.splitext(out)[1].lower()
        if ext not in ('.iso', '.rvz'):
            messagebox.showerror(
                'Wrong file type',
                'Save as .iso, or .rvz for a smaller file.')
            return
        if ext == '.rvz':
            from ssx3.gcdisc import find_dolphin_tool
            if not find_dolphin_tool():
                messagebox.showerror(
                    'DolphinTool not found',
                    'Saving as .rvz needs DolphinTool.exe, which ships beside '
                    'Dolphin.exe.\n\nSave as .iso instead, or move your '
                    'Dolphin folder somewhere findable.')
                return
        if os.path.exists(out):
            if not messagebox.askokcancel(
                    'Overwrite?',
                    f'{os.path.basename(out)} already exists.\n\n'
                    f'Overwrite it?\n\nMake sure this is NOT your original '
                    f'game image - that is the only copy you have.'):
                return
        if any(os.path.splitext(s)[1].lower() != '.wav' for s in songs) \
                and not have_ffmpeg():
            messagebox.showerror(
                'ffmpeg needed',
                f'Those songs need ffmpeg to decode.\n\nRun:\n'
                f'    {ffmpeg_cmd()}\n\nor export your songs as WAV.')
            return

        if os.path.abspath(out).lower() == os.path.abspath(rom).lower():
            messagebox.showerror(
                'Same file',
                'The output is the same file as your game image.\n\n'
                'Choose a different name so your original survives.')
            return

        cmd = [sys.executable, '-u', CLI, 'build', self.songs.get().strip(),
               '--rom', rom, '--iso', out, '--force']
        if self.fill.get():
            cmd.append('--fill')

        self.log.delete('1.0', 'end')
        self.say(f'{len(songs)} song(s) from {self.songs.get()}\n'
                 f'writing {out}\n'
                 f'This takes a couple of minutes - the disc image alone is '
                 f'1.36 GB.\n\n')
        self.build_btn['state'] = 'disabled'
        self.prog.start(12)
        threading.Thread(target=self.run, args=(cmd,), daemon=True).start()

    def run(self, cmd):
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', cwd=ROOT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            for line in self.proc.stdout:
                self.q.put(('log', line))
            self.proc.wait()
            self.q.put(('done', self.proc.returncode))
        except Exception as e:                       # noqa: BLE001
            self.q.put(('log', f'\nfailed to start: {e}\n'))
            self.q.put(('done', 1))

    def drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == 'log':
                    self.say(payload)
                else:
                    self.prog.stop()
                    self.build_btn['state'] = 'normal'
                    if payload == 0:
                        self.say(f'\nDone. Open this in Dolphin:\n'
                                 f'  {self.iso.get()}\n')
                        messagebox.showinfo(
                            'Finished',
                            f'Disc image written:\n\n{self.iso.get()}\n\n'
                            f'Open it in Dolphin and pick any song.')
                    else:
                        self.say(f'\nBuild failed (exit {payload}). '
                                 f'The message above says why.\n')
        except queue.Empty:
            pass
        self.root.after(100, self.drain)


def main():
    root = tk.Tk()
    for theme in (('vista', 'winnative') if WINDOWS else
                  ('aqua',) if MACOS else ('clam', 'default')):
        try:
            ttk.Style().theme_use(theme)
            break
        except tk.TclError:
            continue
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
