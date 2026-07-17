"""Entrypoint for bd_shrink."""

import sys

from bd_shrink.cli import args_to_config, parse_args
from bd_shrink.deps import format_install_deps_output


def main():
    """Main entry point."""
    args, remaining = parse_args()

    # Handle --install-deps early
    if args.install_deps:
        print(format_install_deps_output())
        sys.exit(0)

    # Convert args to config (this also validates)
    try:
        config = args_to_config(args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # For now, print config and exit (placeholder)
    print(f"Config loaded: source={config.source!r}, output={config.output!r}")
    print(f"  target_gb={config.target_gb}, codec={config.codec}, main_passes={config.main_passes}")
    if config.use_tui:
        print("  (TUI mode would launch here in Phase 6)")
    if config.dry_run:
        print("  (dry-run mode)")

    # TODO: Phase 2+ will implement actual logic here
    print("\nPhase 2 complete: CLI + config + deps scaffolded.")
    print("Next: Phase 3 (audio.py).")


if __name__ == "__main__":
    main()
