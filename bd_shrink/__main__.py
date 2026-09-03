"""Entrypoint for bd_shrink: orchestrates the 7-phase pipeline.

Run via `python -m bd_shrink`. Parses args (optionally via TUI), resolves the
source type, then drives inventory -> classify -> budget -> encode -> rebuild
-> validate -> iso/burn, writing JSON checkpoints to the work directory.
"""

import json
import logging
import os
import shutil
import sys
from dataclasses import asdict
from typing import Optional

from bd_shrink import budget, classify, encode, inventory, iso, rebuild, validate
from bd_shrink.cli import args_to_config, parse_args
from bd_shrink.config import Config
from bd_shrink.deps import (
    PYTHON_DEPENDENCIES,
    format_install_deps_output,
    validate_dependencies,
)
from bd_shrink.logging_setup import setup_logging

VIDEO_EXTS = (".mkv", ".mp4", ".m4v", ".m2ts", ".ts")
ISO_EXTS = (".iso",)


class PipelineError(Exception):
    """Raised when a pipeline phase fails."""


class SourceError(Exception):
    """Raised when the source path cannot be resolved to a BDMV folder."""


# ---------------------------------------------------------------------------
# Source / output resolution
# ---------------------------------------------------------------------------


def resolve_source(source: str) -> tuple[str, str]:
    """Resolve a source path to (bdmv_root, source_parent).

    bdv_root is the BDMV/ folder itself (contains STREAM/ and PLAYLIST/).
    source_parent is the directory containing BDMV/ (used for output naming).

    ISO and single video-file inputs are not yet wired and raise SourceError.

    Raises:
        SourceError: if source missing, unsupported, or not a BDMV folder.
    """
    src = os.path.abspath(source)
    if not os.path.exists(src):
        raise SourceError(f"source not found: {source}")

    if os.path.isfile(src):
        ext = os.path.splitext(src)[1].lower()
        if ext in VIDEO_EXTS:
            raise SourceError(
                "Single video-file input is not yet supported in v0.3.0. "
                "Provide a BDMV folder instead (or wait for ISO extraction)."
            )
        if ext in ISO_EXTS:
            raise SourceError(
                "ISO image input is not yet supported in v0.3.0. "
                "Mount/extract the ISO and pass the resulting BDMV folder."
            )
        raise SourceError(f"unsupported source file type: {source}")

    # Directory: locate BDMV/.
    if os.path.isdir(os.path.join(src, "BDMV")):
        bdmv_root = os.path.join(src, "BDMV")
        source_parent = src
    elif os.path.isdir(os.path.join(src, "STREAM")) or os.path.isfile(
        os.path.join(src, "index.bdmv")
    ):
        # source is the BDMV/ folder itself.
        bdmv_root = src
        source_parent = os.path.dirname(src) or src
    else:
        raise SourceError(
            "source is not a BDMV folder (expected a 'BDMV/' subdir with "
            f"STREAM/ and PLAYLIST/): {source}"
        )

    if not os.path.isdir(os.path.join(bdmv_root, "STREAM")):
        raise SourceError(f"BDMV folder has no STREAM/ directory: {bdmv_root}")
    if not os.path.isdir(os.path.join(bdmv_root, "PLAYLIST")):
        raise SourceError(f"BDMV folder has no PLAYLIST/ directory: {bdmv_root}")

    return bdmv_root, source_parent


def resolve_output_work(config: Config, source_parent: str) -> tuple[str, str]:
    """Resolve output directory and work directory.

    If output points at an existing parent dir without BDMV/, a source-named
    subdirectory is created. Work dir defaults to <output>.work unless an
    explicit --work was given.
    """
    requested = config.output
    exact_iso = config.output_iso and requested.lower().endswith(".iso")
    parent_hint = requested.endswith(os.sep) or requested.endswith("/")
    out = os.path.abspath(requested)
    if exact_iso:
        out = out[:-4]
    if parent_hint or (
        not exact_iso and os.path.isdir(out) and not os.path.isdir(os.path.join(out, "BDMV"))
    ):
        src_name = os.path.basename(os.path.normpath(source_parent)) or "BDMV"
        out = os.path.join(out, src_name)

    work_dir = os.path.abspath(config.work_dir) if config.work_dir else out + ".work"
    return out, work_dir


