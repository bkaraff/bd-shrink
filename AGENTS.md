# AGENTS.md — bd_shrink.sh

## Project overview

Single-file **bash** script (~2100 lines) that shrinks BD50 Blu-ray backups or MKV files to BD25-compatible BDMV folders. The `-s` flag accepts BDMV folders or **.mkv files** (MKV forces movie-only mode). Built-in Python heredocs handle MPLS binary parsing, MKV demuxing, and data processing. Output is authored with `tsMuxeR`.

## Key commands

```bash
# Syntax check
bash -n bd_shrink.sh

# Interactive TUI (auto-launched when -s/-o are omitted)
./bd_shrink.sh

# Force TUI even when source/output are provided
./bd_shrink.sh --tui

# Movie-only (fresh BD, no menus, works on any disc)
./bd_shrink.sh -s /path/to/BDMV -o /output -f --movie-only

# Surgical (keep menus, IGS only — the default if no --movie-only)
./bd_shrink.sh -s /path/to/BDMV -o /output -f --keep-one

# MKV input (forces movie-only mode)
./bd_shrink.sh -s movie.mkv -o /output -f

# Dry-run (needs -f if output dir already exists)
./bd_shrink.sh -s /path/to/BDMV -o /tmp/test -n -f

# Show required dependencies and install commands (no source/output needed)
./bd_shrink.sh --install-deps
```

`--iso` works with any mode (surgical and movie-only), not just `--movie-only`.

When `-o` points to an existing directory that doesn't already contain `BDMV/`, the script auto-creates a source-named subdirectory (e.g., `-o /mnt/root/` → `/mnt/root/<source-title>/`). This keeps the work directory as a true sibling in the output root. Gated on `-f`.

## Architecture

Single file `bd_shrink.sh`. Shebang: `#!/usr/bin/env bash`. No separate library files.

**Six labeled phases** in the script (Pre-compute is a sub-step inside Phase 4):

1. **Inventory** — parse `.mpls` (Python heredoc, binary struct), probe `.m2ts` (single Python subprocess); for MKV, demux streams via ffprobe
2. **Classify** — largest long playlist(s) = main movie (size is the primary signal to avoid bogus playlists that repeat a short clip many times); rest = extras or menus; MKV is always a single movie
3. **Budget** — calculate remaining space and target bitrate for main movie
4. **Encode** — pre-compute sub-step (`systemd-run` wrapping `python3 -c` that writes clip metadata), then a bare `python3 -u << PYEOF` heredoc handles ALL extras + main movie encoding via `subprocess.run()`. The heredoc is **not** wrapped in `systemd-run`.
5. **Rebuild** — surgical (keep original menus) or fresh `tsMuxeR` authoring (`--movie-only`). Uses shell `run_ff` function (systemd-run wrapper) for each `cp`/`tsMuxeR` call.
6. **Validate** — file count, CLPI verification (builtins only) then print `.work` retention message

There are **6 Python heredocs** (PYEOF blocks): MPLS parsing, clip probing, inventory assembly, classification, budget calculation, and encoding. The pre-compute block and MKV playlist creation use `python3 -c` instead.

**Two output modes:**
- Default (surgical): keeps `index.bdmv`, `MovieObject.bdmv`, all `.mpls`, copies/remuxes M2TS. IGS menus only.
- `--movie-only`: fresh `tsMuxeR` authoring. No menus. Works on any disc including BD-J.

**Flag interactions:**
- `--movie-only` implies `--keep-one` (only first main playlist encoded)
- `--no-extras` skips extras encoding but keeps menus (surgical mode); distinct from `--movie-only` which discards menus entirely
- `-f` is checked **before** the dry-run exit — you need `-f` even with `-n` if the output directory already exists

### Subtitle handling

- **SRT** subtitles are extracted from source but **skipped** in the tsMuxeR meta file. tsMuxeR 2.7.0 on Linux lacks font rendering, producing `Can't load symbol code '65' from font` errors.
- **PGS** subtitles (`.sup` files) pass through correctly as `S_HDMV/PGS`.
- **DVD/VobSub** subtitles (`dvd_subtitle` codec) are filtered out entirely — not BD-compatible.

