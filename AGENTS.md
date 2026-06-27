# AGENTS.md — bd_shrink.sh

## Project overview

Single-file **bash** script that shrinks BD50 Blu-ray backups or video files to BD25-compatible BDMV folders. Built-in Python heredocs handle MPLS binary parsing, demuxing, and data processing. Output is authored with `tsMuxeR`.

- Source: BDMV folder, parent folder containing `BDMV/`, ISO image file (`.iso`), or a single video file (`.mkv`/`.mp4`/`.m4v`)
- `--movie-only` is the reliable mode; default surgical mode preserves original menus and structure (IGS/HDMV and BD-J)
- `--codec hevc` uses `libx265` when `tsMuxeR` supports `V_MPEGH/ISO/HEVC`

## Key commands

```bash
# Syntax check
bash -n bd_shrink.sh

# Interactive TUI (auto-launched when -s/-o omitted)
./bd_shrink.sh

# Movie-only — fresh BD, no menus, works on any disc including BD-J
./bd_shrink.sh -s /path/to/BDMV -o /output -f --movie-only

# Surgical — keep menus; preserves IGS and BD-J structure
./bd_shrink.sh -s /path/to/BDMV -o /output -f

# Single video file (forces movie-only)
./bd_shrink.sh -s movie.mkv -o /output -f

# ISO image input (auto-mounts or extracts)
./bd_shrink.sh -s disc.iso -o /output -f --movie-only

# Burn directly to BD-R (pipes genisoimage into growisofs; no temp ISO)
./bd_shrink.sh -s /path/to/BDMV -o /output -f --movie-only --burn

# Create ISO for archival + burn
./bd_shrink.sh -s /path/to/BDMV -o /output -f --movie-only --burn --iso

# Dependency check (no source/output needed)
./bd_shrink.sh --install-deps
```

## Architecture

Single file `bd_shrink.sh`, shebang `#!/usr/bin/env bash`, `set -euo pipefail` + `trap ... ERR`. Logic lives in inline `python3` heredocs (MPLS parsing, classify, budget, precompute, the entire encode loop). Editing those heredocs means editing Python embedded in bash — the bash `ERR` trap and `set -e` still apply to the surrounding shell, not inside Python.

Phases:

1. **Inventory** — parse `.mpls`, probe `.m2ts` via ffprobe → `inventory.json`
2. **Classify** — largest HD playlist(s) = main movie; rest = extras/menus → `classify.json`
3. **Budget** — target bitrate for main movie after accounting for menus + estimated extras → `budget.json`
4. **Encode** — extras at 720p CRF; main movie two-pass VBR
5. **Rebuild** — surgical (replace M2TS in place, keep menus) or fresh `tsMuxeR` authoring (`--movie-only`)
6. **Validate** — file/CLPI checks

**Two `run_ff` functions, do not confuse them**: the bash `run_ff()` (top of file) wraps commands in `systemd-run --user --wait` (transient service survives a shell crash). A *separate* Python `run_ff()` inside the Phase 4 encode heredoc wraps `subprocess.run` and implements per-clip resume by skipping outputs that already exist.

**Phase 4 runs as a single `python3 -u` process** (one invocation, all extras + main clips). Phase 4/5 metadata is precomputed by one `systemd-run` call writing `.work/.*.txt` dotfiles that bash then reads with `read`/`while read` loops. Adding data for later phases means adding a dotfile in the precompute heredoc and a matching `read` loop.

**Resumability**: the encode phase skips clips whose output already exists. After a crash or interruption, re-run the same command (without `-f`) to resume from where it left off. Do NOT delete the `.work` directory or use `-f` when resuming.

## Modes

| Mode | Result | Caveats |
|------|--------|---------|
| `--movie-only` | Fresh BD with one main-movie playlist | Always works; no menus |
| Default (surgical) | Original menus/structure preserved | Works for IGS and BD-J; re-encoded clips get new CLPI metadata |

## Flag interactions

