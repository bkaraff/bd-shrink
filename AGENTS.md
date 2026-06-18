# AGENTS.md — bd_shrink.sh

## Project overview

Single-file **zsh** script (~1740 lines) that shrinks BD50 Blu-ray backups or MKV files to BD25-compatible BDMV folders. The `-s` flag accepts BDMV folders or **.mkv files** (MKV forces movie-only mode). Built-in Python heredocs handle MPLS binary parsing, MKV demuxing, and data processing. Output is authored with `tsMuxeR`.

## Key commands

```bash
# Syntax check
zsh -n bd_shrink.sh

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

Single file `bd_shrink.sh`. Shebang: `#!/usr/bin/env zsh`. No separate library files.

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

zsh 5.9 crashes non-deterministically when ANY child process exits. The script minimizes direct shell children:

| Phase | Strategy | Shell children |
|-------|----------|----------------|
| Inventory | Single Python subprocess | 1 |
| Pre-compute | `systemd-run` wrapping Python | 1 (systemd-run exits quickly) |
| Encode | Single bare `python3` heredoc | 1 |
| Rebuild | `systemd-run` per command (shell `run_ff`) | 0 direct (systemd manages) |
| Validate | Builtins only | 0 |

When the shell crashes mid-encode, restarting the script resumes from where it left off (Python `run_ff` skips existing output files).

### Known issue: SIGCHLD crashes persist

Despite the mitigation above, zsh 5.9 still crashes non-deterministically (symptoms: "parse error near \n" at a nonexistent line number, or segfault). These are NOT syntax errors — they are the zsh SIGCHLD bug. The only reliable fix is to **rewrite the script in bash**. No zsh-specific features are actually used:

```bash
# zsh                          bash equivalent
setopt SH_WORD_SPLIT           (default)
setopt NULL_GLOB               shopt -s nullglob
```

Rewriting to bash would eliminate the crash entirely while preserving identical behavior.

