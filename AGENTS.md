# AGENTS.md — bd_shrink.sh

## Project overview

Single-file **zsh** script (~1738 lines) that shrinks BD50 Blu-ray backups or MKV files to BD25-compatible BDMV folders. The `-s` flag accepts BDMV folders or **.mkv files** (MKV forces movie-only mode). Built-in Python heredocs handle MPLS binary parsing, MKV demuxing, and data processing. Output is authored with `tsMuxeR`.

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
```

Note: `--iso` works with any mode (surgical and movie-only), not just `--movie-only`.

## TUI mode

When run without `-s`/`-o` in an interactive terminal with `gum` installed, the script launches an interactive TUI instead of erroring. Pass `--tui` to force TUI mode even when args are provided.

In TUI mode:
- **Source selection** is a single step: pick the movie folder (or `.mkv`/`.m2ts`/`.ts`/`.iso` file) that contains the source. If a `SOURCE_ROOT` is saved, the TUI shows its contents in a `gum filter` fuzzy finder first; otherwise it opens a `gum file` browser starting from `/data-nvme1` or `$HOME`. Pressing escape in the fuzzy finder switches to the file browser.
- **BDMV auto-detection**: after selecting a folder, the script looks for `index.bdmv`, `BDMV/index.bdmv`, or `*/BDMV/index.bdmv` inside it. If no BDMV is found, it looks for a video/ISO file directly inside the folder.
- **SOURCE_ROOT persistence**: the parent directory of the selected movie folder is saved to `~/.config/bd-shrink/source_root`. On the next run, the fuzzy finder starts from that directory. To reset, delete or edit `~/.config/bd-shrink/source_root`.
- **Options**: mode (Full disc / Movie-only) and output format (Folder / ISO) are presented as radio choices (`gum choose --limit=1`). Encoding preset (slow/medium/fast/slower/veryslow) follows as a radio choice. If the output directory already exists, an overwrite checkbox is shown. Canceling any selection preserves the previous value. Pre-selection matches current state.
- **Summary**: a colorized rounded box using the Catppuccin Mocha palette (blue accent `#89b4fa`, labels in muted gray, values in foreground white, booleans green/red) shows source, source size, output, options, and preset. The summary is followed by an action chooser (`Start` / `Edit source` / `Edit output` / `Edit options` / `Cancel`) so any step can be revisited before processing begins.

## Logging

All output is mirrored to a log file. Default log directory:
- `/var/log/bd-shrink` if writable without root
- Otherwise `~/.local/share/bd-shrink/logs`

Log files are named `bd_shrink_YYYYMMDD_HHMMSS.log`.

## Git workflow

```bash
cd ~/projects/bd-shrink && git add ... && git commit -m "..." && git push
# git -C fails across filesystem boundaries for push; cd or workdir works
```

## Architecture

Single file `bd_shrink.sh`. Shebang: `#!/usr/bin/env zsh`. No separate library files.

**Phases:**
1. **Inventory** — parse `.mpls` (Python heredoc, binary struct), probe `.m2ts` (single Python subprocess); for MKV, demux streams via ffprobe
2. **Classify** — largest long playlist(s) = main movie (size is the primary signal to avoid bogus playlists that repeat a short clip many times); rest = extras or menus; MKV is always a single movie
3. **Budget** — calculate remaining space and target bitrate for main movie
4. **Pre-compute** — single `systemd-run` wrapping a Python script that writes clip metadata (`.clip_precompute.txt`, `.budget_values.txt`, `.clip_fps.txt`, `.all_clips.txt`, `.main_playlist.txt`, `.main_fps.txt`, etc.)
5. **Encode** — single bare `python3 -u << PYEOF` heredoc handles ALL extras + main movie encoding via `subprocess.run()`. NOT wrapped in systemd-run.
6. **Rebuild** — surgical (keep original menus) or fresh `tsMuxeR` authoring (`--movie-only`). Uses shell `run_ff` function (systemd-run wrapper) for each `cp`/`tsMuxeR` call.
7. **Validate** — file count, CLPI verification (builtins only) then print `.work` retention message

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

**Shell function** (line 66): wraps each command in `systemd-run --user --wait`, making it a transient systemd service rather than a direct child of the shell. Used in Phase 6 (rebuild) for `cp`, `tsMuxeR`, etc. This avoids SIGCHLD because the exiting process is systemd's child, not the shell's.

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