def resolve_iso_path(config: Config, output_dir: str) -> str:
    """Resolve the final ISO path from an exact file or a folder output."""
    if config.output.lower().endswith(".iso"):
        return os.path.abspath(config.output)
    return output_dir + ".iso"


def has_resume_state(work_dir: str) -> bool:
    """Return True if the work dir contains encode outputs (resumable)."""
    encode_dir = os.path.join(work_dir, "encode")
    if os.path.isdir(encode_dir):
        for name in os.listdir(encode_dir):
            if name.endswith(("_video.h264", "_video.hevc")):
                return True
    return os.path.isfile(os.path.join(work_dir, "inventory.json"))


# ---------------------------------------------------------------------------
# Clip helpers
# ---------------------------------------------------------------------------


def dedup_clips(inv: inventory.Inventory, playlist_ids: list[str]) -> list[str]:
    """Union of clip IDs across playlists, deduped, order-preserving (B9)."""
    seen: set[str] = set()
    result: list[str] = []
    for pl_id in playlist_ids:
        pl = inv.playlists.get(pl_id)
        if pl is None:
            continue
        for cid in pl.clips:
            if cid not in seen and cid in inv.clips:
                seen.add(cid)
                result.append(cid)
    return result


def parse_playlist_csv(csv: str) -> list[str]:
    """Parse a CSV of playlist names into .mpls filenames."""
    result: list[str] = []
    for name in csv.split(","):
        name = name.strip()
        if not name:
            continue
        if not name.endswith(".mpls"):
            name += ".mpls"
        result.append(name)
    return result


def apply_overrides(
    config: Config,
    inv: inventory.Inventory,
    classification: classify.Classification,
    logger: Optional[logging.Logger] = None,
) -> classify.Classification:
    """Apply --main-playlist / --extra / --menu / --not-extra overrides.

    The three result lists are kept mutually exclusive, matching the invariant
    that ``classify_playlists`` maintains: a playlist forced into one bucket
    via an override is removed from the others. Without this, a clip
    simultaneously in ``main`` and ``extras`` would be encoded at extras
    quality first (720p CRF) and then silently skipped by the main pass
    because ``skip_if_exists`` sees the existing output.
    """

    def resolve_override(flag_value: str, flag_name: str) -> Optional[list[str]]:
        """Parse an override CSV, keeping only playlists present on the disc.

        Warns about names not matching a disc playlist (likely typos) and
        raises if none match — an empty override would otherwise silently
        empty the bucket (e.g. a typo'd --extra would encode no extras while
        rebuild still copies the full-size clips, blowing the size budget).
        """
        if not flag_value:
            return None
        parsed = parse_playlist_csv(flag_value)
        matched = [p for p in parsed if p in inv.playlists]
        dropped = [p for p in parsed if p not in inv.playlists]
        if dropped and logger:
            logger.warning(f"{flag_name}: not on disc, ignored: {', '.join(dropped)}")
        if not matched:
            raise PipelineError(f"{flag_name}: none of {', '.join(parsed)} found on disc")
        return matched

    main = list(classification.main_playlists)
    extras = list(classification.extras_playlists)
    menus = list(classification.menu_playlists)

    override_main = resolve_override(config.override_main_playlists, "--main-playlist")
    override_extras = resolve_override(config.override_extras, "--extra")
    override_menus = resolve_override(config.override_menus, "--menu")

    if override_main is not None:
        main = override_main
        main_set = set(main)
        extras = [p for p in extras if p not in main_set]
        menus = [p for p in menus if p not in main_set]
    if override_extras is not None:
        extras = override_extras
        extras_set = set(extras)
        main = [p for p in main if p not in extras_set]
        menus = [p for p in menus if p not in extras_set]
    if override_menus is not None:
        menus = override_menus
        menus_set = set(menus)
        main = [p for p in main if p not in menus_set]
        extras = [p for p in extras if p not in menus_set]
    if config.override_not_extras:
        not_extras = set(parse_playlist_csv(config.override_not_extras))
        extras = [p for p in extras if p not in not_extras]

    return classify.Classification(
        main_playlists=main,
        extras_playlists=extras,
        menu_playlists=menus,
    )


