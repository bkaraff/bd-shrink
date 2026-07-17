"""Command-line interface: argparse and validation."""

import argparse
from typing import Optional

from bd_shrink import __version__
from bd_shrink.config import Config


def create_parser() -> argparse.ArgumentParser:
    """Create and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="bd-shrink",
        description="Shrink BD50 Blu-ray backups to BD25 with menu preservation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive TUI (auto-launched when -s/-o omitted)
  bd-shrink

  # Movie-only backup (no menus, fresh BD)
  bd-shrink -s /path/to/BDMV -o /output -f --movie-only

  # Surgical mode (keep menus and structure)
  bd-shrink -s /path/to/BDMV -o /output -f

  # Single video file input
  bd-shrink -s movie.mkv -o /output -f

  # Check dependencies
  bd-shrink --install-deps
        """,
    )

    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    # Input/output
    parser.add_argument(
        "-s",
        "--source",
        type=str,
        default="",
        help="Source BDMV folder (must contain index.bdmv), video file (.mkv/.mp4/.m4v), or ISO image (.iso)",
    )
    parser.add_argument(
        "-o", "--output", type=str, default="", help="Output directory"
    )
    parser.add_argument(
        "-w",
        "--work",
        type=str,
        default="",
        help="Working directory (default: <output>.work)",
    )

    # Sizing and encoding
    parser.add_argument(
        "-t",
        "--target",
        type=int,
        default=23,
        metavar="GB",
        help="Target size in GB (default: 23 for BD25)",
    )
    parser.add_argument(
        "--overhead",
        type=int,
        default=200,
        metavar="MB",
        help="Overhead for menus/subtitles (default: 200 MB)",
    )
    parser.add_argument(
        "--codec",
        type=str,
        default="h264",
        choices=["h264", "hevc"],
        help="Video codec: h264 or hevc (default: h264)",
    )
    parser.add_argument(
        "--main-preset",
        type=str,
        default="slow",
        help="x264/x265 preset for main movie (default: slow)",
    )
    parser.add_argument(
        "--main-passes",
        type=int,
        default=2,
        choices=[1, 2],
        help="Video encode passes: 1 (fast) or 2 (quality, default)",
    )

    # Extras
    parser.add_argument(
        "--extras-scale",
        type=str,
        default="1280:720",
        help="Extras downscale resolution (default: 1280:720)",
    )
    parser.add_argument(
        "--extras-crf",
        type=int,
        default=22,
        metavar="NUM",
        help="Extras CRF quality value (default: 22)",
    )

    # Modes
    parser.add_argument(
        "--movie-only",
        action="store_true",
        help="Movie-only backup (no menus, fresh BD authoring)",
    )
    parser.add_argument(
        "--no-extras",
        action="store_true",
        help="Skip extras encoding (surgical mode only)",
    )
    parser.add_argument(
        "--keep-one",
        action="store_true",
        help="Only keep the longest movie playlist",
    )

    # Output format
    parser.add_argument(
        "--iso",
        action="store_true",
        help="Output ISO instead of BDMV folder",
    )

    # Burning
    parser.add_argument(
        "--burn",
        action="store_true",
        help="Burn output to BD-R after encoding",
    )
    parser.add_argument(
        "--burn-device",
        type=str,
        default="",
        help="Optical drive device path (auto-detected if omitted)",
    )
    parser.add_argument(
        "--burn-speed",
        type=int,
        default=0,
        metavar="N",
        help="BD-R write speed multiplier (default: drive max)",
    )

    # Behavior
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite output directory if it exists",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show what would be done without encoding",
    )
    parser.add_argument(
        "--clean-work",
        action="store_true",
        help="Remove working directory on success",
    )
    parser.add_argument(
        "--nice",
        type=int,
        nargs="?",
        const=19,
        default=0,
        metavar="N",
        help="Run at low CPU priority (default N=19)",
    )

    # TUI and special modes
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Interactive TUI mode (requires questionary)",
    )
    parser.add_argument(
        "--install-deps",
        action="store_true",
        help="Show required tools and install commands, then exit",
    )

    # Overrides (for advanced users)
    parser.add_argument(
        "--main-playlist",
        type=str,
        default="",
        metavar="NAMES",
        help="CSV list of playlist names (5 digits) to force as main",
    )
    parser.add_argument(
        "--extra",
        type=str,
        default="",
        metavar="NAMES",
        help="CSV list of playlist names to force as extras",
    )
    parser.add_argument(
        "--not-extra",
        type=str,
        default="",
        metavar="NAMES",
        help="CSV list of playlist names to exclude from extras",
    )
    parser.add_argument(
        "--menu",
        type=str,
        default="",
        metavar="NAMES",
        help="CSV list of playlist names to force as menus",
    )

    return parser


def parse_args(argv: Optional[list] = None) -> tuple[argparse.Namespace, list]:
    """Parse command-line arguments.

    Returns:
        Tuple of (parsed args, remaining unparsed args)
    """
    parser = create_parser()
    args, remaining = parser.parse_known_args(argv)
    return args, remaining


def validate_args(args: argparse.Namespace) -> None:
    """Validate parsed arguments. Raises ValueError on invalid input."""
    # Validate target GB floor
    if args.target < 1:
        raise ValueError(f"--target must be >= 1 GB, got {args.target}")

    # Validate nice value range
    if not (0 <= args.nice <= 19):
        raise ValueError(f"--nice must be 0-19, got {args.nice}")

    # Validate burn speed if provided
    if args.burn_speed < 0:
        raise ValueError(f"--burn-speed must be >= 0, got {args.burn_speed}")


def args_to_config(args: argparse.Namespace) -> Config:
    """Convert parsed arguments to a Config object."""
    validate_args(args)

    return Config(
        source=args.source,
        output=args.output,
        work_dir=args.work,
        target_gb=args.target,
        overhead_mb=args.overhead,
        codec=args.codec,
        main_preset=args.main_preset,
        main_passes=args.main_passes,
        extras_scale=args.extras_scale,
        extras_crf=args.extras_crf,
        movie_only=args.movie_only,
        no_extras=args.no_extras,
        keep_one=args.keep_one,
        output_iso=args.iso,
        burn=args.burn,
        burn_device=args.burn_device,
        burn_speed=args.burn_speed,
        dry_run=args.dry_run,
        force=args.force,
        clean_work=args.clean_work,
        nice=args.nice,
        use_tui=args.tui,
        install_deps=args.install_deps,
        override_main_playlists=args.main_playlist,
        override_extras=args.extra,
        override_not_extras=args.not_extra,
        override_menus=args.menu,
    )
