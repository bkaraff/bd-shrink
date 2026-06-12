# AGENTS.md — bd_shrink.sh

## Project overview

Single-file **zsh** script (~1370 lines) that shrinks BD50 Blu-ray backups or MKV files to BD25-compatible BDMV folders. The `-s` flag accepts BDMV folders or **.mkv files** (MKV forces movie-only mode). Built-in Python heredocs handle MPLS binary parsing, MKV demuxing, and data processing. Output is authored with `tsMuxeR`.

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

# Dry-run
./bd_shrink.sh -s /path/to/BDMV -o /tmp/test -n -f
```

Note: `--iso` works with any mode (surgical and movie-only), not just `--movie-only`.

## Git workflow

```bash
cd ~/projects/bd-shrink && git add ... && git commit -m "..." && git push
# git -C fails across filesystem boundaries for push; cd or workdir works
```

## Architecture

Single file `bd_shrink.sh` (~1370 lines). Shebang: `#!/usr/bin/env zsh`. No separate library files.

**Phases:**
1. **Inventory** — parse `.mpls` (Python heredoc, binary struct), probe `.m2ts` (single Python subprocess); for MKV, demux streams via ffprobe
2. **Classify** — longest playlist(s) = main movie, rest = extras or menus; MKV is always a single movie
3. **Budget** — calculate remaining space and target bitrate for main movie
4. **Encode** — **single Python heredoc** handles ALL extras + main movie encoding via `subprocess.run()`. See below.
5. **Rebuild** — surgical (keep original menus) or fresh `tsMuxeR` authoring (`--movie-only`)
6. **Validate** — file count, CLPI verification (builtins only) then print `.work` retention message

**Two output modes:**
- Default (surgical): keeps `index.bdmv`, `MovieObject.bdmv`, all `.mpls`, copies/remuxes M2TS. IGS menus only.
- `--movie-only`: fresh `tsMuxeR` authoring. No menus. Works on any disc including BD-J.

### Subtitle handling

- **SRT** subtitles are extracted from source but **skipped** in the tsMuxeR meta file. tsMuxeR 2.7.0 on Linux lacks font rendering, producing `Can't load symbol code '65' from font` errors.
- **PGS** subtitles (`.sup` files) pass through correctly as `S_HDMV/PGS`.
- **DVD/VobSub** subtitles (`dvd_subtitle` codec) are filtered out entirely — not BD-compatible.

## Phase 4: single Python heredoc

All encoding (extras audio/sub/video + main movie audio/sub/pass1/pass2) runs in ONE `python3 -u << PYEOF` call. This avoids the kernel SIGCHLD race because the shell has only one child (python3) during encoding.

The Python code:
- Reads pre-computed clip metadata from `$WORK_DIR/.clip_precompute.txt`
- Uses `subprocess.run()` for all ffmpeg calls
- **Resumable**: skips files that already exist (`run_ff()` checks `out_file` before encoding)
- Writes progress to `sys.stderr` (goes to run.log)
- Writes `.main_fps.txt` during pre-compute (bug fix: was read but never written)

## Phase 5: zero child processes

Both movie-only and surgical rebuild modes use only shell builtins:
- All data pre-computed in Phase 4's single `systemd-run` call (`.clip_fps.txt`, `.all_clips.txt`, `.main_playlist.txt`, `.main_fps.txt`, etc.)
- File operations: zsh parameter expansion (`${fname##*/}`, `${fname%_video.h264}`) instead of `basename`/`sed`
- File lists: zsh glob qualifiers `(N)` instead of `ls | head`
- Loop counters: `for ((i = 0; i < n; i++))` instead of `{0..$((n-1))}` (zsh `{1..0}` expands to `1 0`)
- Array lookups: `CLIP_FPS[$cid]` instead of per-clip `$(python3)` calls

## Phase 6: builtins only

Validates output using only shell builtins:
- File counting: `for m2ts in ...; do ((++count)); done`
- CLPI check: parameter expansion + `[[ -f ]]`

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

| Disc | Path | Size | Notes |
|------|------|------|-------|
| Dolemite | `/mnt/downloads/Dolemite.../BDMV` | 46.39 GB | IGS menus, 2 movies, test disc |
| Aesthetics of a Bullet | `/data-nvme1/aesthetics-src/BDMV` | 36.91 GB | IGS menus, 1 movie, many corrupt H.264 |
| Internal Affairs | `...Incubo/BDMV` | 46.26 GB | IGS, 19 clips, 14 playlists, tested surgical+iso |
| Gonza MKV | `/data-nvme1/Gonza.the.Spearman...mkv` | 34 GB | 2h6m, 1 AC3, 1 PGS sub, SRT skipped |

## Known issues

### Kernel SIGCHLD race (Fedora 44, kernel 7.0.11)

Bash 5.3.9 AND zsh 5.9 crash non-deterministically when ANY child process (including `cp`, `echo`, `python3`) exits.

**Mitigation strategy:**
1. Phase 1: single Python subprocess for all ffprobe calls
2. Phase 4: single Python heredoc for ALL ffmpeg calls (resumable)
3. Phase 5: zero child processes (pre-computed data, builtins only)
4. Phase 6: builtins only (parameter expansion, loops)

When the shell crashes mid-encode, restarting the script resumes from where it left off (Python `run_ff` skips existing output files).

### SRT subtitles require font rendering

SRT subtitles are skipped by default because tsMuxeR 2.7.0 cannot render fonts on Linux. Only PGS (`.sup`) subtitles are included in the output.

### Corrupt H.264 source streams

Some discs have corrupt H.264 in clips. The script gracefully skips these — the BD output will lack video for the affected clips, but won't fail.

### Movie-only output size inflates

Movie-only mode allocates ALL space to video. Audio + subtitle + tsMuxeR container overhead can push the total slightly above target. The output is still valid (just ~1-2% over).

## Gotchas

- `git -C ~/projects/bd-shrink push` fails — use `cd` or `workdir` parameter
- `{1..0}` in zsh expands to `1 0` (descending range, not empty) — use C-style `for ((...))`
- `$(< file)` in zsh is a subshell (not a builtin like in bash) — use `read < file`
- Work dirs from dry-runs accumulate — clean up `<output>.work` regularly
- **The log buffering issue**: `stdout` is line-buffered, but Python `sys.stderr` writes are unbuffered with `-u`. Progress may appear after Python exits rather than in real time.
- `EXTRAS_CLIPS` and `MAIN_CLIPS` have trailing newlines from the `while read` loop — trimmed with `${VAR%$'\n'}` before use.