## zsh-specific options

```zsh
setopt SH_WORD_SPLIT   # split unquoted $var on IFS (like bash)
setopt NULL_GLOB       # unmatched globs → empty (like bash nullglob)
```

## tsMuxeR binary

Binary is `tsMuxeR` (capital R), not `tsmuxer`. Installed at `/usr/local/bin/tsMuxeR` v2.7.0.

## Work directories

Default: `<output>.work` (sibling of output, NOT inside it — avoids inflating size check).
Configurable via `-w / --work`.

## Test data

Example discs used for regression testing:

| Disc | Path | Notes |
|------|------|-------|
| Dolemite | `/mnt/downloads/Dolemite.../BDMV` | IGS menus, 2 movies |
| Aesthetics of a Bullet | `/data-nvme1/aesthetics-src/BDMV` | IGS menus, corrupt H.264 |
| Internal Affairs | `...Incubo/BDMV` | IGS, 19 clips, 14 playlists |
| Gonza MKV | `/data-nvme1/Gonza.the.Spearman...mkv` | 2h6m, 1 AC3, 1 PGS sub |

## Known issues

### Corrupt H.264 source streams

Some discs have corrupt H.264 in clips. The script gracefully skips these — the BD output will lack video for the affected clips, but won't fail.

### Movie-only output size inflates

Movie-only mode allocates ALL space to video. Audio + subtitle + tsMuxeR container overhead can push the total slightly above target. The output is still valid (just ~1-2% over).

## Gotchas

- `git -C ~/projects/bd-shrink push` fails — use `cd` or `workdir` parameter
- `{1..0}` in zsh expands to `1 0` (descending range, not empty) — use C-style `for ((...))`
- Use `read < file` for line-oriented metadata reads; the script reads metadata files with `read`/`while read` loops, not `$(< file)`
- Work dirs from dry-runs accumulate — clean up `<output>.work` regularly
- **Log buffering**: `stdout` is line-buffered, but Python `sys.stderr` writes are unbuffered with `-u`. Progress may appear after Python exits rather than in real time.
- `EXTRAS_CLIPS` and `MAIN_CLIPS` have trailing newlines from the `while read` loop — trimmed with `${VAR%$'\n'}` before use.
- Playlist `clips` arrays are deduplicated during inventory assembly (preserving order). Duplicate playitems referencing the same clip are collapsed so each clip encodes once and appears once in the tsMuxeR meta file.
- **Main movie classification** prefers the largest long playlist by total size, not the longest by duration. Some discs have bogus playlists that repeat a short clip hundreds of times, producing a huge duration but tiny size; these are now classified as extras.
- There are **6 Python heredocs** (PYEOF blocks) in the script: MPLS parsing (line 372), clip probing (line 507), inventory assembly (line 541), classification (line 654), budget calculation (line 793), and encoding (line 1115). The pre-compute and MKV playlist blocks use `python3 -c` instead.
- `systemd-run` is **required** for both the shell `run_ff` function and pre-compute phase. It is listed in `check_deps` indirectly (via `run_ff` shell function) but not explicitly. If systemd user services aren't available, the script will fail at runtime.
- In zsh, `local` outside a function behaves like a regular assignment. The surgical rebuild block uses `local` at top-level (lines 1594, 1595, 1611) — this is safe but non-idiomatic.
- **Pass 2 encoding validation**: After pass 2, the script validates `.h264` output by checking for an Annex B start code (`\x00\x00\x00` or `\x00\x00\x01`) in the first bytes. Corrupt files (e.g. from VC-1 decode failures) are removed so they don't reach tsMuxeR. Previously, the retry loop accepted any non-empty file, which caused tsMuxeR `Unsupported codec` errors.

## Future goals

### Dependency auto-install

Add a `--install-deps` flag that detects the distro (dnf/apt/pacman/brew), prompts for sudo if needed, and installs missing tools (ffmpeg, tsMuxeR, bc, python3, gum). This would let new users get running with a single command.

### Burn-to-disk (`--burn`)

Add a flag to burn the output directly to BD-R after the rebuild phase. Uses `xorriso` (Linux) or `cdrecord`/`growisofs` to write the BDMV folder or ISO to `/dev/srX`. Requires a BD-R drive — needs testing before implementation.
