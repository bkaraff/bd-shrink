"""Interactive TUI for bd_shrink using questionary and rich."""

import os
from pathlib import Path
from typing import Optional

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from bd_shrink.config import Config


class ColorTheme:
    """Catppuccin Mocha color theme."""
    
    BLUE = "#89b4fa"
    GREEN = "#a6e3a1"
    YELLOW = "#f9e2af"
    RED = "#f38ba8"
    TEXT = "#cdd6f4"
    SUBTEXT1 = "#bac2de"
    SUBTEXT0 = "#a6adc8"
    OVERLAY1 = "#7f849c"


def show_welcome(console: Console) -> None:
    """Display welcome message."""
    welcome_text = Text()
    welcome_text.append("bd_shrink", style=f"bold {ColorTheme.BLUE}")
    welcome_text.append("\n")
    welcome_text.append("Shrink BD50 → BD25", style=ColorTheme.TEXT)
    
    panel = Panel(
        welcome_text,
        border_style=ColorTheme.BLUE,
        padding=(1, 2),
    )
    console.print(panel)


def select_source(console: Console, source_root: str = "") -> Optional[str]:
    """Interactive source selection."""
    console.print(Text("Select source", style=f"bold {ColorTheme.BLUE}"))
    console.print(Text(
        "Choose the movie folder that contains the BDMV directory or video/ISO file.",
        style=ColorTheme.TEXT
    ))
    
    # Ask for confirmation
    confirm = questionary.confirm(
        "Continue to source selection?",
        default=True,
        auto_enter=False,
        style=questionary.Style([(
            "question", f"fg:{ColorTheme.BLUE} bold"
        )])
    ).ask()
    
    if not confirm:
        return None
    
    # File picker
    while True:
        start_dir = source_root if (source_root and os.path.isdir(source_root)) else str(Path.home())
        
        source = questionary.path(
            message="Select source directory or file",
            default=start_dir,
            only_directories=False,
        ).ask()
        
        if not source:
            return None
        
        source_path = Path(source)
        
        # Detect actual source (BDMV directory or file)
        if source_path.is_file():
            # Single file (video or ISO)
            if source_path.suffix.lower() in ['.mkv', '.mp4', '.m4v', '.iso', '.m2ts', '.ts']:
                return str(source_path)
            else:
                console.print(
                    Text(
                        f"Unsupported file format: {source_path.name}\n"
                        "Supported: .mkv, .mp4, .m4v, .iso",
                        style=f"bold {ColorTheme.RED}"
                    )
                )
                continue
        
        # Check for BDMV folder
        if (source_path / "index.bdmv").exists():
            return str(source_path)
        
        if (source_path / "BDMV" / "index.bdmv").exists():
            return str(source_path / "BDMV")
        
        # Check for nested BDMV
        for bdmv_dir in source_path.glob("*/BDMV"):
            if (bdmv_dir / "index.bdmv").exists():
                return str(bdmv_dir)
        
        # Check for video files inside directory
        for ext in ['*.mkv', '*.mp4', '*.m4v', '*.iso']:
            for video_file in source_path.glob(ext):
                return str(video_file)
        
        console.print(
            Text(
                f"No BDMV or video file found in: {source_path}\n"
                "Browse deeper and select the folder containing BDMV/ or a video file.",
                style=f"bold {ColorTheme.RED}"
            )
        )


def select_output(console: Console, source: str, default_dir: str = "") -> Optional[str]:
    """Interactive output selection."""
    # Derive default output name from source
    if source.endswith((".mkv", ".mp4", ".m4v", ".iso", ".m2ts", ".ts")):
        source_title = Path(source).stem
    else:
        source_title = Path(source).parent.name
    
    default_output = os.path.join(default_dir or str(Path.home() / "Movies"), source_title)
    
    choice = questionary.select(
        "Select output",
        choices=[
            f"Use default: {default_output}",
            "Browse for folder...",
            "Type custom path...",
        ],
        style=questionary.Style([(
            "pointer", f"fg:{ColorTheme.BLUE} bold"
        )])
    ).ask()
    
    if not choice:
        return None
    
    if "Browse" in choice:
        output = questionary.path(
            message="Select output directory",
            default=str(Path.home() / "Movies"),
            only_directories=True,
        ).ask()
        return output
    elif "Type" in choice:
        output = questionary.text(
            message="Output path",
            default=default_output,
        ).ask()
        return output
    else:
        return default_output


def select_mode(console: Console, current_movie_only: bool = False) -> Optional[bool]:
    """Select full disc or movie-only mode."""
    default = "Movie-only (no menus, fresh BD)" if current_movie_only else "Full disc (keep menus, extras)"
    
    choice = questionary.select(
        "Select mode",
        choices=[
            "Full disc (keep menus, extras)",
            "Movie-only (no menus, fresh BD)",
        ],
        default=default,
        style=questionary.Style([(
            "pointer", f"fg:{ColorTheme.BLUE} bold"
        )])
    ).ask()
    
    if not choice:
        return None
    
    return "Movie-only" in choice


def select_output_format(console: Console, current_iso: bool = False) -> Optional[bool]:
    """Select output format (folder vs ISO)."""
    default = "ISO (.iso file)" if current_iso else "Folder (BDMV)"
    
    choice = questionary.select(
        "Output format",
        choices=[
            "Folder (BDMV)",
            "ISO (.iso file)",
        ],
        default=default,
        style=questionary.Style([(
            "pointer", f"fg:{ColorTheme.BLUE} bold"
        )])
    ).ask()
    
    if not choice:
        return None
    
    return "ISO" in choice


