#!/usr/bin/env python3
"""Put your own songs into SSX 3 (GameCube) with the game's trick FX intact.

Each replaced song becomes one long EA-XA segment, and every entry in that
song's .mpf segment table is pointed at it, so whichever route the music
map's event graph takes it always lands on your track. The engine's
real-time low-pass (music.inf LOWPASS) and ducking still run on top.

    python ssx3_music.py build "My Songs" --rom ssx3.rvz --iso custom.rvz --fill
    python ssx3_music.py build song.wav --rom ssx3.iso --iso out.iso --slot Labor

Your game image is only ever read. Because every patched file is padded
back to its original length, the disc's file table never changes and the
new bytes are written straight into a copy of the image - no extracting,
no scratch files left behind.
"""
import argparse
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor

# Keep the tool folder clean - no __pycache__ directories appearing next to
# the source, so the folder stays exactly as shipped.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ssx3 import audio, ffmpeg as ffmeta, mpffile, musfile    # noqa: E402
from ssx3.bigfile import BigFile                              # noqa: E402
from ssx3.discimage import DiscImage, prepare_output             # noqa: E402
from ssx3.gcdisc import Disc, compress_to_rvz, find_dolphin_tool  # noqa: E402
from ssx3.inifile import MusicInf                             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(ROOT, 'files', 'data', 'audio')
INF_PATH = os.path.join(ROOT, 'files', 'data', 'config', 'music.inf')
PLAYLIST = os.path.join(ROOT, 'files', 'data', 'config', 'playlist.inf')
RATE = audio.TARGET_RATE
MAX_SAMPLES = 0xFFFFFF              # 12.7 min, the widest sample-count field
AUDIO_EXT = {'.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus',
             '.wma', '.aiff', '.aif', '.mp4', '.webm'}


# ---------------------------------------------------------------- inventory
def playlist_order(text=None):
    """The in-game "SSX Mix" order, from playlist.inf text or the disc."""
    if text is None:
        if not os.path.isfile(PLAYLIST):
            return []
        text = open(PLAYLIST, encoding='latin-1').read()
    order = []
    for line in text.split('\n'):
        line = line.split('#')[0].strip()
        if line.upper().startswith('SONG'):
            order.append(line.split('=', 1)[1].strip().strip('"'))
    return order


def song_table(inf):
    """Slots that are real, replaceable gameplay songs."""
    out = {}
    for name in inf.sections():
        if name == 'GLOBAL':
            continue
        mus = inf.get(name, 'MUSDATA')
        mpf = inf.get(name, 'PATHDATA')
        if not mus or not mpf:
            continue
        out[name] = {
            'id': name,
            'mus': mus,
            'mpf': mpf,
            'loops': inf.get(name, 'LOOPDATA'),
            'bpm': inf.get(name, 'BPM'),
            'lowpass': inf.get(name, 'LOWPASS'),
            'big': f"music{inf.get(name, 'SONGBIG') or '1'}.big".replace('1.big', '.big'),
            'title': inf.get(name, 'TITLE'),
            'artist': inf.get(name, 'ARTIST'),
        }
    return out


OUT_EXT = {'.iso', '.rvz'}
BAD_EXT = {'.gcz', '.ciso', '.gcm', '.wbfs', '.wia'}


def in_extracted_disc():
    """True only if this tool is sitting inside an extracted disc."""
    return (os.path.isdir(os.path.join(ROOT, 'files'))
            and os.path.isdir(os.path.join(ROOT, 'sys')))


def check_iso_target(path, force=False, guard_root=True):
    """Refuse to clobber an existing image or write inside the extracted disc.

    A brand new file is always written; the source .rvz and the extracted
    tree are only ever read. The danger is aiming the OUTPUT at one of them,
    which is what these checks are for.
    """
    p = os.path.abspath(path)
    ext = os.path.splitext(p)[1].lower()
    if ext in BAD_EXT:
        sys.exit(f'cannot write "{ext}" images.\nUse .iso, or .rvz for a '
                 f'smaller file (needs DolphinTool).')
    if ext not in OUT_EXT:
        sys.exit(f'output must end in .iso or .rvz (got "{ext or "nothing"}")')
    if guard_root and in_extracted_disc():
        root = os.path.abspath(ROOT)
        if os.path.commonpath([p, root]) == root:
            sys.exit('refusing to write inside the extracted disc folder.\n'
                     'Pick somewhere else, e.g. your Downloads folder.')
    if os.path.exists(p) and not force:
        sys.exit(f'"{p}" already exists.\nDelete it, pick another name, or '
                 f'pass --force to overwrite.')
    return p