def first_main_fps(inv: inventory.Inventory, main_clips: list[str]) -> str:
    """Derive the main movie frame rate from the first main clip with video."""
    for cid in main_clips:
        clip = inv.clips.get(cid)
        if clip and clip.video and clip.video.r_frame_rate:
            return normalize_fps(clip.video.r_frame_rate)
    return "23.976"


def normalize_fps(rate_str: str) -> str:
    """Convert an ffprobe r_frame_rate (e.g. '24000/1001') to a BD fps string."""
    try:
        if "/" in rate_str:
            num, den = rate_str.split("/")
            fps = float(num) / float(den)
        else:
            fps = float(rate_str)
    except (ValueError, ZeroDivisionError):
        return "23.976"
    for standard, label in (
        (23.976, "23.976"),
        (24.0, "24"),
        (25.0, "25"),
        (29.97, "29.97"),
        (50.0, "50"),
        (59.94, "59.94"),
    ):
        if abs(fps - standard) < 0.01:
            return label
    return f"{fps:.3f}".rstrip("0").rstrip(".")


def build_clip_fps_map(inv: inventory.Inventory, clip_ids: list[str]) -> dict[str, str]:
    """Map clip_id -> normalized fps for clips with video."""
    fps_map: dict[str, str] = {}
    for cid in clip_ids:
        clip = inv.clips.get(cid)
        if clip and clip.video:
            fps_map[cid] = normalize_fps(clip.video.r_frame_rate)
    return fps_map


def detect_burn_device() -> str:
    """Best-effort optical drive detection; returns '' if none found."""
    for dev in ("/dev/sr0", "/dev/cdrom", "/dev/dvd", "/dev/sr1"):
        if os.path.exists(dev):
            return dev
    return ""


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------


def write_checkpoint(work_dir: str, name: str, data: str) -> None:
    """Write a JSON checkpoint file to the work directory (best effort)."""
    try:
        os.makedirs(work_dir, exist_ok=True)
        with open(os.path.join(work_dir, name), "w") as f:
            f.write(data)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------


def _source_root_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".config", "bd-shrink", "source_root")


def load_source_root() -> str:
    """Load the persisted SOURCE_ROOT for the TUI, '' if unset/unreadable."""
    try:
        with open(_source_root_path()) as f:
            return f.read().strip()
    except OSError:
        return ""


def save_source_root(source: str) -> None:
    """Persist SOURCE_ROOT for future TUI sessions (best effort)."""
    try:
        os.makedirs(os.path.dirname(_source_root_path()), exist_ok=True)
        with open(_source_root_path(), "w") as f:
            f.write(os.path.dirname(os.path.dirname(source)) or source)
    except OSError:
        pass