def select_codec(console: Console, current_codec: str = "h264") -> Optional[str]:
    """Select video codec."""
    default = "hevc (x265, slower)" if current_codec == "hevc" else "h264 (AVC, BD-compatible)"
    
    choice = questionary.select(
        "Video codec",
        choices=[
            "h264 (AVC, BD-compatible)",
            "hevc (x265, slower)",
        ],
        default=default,
        style=questionary.Style([(
            "pointer", f"fg:{ColorTheme.BLUE} bold"
        )])
    ).ask()
    
    if not choice:
        return None
    
    return "hevc" if "hevc" in choice else "h264"


def select_speed_profile(
    console: Console,
    current_preset: str = "slow",
    current_passes: int = 2,
) -> Optional[tuple[str, int]]:
    """Select encoding speed profile."""
    profiles = {
        "Quality (slow, 2-pass)": ("slow", 2),
        "Fast (medium, 1-pass)": ("medium", 1),
        "Quick (fast, 1-pass)": ("fast", 1),
        "Max Quality (slower, 2-pass)": ("slower", 2),
        "Extreme (veryslow, 2-pass)": ("veryslow", 2),
    }
    
    # Determine current profile
    current_profile = "Quality (slow, 2-pass)"  # Default
    for name, (preset, passes) in profiles.items():
        if preset == current_preset and passes == current_passes:
            current_profile = name
            break
    
    choice = questionary.select(
        "Encoding speed",
        choices=list(profiles.keys()),
        default=current_profile,
        style=questionary.Style([(
            "pointer", f"fg:{ColorTheme.BLUE} bold"
        )])
    ).ask()
    
    if not choice:
        return None
    
    return profiles.get(choice, ("slow", 2))


def show_summary(console: Console, config: Config, source_size: str) -> None:
    """Display configuration summary."""
    mode_color = ColorTheme.GREEN if config.movie_only else ColorTheme.RED
    mode_text = "Yes" if config.movie_only else "No"
    format_label = "ISO" if config.output_iso else "Folder"
    
    summary = Text()
    summary.append("▶ Ready to start\n\n", style=f"bold {ColorTheme.BLUE}")
    summary.append("Source:      ", style=ColorTheme.SUBTEXT1)
    summary.append(f"{config.source}\n", style=ColorTheme.TEXT)
    summary.append("Source size: ", style=ColorTheme.SUBTEXT1)
    summary.append(f"{source_size}\n", style=ColorTheme.TEXT)
    summary.append("Output:      ", style=ColorTheme.SUBTEXT1)
    summary.append(f"{config.output}\n", style=ColorTheme.TEXT)
    summary.append("Movie-only:  ", style=ColorTheme.SUBTEXT1)
    summary.append(f"{mode_text}\n", style=mode_color)
    summary.append("Format:      ", style=ColorTheme.SUBTEXT1)
    summary.append(f"{format_label}\n", style=ColorTheme.TEXT)
    summary.append("Speed:       ", style=ColorTheme.SUBTEXT1)
    summary.append(f"{config.main_preset}, {config.main_passes}-pass\n", style=ColorTheme.TEXT)
    
    panel = Panel(summary, border_style=ColorTheme.BLUE, padding=(1, 2))
    console.print(panel)


def interactive_tui(console: Console, config: Config, source_root: str = "") -> Optional[Config]:
    """Run interactive TUI mode.
    
    Args:
        console: Rich console instance
        config: Current config (for defaults)
        source_root: Default source root directory
    
    Returns:
        Updated Config if user confirms, None if cancelled
    """
    while True:
        console.clear()
        show_welcome(console)
        
        # Source selection
        if not config.source:
            source = select_source(console, source_root)
            if not source:
                return None
            config.source = source
            source_root = str(Path(source).parent.parent)
        
        # Output selection
        if not config.output:
            output = select_output(console, config.source, default_dir=str(Path.home() / "Movies"))
            if not output:
                return None
            config.output = output
        
        # Mode selection
        movie_only = select_mode(console, config.movie_only)
        if movie_only is not None:
            config.movie_only = movie_only
        
        # Output format
        output_iso = select_output_format(console, config.output_iso)
        if output_iso is not None:
            config.output_iso = output_iso
        
        # Codec selection
        codec = select_codec(console, config.codec)
        if codec:
            config.codec = codec
        
        # Speed profile
        speed = select_speed_profile(console, config.main_preset, config.main_passes)
        if speed:
            config.main_preset, config.main_passes = speed
        
        # Overwrite options
        if os.path.isdir(config.output):
            if questionary.confirm("Overwrite existing output?", default=False).ask():
                config.force = True
        
        # Show summary
        console.clear()
        show_welcome(console)
        
        try:
            source_size_bytes = 0
            if os.path.isfile(config.source):
                source_size_bytes = os.path.getsize(config.source)
            elif os.path.isdir(config.source):
                for root, dirs, files in os.walk(config.source):
                    for file in files:
                        source_size_bytes += os.path.getsize(os.path.join(root, file))
            
            source_size = f"{source_size_bytes / (1024**3):.2f} GB"
        except Exception:
            source_size = "unknown"
        
        show_summary(console, config, source_size)
        
        # Final action
        action = questionary.select(
            "Select action",
            choices=["Start", "Edit source", "Edit output", "Edit options", "Cancel"],
            style=questionary.Style([(
                "pointer", f"fg:{ColorTheme.BLUE} bold"
            )])
        ).ask()
        
        if action == "Start":
            return config
        elif action == "Edit source":
            config.source = ""
        elif action == "Edit output":
            config.output = ""
        elif action == "Edit options":
            # Source/output stay set, so the loop skips them and re-prompts
            # mode/format/codec/speed/burn — i.e. re-edits the options.
            pass
        elif action == "Cancel" or action is None:
            # None = user pressed Ctrl-C at the action prompt.
            return None
