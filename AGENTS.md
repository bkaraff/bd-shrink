# AGENTS.md — bd_shrink (v0.3.0 Python Package)

## Project overview

**bd_shrink** is a self-contained Python package that shrinks BD50 Blu-ray backups or video files to BD25-compatible BDMV folders. The package is invoked via `python -m bd_shrink` (or the thin bash shim `./bd_shrink.sh`), with an optional interactive TUI for source/output selection, encoding profiles, and burn options.

- **Source**: BDMV folder, parent folder containing `BDMV/`, ISO image file (`.iso`), or a single video file (`.mkv`/`.mp4`/`.m4v`)
- **`--movie-only`**: reliable mode; fresh BD, no menus. Works on any disc including BD-J.
- **Default (surgical)**: preserves original menus and structure (IGS/HDMV and BD-J), re-encodes only video clips.
- **`--codec hevc`**: uses `libx265` when `tsMuxeR` supports `V_MPEGH/ISO/HEVC`; default is H.264.

## Key commands

```bash
# Interactive TUI (auto-launched when -s/-o omitted; requires questionary)
python -m bd_shrink
./bd_shrink.sh

# Movie-only — fresh BD, no menus, works on any disc including BD-J
python -m bd_shrink -s /path/to/BDMV -o /output -f --movie-only
./bd_shrink.sh -s /path/to/BDMV -o /output -f --movie-only

# Surgical — keep menus; preserves IGS and BD-J structure
python -m bd_shrink -s /path/to/BDMV -o /output -f
./bd_shrink.sh -s /path/to/BDMV -o /output -f

# Single video file (forces movie-only)
python -m bd_shrink -s movie.mkv -o /output -f
./bd_shrink.sh -s movie.mkv -o /output -f

# ISO image input (auto-mounts or extracts)
python -m bd_shrink -s disc.iso -o /output -f --movie-only
./bd_shrink.sh -s disc.iso -o /output -f --movie-only

# Burn directly to BD-R (pipes genisoimage into growisofs; no temp ISO)
python -m bd_shrink -s /path/to/BDMV -o /output -f --movie-only --burn
./bd_shrink.sh -s /path/to/BDMV -o /output -f --movie-only --burn

# Create ISO for archival + burn
python -m bd_shrink -s /path/to/BDMV -o /output -f --movie-only --burn --iso
./bd_shrink.sh -s /path/to/BDMV -o /output -f --movie-only --burn --iso

# Dependency check (no source/output needed)
python -m bd_shrink --install-deps
./bd_shrink.sh --install-deps
```

## Architecture

**bd_shrink** is a Python package (`bd_shrink/`) with 13 modules orchestrating a 7-phase pipeline. The bash shim (`bd_shrink.sh`) is a 3-line forward to `exec python3 -m bd_shrink "$@"`.

### Package structure

```
bd_shrink/
├── __init__.py __main__.py       # entrypoint: python -m bd_shrink
├── cli.py                        # argparse + validation (40+ flags)
├── config.py                     # Config dataclass; audio-bitrate flags removed
├── logging_setup.py              # logging to /var/log/bd-shrink or ~/.local/share/bd-shrink/logs
├── runner.py                     # systemd-run --user --wait via subprocess
├── deps.py                       # --install-deps dispatch
├── audio.py                      # codec→ext map, bitrate fallback table, helpers
├── mpls.py                       # MPLS binary parser + timestamp conversion
├── inventory.py                  # ffprobe orchestration, Clip/Inventory dataclasses
├── classify.py                   # playlist heuristics (menu detection, clip dedup)
├── budget.py                     # audio/subtitle size estimation, bitrate math
├── encode.py                     # 400+ line encode loop (audio extraction, video encoding, resumability)
├── rebuild.py                    # tsMuxeR metafile generation (movie-only + surgical modes)
├── validate.py                   # BDMV structure + M2TS/CLPI file validation
├── iso.py                        # ISO creation (genisoimage UDF), BD-R burning (growisofs)
└── tui.py                        # questionary + rich interactive TUI
```

### Pipeline phases

