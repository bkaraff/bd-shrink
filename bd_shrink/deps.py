"""Dependency checking and installation information."""

import importlib.util
import shutil
from typing import Optional

REQUIRED_TOOLS = {
    "ffmpeg": "Video encoding",
    "ffprobe": "Stream probing",
    "tsMuxeR": "Blu-ray authoring (v2.7.0+)",
    "bc": "Math calculations",
    "python3": "Script runtime",
    "systemd-run": "Process management (part of systemd)",
}

OPTIONAL_TOOLS = {
    "genisoimage": "UDF ISO creation (for --burn)",
    "growisofs": "BD-R burning (from dvd+rw-tools)",
    "mount": "ISO mounting (fallback: bsdtar or 7z)",
}

PYTHON_DEPENDENCIES = {
    "questionary": ("questionary", "Interactive prompts", "sudo dnf install python3-questionary"),
    "rich": ("rich", "Terminal rendering", "sudo dnf install python3-rich"),
    "wcwidth": ("wcwidth", "Terminal width support", "sudo dnf install python3-wcwidth"),
}

INSTALL_COMMANDS = {
    "fedora": {
        "ffmpeg": "sudo dnf install ffmpeg",
        "tsMuxeR": "# Download from https://github.com/InitialDLAN/tsMuxeR/releases",
        "genisoimage": "sudo dnf install genisoimage",
        "growisofs": "sudo dnf install dvd+rw-tools",
    },
    "debian": {
        "ffmpeg": "sudo apt-get install ffmpeg",
        "tsMuxeR": "# Download from https://github.com/InitialDLAN/tsMuxeR/releases",
        "genisoimage": "sudo apt-get install genisoimage",
        "growisofs": "sudo apt-get install dvd+rw-tools",
    },
    "ubuntu": {
        "ffmpeg": "sudo apt install ffmpeg",
        "tsMuxeR": "# Download from https://github.com/InitialDLAN/tsMuxeR/releases",
        "genisoimage": "sudo apt install genisoimage",
        "growisofs": "sudo apt install dvd+rw-tools",
    },
}


def find_tool(name: str) -> Optional[str]:
    """Find tool in PATH. Returns full path if found, None otherwise."""
    return shutil.which(name)


def find_python_module(name: str) -> bool:
    """Return whether a Python module can be imported from the active runtime."""
    return importlib.util.find_spec(name) is not None


def check_python_dependencies() -> tuple[list[str], list[str]]:
    """Check Python modules needed by the optional interactive TUI."""
    found = []
    missing = []
    for module in PYTHON_DEPENDENCIES:
        if find_python_module(module):
            found.append(module)
        else:
            missing.append(module)
    return found, missing


def check_required_tools() -> tuple[list[str], list[str]]:
    """Check for required tools.

    Returns:
        Tuple of (found_tools, missing_tools)
    """
    found = []
    missing = []

    for tool in REQUIRED_TOOLS.keys():
        if find_tool(tool):
            found.append(tool)
        else:
            missing.append(tool)

    return found, missing


def check_optional_tools() -> tuple[list[str], list[str]]:
    """Check for optional tools.

    Returns:
        Tuple of (found_tools, missing_tools)
    """
    found = []
    missing = []

    for tool in OPTIONAL_TOOLS.keys():
        if find_tool(tool):
            found.append(tool)
        else:
            missing.append(tool)

    return found, missing


def format_install_deps_output() -> str:
    """Format the --install-deps help text."""
    lines = []
    lines.append("bd-shrink v0.3.0-dev: Dependency Check\n")

    # Required tools
    lines.append("REQUIRED TOOLS:")
    found, missing = check_required_tools()
    for tool in REQUIRED_TOOLS.keys():
        status = "✓" if tool in found else "✗"
        desc = REQUIRED_TOOLS[tool]
        lines.append(f"  {status} {tool:15} — {desc}")

    if missing:
        lines.append("\nMISSING REQUIRED TOOLS (install at least one distribution):\n")
        lines.append("# Fedora / RHEL:")
        for cmd in [
            INSTALL_COMMANDS["fedora"].get(t) for t in missing if t in INSTALL_COMMANDS["fedora"]
        ]:
            if cmd:
                lines.append(f"  {cmd}")
        lines.append("\n# Debian / Ubuntu:")
        for cmd in [
            INSTALL_COMMANDS["debian"].get(t) for t in missing if t in INSTALL_COMMANDS["debian"]
        ]:
            if cmd:
                lines.append(f"  {cmd}")

    # Python modules used by the optional TUI
    lines.append("\n\nPYTHON DEPENDENCIES (for --tui):")
    python_found, python_missing = check_python_dependencies()
    for module, (_, desc, _) in PYTHON_DEPENDENCIES.items():
        status = "✓" if module in python_found else "✗"
        lines.append(f"  {status} {module:15} — {desc}")
    if python_missing:
        lines.append("\nMISSING TUI DEPENDENCIES (Fedora / RHEL):")
        for module in python_missing:
            lines.append(f"  {PYTHON_DEPENDENCIES[module][2]}")

    # Optional tools
    lines.append("\n\nOPTIONAL TOOLS (for advanced features):")
    found, missing = check_optional_tools()
    for tool in OPTIONAL_TOOLS.keys():
        status = "✓" if tool in found else "○"
        desc = OPTIONAL_TOOLS[tool]
        lines.append(f"  {status} {tool:15} — {desc}")

    return "\n".join(lines)


def validate_dependencies(strict: bool = True) -> bool:
    """Validate that required dependencies are available.

    Args:
        strict: If True, raise RuntimeError if any required tool is missing.
                If False, just return False without raising.

    Returns:
        True if all required tools found, False otherwise.

    Raises:
        RuntimeError if strict=True and any required tool is missing.
    """
    found, missing = check_required_tools()

    if missing:
        msg = f"Missing required tools: {', '.join(missing)}\n"
        msg += "Run 'bd-shrink --install-deps' for installation instructions."
        if strict:
            raise RuntimeError(msg)
        return False

    return True
