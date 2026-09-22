# SSX 3 custom music

Put your own songs into **SSX 3** (GameCube) and keep the engine's real-time
trick FX — the low-pass sweep and duck when you catch big air still apply,
to your music.

ffmpeg is needed if your songs are not already WAV.

Pure Python + numpy. No SuperSX, no vgmstream.

## Requirements

| | |
|---|---|
| Python | 3.9 or newer |
| numpy | `pip install numpy` |
| ffmpeg | only for MP3/FLAC/M4A/etc. input |
| DolphinTool | only for `.rvz` input or output (ships with Dolphin) |

## Quick start

| Platform | Double-click | Needs |
|---|---|---|
| Windows | **`SSX3 Music Patcher (Windows).cmd`** | Python + numpy |
| macOS | **`SSX3 Music Patcher (Mac).command`** | Python + numpy + `python-tk` |

Point it at your SSX 3 disc image and a folder of songs, choose where to
save the patched copy, press Build.

**This folder is self-contained — copy it anywhere.** It does not need an
extracted disc, and it never modifies the image you point it at.

From a terminal, on any platform:

```bash
python ssx3_music.py build "My Songs" --rom "SSX 3 (USA).rvz" --iso "SSX3 Custom.rvz" --fill
```

### Song titles

Titles and artists shown in the game's menus come from your files: embedded
tags first (read with `ffprobe`), then a `Artist - Title.mp3` filename, then
the bare filename. A leading track number is stripped.

```
Basement Jaxx - Good Luck.mp3              -> 'Good Luck' by 'Basement Jaxx'
07. Queens of the Stone Age - No One Knows -> 'No One Knows' by 'Queens of the Stone Age'
watchyaback!.mp3                           -> 'watchyaback!' by 'Custom'
```

Pass `--keep-tags` to leave the retail titles alone instead.

Input can be `.iso`, `.rvz`, `.gcz`, `.ciso` or `.gcm`; anything other than
`.iso` is decompressed with DolphinTool first (it ships beside Dolphin and
is found automatically). Output can be `.iso` or `.rvz`.

If you happen to be running inside a Dolphin-extracted disc folder, you can
drop `--rom` and it will use `sys/` + `files/` in place.

## Platforms

Windows and macOS. Nothing in the code is Windows-only, but those are the
two that are set up and documented.

### Windows

Tested end to end. For non-WAV songs, `winget install Gyan.FFmpeg` — the
patcher offers a button that runs it for you. DolphinTool is found beside
`Dolphin.exe` automatically.

### macOS

```bash
brew install python-tk ffmpeg
```

`python-tk` matters: Apple's built-in Python has no working tkinter, so the
window will not open without it. A python.org install works too — those
bundle tkinter.

DolphinTool is looked for inside `/Applications/Dolphin.app/Contents/MacOS/`
and the Homebrew bin directories.

If macOS refuses to launch the `.command` file, right-click → **Open** once,
or run `chmod +x "SSX3 Music Patcher (Mac).command"`.

**The macOS side is written from documentation, not tested on a Mac.** The
patching itself is platform-neutral Python that is well tested; what is
unverified is tool discovery, the launcher, and tkinter behaviour.

### How it patches without extracting

Every file the toolkit rewrites is padded back to its **exact original byte
length**, so the disc's file table never changes. That means the patched
bytes can be written straight over the originals inside a copy of the
image — no unpacking, no rebuilding, no chance of a malformed disc. A
35-song `.rvz` → `.rvz` run takes well under a minute, most of it
compression.

```bash
py ssx3_music.py list      # only works inside an extracted disc
```

### Output

Name the output `.iso`, or `.rvz` for a file about half the size —
DolphinTool (which ships beside `Dolphin.exe`) compresses it, and Dolphin
loads either one directly. It is found automatically.

A **brand new file is always created.** Your source image is opened
read-only and copied before anything is touched. Aiming the *output* at an
existing file is the one real hazard, so that is refused up front unless you
pass `--force`, and naming the output the same as the input is refused
outright.

### Filling every slot