## zsh-specific options
```zsh
setopt SH_WORD_SPLIT   # split unquoted $var on IFS (like bash default)
setopt NULL_GLOB       # unmatched globs → empty (like bash nullglob)
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
- In zsh, `local` outside a function behaves like a regular assignment. The surgical rebuild block uses `local` at top-level — this is safe but non-idiomatic.
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

- [ ] **P0-1** `check_deps()` defined but never called (line 464). Also, its `command -v run_ff` check never finds shell functions — replace with `type run_ff` or remove `run_ff` from the list. Call `check_deps` immediately after arg parsing.
- [ ] **P0-2** `tsMuxeR` in movie-only authoring is a bare shell child, not wrapped in `run_ff` (line 1847). Surgical mode correctly uses `run_ff tsMuxeR` (line 1916). Fix: `run_ff tsMuxeR "$META_FILE" "$DST" ...`
- [ ] **P0-3** `MKISOFS=genisoimage` prefix before `run_ff` is silently dropped — `systemd-run` does not inherit the shell environment. `growisofs` never sees the override. Fix: `run_ff env MKISOFS=genisoimage growisofs ...` (line 530).
- [ ] **P0-4** All unquoted PYEOF heredocs shell-expand `$SOURCE`, `$WORK_DIR`, etc. directly into Python string literals. Paths with apostrophes (e.g. `O'Brien's`) cause `SyntaxError`; crafted paths can inject Python code. The clip-probe heredoc (line 806) already uses the correct pattern (`python3 - "$ARG" << 'PYEOF'` + `sys.argv`). Apply the same fix to every other heredoc (lines 749, 843, 957, 1101+).

### P1 — Silent wrong behavior / incorrect output

- [ ] **P1-1** Nine-plus bare `cp` calls in surgical Phase 5 are direct shell children (lines 1869–1872, 1953, 1957, 1961–1964). Wrap each in `run_ff cp ...` to match the SIGCHLD mitigation strategy.
- [ ] **P1-2** Five dead shell arrays (`PROBED_CLIPS`, `CLIP_AUD`, `CLIP_SUB`, `CLIP_HEIGHT`, `CLIP_WIDTH`) and their `while read` loop are declared/populated but never read in shell code — Phase 4 reads `.clip_precompute.txt` directly in Python (lines 767, 1361–1367). Delete them.
- [ ] **P1-3** Budget Phase 3 hard-codes 8 audio tracks per clip (lines 1193–1201). Phase 4 uses the actual count (`for ai in range(src_aud)`). Fix the budget to read the real audio track count from the first main clip's inventory data; cap at 8 as a safety limit.
- [ ] **P1-4** `mkisofs` fallback for ISO creation omits `-allow-limited-size` (lines 1989–1991). Required for M2TS files > 4 GB or the ISO is silently corrupt. The `genisoimage` path (line 1984) already includes it.
- [ ] **P1-5** `--no-extras` has no effect on disc space. `PY_NO_EXTRAS` is never checked in budget Phase 3, so the video bitrate is computed too conservatively. Phase 5 also copies extras verbatim (original size) without checking `NO_EXTRAS`, risking an over-budget disc. Fix both the budget and the Phase 5 copy loop.
- [ ] **P1-6** MKV source detection is case-sensitive: `*.mkv` rejects `.MKV` files common on NTFS mounts (line 602). Fix: `[[ "${SOURCE:l}" == *.mkv ]]`.
- [ ] **P1-7** `rm -rf "${REBUILD_DIR:-}"` at cleanup (line 2058) expands to `rm -rf ""` in movie-only mode where `REBUILD_DIR` is never set. Guard: `[[ -n "${REBUILD_DIR:-}" ]] && rm -rf "$REBUILD_DIR"`.

### P2 — Reliability / quality

- [ ] **P2-1** `genisoimage`, `mkisofs`, and `xorriso` ISO creation runs are bare shell children (lines 1984, 1989, 1994). Writing a 25 GB ISO takes 5–15 minutes — high SIGCHLD risk. Wrap each in `run_ff`.
- [ ] **P2-2** Phase 5 surgical rebuild is not resumable. Unlike Phase 4 (which skips existing `.h264` files), a crash in Phase 5 causes a full re-run of all `tsMuxeR` remux + copy operations. Add a per-clip guard: skip clips whose `.m2ts` already exists in `$DST/BDMV/STREAM/`.
- [ ] **P2-3** Audio extraction in Phase 4 Python is not resumable — `run_ff` is called without `out_file`, bypassing the existence check (lines 1555, 1633). Pass the first audio output file as `out_file` so restarts skip already-extracted tracks.
- [ ] **P2-4** No final output size check in Phase 6. Budget miscalculations and verbatim-copy overruns go undetected. Add a `du -sb "$DST"` check and warn if the result exceeds `TARGET_GB`.
- [ ] **P2-5** Existing AC3/EAC3 audio tracks are always re-encoded to AC3 (lines 1551, 1629) — a generation loss with no benefit. Pass through with `-c:a copy` when the source codec is `ac3` or `eac3`.
- [ ] **P2-6** `SCRIPT_DIR` is computed via a subshell at startup (line 9) but never referenced anywhere in the script. Delete it.
- [ ] **P2-7** Phase 2 logging spawns 5+ separate Python subprocesses just to extract label strings from `classify.json` (lines 1082–1091). Consolidate into a single reporting block at the end of the classification heredoc.

### P3 — Code quality

- [ ] **P3-1** `local` used at top-level (outside any function) in several places (lines 589, 619, 1922, 1939). In zsh this is silently a global assignment. Remove the `local` keyword from top-level assignments.
- [ ] **P3-2** Phase 2 detail loop always iterates over tuples from `dict.items()`, so `isinstance(pl_name, tuple)` is always `True` (lines 1054–1057). Simplify to `for pl_name, pl_data in pl_sorted:`.
- [ ] **P3-3** `import glob` is repeated twice inside the `run_ff` function body and once in the pass-1 cleanup block (lines 1497, 1508, 1664). Move to the top-level imports of the encoding heredoc.
- [ ] **P3-4** All `open()` calls in Python heredocs omit `encoding='utf-8'`. On non-UTF-8 locales or discs with non-ASCII metadata filenames this raises `UnicodeDecodeError`. Add `encoding='utf-8'` throughout (binary MPLS files already use `'rb'` — leave those alone).
- [ ] **P3-5** ISO/disc volume label is hardcoded as `"BD_SHRINK"` (lines 530, 1984, 1989, 1994, 2000). Use the source title instead (truncated to 32 chars, uppercased, spaces → underscores per ISO9660 rules).
- [ ] **P3-6** `VERSION="0.1.0"` (line 8) has never been bumped despite burn, TUI, MKV, ISO, and HEVC-groundwork features being added. Bump to a meaningful value.
- [ ] **P3-7** BD-J detection warning fires in `--movie-only` mode (line 647) where menu preservation is irrelevant. Gate the warning on `! $MOVIE_ONLY`.

### P4 — Feature gaps

- [ ] **P4-1** No encoding progress visibility during multi-hour x264 two-pass encode. Consider writing periodic elapsed-time lines to the log, or passing ffmpeg a `-progress` pipe that appends a summary line every N seconds.
- [ ] **P4-2** Add `--codec hevc` option using `libx265`. BD-compatible HEVC needs `--level 4.1 --high-tier` and `vbv-bufsize`/`vbv-maxrate` constraints. tsMuxeR 2.7.0 supports HEVC. Would significantly improve quality-per-bit on smaller targets.
- [ ] **P4-3** Pre-compute block hits `break` after the first main playlist (lines 1289–1293), so only the first playlist's clips are written to `.main_clips.txt`. In surgical non-`--keep-one` mode, alternate cuts are present in MPLS files but their M2TS clips are copied verbatim at original bitrate, silently risking an over-budget disc.
- [ ] **P4-4** Add `--clean-work` flag (or document an age-based cleanup one-liner). Accumulated `.work` directories from repeated runs can exceed 20 GB each.

### ARCH — Long-term

- [ ] **ARCH-1** Evaluate full rewrite to bash. No zsh-specific features are used (`SH_WORD_SPLIT` is bash default; `NULL_GLOB` → `shopt -s nullglob`). A bash rewrite eliminates the SIGCHLD crash entirely and removes the need for most `systemd-run` wrapping. This is the only permanent fix for the crash bug.