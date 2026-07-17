"""Runner: subprocess orchestration with systemd-run transient services.

Wraps subprocess.run() to:
  - Execute commands in systemd-run --user --wait (transient service survives shell crash)
  - Apply CPU niceness and resource limits
  - Capture stderr/stdout with optional logging
  - Implement per-process status/logging
"""

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class RunResult:
    """Result of a command execution."""
    returncode: int
    stdout: str
    stderr: str
    succeeded: bool  # True if returncode == 0


def run_ff(
    cmd: list[str],
    nice: int = 0,
    description: str = "",
    env: Optional[dict] = None,
    logger: Optional[logging.Logger] = None,
) -> RunResult:
    """Execute command via systemd-run --user --wait with optional niceness.
    
    All ffmpeg/ffprobe/tsMuxeR commands run through this to ensure:
    - Transient systemd service survives shell crashes
    - CPU niceness applied (default 0, max 19)
    - Logging to stderr/stdout captured
    
    Args:
        cmd: Command list (e.g., ['ffmpeg', '-i', 'input.m2ts', ...])
        nice: CPU niceness (0-19, higher = lower priority)
        description: Description for logging
        env: Extra environment variables to pass through to the service
             (e.g., {"MKISOFS": "/usr/bin/genisoimage"}). Passed via
             systemd-run --setenv so the transient service sees them.
        logger: Logger instance (optional)
    
    Returns:
        RunResult with returncode, stdout, stderr, succeeded flag
    
    Raises:
        FileNotFoundError: if systemd-run not found
        ValueError: if nice is out of range
    """
    if not 0 <= nice <= 19:
        raise ValueError(f"nice must be 0-19, got {nice}")
    
    # Build systemd-run wrapper
    systemd_cmd = [
        "systemd-run",
        "--user",
        "--wait",
        "--pipe",
        f"--property=Nice={nice}",
    ]

    # Forward requested environment variables into the transient service.
    if env:
        for key, value in env.items():
            systemd_cmd.append(f"--setenv={key}={value}")

    full_cmd = systemd_cmd + cmd
    
    if logger:
        logger.debug(f"Running: {' '.join(full_cmd)}")
        if description:
            logger.info(f"{description}")
    
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        
        succeeded = result.returncode == 0
        
        if logger:
            if succeeded:
                logger.info("Command succeeded (exit code 0)")
            else:
                logger.warning(f"Command failed with exit code {result.returncode}")
                if result.stderr:
                    logger.error(f"stderr: {result.stderr[:500]}")
        
        return RunResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            succeeded=succeeded,
        )
    
    except FileNotFoundError as e:
        if "systemd-run" in str(e):
            raise FileNotFoundError(
                "systemd-run not found. Install systemd or use --no-systemd flag"
            ) from e
        raise


def run_simple(
    cmd: list[str],
    cwd: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> RunResult:
    """Execute command directly without systemd-run (for simple tasks).
    
    Used for non-critical tasks (dependency checks, etc.) that don't need
    transient service protection.
    
    Args:
        cmd: Command list
        cwd: Working directory (optional)
        logger: Logger instance (optional)
    
    Returns:
        RunResult with returncode, stdout, stderr, succeeded flag
    """
    if logger:
        logger.debug(f"Running (no systemd): {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
        )
        
        succeeded = result.returncode == 0
        
        if logger and not succeeded:
            logger.warning(f"Command failed with exit code {result.returncode}")
        
        return RunResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            succeeded=succeeded,
        )
    
    except FileNotFoundError:
        if logger:
            logger.error(f"Command not found: {cmd[0]}")
        # Return failure result instead of raising
        return RunResult(
            returncode=127,
            stdout="",
            stderr=f"Command not found: {cmd[0]}",
            succeeded=False,
        )
    
    except Exception as e:
        if logger:
            logger.error(f"Error running command: {e}")
        # Return failure result instead of raising
        return RunResult(
            returncode=1,
            stdout="",
            stderr=str(e),
            succeeded=False,
        )


def find_tool(tool_name: str) -> Optional[str]:
    """Find full path to a tool on PATH.

    Uses shutil.which() (equivalent to `command -v` but without invoking a
    shell builtin via subprocess, which would fail). If tool_name is already an
    absolute path to an executable, it is returned as-is.

    Args:
        tool_name: Name or path of tool to find (e.g., 'ffmpeg')

    Returns:
        Full path if found, None otherwise
    """
    return shutil.which(tool_name)


def systemd_run_available() -> bool:
    """Return True if systemd-run --user is usable on this host.

    Checks the binary is on PATH and that a user manager is reachable. Used to
    decide whether to wrap subprocesses in a transient service (run_ff) or fall
    back to a direct subprocess (run_simple), e.g. in CI/containers.
    """
    if shutil.which("systemd-run") is None:
        return False
    return bool(os.environ.get("XDG_RUNTIME_DIR")) or os.path.isdir("/run/systemd/system")


def run_managed(
    cmd: list[str],
    nice: int = 0,
    description: str = "",
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> RunResult:
    """Run a command in a transient systemd service when available.

    Wraps run_ff (systemd-run --user --wait) so the process survives a shell
    crash and honours --nice. Falls back to run_simple when systemd-run is not
    usable (containers, CI). This is the entry point encode/rebuild/iso should
    use for external tools.

    Args:
        cmd: Command list
        nice: CPU niceness (0-19) applied by run_ff
        description: Description for logging
        env: Extra environment variables to forward (systemd --setenv)
        cwd: Working directory (only honoured by the run_simple fallback)
        logger: Logger instance

    Returns:
        RunResult
    """
    if systemd_run_available():
        return run_ff(cmd, nice=nice, description=description, env=env, logger=logger)

    # Fallback: direct subprocess. Merge env into the current environment.
    merged_env = None
    if env:
        merged_env = dict(os.environ)
        merged_env.update(env)
    return _run_simple_with_env(cmd, cwd=cwd, env=merged_env, logger=logger)


def _run_simple_with_env(
    cmd: list[str],
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    logger: Optional[logging.Logger] = None,
) -> RunResult:
    """run_simple variant that accepts an explicit environment."""
    if logger:
        logger.debug(f"Running (no systemd): {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            env=env,
        )
        succeeded = result.returncode == 0
        if logger and not succeeded:
            logger.warning(f"Command failed with exit code {result.returncode}")
        return RunResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            succeeded=succeeded,
        )
    except FileNotFoundError:
        if logger:
            logger.error(f"Command not found: {cmd[0]}")
        return RunResult(returncode=127, stdout="", stderr=f"Command not found: {cmd[0]}", succeeded=False)
    except Exception as e:
        if logger:
            logger.error(f"Error running command: {e}")
        return RunResult(returncode=1, stdout="", stderr=str(e), succeeded=False)


def check_returncode(result: RunResult, expected: int = 0) -> bool:
    """Check if command succeeded with expected return code.
    
    Args:
        result: RunResult from run_ff or run_simple
        expected: Expected return code (default 0)
    
    Returns:
        True if returncode matches expected
    """
    return result.returncode == expected
