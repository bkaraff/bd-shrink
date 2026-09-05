"""ISO creation and burning phase."""

import logging
import os
import shutil
from dataclasses import dataclass
from typing import Optional

from bd_shrink.runner import find_tool, run_managed, run_simple


@dataclass
class ISOResult:
    """Result of ISO creation or burning."""

    success: bool
    iso_path: Optional[str]
    burned: bool
    error_message: Optional[str]


def create_iso(
    bdmv_dir: str,
    iso_path: str,
    logger: Optional[logging.Logger] = None,
    nice: int = 0,
) -> ISOResult:
    """Create ISO file from BDMV directory.

    Args:
        bdmv_dir: Path to BDMV directory (parent of BDMV/)
        iso_path: Path to output ISO file
        nice: CPU niceness for the transient service (0-19)
        logger: Logger instance

    Returns:
        ISOResult with success status and ISO path
    """
    if logger:
        logger.info(f"Creating ISO: {iso_path}")

    try:
        # Ensure output directory exists
        os.makedirs(os.path.dirname(iso_path) or ".", exist_ok=True)

        # Resolve full path: systemd-run uses a restricted PATH that may not
        # include Homebrew/other non-standard bin directories.
        genisoimage = find_tool("genisoimage")
        if genisoimage is None:
            error_msg = "genisoimage not found on PATH"
            if logger:
                logger.error(error_msg)
            return ISOResult(
                success=False,
                iso_path=None,
                burned=False,
                error_message=error_msg,
            )

        # Use genisoimage with UDF filesystem for BD compatibility
        cmd = [
            genisoimage,
            "-udf",
            "-allow-limited-size",
            "-V",
            "BDMV",
            "-o",
            iso_path,
            bdmv_dir,
        ]

        result = run_managed(cmd, nice=nice, logger=logger)

        if result.succeeded and os.path.isfile(iso_path):
            if logger:
                logger.info(f"ISO created: {iso_path}")
            return ISOResult(
                success=True,
                iso_path=iso_path,
                burned=False,
                error_message=None,
            )
        else:
            error_msg = result.stderr or "genisoimage failed"
            if logger:
                logger.error(f"ISO creation failed: {error_msg}")
            return ISOResult(
                success=False,
                iso_path=None,
                burned=False,
                error_message=error_msg,
            )

    except Exception as e:
        if logger:
            logger.error(f"Exception during ISO creation: {e}")
        return ISOResult(
            success=False,
            iso_path=None,
            burned=False,
            error_message=str(e),
        )


def burn_iso(
    iso_path: str,
    burn_device: str,
    logger: Optional[logging.Logger] = None,
    nice: int = 0,
) -> ISOResult:
    """Burn ISO to BD-R disc.

    Args:
        iso_path: Path to ISO file
        burn_device: Device path (e.g., /dev/sr0)
        nice: CPU niceness for the transient service (0-19)
        logger: Logger instance

    Returns:
        ISOResult with success status
    """
    if logger:
        logger.info(f"Burning to {burn_device}...")

    if not os.path.isfile(iso_path):
        error_msg = f"ISO file not found: {iso_path}"
        if logger:
            logger.error(error_msg)
        return ISOResult(
            success=False,
            iso_path=iso_path,
            burned=False,
            error_message=error_msg,
        )

    try:
        # Resolve full paths (systemd-run has a restricted PATH).
        growisofs = find_tool("growisofs")
        if growisofs is None:
            error_msg = "growisofs not found on PATH"
            if logger:
                logger.error(error_msg)
            return ISOResult(
                success=False,
                iso_path=iso_path,
                burned=False,
                error_message=error_msg,
            )

        # growisofs internally calls mkisofs; /usr/bin/mkisofs is often the
        # xorriso stub which does NOT support -udf. Point MKISOFS at genisoimage.
        env = {}
        genisoimage = find_tool("genisoimage")
        if genisoimage is not None:
            env["MKISOFS"] = genisoimage

        # Use growisofs to burn
        cmd = [
            growisofs,
            "-dvd-compat",
            "-Z",
            f"{burn_device}={iso_path}",
        ]

        result = run_managed(cmd, nice=nice, env=env, logger=logger)

        if result.succeeded:
            if logger:
                logger.info(f"Burn completed successfully to {burn_device}")
            return ISOResult(
                success=True,
                iso_path=iso_path,
                burned=True,
                error_message=None,
            )
        else:
            error_msg = result.stderr or "growisofs failed"
            if logger:
                logger.error(f"Burn failed: {error_msg}")
            return ISOResult(
                success=False,
                iso_path=iso_path,
                burned=False,
                error_message=error_msg,
            )

    except Exception as e:
        if logger:
            logger.error(f"Exception during burn: {e}")
        return ISOResult(
            success=False,
            iso_path=iso_path,
            burned=False,
            error_message=str(e),
        )