All phases pass in-memory objects (Clip, Inventory, ClassifyResult, BudgetResult dataclasses); JSON checkpoints are kept in `.work/` for resume/debug, replacing v0.2.x `.*.txt` dotfiles.

1. **Inventory** (mpls.py + inventory.py)
   - Parse `.mpls` binary files → extract playlists, clips, audio/subtitle tracks
   - Probe `.m2ts` clips via ffprobe → video codec, bitrate, duration, frame rate
   - Output: `Inventory` dataclass with dicts of `Clip` and `PlaylistMetadata`

2. **Classify** (classify.py)
   - Identify main movie playlist(s): largest HD resolution, longest duration
   - Mark menus: `PlayList_type == 1`, playlists sharing clips with menus, short clips (<120s, no chapters)
   - Extras: everything else re-encoded to 720p
   - **B9 fix**: dedup shared clips across playlists; main duration = sum of unique encoded clips
   - Output: `ClassifyResult` dataclass with main/menu/extra clip sets

3. **Budget** (budget.py)
   - Estimate audio + subtitle + container sizes from source data or fallback table
   - Calculate target bitrate for main movie: `(target_gb - overhead) / main_duration_sec`
   - Output: `BudgetResult` dataclass with per-clip bitrates

4. **Encode** (encode.py)
   - **Audio extraction** (MPEG skipped, all others `-c:a copy`): `.ac3/.eac3/.dts/.thd/.wav`
   - **Subtitle extraction** (DVD/VobSub skipped, PGS+SRT pass through)
   - **Video encoding**:
     - Extras: 720p CRF (constant quality)
     - Main: single-pass or two-pass VBR depending on `--main-passes` flag (default 2)
   - **Resumability**: skip clips with existing outputs; retry up to 3 times on failure
   - **Pass 2 validation**: check raw output (`.h264`/`.hevc`) for NAL start code; remove corrupt files
   - All external-tool subprocesses run via `runner.run_managed()`, which wraps
     them in `systemd-run --user --wait` when available (crash-surviving
     transient service + `--nice`) and falls back to a direct subprocess in
     containers/CI where systemd-run is unusable

5. **Rebuild** (rebuild.py)
   - **Movie-only mode**: generate fresh `tsMuxeR` metafile for one main-movie playlist
   - **Surgical mode**: preserve menus and structure; remux re-encoded clips in place, copy menu clips verbatim
   - **Orphan-clip safety**: after playlist-referenced copies, scan `SOURCE/STREAM/*.m2ts` and copy any unreferenced files
   - Output: BDMV folder with updated playlists + clips

6. **Validate** (validate.py)
   - Check BDMV structure: required dirs (`AUXDATA`, `BACKUP`, `CLIPINF`, `JAR`, `PLAYLIST`, `STREAM`, `AUXDATA`, `CERTIFICATE`)
   - Validate M2TS/CLPI magic bytes (`0x47` for M2TS, `0x00` for CLPI)
   - Check output size against target GB; warn if over
   - Output: `ValidationResult` with pass/fail + warnings

