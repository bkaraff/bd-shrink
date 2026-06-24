# AGENTS.md — bd_shrink.sh

## Project overview

Single-file **bash** script that shrinks BD50 Blu-ray backups or video files to BD25-compatible BDMV folders. Built-in Python heredocs handle MPLS binary parsing, demuxing, and data processing. Output is authored with `tsMuxeR`.

- Source: BDMV folder, parent folder containing `BDMV/`, or a single video file (`.mkv`/`.mp4`/`.m4v`)
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

# Burn directly to BD-R (pipes genisoimage into growisofs; no temp ISO)
./bd_shrink.sh -s /path/to/BDMV -o /output -f --movie-only --burn

# Create ISO for archival + burn
./bd_shrink.sh -s /path/to/BDMV -o /output -f --movie-only --burn --iso

# Dependency check (no source/output needed)
./bd_shrink.sh --install-deps
```

## Architecture

Single file `bd_shrink.sh`, shebang `#!/usr/bin/env bash`.

Phases:

1. **Inventory** — parse `.mpls`, probe `.m2ts` via ffprobe
2. **Classify** — largest HD playlist(s) = main movie; rest = extras/menus
3. **Budget** — target bitrate for main movie after accounting for menus + estimated extras
4. **Encode** — extras at 720p CRF; main movie two-pass VBR
5. **Rebuild** — surgical (replace M2TS in place, keep menus) or fresh `tsMuxeR` authoring (`--movie-only`)
6. **Validate** — file/CLPI checks

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
| `systemd-run` | Transient services for Phase 5 I/O | Required |
| `genisoimage` | UDF ISO creation for burn | |
| `growisofs` | BD-R burning | From `dvd+rw-tools` |
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
- **Pass 2 validation**: after encoding, `.h264` output is checked for Annex B start code; corrupt files are removed so they don't reach `tsMuxeR`.
- SRT subtitles are extracted but skipped in the tsMuxeR meta (tsMuxeR 2.7.0 on Linux lacks font rendering). PGS subtitles pass through. DVD/VobSub subtitles are filtered out.
- Movie-only mode allocates all space to video; audio + subtitle + container overhead can push total ~1–2% over target.

## Known issues

- BD-J discs with Java code that depends on specific CLPI metadata (timestamps, PIDs) of re-encoded clips may malfunction. Most BD-J menus only play playlists, so surgical mode works for the majority of discs. If a BD-J disc fails, `--movie-only` is the fallback.
- Some discs have corrupt H.264 in source clips. The script skips these gracefully; the output will lack video for affected clips but won't fail.