## The two `run_ff` functions

There are **two separate** `run_ff` definitions — a shell function and a Python function — serving different phases:

**Shell function** (early in file): wraps each command in `systemd-run --user --wait`, making it a transient systemd service rather than a direct child of the shell. Used in Phase 5 (rebuild) for `cp`, `tsMuxeR`, etc. This avoids SIGCHLD because the exiting process is systemd's child, not the shell's.

**Python function** (inside the encoding heredoc): uses `subprocess.run()` for all ffmpeg calls. Checks `out_file` existence before encoding — **resumable** across crashes.

## SIGCHLD mitigation strategy

The script was originally written for zsh, but zsh 5.9 crashes non-deterministically when child processes exit (the zsh SIGCHLD bug). The script has been rewritten in bash, which does not have this bug.

`systemd-run --user --wait` is still used in Phase 5 (Rebuild) for `cp`/`tsMuxeR`/ISO operations to keep long-running I/O commands as transient systemd services, but it is no longer required for crash avoidance.

| Phase | Strategy | Shell children |
|-------|----------|----------------|
| Inventory | Single Python subprocess | 1 |
| Pre-compute | `systemd-run` wrapping Python | 1 (systemd-run exits quickly) |
| Encode | Single bare `python3` heredoc | 1 |
| Rebuild | `systemd-run` per command (shell `run_ff`) | 0 direct (systemd manages) |
| Validate | Builtins only | 0 |

When the script crashes mid-encode, restarting resumes from where it left off (Python `run_ff` skips existing output files).

### SIGCHLD crashes: RESOLVED

The bash rewrite eliminated the zsh SIGCHLD crash entirely. No zsh-specific features were needed.

## bash-specific options
```bash
shopt -s nullglob   # unmatched globs → empty
set -euo pipefail   # strict mode
```

## Dependencies

`tsMuxeR` binary is `tsMuxeR` (capital R), not `tsmuxer`. Installed at `/usr/local/bin/tsMuxeR` v2.7.0.

`systemd-run` is **required** for both the shell `run_ff` function and the pre-compute phase. It is listed in `check_deps` indirectly (via `run_ff`) but not explicitly. If systemd user services aren't available, the script fails at runtime.

`--install-deps` checks all required tools and prints install commands for missing ones (tsMuxeR shows the GitHub download steps; everything else shows `dnf install`). It does NOT auto-install. Optional tools (gum, xorriso) are listed separately. Requires no `-s`/`-o` args — runs and exits immediately.

## Work directories

Default: `${OUTPUT}.work` (sibling of output, NOT inside it). Configurable via `-w / --work`.

When `-o` points to a parent directory (existing, no `BDMV/`), the script auto-creates a source-named subdirectory so the work directory stays a sibling in the output root rather than mixed in with BDMV/CERTIFICATE.

## Logging

All output is mirrored to a log file. Default log directory:
- `/var/log/bd-shrink` if writable without root
- Otherwise `~/.local/share/bd-shrink/logs`

Log files are named `bd_shrink_YYYYMMDD_HHMMSS.log`.

## TUI mode

When run without `-s`/`-o` in an interactive terminal with `gum` installed, the script launches an interactive TUI instead of erroring. Pass `--tui` to force TUI mode even when args are provided.

- **Source selection**: `gum filter` fuzzy finder from `SOURCE_ROOT` (saved at `~/.config/bd-shrink/source_root`), falling back to `gum file` browser from `/data-nvme1` or `$HOME`.
- **BDMV auto-detection**: after selecting a folder, looks for `index.bdmv`, `BDMV/index.bdmv`, or `*/BDMV/index.bdmv`. If no BDMV found, looks for a video/ISO file directly inside.
- **Options**: mode, output format, and encoding preset via `gum choose --limit=1`.
- **Summary**: colorized Catppuccin Mocha box, then action chooser to revise any step before processing.

