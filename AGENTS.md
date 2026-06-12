# AGENTS.md — bd_shrink.sh

## Project overview

Single-file **zsh** script (~1470 lines) that shrinks BD50 Blu-ray backups or MKV files to BD25-compatible BDMV folders. The `-s` flag accepts BDMV folders or **.mkv files** (MKV forces movie-only mode). Built-in Python heredocs handle MPLS binary parsing, MKV demuxing, and data processing. Output is authored with `tsMuxeR`.

## Key commands

```bash
# Syntax check
zsh -n bd_shrink.sh

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

## Git workflow

```bash
cd ~/projects/bd-shrink && git add ... && git commit -m "..." && git push
# git -C fails across filesystem boundaries for push; cd or workdir works
```

## Architecture

Single file `bd_shrink.sh`. Shebang: `#!/usr/bin/env zsh`. No separate library files.

**Phases:**
1. **Inventory** — parse `.mpls` (Python heredoc, binary struct), probe `.m2ts` (single Python subprocess); for MKV, demux streams via ffprobe
2. **Classify** — longest playlist(s) = main movie, rest = extras or menus; MKV is always a single movie
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

**Shell function** (line 35): wraps each command in `systemd-run --user --wait`, making it a transient systemd service rather than a direct child of the shell. Used in Phase 6 (rebuild) for `cp`, `tsMuxeR`, etc. This avoids SIGCHLD because the exiting process is systemd's child, not the shell's.

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
- There are **6 Python heredocs** (PYEOF blocks) in the script: MPLS parsing (line 156), clip probing (291), inventory assembly (325), classification (432), budget calculation (560), and encoding (882). The pre-compute and MKV playlist blocks use `python3 -c` instead.
- `systemd-run` is **required** for both the shell `run_ff` function and pre-compute phase. It is listed in `check_deps` indirectly (via `run_ff` shell function) but not explicitly. If systemd user services aren't available, the script will fail at runtime.
- In zsh, `local` outside a function behaves like a regular assignment. The surgical rebuild block uses `local` at top-level (lines 1359, 1360, 1376) — this is safe but non-idiomatic.
- **Pass 2 encoding validation**: After pass 2, the script validates `.h264` output by checking for an Annex B start code (`\x00\x00\x00` or `\x00\x00\x01`) in the first bytes. Corrupt files (e.g. from VC-1 decode failures) are removed so they don't reach tsMuxeR. Previously, the retry loop accepted any non-empty file, which caused tsMuxeR `Unsupported codec` errors.