def cmd_list(args):
    inf = MusicInf.load(INF_PATH)
    songs = song_table(inf)
    order = [s for s in playlist_order() if s in songs]
    order += [s for s in songs if s not in order]
    print(f'{"slot":12s} {"title":34s} {"bpm":>6s} {"lowpass":>7s} '
          f'{"archive":11s} {"mus"}')
    for s in order:
        d = songs[s]
        lp = d['lowpass'] or '-'
        print(f'{d["id"]:12s} {(d["title"] or "")[:34]:34s} '
              f'{d["bpm"] or "-":>6s} {lp:>7s} {d["big"]:11s} {d["mus"]}')
    print(f'\n{len(order)} slots. The first {len(playlist_order())} are the '
          f'in-game "SSX Mix" playlist order.')


# ------------------------------------------------------------------- encode
def encode_song(job):
    """Runs in a worker process. Returns (slot, mus_bytes, nsamples)."""
    slot, path, template = job
    chans, rate = audio.load(path, RATE)
    n = len(chans[0])
    if n == 0:
        raise ValueError(f'{path} decoded to zero samples')
    note = ''
    if n > MAX_SAMPLES:
        note = (f'trimmed from {n/RATE/60:.1f} to '
                f'{MAX_SAMPLES/RATE/60:.1f} minutes')
        n = MAX_SAMPLES
        chans = [c[:n] for c in chans]
    seg = musfile.build_segment(chans, rate, template)
    return slot, musfile.pad_segment(seg), n, note