def burn_direct_pipe(
    bdmv_dir: str,
    burn_device: str,
    logger: Optional[logging.Logger] = None,
    nice: int = 0,
) -> ISOResult:
    """Burn BDMV directly via genisoimage piped to growisofs (no temp ISO).

    Pipes `genisoimage -udf -allow-limited-size ... | growisofs ... /dev/fd/0`,
    matching the v0.2.x direct-burn path. No intermediate ISO is written.

    Args:
        bdmv_dir: Path to BDMV directory (parent of BDMV/)
        burn_device: Device path (e.g., /dev/sr0)
        nice: reserved for parity with other burn paths (not applied to the
              raw pipe; systemd-run cannot wrap a shell pipeline)
        logger: Logger instance

    Returns:
        ISOResult with success status
    """
    import subprocess

    if logger:
        logger.info(f"Burning directly to {burn_device} (no temp ISO)...")

    genisoimage = find_tool("genisoimage")
    growisofs = find_tool("growisofs")
    if genisoimage is None or growisofs is None:
        missing = "genisoimage" if genisoimage is None else "growisofs"
        error_msg = f"{missing} not found on PATH"
        if logger:
            logger.error(error_msg)
        return ISOResult(
            success=False,
            iso_path=None,
            burned=False,
            error_message=error_msg,
        )

    # growisofs reads the piped image from stdin via /dev/fd/0.
    # MKISOFS must point at genisoimage (xorriso stub lacks -udf).
    env = dict(os.environ)
    env["MKISOFS"] = genisoimage

    gen_cmd = [
        genisoimage,
        "-udf",
        "-allow-limited-size",
        "-V",
        "BDMV",
        bdmv_dir,
    ]
    grow_cmd = [
        growisofs,
        "-dvd-compat",
        "-Z",
        f"{burn_device}=/dev/fd/0",
    ]

    gen_proc = None
    grow_proc = None
    try:
        gen_proc = subprocess.Popen(
            gen_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        grow_proc = subprocess.Popen(
            grow_cmd,
            stdin=gen_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        # Allow gen_proc to receive SIGPIPE if grow_proc exits.
        if gen_proc.stdout is not None:
            gen_proc.stdout.close()

        grow_out, grow_err = grow_proc.communicate()
        gen_proc.wait()

        if grow_proc.returncode == 0:
            if logger:
                logger.info(f"Direct burn completed successfully to {burn_device}")
            return ISOResult(
                success=True,
                iso_path=None,
                burned=True,
                error_message=None,
            )

        error_msg = (
            grow_err.decode(errors="replace") if grow_err else ""
        ).strip() or "growisofs failed"
        if logger:
            logger.error(f"Direct burn failed: {error_msg}")
        return ISOResult(
            success=False,
            iso_path=None,
            burned=False,
            error_message=error_msg,
        )

    except Exception as e:
        # Clean up any running children.
        for proc in (grow_proc, gen_proc):
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        if logger:
            logger.error(f"Exception during direct burn: {e}")
        return ISOResult(
            success=False,
            iso_path=None,
            burned=False,
            error_message=str(e),
        )


def cleanup_iso_mounts(
    mount_points: list[str],
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Clean up temporary ISO mount points.

    Args:
        mount_points: List of temporary directories to remove
        logger: Logger instance

    Returns:
        True if all cleaned up successfully
    """
    all_ok = True
    for mp in mount_points:
        try:
            if os.path.ismount(mp):
                result = run_simple(["umount", mp], logger=logger)
                if not result.succeeded:
                    all_ok = False

            if os.path.isdir(mp):
                shutil.rmtree(mp)
        except Exception as e:
            if logger:
                logger.debug(f"Error cleaning up {mp}: {e}")
            all_ok = False

    return all_ok