7. **ISO/Burn** (iso.py)
   - **ISO creation** via `genisoimage -udf -allow-limited-size` (UDF 2.00, supports M2TS > 4 GiB)
   - **BD-R burning** via `growisofs` with `MKISOFS` env var set to `genisoimage` path (xorriso stub doesn't support UDF)
   - **Direct-pipe burn**: `genisoimage | growisofs` (no temp ISO; `--burn` alone)
   - **ISO + burn**: `genisoimage → temp ISO → growisofs` (`--burn --iso`)
   - Temp ISO mount points created in `/tmp`, cleaned up on exit
   - Output: `ISOResult` with ISO path (if created) and burn status

### Dataclasses

- **Clip**: `clip_id, duration_sec, video: VideoTrack, audio: list[AudioTrack], subtitles: list[SubtitleTrack]`
- **Inventory**: `clips: dict[str, Clip], playlists: dict[str, PlaylistMetadata]`
- **ClassifyResult**: `main_clips: set[str], menu_clips: set[str], extra_clips: set[str], main_playlists: list[str]`
- **BudgetResult**: `main_bitrate_kbps: int, target_size_mb: int, ...`
- **RebuildStats**: `clips_encoded: int, clips_copied: int, menus_preserved: bool`
- **ValidationResult**: `valid: bool, warnings: list[str], errors: list[str]`
- **ISOResult**: `iso_path: str, burned: bool, burn_device: str`

### In-memory pipeline benefits

- **No dotfile glue**: all data flows as objects; JSON checkpoints kept only for resume/debug
- **Type safety**: dataclasses catch errors early (vs. string parsing)
- **Testability**: pure functions ideal for pytest fixtures; no subprocess coordination needed
- **Refactoring**: changes to one phase don't scatter across multiple heredocs

### Resumability

After crash or interruption, re-run the same command (without `-f`) to resume from where it left off:

- **Inventory/Classify/Budget**: regenerated from scratch (fast, <1s)
- **Encode**: skips clips whose output files already exist in `ENCODE_DIR` (or `.work/encode`)
- **Rebuild**: regenerated from encoded clips
- **Validate/ISO/Burn**: regenerated

Do **not** delete the `.work` directory or use `-f` when resuming.

## Modes

| Mode | Result | Caveats |
|------|--------|---------|
| `--movie-only` | Fresh BD with one main-movie playlist, no menus | Always works; no interactive features |
| Default (surgical) | Original menus/structure preserved | Works for IGS and BD-J; re-encoded clips get new CLPI metadata |

## Flag interactions

- `--movie-only` implies `--keep-one` (only output main playlist)
- `--no-extras` skips extras encoding but keeps menus (surgical only)
- `-f` / `--force` checks **before** dry-run exit — needed even with `-n` if output exists
- `--iso` creates an ISO; `--burn --iso` also creates one and then burns from it
- `--nice` with no argument defaults to `N=19`; valid range 0–19. Applied via `systemd-run --property=Nice` for subprocesses.
- `--main-passes` controls single- vs two-pass encoding:
  - `1` = faster encode (matches BD-Rebuilder's "Very Fast" mode)
  - `2` = default quality (two-pass VBR)
- `--codec h264|hevc`: H.264 (default) or HEVC; tsMuxeR must support codec for output
- `--target N`: target size in GB (≥1 GB floor); e.g., `--target 25` for BD25
- `--main-preset preset`: x264/x265 preset for main movie (default: `medium`)

## Audio passthrough

All non-MPEG audio is stream-copied (`-c:a copy`) to preserve original codecs and avoid transcoding quality loss:

- **Supported codecs**: DTS, TrueHD, E-AC-3, LPCM, PCM Blu-ray, AC-3
- **Skipped**: MPEG audio (mp2, mp3) due to BDMV spec constraints
- **Extension mapping** (for extraction):
  - `.ac3` → AC-3
  - `.eac3` → E-AC-3
  - `.dts` → DTS
  - `.thd` → TrueHD
  - `.wav` → LPCM / PCM Blu-ray
- **Bitrate estimation** (budget phase):
  - Uses source `bit_rate` from ffprobe
  - Fallback table: `dts=1509k, truehd=2000k, eac3=1536k, pcm_bluray=4608k`
  - MPEG audio skipped entirely (no budget allocation)

**Dead flags removed** (no effect since v0.2.x b070cf7): `--main-audio`, `--commentary-ab`, `--extras-ab` are gone from CLI.

## Menu preservation (surgical mode)

Menu clips are excluded from re-encoding via three signals:

1. `PlayList_type == 1` read from MPLS `AppInfoPlayList` struct
2. Any playlist sharing a clip with a known menu playlist
3. Zero chapter marks + duration < 120 s (warnings, logos, transitions)

Their clips are copied verbatim so IGS (Interactive Graphics) overlays stay intact. Anything else classified as an extra is re-encoded to 720p.

### SubPath clips

The MPLS parser extracts `SubPlayItem` clips from SubPath entries (not just main PlayList). SubPath length is a 4-byte uint per BDMV spec. SubPath clips are added to the playitems list with duration 0 so they flow through inventory → classification → rebuild without affecting playlist duration. This catches clips referenced only via sub-paths (e.g., menu background video, PiP, seamless branching).

### Orphan-clip safety net

After copying all playlist-referenced clips, the surgical rebuild does a final pass over `SOURCE/STREAM/*.m2ts` and copies any file not already in the output. This catches clips that exist on the disc but are referenced by navigation structures outside MPLS (e.g., `MovieObject.bdmv` FirstPlayback/TopMenu entries).

### B9 fix (unique clip deduplication)

Seamless-branching titles (alternate cuts/angles) often have multiple playlists referencing the same clips. Previously, budget duration summed per-playlist durations, inflating the total (e.g., 3h30m instead of 1h51m) and producing too-low bitrate + too-small ISO. Now:

- Inventory deduplicates clips while preserving order
- Classify identifies `main_clips` as the **unique set** of clips to encode
- Budget sums `duration_sec` over unique `main_clips` only
- Encode processes each clip once
- Result: correct bitrate and correct ISO size

## Burn path

`--burn` alone pipes `genisoimage -udf -allow-limited-size` directly to `growisofs` — no temp ISO. `--burn --iso` creates an ISO first, then burns it.

Critical details:

- `genisoimage` and `growisofs` are required for direct burning
- `iso.py` resolves full binary paths via `runner.find_tool()` (`shutil.which`) before calling `run_managed()`, because `systemd-run --user --wait` uses a restricted `PATH` (`/usr/local/bin:/usr/bin:/bin`) that may not include Homebrew or other non-standard bin directories
- `MKISOFS` env var is set to the full path of `genisoimage` when calling `growisofs`, because `/usr/bin/mkisofs` on many systems is `xorriso`'s stub and does **not** support `-udf`
- `-allow-limited-size` is required; M2TS files commonly exceed 4 GiB
- UDF 2.00 is what `genisoimage` produces; players that strictly require UDF 2.50 may still reject the disc
- No MD5 verification; growisofs handles BD-R write verification internally

## Dependencies

| Tool | Purpose | Note |
|------|---------|------|
| `python3` (3.10+) | Runtime | Package requires Python 3.10+ for match/case syntax |
| `ffmpeg` + `ffprobe` | Encoding/probing | rpmfusion-free on Fedora |
| `tsMuxeR` | BD authoring | v2.7.0 from GitHub binary |
| `bc` | Math | Fallback for bitrate calculations (Python handles most) |
| `systemd-run` | Process isolation | Transient services for all subprocesses; escalates to sudo if needed |
| `genisoimage` | UDF ISO creation | Required for burn; `mount` not required (fallback: `bsdtar`/`7z`) |
| `growisofs` | BD-R burning | From `dvd+rw-tools` |
| `questionary` | Interactive TUI | Python package; installed via pip; optional if `-s`/`-o` provided |
| `rich` | Terminal rendering | Python package; installed via pip; used by questionary + logging |

Dev dependencies (testing/linting):

| Tool | Purpose |
|------|---------|
| `pytest` | Unit tests |
| `ruff` | Linting + formatting |

Run `python -m bd_shrink --install-deps` to see install commands for system dependencies.

## Work directories

Default: `${OUTPUT}.work` (sibling of output). Configurable via `-w / --work`. When `-o` points to an existing parent directory without `BDMV/`, a source-named subdirectory is created; the `.work` directory stays a sibling of that subdirectory.

### `.work` layout

- `inventory.json` — Inventory checkpoint (Clip + PlaylistMetadata dicts)
- `classify.json` — ClassifyResult checkpoint (main/menu/extra clip sets)
- `budget.json` — BudgetResult checkpoint (bitrates + sizes)
- `encode/` — Output video/audio/subtitle files during encoding phase
- `bdmv_output/` — Temporary BDMV folder during rebuild phase
- `bd_shrink.log` — Local copy of run log (main log mirrors to `/var/log/bd-shrink` or `~/.local/share/bd-shrink/logs`)

## Logging

Main log file mirrors to:
- `/var/log/bd-shrink/bd_shrink_YYYYMMDD_HHMMSS.log` (if writable)
- `~/.local/share/bd-shrink/logs/bd_shrink_YYYYMMDD_HHMMSS.log` (fallback)
- `${WORK_DIR}/bd_shrink.log` (always; for convenience during resume)

## TUI mode

Auto-launched when `-s`/`-o` are omitted (requires `questionary`). The TUI runs in a loop so any step can be revisited from the summary.

**Color theme**: Catppuccin Mocha (BLUE=#89b4fa, GREEN=#a6e3a1, RED=#f38ba8, TEXT=#cdd6f4).

**Flow**:

1. **Source** — fuzzy-filter contents of saved `SOURCE_ROOT`, or browse filesystem. Auto-detects:
   - BDMV: `index.bdmv`, `BDMV/index.bdmv`, `*/BDMV/index.bdmv`
   - Video files: `.mkv`, `.mp4`, `.m4v`, `.m2ts`, `.ts`
   - ISO files: `.iso`

2. **Output** — text input with sensible default based on source name.

3. **Mode** — radio choice:
   - *Full disc (keep menus, extras)* → surgical mode
   - *Movie-only (no menus, fresh BD)* → fresh output

4. **Output format** — radio choice:
   - *Folder (BDMV)* → directory output
   - *ISO (.iso file)* → ISO file (can be burned later)

5. **Codec** — radio choice:
   - *H.264* (default)
   - *HEVC* (if tsMuxeR supports it)

6. **Encoding speed** — radio choice combining preset + pass count:
   - *Quality (slow, 2-pass)* (default)
   - *Fast (medium, 1-pass)*
   - *Quick (fast, 1-pass)*
   - *Max Quality (slower, 2-pass)*
   - *Extreme (veryslow, 2-pass)*

7. **Burn options** (if applicable) — checkboxes:
   - *Burn to BD-R*
   - *Also create ISO*

8. **Overwrite** — checkbox shown only if output dir already exists.

9. **Summary** — colorized box with all choices, then action chooser:
   - Start / Edit source / Edit output / Edit mode / Edit codec / Edit speed / Edit burn / Cancel

`SOURCE_ROOT` is persisted to `~/.config/bd-shrink/source_root`. Canceling any radio selection preserves the previous value. Pre-selection matches current state.

## Known issues

- **BD-J discs with Java code** that depends on specific CLPI metadata (timestamps, PIDs) of re-encoded clips may malfunction. Most BD-J menus only play playlists, so surgical mode works for the majority of discs. If a BD-J disc fails, `--movie-only` is the fallback.
- **Corrupt H.264 in source clips**: some discs have malformed video. The script skips these gracefully; the output will lack video for affected clips but won't fail.
- **SRT subtitles skipped in tsMuxeR meta**: tsMuxeR 2.7.0 on Linux lacks font rendering. PGS subtitles pass through; DVD/VobSub subtitles are filtered out.
- **Movie-only mode size estimate**: audio + subtitle + container overhead can push total ~1–2% over target.

## Test coverage

226 pytest tests across all modules:

- `test_scaffold.py`: package structure + entrypoints
- `test_cli.py`: argument parsing + validation (target ≥1 GB, codec, nice range, decimal-only playlist regex)
- `test_audio.py`: codec→ext mapping, bitrate fallback table, MPEG skip logic
- `test_mpls.py`: MPLS binary parser over synthetic `.mpls` files
- `test_inventory.py`: ffprobe orchestration, Clip/Inventory dataclasses
- `test_classify.py`: playlist heuristics, single-main / alternate-cut splits, clip deduplication (B9)
- `test_budget.py`: bitrate math, audio/subtitle size estimation, unique-clip dedup
- `test_runner.py`: systemd-run subprocess wrapper
- `test_encode.py`: audio/subtitle extraction, video encoding, resumability, retry logic
- `test_rebuild_validate_iso.py`: tsMuxeR metafile generation, BDMV validation, ISO creation, burn options
- `test_tui.py`: TUI components, interactive flow, questionary mocks

**No Blu-ray disc or ffmpeg needed** for unit tests; all logic is pure functions with fixtures.

## CI/CD (`.github/workflows/ci.yml`)

Matrix: Python 3.10, 3.11, 3.12 on Linux

Steps:
1. `ruff check` + `ruff format --check` (lint + format check)
2. `py_compile` (syntax validation)
3. `pytest` (unit tests)
4. `shellcheck bd_shrink.sh` (shim syntax check)

Green at all steps before merge to `main`.

## Development notes

### Branching

- **`main`**: stable; v0.3.0+ (Python package) once merged
- **`dev-next`**: active development; phases 1–6 complete; Phase 7 (docs) in progress

### Adding a new feature

1. Add dataclass fields to `config.py` (CLI options) and relevant result dataclasses (pipeline output)
2. Add argparse flags to `cli.py` with validation
3. Implement logic in the relevant module (`classify.py`, `encode.py`, etc.)
4. Add fixtures + tests in `tests/test_*.py`
5. Update `.work` checkpoint JSON if needed (document in module docstring)
6. Run `pytest` + `ruff check` before commit

### Porting from v0.2.x

The old bash script (`bd_shrink.sh` in `main`) is archived for reference. Key design changes:

- **No heredocs**: Python code is now in proper modules, not embedded shell strings
- **No dotfiles**: data flows as objects; JSON checkpoints are optional (for resume/debug)
- **Type safety**: dataclasses replace string parsing
- **Test coverage**: 226 tests vs. zero in v0.2.x
- **Audio flags gone**: `--main-audio`, `--commentary-ab`, `--extras-ab` removed (dead since b070cf7)

### Resuming interrupted runs

```bash
# After crash, re-run WITHOUT -f to resume from checkpoint
python -m bd_shrink -s /path/to/BDMV -o /output --movie-only

# To force restart from scratch, add -f
python -m bd_shrink -s /path/to/BDMV -o /output -f --movie-only
```

## Gotchas

- **Python 3.10+ required**: `match`/`case` syntax used in several modules
- **questionary optional**: if you provide `-s`/`-o` on CLI, TUI is skipped (no questionary needed)
- **systemd-run escalation**: encode/rebuild/iso/burn may prompt for sudo if user lacks permissions
- **M2TS files > 4 GiB**: require `-allow-limited-size` flag in `genisoimage` (handled automatically)
- **UDF 2.00 only**: some older BD players may not support; fallback is no UDF (ISO 9660 only, not recommended for BD)
- **No cross-platform support**: Linux only (bash shim, systemd-run, genisoimage)

## Test Encode & Release Checkpoint (v0.3.0)

All 10 code review bugs fixed; ready for real-world validation. After test encodes complete, three decision points:

### 1. Pre-release polish (choose one or both)
- **Option A**: Fix `mpls.py` F841 linter warnings (pre-existing, out of scope) so CI passes cleanly on `ruff check`
- **Option B**: Polish error messages and user-facing docs for better UX during failures
- **Option C**: Both A and B
- **Option D**: Neither; acceptable to merge with pre-existing F841 warnings (low priority)

### 2. Release timing
- **Option A**: Merge `dev-next` → `main` and tag v0.3.0 immediately after successful test encodes
- **Option B**: Wait for additional review/testing cycle before release
- **Option C**: Release as v0.3.0-rc1 (release candidate) for broader beta testing first

### 3. Post-release roadmap (v0.4.0+)
- **High priority**: Parallel encoding (multi-clip batches via `multiprocessing`)
- **Medium priority**: Better error recovery (ffmpeg retry logic, fallback codecs)
- **Medium priority**: Windows/macOS support (rewrite `systemd-run` wrapper)
- **Nice-to-have**: Web UI (FastAPI + React), streaming output to TUI, performance profiling

## Future improvements (v0.4.0+)

- **Windows/macOS support**: rewrite `systemd-run` wrapper to use subprocess directly or platform-specific process isolation
- **Parallel encoding**: current encode loop is serial; could use `multiprocessing` for multi-clip batches
- **Better error recovery**: retry logic for ffmpeg failures with fallback codecs
- **Web UI**: FastAPI + React frontend (optional)
- **Streaming output**: live progress updates to TUI instead of polling `.work` files