## Gotchas

- `git -C ~/projects/bd-shrink push` fails — use `cd` or `workdir` parameter
- `{1..0}` in zsh expands to `1 0` (descending range, not empty) — use C-style `for ((...))`
- Use `read < file` for line-oriented metadata reads; the script reads metadata files with `read`/`while read` loops, not `$(< file)`
- Work dirs from dry-runs accumulate — clean up `<output>.work` regularly
- **Log buffering**: `stdout` is line-buffered, but Python `sys.stderr` writes are unbuffered with `-u`. Progress may appear after Python exits rather than in real time.
- `EXTRAS_CLIPS` and `MAIN_CLIPS` have trailing newlines from the `while read` loop — trimmed with `${VAR%$'\n'}` before use.
- Playlist `clips` arrays are deduplicated during inventory assembly (preserving order). Duplicate playitems referencing the same clip are collapsed so each clip encodes once and appears once in the tsMuxeR meta file.
- **Main movie classification** prefers the largest long playlist by total size, not the longest by duration. Some discs have bogus playlists that repeat a short clip hundreds of times, producing a huge duration but tiny size; these are now classified as extras.
- `local` outside a function is an error in bash; all top-level assignments in the original zsh version have been converted to plain assignments.
- **Pass 2 encoding validation**: After pass 2, the script validates `.h264` output by checking for an Annex B start code (`\x00\x00\x00` or `\x00\x00\x01`) in the first bytes. Corrupt files (e.g. from VC-1 decode failures) are removed so they don't reach tsMuxeR.
- `get_source_title` strips trailing slashes from source paths so `/.../BDMV/` returns the parent directory name (previously returned an empty title).

### Known issues

- Some discs have corrupt H.264 in clips. The script gracefully skips these — the BD output will lack video for the affected clips, but won't fail.
- Movie-only mode allocates ALL space to video. Audio + subtitle + tsMuxeR container overhead can push the total slightly above target (~1-2% over, still valid).

## Next steps

### Burn-to-disk (`--burn`) — IMPLEMENTED

Burns output BDMV folder directly to BD-R. Two modes:

- **`--burn` alone**: Pipes `genisoimage -udf` directly into `growisofs` — **no temp ISO created**. This avoids duplicating the ~22GB output.
- **`--burn --iso`**: Creates an ISO file with `genisoimage -udf` first (for archival), then burns from the ISO.

Both modes produce UDF-formatted discs (via `genisoimage -udf -allow-limited-size`). The `-allow-limited-size` flag is required because M2TS files on BD often exceed 4GiB. Only `BDMV/` and `CERTIFICATE/` are included in the ISO/disc (via `--graft-points` for genisoimage/mkisofs, `-map` for xorriso); the `.work` directory is never included.

Key details:
- `genisoimage` and `growisofs` are required for `--burn` (direct pipe mode)
- `growisofs` (from `dvd+rw-tools`) provides UDF bridge for BD player compatibility
- `MKISOFS=genisoimage` is set so `growisofs` uses the working UDF-capable mkisofs rather than xorriso's stub
- Without `growisofs`, falls back to `xorriso -as cdrecord` for ISO file mode only (no UDF)
- `--burn-device /dev/sr0` specifies the optical drive; auto-detected if omitted
- No MD5 verification (genisoimage lacks `-md5` support; growisofs handles burn verification internally for BD-R)
- ISO filenames use the source title (e.g., `The_Himalayan_1976.iso`) rather than a hidden `.iso` file
- `mkdir -p "$DST/CERTIFICATE"` runs unconditionally before ISO/burn phases to guarantee the directory exists regardless of output mode

### Menu preservation (surgical mode) — IMPLEMENTED

Surgical mode reads `PlayList_type` from the MPLS binary's `AppInfoPlayList` struct. Playlists with `PlayList_type == 1` (menu/interactive) are forced into the menu category regardless of duration. Their clips are excluded from re-encoding in the budget phase (`extras_clips -= menu_clips`), preserving IGS (Interactive Graphics) button overlays.