- `--movie-only` implies `--keep-one`
- `--no-extras` skips extras encoding but keeps menus (surgical only)
- `-f` is checked **before** dry-run exit — needed even with `-n` if output exists
- `--iso` creates an ISO; `--burn --iso` also creates one and then burns from it
- `--nice` with no argument defaults to `N=19`; valid range 0–19. Applied via `systemd-run --property=Nice` for the bash `run_ff` and `nice -n` for encoder subprocesses inside the Phase 4 Python process.

## Burn path

`--burn` alone pipes `genisoimage -udf -allow-limited-size` directly to `growisofs` — no temp ISO. `--burn --iso` creates an ISO first, then burns it.

Critical details:

- `genisoimage` and `growisofs` are required for direct burning
- `burn_output()` resolves full binary paths via `command -v` before passing to `run_ff()`, because `run_ff()` launches via `systemd-run` with a restricted `PATH` (`/usr/local/bin:/usr/bin:/bin`) that may not include Homebrew or other non-standard bin directories
- `MKISOFS` is set to the full path of `genisoimage` when calling `growisofs` because `/usr/bin/mkisofs` on this system is `xorriso`'s stub and does **not** support `-udf`
- `-allow-limited-size` is required; M2TS files commonly exceed 4 GiB
- UDF 2.00 is what `genisoimage` produces; players that strictly require UDF 2.50 may still reject the disc
- No MD5 verification; growisofs handles BD-R write verification internally

## Menu preservation (surgical mode)

Menu clips are excluded from re-encoding via three signals:

1. `PlayList_type == 1` read from the MPLS `AppInfoPlayList` struct
2. Any playlist sharing a clip with a known menu playlist
3. Zero chapter marks + duration < 120 s (warnings, logos, transitions)

Their clips are copied verbatim so IGS (Interactive Graphics) overlays stay intact. Anything else classified as an extra is re-encoded to 720p.

### SubPath clips

The MPLS parser extracts SubPlayItem clips from SubPath entries (not just the main PlayList). SubPath length is a 4-byte uint per the BDMV spec. SubPath clips are added to the playitems list with duration 0 so they flow through inventory → classification → rebuild without affecting the playlist's total duration. This catches clips referenced only via sub-paths (e.g. menu background video, PiP, seamless branching).

### Orphan-clip safety net

After copying all playlist-referenced clips, the surgical rebuild does a final pass over `SOURCE/STREAM/*.m2ts` and copies any file not already in the output. This catches clips that exist on the disc but are referenced by navigation structures outside of MPLS (e.g. `MovieObject.bdmv` FirstPlayback/TopMenu entries).

## Dependencies

| Tool | Purpose | Note |
|------|---------|------|
| `ffmpeg` + `ffprobe` | Encoding/probing | rpmfusion-free on Fedora |
| `tsMuxeR` | BD authoring | v2.7.0 from GitHub binary |
| `bc` | Math | |
| `python3` | MPLS parsing, data | |
| `systemd-run` | Transient services for all `run_ff` subprocesses (encode precompute, rebuild, ISO, burn) | Required |
| `genisoimage` | UDF ISO creation for burn | |
| `growisofs` | BD-R burning | From `dvd+rw-tools` |
| `mount` | ISO mounting (optional) | For `.iso` input; falls back to `bsdtar` or `7z` if unavailable |
| `bsdtar` or `7z` | ISO extraction fallback | If `mount` unavailable |
| `gum` | Optional TUI | |

Run `./bd_shrink.sh --install-deps` to see install commands.

## Work directories

Default: `${OUTPUT}.work` (sibling of output). Configurable via `-w / --work`. When `-o` points to an existing parent directory without `BDMV/`, a source-named subdirectory is created; the `.work` directory stays a sibling of that subdirectory.

## Logging

Mirrored to a log file in `/var/log/bd-shrink` if writable, otherwise `~/.local/share/bd-shrink/logs`, named `bd_shrink_YYYYMMDD_HHMMSS.log`. Also mirrored to `${WORK_DIR}/bd_shrink.log` for convenience during resume.