def cmd_build(args):
    t0 = time.time()
    # Validate everything cheap before spending minutes on the encoder.
    if args.iso:
        check_iso_target(args.iso, args.force, guard_root=not args.rom)
        if args.rom:
            if not os.path.isfile(args.rom):
                sys.exit(f'game image not found: {args.rom}')
            if os.path.abspath(args.rom) == os.path.abspath(args.iso):
                sys.exit('the output is the same file as the game image.\n'
                         'Pick a different name so your original survives.')
        elif not in_extracted_disc():
            sys.exit(f'no game to patch.\n\nEither pass --rom <your SSX 3 '
                     f'.iso/.rvz>, or put this tool inside an extracted\n'
                     f'disc folder (next to files\\ and sys\\).')
        if os.path.splitext(args.iso)[1].lower() == '.rvz':
            if not (args.dolphin_tool or find_dolphin_tool()):
                sys.exit('.rvz output needs DolphinTool.exe, which ships '
                         'beside Dolphin.exe.\nPass --dolphin-tool <path>, '
                         'or write a .iso instead.')
    if args.rom and not args.iso:
        sys.exit('--rom needs --iso <where to save the patched image>')

    # ---- gather input files
    inputs = []
    for item in args.songs:
        if os.path.isdir(item):
            for f in sorted(os.listdir(item)):
                if os.path.splitext(f)[1].lower() in AUDIO_EXT:
                    inputs.append(os.path.join(item, f))
        elif os.path.isfile(item):
            inputs.append(item)
        else:
            sys.exit(f'not found: {item}')
    if not inputs:
        sys.exit('no audio files found')
    needs_ffmpeg = [f for f in inputs
                    if os.path.splitext(f)[1].lower() != '.wav']
    if needs_ffmpeg and not ffmeta.find('ffmpeg'):
        sys.exit(
            f'{len(needs_ffmpeg)} of {len(inputs)} song(s) are not WAV '
            f'(e.g. {os.path.basename(needs_ffmpeg[0])}) and ffmpeg was not '
            f'found.\n\nEither install it:\n    {ffmeta.install_hint()}\n'
            f'(then open a NEW terminal so PATH updates)\n\n'
            f'or export your songs as WAV.')

    # ---- open the game: a disc image, or the extracted folder we live in
    img = work_iso = None
    if args.rom:
        out = os.path.abspath(args.iso)
        want_rvz = os.path.splitext(out)[1].lower() == '.rvz'
        work_iso = (os.path.splitext(out)[0] + '.building.iso'
                    if want_rvz else out)
        prepare_output(args.rom, work_iso, tool=args.dolphin_tool,
                       progress=lambda m: print(m, flush=True))
        img = DiscImage(work_iso)
        print(f'{img.title} ({img.game_id}) - {len(img.entries)} files')
        if not img.game_id.startswith('GXB'):
            print(f'  warning: {img.game_id} does not look like SSX 3; '
                  f'continuing anyway')
        inf_size = img.find('data/config/music.inf')['size']
        inf = MusicInf(img.read('data/config/music.inf').decode('latin-1'))
        order_text = img.read('data/config/playlist.inf').decode('latin-1')
        bigs = {n: BigFile(work_iso, base=img.find(f'data/audio/{n}')['offset'])
                for n in ('music.big', 'music2.big')}
    else:
        inf_size = os.path.getsize(INF_PATH)
        inf = MusicInf.load(INF_PATH)
        order_text = None
        bigs = {n: BigFile(os.path.join(AUDIO_DIR, n))
                for n in ('music.big', 'music2.big')}
    songs = song_table(inf)

    # ---- map inputs onto slots
    order = [s for s in playlist_order(order_text) if s in songs]
    if args.slot:
        if len(args.slot) != len(inputs):
            sys.exit(f'{len(args.slot)} --slot values for {len(inputs)} songs')
        slots = args.slot
    elif args.fill:
        slots = order
        inputs = [inputs[i % len(inputs)] for i in range(len(order))]
    else:
        slots = order[:len(inputs)]
        if len(inputs) > len(order):
            sys.exit(f'{len(inputs)} songs but only {len(order)} playlist '
                     f'slots; pass --slot to choose targets explicitly')
    for s in slots:
        if s not in songs:
            sys.exit(f'unknown slot "{s}" - run "list" to see valid names')

    print(f'{len(inputs)} song(s) -> {", ".join(slots)}\n')

    # ---- build jobs, reusing each slot's retail SCHl header as a template
    jobs, meta, seen = [], {}, {}
    for slot, path in zip(slots, inputs):
        d = songs[slot]
        big = bigs[d['big']]
        orig_mus = big.get(d['mus'])
        segs = musfile.segments(orig_mus)
        if not segs:
            sys.exit(f'{d["mus"]}: no segments found')
        template = orig_mus[segs[0]['start']:
                            segs[0]['start'] + segs[0]['header_len']]
        meta[slot] = {'d': d, 'offsets': [s['start'] for s in segs],
                      'nsegs': len(segs), 'path': path,
                      'orig_size': big.find(d['mus']).size}
        # The same file against an equivalent header only needs encoding once.
        # Fields we overwrite anyway are excluded, so slots whose templates
        # differ only by their original sample count still share one encode.
        items, plat, _ = musfile.parse_header(template, 0)
        shape = (plat, tuple(i for i in items
                             if i[0] != 'tag' or i[1] not in
                             (musfile.TAG_NUM_SAMPLES, musfile.TAG_CHANNELS,
                              musfile.TAG_SAMPLE_RATE, 0x0B)))
        key = (path, shape)
        if key in seen:
            meta[slot]['same_as'] = seen[key]
        else:
            seen[key] = slot
            jobs.append((slot, path, template))

    # ---- encode (the slow part; one process per song)
    results = {}
    jobs_n = max(1, min(args.jobs, len(jobs)))
    reused = len(slots) - len(jobs)
    print(f'encoding {len(jobs)} unique track(s) with {jobs_n} process(es)'
          + (f'; {reused} slot(s) reuse an encode' if reused else '') + '...')
    def note_result(slot, data, n, note):
        results[slot] = (data, n)
        print(f'  {slot:12s} {n/RATE:6.1f}s  {len(data):>9,} bytes'
              + (f'   [{note}]' if note else ''))

    if jobs_n == 1:
        for j in jobs:
            note_result(*encode_song(j))
    else:
        with ProcessPoolExecutor(max_workers=jobs_n) as ex:
            for r in ex.map(encode_song, jobs):
                note_result(*r)
    for slot in slots:
        if 'same_as' in meta[slot]:
            results[slot] = results[meta[slot]['same_as']]

    # ---- patch .mus, .mpf and music.inf
    print()
    for slot in slots:
        m = meta[slot]
        d = m['d']
        data, n = results[slot]
        dur_ms = n * 1000 // RATE
        big = bigs[d['big']]
        big.replace(d['mus'], data)
        new_mpf, table_pos = mpffile.point_all_at_single(
            big.get(d['mpf']), m['offsets'], dur_ms)
        big.replace(d['mpf'], new_mpf)
        if args.no_airloops:
            # Silences the retail air loop, but it very likely also gates the
            # big-air duck-and-filter, so this is opt-in rather than default.
            inf.set(slot, 'DUCKTOLOOPS', 0)
        if not args.keep_tags:
            title, artist, album = ffmeta.metadata_for(m['path'])
            inf.set(slot, 'TITLE', f'"{title}"')
            inf.set(slot, 'ARTIST', f'"{artist}"')
            inf.set(slot, 'ALBUM', f'"{album}"')
        if args.stretch_bpm:
            inf.set(slot, 'BPM', f'{4 * 60 * RATE / n:.3f}')
        print(f'  {slot:12s} {d["mus"]:16s} {m["orig_size"]:>10,} -> '
              f'{len(data):>9,} bytes   {d["mpf"]}: {m["nsegs"]} table '
              f'entries @0x{table_pos:X} -> (0, {dur_ms} ms)')

    if args.dry_run:
        print('\n--dry-run: nothing written')
        if work_iso and os.path.exists(work_iso):
            os.remove(work_iso)
        return

    # ---- rebuild each archive at exactly its original byte size
    rebuilt = {}
    for name, big in bigs.items():
        if not any(e.data is not None for e in big.entries):
            continue
        rebuilt[name] = big.build(pad_to=big.total_size)
        print(f'\n{name}: {big.last_used:,} bytes of content in a '
              f'{big.total_size:,} byte archive '
              f'({big.total_size - big.last_used:,} spare)')
    inf_bytes = inf.to_bytes(pad_to=inf_size)
    print(f'music.inf: {len(inf_bytes):,} bytes (padded to original size)')

    if img is not None:
        # Patch straight into the copied image; sizes are unchanged, so the
        # disc's file table stays valid and nothing has to be extracted.
        print()
        for name, data in rebuilt.items():
            off = img.write(f'data/audio/{name}', data)
            print(f'patched data/audio/{name} at 0x{off:X} '
                  f'({len(data):,} bytes)')
        off = img.write('data/config/music.inf', inf_bytes)
        print(f'patched data/config/music.inf at 0x{off:X}')
        out = os.path.abspath(args.iso)
        want_rvz = os.path.splitext(out)[1].lower() == '.rvz'
        iso_path = work_iso
        size = os.path.getsize(iso_path)
    elif args.iso:
        # Folder mode has to hand whole files to the disc writer, so they go
        # to a temp directory rather than cluttering the tool folder.
        stage = tempfile.mkdtemp(prefix='ssx3music-')
        try:
            overrides = {}
            for name, data in rebuilt.items():
                p = os.path.join(stage, name)
                with open(p, 'wb') as f:
                    f.write(data)
                overrides[f'data/audio/{name}'] = p
            p = os.path.join(stage, 'music.inf')
            with open(p, 'wb') as f:
                f.write(inf_bytes)
            overrides['data/config/music.inf'] = p
            out = os.path.abspath(args.iso)
            want_rvz = os.path.splitext(out)[1].lower() == '.rvz'
            iso_path = (os.path.splitext(out)[0] + '.building.iso'
                        if want_rvz else out)
            disc = Disc(ROOT)
            print(f'\nwriting {iso_path} ...')
            size = disc.build(iso_path, overrides=overrides)
            print(f'done: {size:,} bytes')
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    if args.iso:
        if want_rvz:
            print('\ncompressing to .rvz (a minute or two) ...')
            try:
                compress_to_rvz(iso_path, out, tool=args.dolphin_tool)
            finally:
                if os.path.exists(iso_path):
                    os.remove(iso_path)
            print(f'done: {out}  ({os.path.getsize(out):,} bytes, '
                  f'{100 * os.path.getsize(out) / size:.0f}% of the .iso)')
        else:
            print(f'\ndone: {out}  ({os.path.getsize(out):,} bytes)')
    else:
        print('\nnothing written - pass --iso <path> to build a disc image')
    print(f'\ntotal {time.time() - t0:.1f}s')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('list', help='show the replaceable song slots')

    b = sub.add_parser('build', help='convert songs and stage a patched disc')
    b.add_argument('songs', nargs='+',
                   help='audio files, or a folder of them')
    b.add_argument('--rom', metavar='PATH',
                   help='an SSX 3 disc image (.iso/.rvz/.gcz/.ciso) to patch. '
                        'Without this, the extracted disc this tool lives in '
                        'is used. The source is only ever read.')
    b.add_argument('--slot', action='append',
                   help='target slot name (repeat, one per song)')
    b.add_argument('--fill', action='store_true',
                   help='repeat the given song(s) across every playlist slot '
                        '- use this to test, so it cannot matter which songs '
                        'your save file has unlocked')
    b.add_argument('--iso', metavar='PATH',
                   help='write the disc image here (.iso, or .rvz for a '
                        'smaller file)')
    b.add_argument('--dolphin-tool', metavar='EXE',
                   help='path to DolphinTool.exe (only needed for .rvz '
                        'output, and usually found automatically)')
    b.add_argument('--jobs', type=int, default=os.cpu_count() or 4,
                   help='parallel encoder processes (default: all cores)')
    b.add_argument('--keep-tags', action='store_true',
                   help='leave TITLE/ARTIST/ALBUM as the retail values')
    b.add_argument('--no-airloops', action='store_true',
                   help='set DUCKTOLOOPS = 0 so the retail air loop stays '
                        'silent. Warning: this appears to also switch off the '
                        'big-air duck and low-pass, so leave it off if you '
                        'want the trick FX')
    b.add_argument('--stretch-bpm', action='store_true',
                   help='also set BPM so one bar spans the whole song; try '
                        'this only if playback restarts every few seconds')
    b.add_argument('--force', action='store_true',
                   help='allow overwriting an existing output .iso')
    b.add_argument('--dry-run', action='store_true')

    args = ap.parse_args()
    {'list': cmd_list, 'build': cmd_build}[args.cmd](args)


if __name__ == '__main__':
    main()
