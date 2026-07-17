# bd_shrink.sh

Shrink BD50 Blu-ray backups to BD25 (or any target size) on Linux — preserving menus or movie-only.

A Linux-native alternative to BD Rebuilder that uses `ffmpeg` + `tsMuxeR` to re-encode Blu-ray content while keeping the original structure intact.

**Requires bash**. All encoding runs in a single resumable Python process.

## Quick Start

```bash
# Show required dependencies and install commands (no source/output needed)
./bd_shrink.sh --install-deps

# Movie-only backup — fit on BD25
./bd_shrink.sh -s /path/to/BDMV -o /output/movie -t 23 --movie-only

# Full disc with menus (IGS discs only) — parent dir auto-names subfolder
./bd_shrink.sh -s /path/to/BDMV -o /mnt/nvme/ -t 23 -f

# Preview without encoding
./bd_shrink.sh -s /path/to/BDMV -o /tmp/test -n -f

# Help
./bd_shrink.sh -h
```

## Modes

### `--movie-only` (recommended)

Fresh BD authoring with `tsMuxeR`. Creates a clean BD structure with just the main movie, all audio tracks, subtitles, and chapters. **Works on any disc** (IGS and BD-J menus alike).

The output is playable in software players and hardware Blu-ray players.

### Default (surgical replacement)

Preserves the original menus, `index.bdmv`, `MovieObject.bdmv`, and all playlist/CLIP metadata. Only re-encodes the video streams in-place.

**Limitations:**
- Only works with IGS (bitmap) menus — not BD-J (Java) menus
- BD-J discs emit a warning but attempt anyway (test carefully)
- Multi-angle and complex seamless branching may fail

## Audio & Subtitles

All audio tracks are preserved:

| Track | Codec | Bitrate |
|-------|-------|---------|
| Primary | AC3 | 640 kbps (configurable via `--main-audio`) |
| Secondary/commentary | AC3 | 128 kbps (configurable via `--commentary-ab`) |

Original lossless codecs (DTS-HD MA, TrueHD, PCM) are re-encoded to AC3 for compatibility and size savings. Lossy codecs (DTS core, existing AC3) are passed through.

All PGS subtitle tracks are preserved as SUP streams.

Chapter markers from the original Blu-ray are carried forward (movie-only mode only).

## Space Budget

The script calculates the optimal video bitrate to fit everything within your target:

```
Target:           23 GB
Menu (pass-thru):  34 MB
Extras original: 6689 MB
Extras estimated:1838 MB  (720p compression)
Main original:  40784 MB
Main available: 23352 MB
Main bitrate:   17.53 Mbps  (to fill BD25)
```

## Output

- **Folder**: Complete BDMV structure in a source-named subdirectory. When `-o` points to a parent directory (e.g., `/mnt/nvme/`), the script creates `<source-title>/` inside it with `BDMV/` and `CERTIFICATE/`. The `.work` directory lives as a sibling in the output root.
- **ISO** (`--iso`): ISO file named after the source title (e.g., `<source-title>.iso`) containing only `BDMV/` and `CERTIFICATE/`. The `.work` directory is never included.
- **Burn** (`--burn`): Burn output to BD-R disc via `growisofs` or `xorriso`. Same exclusion of work files applies.

## File structure of a typical BD50

```
BDMV/
├── index.bdmv          ← Disc navigation (preserved in surgical mode)
├── MovieObject.bdmv    ← Menu logic (preserved in surgical mode)
├── PLAYLIST/
│   ├── 00000.mpls      ← Main movie
│   └── 00001.mpls      ← Alternate cut / extras
├── CLIPINF/            ← Metadata for each M2TS
├── STREAM/             ← The actual video/audio/subtitle streams
│   ├── 00000.m2ts      ← Main movie (25 GB)
│   ├── 00002.m2ts      ← Alternate version (17 GB)
│   ├── 00006.m2ts      ← Featurette (3.8 GB)
│   └── ...
├── BACKUP/             ← Copies of critical files
├── AUXDATA/            ← Fonts, sound effects for menus
└── BDJO/ or JAR/       ← BD-J Java menus (present = not ideal for surgical mode)
CERTIFICATE/
```

## Full Options

```
  -s, --source DIR       Source BDMV folder (must contain index.bdmv)
  -o, --output DIR       Output directory (auto-creates source-named
                           subfolder when pointed at a parent directory)
  -t, --target NUM       Target size in GB (default: 23 for BD25)
  --movie-only           Movie-only backup (no menus, fresh BD author)
  --iso                  Output ISO instead of BDMV folder
  --burn                  Burn output to BD-R after validation
  --burn-device DEV       Optical drive device path (auto-detected if omitted)
  --no-extras            Skip extras entirely
  --keep-one             Only keep the longest movie playlist
  --extras-scale WxH     Extras downscale resolution (default: 1280:720)
  --extras-ab BITRATE    Extras audio bitrate (default: 128k)
  --extras-crf NUM       Extras CRF value (default: 22)
  --main-preset NAME     x264 preset for main movie (default: slow)
  --main-audio BITRATE   Main audio bitrate (default: 640k)
  --commentary-ab        Commentary/secondary audio bitrate (default: 128k)
  -f, --force            Overwrite output if it exists
  -n, --dry-run          Show what would be done without encoding
      --install-deps     Show required tools and install commands, then exit
  -h, --help             Show this help
```

## Requirements

| Tool | Purpose |
|------|---------|
| `ffmpeg` / `ffprobe` | Encoding, stream probing, extraction |
| `tsMuxeR` | Blu-ray structure authoring (v2.7.0+) |
| `bc` | Math calculations |
| `python3` | MPLS binary parsing, data processing |
| `systemd-run` | Transient service management (part of systemd) |
| `libbluray-utils` | `bd_info` / `bd_list_titles` (optional) |
| `growisofs` (from `dvd+rw-tools`) | BD-R burning with UDF bridge (optional, `--burn`) |
| `genisoimage` | UDF ISO creation for `growisofs` (optional, `--burn`) |
| `eject` (from `util-linux`) | Disc ejection after burn (optional, `--burn`) |
| `vlc` or `mpv` + `libbluray` | Playback / testing output before burning (optional) |

See [INSTALL.md](INSTALL.md) for setup instructions, or run `./bd_shrink.sh --install-deps` to check for missing tools.

## How It Works

1. **Inventory** — Parse all `.mpls` playlists, probe every `.m2ts` with `ffprobe`
2. **Classify** — Identify main movie (longest HD playlist), extras, and menu clips
3. **Budget** — Calculate available space and optimal video bitrate
4. **Encode** — Extras at 720p CRF, main movie two-pass VBR with BD-compatible x264 flags
5. **Rebuild** — Either surgical replacement (keep menus) or fresh `tsMuxeR` authoring
6. **Validate** — Check size, verify playlist/CLIP/M2TS chain

## License

MIT
