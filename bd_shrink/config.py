"""Configuration: dataclass and defaults for bd_shrink."""

from dataclasses import dataclass


@dataclass
class Config:
    """Main configuration object."""

    # Input/output
    source: str = ""
    output: str = ""
    work_dir: str = ""

    # Sizing and encoding
    target_gb: int = 23
    overhead_mb: int = 200
    codec: str = "h264"  # h264 or hevc
    main_preset: str = "slow"  # x264/x265 preset
    main_passes: int = 2  # 1 or 2 pass encoding

    # Extras
    extras_scale: str = "1280:720"
    extras_crf: int = 22

    # Modes
    movie_only: bool = False
    no_extras: bool = False
    keep_one: bool = False

    # Output format
    output_iso: bool = False

    # Burning
    burn: bool = False
    burn_device: str = ""
    burn_speed: int = 0

    # Behavior
    dry_run: bool = False
    force: bool = False
    clean_work: bool = False
    nice: int = 0
    use_tui: bool = False
    install_deps: bool = False

    # Overrides (CSV strings)
    override_main_playlists: str = ""
    override_extras: str = ""
    override_not_extras: str = ""
    override_menus: str = ""

    def __post_init__(self):
        """Validate configuration after initialization."""
        # Validate target GB
        if self.target_gb < 1:
            raise ValueError(f"target_gb must be >= 1, got {self.target_gb}")

        # Validate codec
        if self.codec not in ("h264", "hevc"):
            raise ValueError(f"codec must be 'h264' or 'hevc', got {self.codec}")

        # Validate main_passes
        if self.main_passes not in (1, 2):
            raise ValueError(f"main_passes must be 1 or 2, got {self.main_passes}")

        # Validate nice value
        if not (0 <= self.nice <= 19):
            raise ValueError(f"nice must be 0-19, got {self.nice}")

        # Validate playlist override names (5 decimal digits, optionally with .mpls)
        for override_list in [
            self.override_main_playlists,
            self.override_extras,
            self.override_not_extras,
            self.override_menus,
        ]:
            if override_list:
                for pl_name in override_list.split(","):
                    pl_name = pl_name.strip()
                    if not pl_name:
                        continue
                    # Must be 5 decimal digits, optionally with .mpls
                    import re

                    if not re.match(r"^\d{5}(\.mpls)?$", pl_name):
                        raise ValueError(
                            f"Invalid playlist name: {pl_name} — must be 5 decimal digits, "
                            "optionally with .mpls (e.g., 00000 or 00000.mpls)"
                        )

        # movie_only implies keep_one
        if self.movie_only:
            self.keep_one = True