## Planned fixes

Identified via code review. Work through these in priority order. After completing any item, mark it `DONE` inline and update the Gotchas / Known issues sections above if relevant.

### P0 — Crash / silent data corruption

- [x] **P0-1** `check_deps()` is now called after arg/output validation; `run_ff` removed from dependency list (it's a shell function, not an executable).
- [x] **P0-2** Movie-only `tsMuxeR` is now wrapped in `run_ff` for consistency with surgical mode.
- [x] **P0-3** `MKISOFS=genisoimage` is now passed via `env` inside `run_ff`, so `growisofs` sees the override.
- [x] **P0-4** All Python heredocs and `python3 -c` blocks that previously interpolated shell variables are now quoted and pass values through `sys.argv`.

### P1 — Silent wrong behavior / incorrect output

- [x] **P1-1** All bare `cp` calls in surgical Phase 5 are now wrapped in `run_ff` for consistent systemd management.
- [x] **P1-2** Dead shell arrays (`PROBED_CLIPS`, `CLIP_AUD`, `CLIP_SUB`, `CLIP_HEIGHT`, `CLIP_WIDTH`) and their read loop removed.
- [x] **P1-3** Budget now reads the actual audio track count from the first main clip, capped at 8.
- [x] **P1-4** `mkisofs` fallback now includes `-allow-limited-size`.
- [x] **P1-5** `--no-extras` is now honored in the budget (skips extras size) and Phase 5 surgical copy loop (skips extras clips).
- [x] **P1-6** MKV detection is now case-insensitive (`${SOURCE,,}`).
- [x] **P1-7** Cleanup now guards `rm -rf` so it never expands to an empty path.

### P2 — Reliability / quality

- [x] **P2-1** ISO creation (`genisoimage`/`mkisofs`/`xorriso`) now wrapped in `run_ff` for consistency.
- [x] **P2-2** Surgical rebuild now skips clips whose `.m2ts` already exists in `$DST/BDMV/STREAM/` (guards tsMuxeR remux).
- [x] **P2-3** Audio extraction now passes first output audio file as `out_file` to `run_ff` for resumability.
- [x] **P2-4** Final output size check added in Phase 6; warns if `du -sb "$DST"` exceeds `TARGET_GB`.
- [x] **P2-5** AC3/EAC3 source audio tracks are now passed through with `-c:a copy` instead of re-encoded.
- [x] **P2-6** Unused `SCRIPT_DIR` startup variable removed.
- [ ] **P2-7** Phase 2 logging still spawns separate `python3 -c` calls. Consolidate into a single reporting block.

### P3 — Code quality

- [x] **P3-1** `local` at top-level removed (bash requires `local` inside functions only).
- [x] **P3-2** Redundant `isinstance(pl_name, tuple)` check simplified to `for pl_name, pl_data in pl_sorted:`.
- [x] **P3-3** Repeated `import glob` inside the encoding heredoc moved to the top-level imports.
- [x] **P3-4** All text `open()` calls in Python heredocs and `-c` one-liners now specify `encoding='utf-8'`.
- [x] **P3-5** ISO/disc volume label now uses sanitized source title instead of hardcoded `"BD_SHRINK"`.
- [x] **P3-6** `VERSION` bumped to `0.2.0`.
- [x] **P3-7** BD-J detection warning now gated on `! $MOVIE_ONLY`.

### P4 — Feature gaps

- [ ] **P4-1** No encoding progress visibility during multi-hour x264 two-pass encode.
- [ ] **P4-2** Add `--codec hevc` option using `libx265`.
- [ ] **P4-3** Pre-compute block only writes the first main playlist's clips to `.main_clips.txt`.
- [ ] **P4-4** Add `--clean-work` flag or document cleanup one-liner.

### ARCH — Long-term

- [x] **ARCH-1** Full rewrite to bash completed. SIGCHLD crashes eliminated.