With `--fill` (the GUI's "fill all 35 slots" box), however many songs you
give it are cycled across all 35 playlist slots. Six songs become 35
assignments, each encoded only once. This also sidesteps SSX 3's music
shop: the game makes you buy most tracks in the lodge, so with only a few
slots patched you may never hear your own music. Fill everything and
whatever the game picks is yours.

Songs are assigned to the in-game "SSX Mix" playlist slots in filename order.
To target specific slots:

```bash
python ssx3_music.py build a.mp3 b.flac --rom ssx3.iso --iso out.iso \
    --slot Labor --slot Wobble
```

## Are the trick FX real-time?

Yes. `files/data/config/music.inf` is plain text on the disc and documents
its own format:

```
#  LOWPASS - value applied to low pass filter on stream (0 to 65535)
#  DUCKTOLOOPS - set to 1 to have the music duck and the loops play, 0 otherwise
#  DelayTime / DelayFeedback / DelayLevel ...
```

The low-pass is applied to the stream at runtime, per song (retail values run
30000–42000). `main.dol` corroborates it with `LOWPASS` and `Cutoff` strings
and `TRICKY` / `LAND` audio-bus names. So your track gets the same filter
sweep the retail songs do, with no work on your part.

**Leave `DUCKTOLOOPS` alone.** It is documented as "set to 1 to have the
music duck and the loops play, 0 otherwise", and measurement says it gates
the whole big-air duck-and-filter path, not just the loop layer. Setting it
to 0 silences the mismatched retail air loop but appears to cost you the
trick FX as well. `--no-airloops` exists if you want that trade, but it is
opt-in and off by default.

Verified by dumping Dolphin's DSP output and comparing bands: with
`DUCKTOLOOPS = 0`, only 0.2 s of a 58 s race showed any filtering, while the
front-end menus filtered normally — so the filter engine was alive but never
triggered in play.

## Disc formats

| Thing | Format |
|---|---|
| `music.big`, `music2.big` | EA `BIGF`; LE total size, BE entry table, 128-byte alignment, `L222` trailer |
| `*.mus` (songs) | Chain of EA `SCHl`/`SCCl`/`SCDl`/`SCEl` sub-streams |
| Codec | EA-XA ADPCM, 22050 Hz stereo, platform byte `0x06` (GC/Wii) |
| `*loops0.mus` | `BNKb` v5 sample banks (18–67 sounds) — air loops, not streams |
| `*.mpf` | `PFDx` music map: header, event graph, segment table |

### The segment table

Each `.mpf` ends with one 8-byte entry per segment:

```
u32 BE   segment start offset in the .mus, in units of 128 bytes
u32 BE   segment duration in milliseconds (truncated, not rounded)
```

Verified by matching against the real segment offsets parsed out of every
retail `.mus`: **45 of 45 files match exactly**, durations included.

Retail songs are cut into one segment per **measure** — cross-referencing the
declared `BPM` in `music.inf` against real segment lengths gives exactly
**4.000 beats per segment** across all 35 gameplay songs. `bep.mus` is 229
such segments, `apo.mus` is 414.

### How the replacement works

Your song becomes **one long segment**, and every entry in that song's
segment table is rewritten to `(offset 0, your duration)`. Whatever route the
map's event graph takes, every choice resolves to your track — so the event
graph never has to be decoded or edited. Everything outside the table stays
byte-identical.

Long segments are not a hack: the game ships `Peak1_Strm.mus` as a single
81-second segment, and `charsel.mus` segments run 20.5 s at a non-integer
45.17 beats.

## Audio quality

EA-XA is 4-bit ADPCM at 22050 Hz, so expect **~34 dB SNR** on music. That is
the format's ceiling, not this encoder's: a frame-exhaustive search over all
coefficient/shift pairs scores **identically to 0.00 dB**, and the retail
songs went through the same codec. Encoding runs ~5x faster than realtime per
core, and `--jobs` spreads songs across cores.

(If you ever measure ~56 dB, you are re-encoding audio that has already been
through EA-XA — it sits exactly on the reconstruction lattice, which flatters
the result. Any gain change reveals the true figure.)

## Budget

About **24 KB/s** of stereo audio, so roughly 86 MB per hour. Each archive is
rebuilt to its exact original byte size, which keeps the disc FST untouched:

| Archive | Size | Typical spare after replacing everything |
|---|---|---|
| `music.big` | 266,654,848 | plenty — retail songs are 8–16 MB, yours ~4 MB per 3 min |
| `music2.big` | 231,370,240 | same |

The hard ceiling per song is 12.7 minutes (the widest sample-count field).
If an archive would overflow, the build stops with the exact byte overage
rather than producing a broken disc.

## If playback restarts every few seconds

It shouldn't — the engine schedules segment changes from the table's
duration field, not the BPM grid, and that has been confirmed in game. But
if a song ever does restart every bar, `--stretch-bpm` also sets `BPM` so
one measure spans the whole track. `BPM` is parsed as a float (retail uses
160.42, 125.07), so fractional values are fine.

## Layout

```
SSX3 Music Patcher (Windows).cmd   double-click on Windows
SSX3 Music Patcher (Mac).command   double-click on macOS
ssx3_music.py                      command-line interface
ssx3_music_gui.py                  the window
ssx3/
  audio.py        loading, band-limited resampling, ffmpeg bridge
  bigfile.py      EA BIGF archive reader/writer
  discimage.py    read and patch files inside a disc image
  eaxa.py         EA-XA ADPCM decoder + encoder
  ffmpeg.py       finding ffmpeg/ffprobe, reading song tags
  gcdisc.py       FST walk, disc rebuild, DolphinTool bridge
  inifile.py      music.inf editor (in-place, size-preserving)
  mpffile.py      segment table locate/rewrite
  musfile.py      SCHl stream reader/writer
```

## Verified

- All three `.big` archives rebuild **byte-identically** from their own parts
- SCHl headers rebuild **byte-identically** for all 45 retail streams
- Segment table located and matched for **45/45** `.mpf` files
- After a build, every untouched archive entry is byte-identical and the
  patched `.mpf` differs **only** inside the segment table
- FST walk finds all 110 files at their recorded sizes; rebuilt image is
  1,459,978,240 bytes with game ID `GXBE69`
- Patching a real `.rvz` leaves **107 of 110 files byte-identical** to the
  source, and DolphinTool reads the result back as `SSX3 / GXBE69 / NTSC-U`
- The trick FX were confirmed in game by dumping Dolphin's DSP output: while
  airborne the 7 kHz band drops 10–37 dB while the bass moves under 1.5 dB,
  in 0.75–2.75 s episodes. That is a real-time low-pass, not ducking.

## Credits

Formats were reverse-engineered from the retail disc for this tool. No game
data is included in this repository — you supply your own disc image.
