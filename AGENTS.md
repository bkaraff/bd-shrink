# AGENTS.md — bd_shrink.sh

## Project overview

Single-file bash script that shrinks BD50 Blu-ray backups to BD25. Built-in Python heredocs handle MPLS binary parsing and data processing. Output is authored with `tsMuxeR`.

## Key commands

```bash
# Syntax check (only "lint" available)
bash -n bd_shrink.sh

# Dry-run against reference test disc
./bd_shrink.sh -s /mnt/downloads/Dolemite.1975.1080p.USA.Blu-Ray.AVC.DTS-HD.1.0.B1rDCAg3/BDMV \
  -o /tmp/bd-test -n -f --movie-only

# Dry-run for full menu preservation (surgical mode)
./bd_shrink.sh -s /path/to/BDMV -o /tmp/bd-test -n -f

# Check budget with --keep-one (single movie version)
./bd_shrink.sh -s /path/to/BDMV -o /tmp/bd-test -n -f --movie-only --keep-one
```

## Git workflow

```bash
# Push — git -C fails across filesystem boundary here, use workdir
cd ~/projects/bd-shrink && git add ... && git commit -m "..." && git push

# Or use workdir parameter
git push   # when workdir is set to ~/projects/bd-shrink
```

## Architecture

Single file `bd_shrink.sh` (~1200 lines). No separate library files.

**Phases:**
1. Inventory — parse `.mpls` (Python heredoc, binary struct), probe `.m2ts` (ffprobe)
2. Classify — longest playlist(s) = main movie, rest = extras or menus
3. Budget — calculate remaining space and target bitrate for main movie
4. Encode — extras at 720p CRF 22, main movie two-pass VBR
5. Rebuild — surgical (keep original menus) or fresh `tsMuxeR` authoring (`--movie-only`)
6. Validate — size check, playlist→CLIP→M2TS chain verification

**Two output modes:**
- Default (surgical): keeps `index.bdmv`, `MovieObject.bdmv`, all `.mpls`, re-encodes M2TS in-place. IGS menus only. Fragile.
- `--movie-only`: fresh `tsMuxeR` authoring. No menus. Works on any disc including BD-J.

## Embedded Python conventions

Python lives in bash heredocs. Two patterns:

```bash
# UNQUOTED heredoc (<< PYEOF): bash variables are expanded — use for file paths, numbers
python3 << PYEOF > "$OUTPUT_FILE"
import json
data = json.load(open('$INVENTORY_FILE'))   # $INVENTORY_FILE expanded by bash
rate = $MAIN_AUDIO_BITRATE                   # numeric value expanded
PYEOF

# QUOTED heredoc (<< 'PYEOF'): no expansion — use python3 - "$arg" for args
python3 - "$1" << 'PYEOF'
import sys
playlist_dir = sys.argv[1]   # passed via python3 - "$1"
PYEOF
```

**Critical:** Bash booleans (`true`/`false`) are not Python-compatible. Convert first:
```bash
if $KEEP_ONE; then PY_KEEP_ONE="True"; else PY_KEEP_ONE="False"; fi
```

## tsMuxeR binary name

The binary is `tsMuxeR` (capital R), not `tsmuxer`. Installed at `/usr/local/bin/tsMuxeR` v2.7.0.

## Test data

Reference disc for testing: `/mnt/downloads/Dolemite.1975.1080p.USA.Blu-Ray.AVC.DTS-HD.1.0.B1rDCAg3/`
- IGS menus (no BD-J)
- Two 90-min movie versions (theatrical + alternate)
- ~24 min of extras, SD documentary, menu animations
- Source: 46.39 GB → target: 23 GB (BD25)
- Expected budget: ~17 Mbps for both movies, ~30 Mbps with `--keep-one`

## Gotchas

- `git -C ~/projects/bd-shrink push` fails with "not a git repository" — use `workdir` parameter or `cd` instead
- `git -C` works fine for `status`, `diff`, `config`, `add`, `commit` — only `push` fails
- `REMUX_CLIPS` uses `declare -A` (associative array) but is never read from — only set
- `seq 0 -1` is a bash error — always guard `seq` with `if [[ $count -gt 0 ]]`
- Audio extraction from the full 89-min Dolemite clip times out — use `-t 30` for quick tests
- The `$HAS_BDJ` variable is set early but only used in Phase 6 validation
- Work dirs from dry-runs accumulate in `/tmp/bd-shrink-*` — clean up regularly
- `PY_KEEP_ONE`, `PY_MOVIE_ONLY`, etc. must be converted *before* the Phase 2/3 Python heredocs that read them
