"""Validate phase: file and CLPI sanity checks."""

import logging
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ValidationResult:
    """Result of validation checks."""

    valid: bool
    missing_files: list[str]
    corrupted_files: list[str]
    warnings: list[str]
    output_bytes: int


def validate_m2ts_file(m2ts_path: str, logger: Optional[logging.Logger] = None) -> bool:
    """Validate an M2TS file has a valid video stream.

    Args:
        m2ts_path: Path to M2TS file
        logger: Logger instance

    Returns:
        True if file is valid
    """
    try:
        if not os.path.isfile(m2ts_path) or os.path.getsize(m2ts_path) == 0:
            return False

        # Check for TS sync bytes (0x47 = MPEG-TS sync)
        with open(m2ts_path, "rb") as f:
            # Skip BD-specific header (4 bytes) and read first sync byte
            f.seek(4)
            sync_byte = f.read(1)
            if sync_byte != b"\x47":
                return False

        return True

    except Exception as e:
        if logger:
            logger.debug(f"Error validating M2TS: {e}")
        return False


def validate_clpi_file(clpi_path: str, logger: Optional[logging.Logger] = None) -> bool:
    """Validate a CLPI file has valid header.

    Args:
        clpi_path: Path to CLPI file
        logger: Logger instance

    Returns:
        True if file is valid
    """
    try:
        if not os.path.isfile(clpi_path) or os.path.getsize(clpi_path) == 0:
            return False

        # Check CLPI magic bytes: real files start with "HDMV" or "CLPI"
        # followed by version "0100" or "0200" (8 bytes total).
        with open(clpi_path, "rb") as f:
            magic = f.read(8)
            if len(magic) < 8:
                return False
            if magic[:4] not in (b"HDMV", b"CLPI"):
                return False
            if magic[4:] not in (b"0100", b"0200"):
                return False

        return True

    except Exception as e:
        if logger:
            logger.debug(f"Error validating CLPI: {e}")
        return False


def validate_bdmv_structure(
    output_dir: str,
    logger: Optional[logging.Logger] = None,
) -> ValidationResult:
    """Validate complete BDMV structure.

    Args:
        output_dir: BDMV output directory
        logger: Logger instance

    Returns:
        ValidationResult with findings
    """
    missing = []
    corrupted = []
    warnings = []

    # Check required directories
    required_dirs = [
        "BDMV",
        "BDMV/STREAM",
        "BDMV/CLIPINF",
        "BDMV/PLAYLIST",
    ]

    for dir_name in required_dirs:
        dir_path = os.path.join(output_dir, dir_name)
        if not os.path.isdir(dir_path):
            missing.append(dir_name)

    # Check required files
    required_files = [
        "BDMV/index.bdmv",
    ]

    for file_name in required_files:
        file_path = os.path.join(output_dir, file_name)
        if not os.path.isfile(file_path):
            missing.append(file_name)

    # Validate M2TS files
    stream_dir = os.path.join(output_dir, "BDMV/STREAM")
    if os.path.isdir(stream_dir):
        for m2ts_file in os.listdir(stream_dir):
            if not m2ts_file.endswith(".m2ts"):
                continue

            m2ts_path = os.path.join(stream_dir, m2ts_file)
            if not validate_m2ts_file(m2ts_path, logger):
                corrupted.append(m2ts_file)

    # Validate CLPI files
    clipinf_dir = os.path.join(output_dir, "BDMV/CLIPINF")
    if os.path.isdir(clipinf_dir):
        for clpi_file in os.listdir(clipinf_dir):
            if not clpi_file.endswith(".clpi"):
                continue

            clpi_path = os.path.join(clipinf_dir, clpi_file)
            if not validate_clpi_file(clpi_path, logger):
                corrupted.append(clpi_file)

    # Check for orphan M2TS without corresponding CLPI
    if os.path.isdir(stream_dir):
        for m2ts_file in os.listdir(stream_dir):
            if not m2ts_file.endswith(".m2ts"):
                continue

            clip_id = m2ts_file[:-5]  # Remove .m2ts
            clpi_file = os.path.join(clipinf_dir, f"{clip_id}.clpi")

            if not os.path.isfile(clpi_file):
                warnings.append(f"M2TS file {m2ts_file} has no corresponding CLPI")

    # Compute total output size
    output_bytes = 0
    for root, dirs, files in os.walk(output_dir):
        for file in files:
            output_bytes += os.path.getsize(os.path.join(root, file))

    valid = len(missing) == 0 and len(corrupted) == 0

    return ValidationResult(
        valid=valid,
        missing_files=missing,
        corrupted_files=corrupted,
        warnings=warnings,
        output_bytes=output_bytes,
    )


def check_output_size(
    output_path: str,
    target_gb: int,
    logger: Optional[logging.Logger] = None,
) -> tuple[bool, float]:
    """Check if output fits within target size.

    Args:
        output_path: Path to output (file or directory)
        target_gb: Target size in GB
        logger: Logger instance

    Returns:
        Tuple (fits, actual_size_gb)
    """
    try:
        if os.path.isfile(output_path):
            output_bytes = os.path.getsize(output_path)
        elif os.path.isdir(output_path):
            output_bytes = 0
            for root, dirs, files in os.walk(output_path):
                for file in files:
                    output_bytes += os.path.getsize(os.path.join(root, file))
        else:
            return False, 0.0

        output_gb = output_bytes / (1024**3)
        fits = output_bytes <= (target_gb * 1024**3)

        if not fits and logger:
            logger.warning(f"Output size ({output_gb:.2f} GB) exceeds target ({target_gb} GB)")

        return fits, output_gb

    except Exception as e:
        if logger:
            logger.error(f"Error checking output size: {e}")
        return False, 0.0