## TUI mode

Auto-launched when `-s`/`-o` are omitted (requires `gum`). Source selection retries if the chosen folder contains no BDMV or video file, rather than dying.

## Gotchas

- **bash only**: `README.md` still incorrectly says "Requires zsh"; the script was rewritten in bash. Use `bash -n bd_shrink.sh` for syntax checks, not `zsh -n`.
- **`local` is only valid inside functions** in bash.
- Use `read < file` for line-oriented metadata reads; the script reads metadata files with `read`/`while read` loops, not `$(< file)`.
- `EXTRAS_CLIPS` and `MAIN_CLIPS` have trailing newlines from `while read` — trimmed with parameter expansion (`${VAR%$'\n'}`) before use.
- Playlist `clips` arrays are deduplicated during inventory assembly (preserving order).
- **Pass 2 validation**: after encoding, the raw video output (`.h264` or `.hevc`) is checked for a NAL start code; corrupt files are removed so they don't reach `tsMuxeR`.
- SRT subtitles are extracted but skipped in the tsMuxeR meta (tsMuxeR 2.7.0 on Linux lacks font rendering). PGS subtitles pass through. DVD/VobSub subtitles are filtered out.
- Movie-only mode allocates all space to video; audio + subtitle + container overhead can push total ~1–2% over target.

## Known issues

- BD-J discs with Java code that depends on specific CLPI metadata (timestamps, PIDs) of re-encoded clips may malfunction. Most BD-J menus only play playlists, so surgical mode works for the majority of discs. If a BD-J disc fails, `--movie-only` is the fallback.
- Some discs have corrupt H.264 in source clips. The script skips these gracefully; the output will lack video for affected clips but won't fail.

## Recent improvements (v0.2.0+)

### Input enhancements
- **ISO image support** — accepts `.iso` input files; auto-mounts with `mount -o loop,ro` or falls back to `bsdtar`/`7z` extraction if unavailable
- **TUI validation** — updated source selection to accept `.iso` alongside `.mkv`/`.mp4`/`.m4v` files

### Bug fixes
- **Brace-in-path crashes (H1)** — fixed `.format()` scope in path joining (lines 1739, 1823)
- **HEVC passlog glob (H2)** — defensive glob for `.log` files instead of hardcoded `-0.log` (lines 1880, 1902, 1906)
- **Argument validation** — added `--target` integer check and audio-bitrate format validation (`NNNk` or `NNN`)
- **Missing file guards** — `MovieObject.bdmv` copy now skips gracefully if file doesn't exist; no more fatal errors on incomplete discs
- **Resume parity (M5)** — audio track count verified on resume (matches extras behavior)
- **Branched-title detection (B8)** — warns if multi-clip titles have differing audio/subtitle track counts (seamless branching edge case)
- **Budget double-count (B9)** — main bitrate is now computed from the UNIQUE set of clips that will actually be encoded (sum of `duration_sec` over `main_clips`), not from summed per-playlist durations. Previously, seamless-branching titles with multiple playlists sharing the same clips (e.g. alternate cuts/angles) had their budgeted duration inflated (e.g. 3h30m instead of 1h51m), producing a too-low bitrate and a too-small ISO. `main_duration_str`/`main_duration_sec` now reflect encoded-clip runtime rather than summed-playlist runtime.
- **Early diagnosis (B7)** — script exits immediately if no main movie playlist is identified after classification (better error message than later phases)
- **TUI retry loop (B4)** — rejects unsupported source extensions early with helpful message, loops to let user retry
- **Budget read tolerance (C1)** — reads are now `|| true` to prevent early exit on truncated `.budget_values.txt`
- **Clip count reporting (C4)** — fixed zero-clip count display (was showing "1" for empty input)
- **ISO temp cleanup** — ISO mount points created in `/tmp` and cleaned up on exit (even on error)