def maybe_launch_tui(config: Config) -> Optional[Config]:
    """Launch the TUI when -s/-o are missing; return None if cancelled.

    Raises nothing on import failure — callers should exit 2.
    """
    if not (config.use_tui or (not config.source and not config.output)):
        return config
    if not sys.stdin.isatty():
        print(
            "ERROR: TUI requires an interactive terminal. Provide -s and -o.",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        from rich.console import Console

        from bd_shrink.tui import interactive_tui
    except ImportError as e:
        missing = getattr(e, "name", None) or str(e)
        install = next(
            (details[2] for module, details in PYTHON_DEPENDENCIES.items() if module == missing),
            "sudo dnf install python3-questionary python3-rich python3-wcwidth",
        )
        print(
            f"ERROR: TUI dependency '{missing}' is unavailable. Install with: {install} ({e})",
            file=sys.stderr,
        )
        sys.exit(2)
    print("Launching TUI...", file=sys.stderr)
    new_config = interactive_tui(Console(), config, load_source_root())
    if new_config is None:
        print("Cancelled.", file=sys.stderr)
        sys.exit(0)
    if new_config.source:
        save_source_root(new_config.source)
    return new_config


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_pipeline(
    config: Config,
    bdmv_root: str,
    output_dir: str,
    work_dir: str,
    logger,
    iso_source_dir: Optional[str] = None,
    iso_path: Optional[str] = None,
) -> None:
    """Run the 7-phase pipeline. Raises PipelineError on phase failure."""
    source_dir = os.path.join(bdmv_root, "STREAM")
    encode_dir = os.path.join(work_dir, "encode")
    os.makedirs(encode_dir, exist_ok=True)

    # Phase 1: Inventory
    logger.info(f"[1/7] Inventory: probing {bdmv_root}")
    inv = inventory.build_inventory(bdmv_root)
    write_checkpoint(work_dir, "inventory.json", inventory.to_json(inv))
    logger.info(f"  {len(inv.clips)} clips, {len(inv.playlists)} playlists")

    # Phase 2: Classify
    logger.info("[2/7] Classify")
    classification = classify.classify_playlists(inv)
    classification = apply_overrides(config, inv, classification, logger)
    write_checkpoint(work_dir, "classify.json", json.dumps(asdict(classification), indent=2))
    main_clips = dedup_clips(inv, classification.main_playlists)
    extras_clips = [] if config.movie_only else dedup_clips(inv, classification.extras_playlists)
    logger.info(
        f"  main={len(main_clips)} extras={len(extras_clips)} "
        f"menus={len(classification.menu_playlists)}"
    )
    if not main_clips:
        raise PipelineError("no main clips identified; check --main-playlist override")

    # Phase 3: Budget
    logger.info("[3/7] Budget")
    budget_result = budget.calculate_budget(
        inv,
        classification.main_playlists,
        classification.extras_playlists,
        classification.menu_playlists,
        target_gb=config.target_gb,
        overhead_mb=config.overhead_mb,
    )
    write_checkpoint(work_dir, "budget.json", json.dumps(budget_result, indent=2))
    budget.apply_bitrate_to_config(config, budget_result["main_bitrate_kbps"])
    logger.info(
        f"  main bitrate: {config.main_bitrate} "
        f"(max {config.main_maxrate}, buf {config.main_bufsize}), "
        f"main duration: {budget_result['main_duration_sec']:.0f}s "
        f"across {budget_result['main_clip_count']} unique clips"
    )

    # Phase 4: Encode
    logger.info(
        f"[4/7] Encode: {len(main_clips)} main + {len(extras_clips)} extras "
        f"(codec={config.codec}, passes={config.main_passes}, "
        f"preset={config.main_preset})"
    )
    encode_stats = encode.encode_all(
        inv,
        extras_clips,
        main_clips,
        source_dir,
        encode_dir,
        work_dir,
        config,
        no_extras=config.no_extras or config.movie_only,
        logger=logger,
    )
    write_checkpoint(
        work_dir,
        "encode.json",
        json.dumps([asdict(s) for s in encode_stats], indent=2),
    )
    failed = [s for s in encode_stats if not s.success]
    if failed:
        raise PipelineError(
            f"encoding failed for {len(failed)} clip(s): {', '.join(s.clip_name for s in failed)}"
        )
    logger.info(f"  encoded {len(encode_stats)} clip(s)")

    # Phase 5: Rebuild
    logger.info("[5/7] Rebuild")
    if config.movie_only:
        fps = first_main_fps(inv, main_clips)
        stats = rebuild.rebuild_movie_only(
            encode_dir,
            output_dir,
            work_dir,
            main_clips,
            config,
            main_fps=fps,
            logger=logger,
        )
    else:
        clip_fps_map = build_clip_fps_map(inv, main_clips + extras_clips)
        stats = rebuild.rebuild_surgical(
            bdmv_root,
            encode_dir,
            output_dir,
            work_dir,
            main_clips,
            extras_clips,
            config,
            clip_fps_map,
            no_extras=config.no_extras,
            preserve_orphans=config.preserve_orphans,
            logger=logger,
        )
    write_checkpoint(work_dir, "rebuild.json", json.dumps(asdict(stats), indent=2))
    if not stats.success:
        raise PipelineError(
            f"rebuild failed (mode={stats.mode}, "
            f"remuxed={stats.remuxed_clips}, copied={stats.copied_clips})"
        )
    logger.info(f"  mode={stats.mode} remuxed={stats.remuxed_clips} copied={stats.copied_clips}")

    # Phase 6: Validate
    logger.info("[6/7] Validate")
    validation = validate.validate_bdmv_structure(output_dir, logger)
    write_checkpoint(work_dir, "validate.json", json.dumps(asdict(validation), indent=2))
    if not validation.valid:
        raise PipelineError(
            f"validation failed: missing={validation.missing_files} "
            f"corrupted={validation.corrupted_files}"
        )
    actual_gb = enforce_output_size(output_dir, config, logger)
    logger.info(f"  output: {output_dir} ({actual_gb:.2f} GB / target {config.target_gb} GB)")

    # Phase 7: ISO/Burn
    if config.burn or config.output_iso:
        logger.info("[7/7] ISO/Burn")
        _run_iso_burn(
            config,
            output_dir,
            logger,
            iso_source_dir=iso_source_dir,
            iso_path=iso_path,
        )


def _run_iso_burn(
    config: Config,
    output_dir: str,
    logger,
    iso_source_dir: Optional[str] = None,
    iso_path: Optional[str] = None,
) -> None:
    """Dispatch ISO creation and burning per the --burn/--iso/flag combination."""
    iso_path = iso_path or resolve_iso_path(config, output_dir)
    source_dir = iso_source_dir or output_dir

    if config.burn and config.output_iso:
        result = iso.create_iso(source_dir, iso_path, logger, config.nice)
        if not result.success:
            raise PipelineError(f"ISO creation failed: {result.error_message}")
        device = config.burn_device or detect_burn_device()
        if not device:
            raise PipelineError("--burn requires --burn-device (auto-detection found no drive)")
        burn_result = iso.burn_iso(iso_path, device, logger, config.nice)
        if not burn_result.success:
            raise PipelineError(f"burn failed: {burn_result.error_message}")
        logger.info(f"  burned {iso_path} -> {device}")
    elif config.burn:
        device = config.burn_device or detect_burn_device()
        if not device:
            raise PipelineError("--burn requires --burn-device (auto-detection found no drive)")
        result = iso.burn_direct_pipe(source_dir, device, logger, config.nice)
        if not result.success:
            raise PipelineError(f"burn failed: {result.error_message}")
        logger.info(f"  direct-pipe burned -> {device}")
    elif config.output_iso:
        result = iso.create_iso(source_dir, iso_path, logger, config.nice)
        if not result.success:
            raise PipelineError(f"ISO creation failed: {result.error_message}")
        logger.info(f"  ISO: {iso_path}")

    if iso_source_dir and os.path.isdir(iso_source_dir):
        shutil.rmtree(iso_source_dir)
        logger.info(f"  removed ISO staging directory: {iso_source_dir}")


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def print_dry_run(config: Config, bdmv_root: str, output_dir: str, work_dir: str, logger) -> None:
    """Print the planned run without touching the disc."""
    logger.info("DRY RUN — no changes will be made")
    logger.info(f"  source: {bdmv_root}")
    logger.info(f"  output: {output_dir}")
    logger.info(f"  work:   {work_dir}")
    logger.info(f"  target: {config.target_gb} GB (overhead {config.overhead_mb} MB)")
    logger.info(
        f"  codec:  {config.codec}, passes={config.main_passes}, preset={config.main_preset}"
    )
    mode = "movie-only" if config.movie_only else "surgical (keep menus)"
    logger.info(f"  mode:   {mode}")
    if config.no_extras:
        logger.info("  extras: skipped (--no-extras)")
    if config.output_iso:
        logger.info(f"  output: ISO ({resolve_iso_path(config, output_dir)})")
    if config.burn:
        device = config.burn_device or detect_burn_device() or "<none>"
        logger.info(
            f"  burn:   {device}" + (" (incl. ISO)" if config.output_iso else " (direct pipe)")
        )


def enforce_output_size(output_dir: str, config: Config, logger) -> float:
    """Raise when a completed BDMV exceeds the requested target size."""
    fits, actual_gb = validate.check_output_size(output_dir, config.target_gb, logger)
    if not fits:
        consequence = (
            "ISO creation and burning skipped"
            if config.output_iso or config.burn
            else "encoded folder retained for diagnosis"
        )
        raise PipelineError(
            f"output exceeds target size: {actual_gb:.2f} GB > {config.target_gb} GB; {consequence}"
        )
    return actual_gb


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> None:
    """Main entry point."""
    args, _ = parse_args(argv)

    if args.install_deps:
        print(format_install_deps_output())
        return

    try:
        config = args_to_config(args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # TUI auto-launch (or --tui).
    config = maybe_launch_tui(config)

    if not config.source:
        print("ERROR: --source required (or run with no args for TUI)", file=sys.stderr)
        sys.exit(2)
    if not config.output:
        print("ERROR: --output required (or run with no args for TUI)", file=sys.stderr)
        sys.exit(2)

    # Resolve source.
    try:
        bdmv_root, source_parent = resolve_source(config.source)
    except SourceError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    # Resolve output + work dir.
    output_dir, work_dir = resolve_output_work(config, source_parent)
    iso_path = resolve_iso_path(config, output_dir) if config.output_iso else ""
    pipeline_output_dir = output_dir
    iso_source_dir = None
    if config.output_iso:
        pipeline_output_dir = os.path.join(work_dir, "iso-staging")
        iso_source_dir = pipeline_output_dir

    # Force / resume handling: force check is BEFORE dry-run (per AGENTS.md).
    resuming = has_resume_state(work_dir)
    existing_artifact = iso_path if config.output_iso else output_dir
    if os.path.exists(existing_artifact) and not resuming and not config.force:
        print(
            f"ERROR: output exists: {existing_artifact} — use -f to overwrite "
            "(do NOT use -f when resuming)",
            file=sys.stderr,
        )
        sys.exit(1)
    if config.force and not resuming and not config.dry_run and os.path.exists(existing_artifact):
        if os.path.isdir(existing_artifact):
            shutil.rmtree(existing_artifact)
        else:
            os.unlink(existing_artifact)

    if config.clean_work and not config.dry_run and os.path.isdir(work_dir) and not resuming:
        shutil.rmtree(work_dir)
    if config.force and not config.dry_run and not resuming and os.path.isdir(work_dir):
        # Fresh --force run should also start a clean work dir.
        shutil.rmtree(work_dir)
    os.makedirs(work_dir, exist_ok=True)

    logger = setup_logging(work_dir, config)

    # Dry-run.
    if config.dry_run:
        print_dry_run(config, bdmv_root, output_dir, work_dir, logger)
        return

    # Dependency check.
    try:
        validate_dependencies(strict=True)
    except RuntimeError as e:
        logger.error(str(e))
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(3)

    # Run pipeline.
    try:
        run_pipeline(
            config,
            bdmv_root,
            pipeline_output_dir,
            work_dir,
            logger,
            iso_source_dir=iso_source_dir,
            iso_path=iso_path,
        )
    except PipelineError as e:
        logger.error(str(e))
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.error("interrupted by user")
        print("\nInterrupted. Re-run without -f to resume.", file=sys.stderr)
        sys.exit(130)

    logger.info("Done.")
    if config.clean_work:
        try:
            shutil.rmtree(work_dir)
        except OSError:
            pass


if __name__ == "__main__":
    main()
