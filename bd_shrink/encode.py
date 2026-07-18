"""Encode phase: video, audio, and subtitle encoding with resumability.

Handles:
  - Audio extraction (passthrough stream-copy with MPEG skip)
  - Subtitle extraction (PGS/DVB, skip DVD/VobSub)
  - Video encoding (extras at 720p CRF; main at target bitrate 1-pass or 2-pass VBR)
  - Resumability (skip if output file exists)
  - Retry logic (3 attempts per clip)
"""

import glob
import logging
import os
from dataclasses import dataclass
from typing import Optional

from bd_shrink.audio import (
    AUDIO_FORMAT_OVERRIDE,
    AUDIO_TRANSCODE,
    audio_ext,
    should_skip,
    subtitle_ext,
    subtitle_format,
    tsmuxer_type,
)
from bd_shrink.config import Config
from bd_shrink.inventory import Clip, Inventory
from bd_shrink.runner import run_managed


@dataclass
class EncodeStats:
    """Statistics for an encoded clip."""

    clip_name: str
    clip_type: str  # "extras" or "main"
    audio_tracks: int
    subtitle_tracks: int
    video_encoded: bool
    success: bool


def skip_if_exists(out_file: Optional[str] = None, pass_log_base: Optional[str] = None) -> bool:
    """Check if output already exists (resumability).

    Args:
        out_file: Output video file path
        pass_log_base: Base path for pass log files (glob for *, e.g., /path/to/log)

    Returns:
        True if output exists and is non-empty
    """
    if out_file and os.path.isfile(out_file) and os.path.getsize(out_file) > 0:
        return True
    if pass_log_base:
        matches = glob.glob(pass_log_base + "*")
        if matches:
            return True
    return False


def get_audio_codecs(
    clip: Clip,
    logger: Optional[logging.Logger] = None,
) -> list[str]:
    """Get audio codec for each track in a clip.

    Args:
        clip: Clip metadata
        logger: Logger instance

    Returns:
        List of codec names (e.g., ['aac', 'ac3', 'dts'])
    """
    codecs = []
    if clip.audio:
        for stream in clip.audio:
            codec = stream.codec_name or "unknown"
            codecs.append(codec)
    return codecs


def get_subtitle_codecs(
    clip: Clip,
    logger: Optional[logging.Logger] = None,
) -> list[str]:
    """Get subtitle codec for each track in a clip.

    Args:
        clip: Clip metadata
        logger: Logger instance

    Returns:
        List of codec names (e.g., ['hdmv_pgs_subtitle', 'dvd_subtitle'])
    """
    codecs = []
    if clip.subtitles:
        for stream in clip.subtitles:
            codec = stream.codec_name or "hdmv_pgs_subtitle"
            codecs.append(codec)
    return codecs


def extract_audio(
    clip: Clip,
    src_path: str,
    encode_dir: str,
    logger: Optional[logging.Logger] = None,
) -> tuple[int, list[str]]:
    """Extract audio tracks from clip (passthrough stream-copy, skip MPEG).

    Args:
        clip: Clip metadata
        src_path: Source M2TS file path
        encode_dir: Output directory for audio files
        logger: Logger instance

    Returns:
        Tuple (audio_track_count, list_of_audio_extensions)
        Returns (0, []) if no tracks extracted
    """
    if not clip.audio or len(clip.audio) == 0:
        return 0, []

    audio_codecs = get_audio_codecs(clip, logger)

    actual_audio_idx = 0
    track_exts = []

    for ai, codec in enumerate(audio_codecs):
        # Skip MPEG audio codecs (mp2, mp3, mp2float, mp3float)
        if should_skip(codec):
            if logger:
                logger.debug(f"Skipping MPEG audio track {ai} (codec: {codec})")
            continue

        ext = audio_ext(codec)
        track_exts.append(ext)
        out_path = os.path.join(encode_dir, f"{clip.clip_id}_audio_{actual_audio_idx}{ext}")

        # Check if already extracted
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            actual_audio_idx += 1
            continue

        # Build per-track command to isolate failures
        codec_args = ["ffmpeg", "-y", "-v", "error", "-fflags", "+genpts", "-i", src_path]
        codec_args += ["-map", f"0:a:{ai}"]

        if codec in AUDIO_TRANSCODE:
            codec_args += ["-c:a", AUDIO_TRANSCODE[codec]]
        else:
            codec_args += ["-c:a", "copy"]

        if codec in AUDIO_FORMAT_OVERRIDE:
            codec_args += ["-f", AUDIO_FORMAT_OVERRIDE[codec]]

        codec_args += [out_path]

        result = run_managed(codec_args, logger=logger)
        if result.succeeded and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            actual_audio_idx += 1
        elif logger:
            logger.warning(f"Audio track {ai} ({codec}) extraction failed for {clip.clip_id}")

    if actual_audio_idx == 0:
        return 0, []

    return actual_audio_idx, track_exts


def extract_subtitles(
    clip: Clip,
    src_path: str,
    encode_dir: str,
    logger: Optional[logging.Logger] = None,
) -> int:
    """Extract subtitle tracks from clip (skip DVD/VobSub, keep PGS/DVB).

    Args:
        clip: Clip metadata
        src_path: Source M2TS file path
        encode_dir: Output directory for subtitle files
        logger: Logger instance

    Returns:
        Number of subtitle tracks extracted
    """
    if not clip.subtitles or len(clip.subtitles) == 0:
        return 0

    sub_codecs = get_subtitle_codecs(clip, logger)
    sb_args = ["ffmpeg", "-y", "-v", "error", "-i", src_path]

    actual_sub_idx = 0
    for si, codec in enumerate(sub_codecs):
        # Skip non-BD-compatible subtitle codecs (DVD/VobSub)
        if codec in ("dvd_subtitle", "dvdsub", "dvd_sub", "dvd"):
            if logger:
                logger.debug(f"Skipping DVD subtitle track {si} (codec: {codec})")
            continue

        ext = subtitle_ext(codec)
        fmt = subtitle_format(codec)

        sb_args += ["-map", f"0:s:{si}", "-c", "copy"]
        if fmt:
            sb_args += ["-f", fmt]
        sb_args += [os.path.join(encode_dir, f"{clip.clip_id}_sub_{actual_sub_idx}{ext}")]
        actual_sub_idx += 1

    if actual_sub_idx == 0:
        return 0

    # Check if already extracted
    first_sub = (
        os.path.join(encode_dir, f"{clip.clip_id}_sub_0.sup") if actual_sub_idx > 0 else None
    )
    if skip_if_exists(out_file=first_sub):
        return actual_sub_idx

    result = run_managed(sb_args, logger=logger)

    if (
        result.succeeded
        and first_sub
        and os.path.isfile(first_sub)
        and os.path.getsize(first_sub) > 0
    ):
        return actual_sub_idx

    return 0


def encode_video_single_pass(
    clip: Clip,
    src_path: str,
    encode_dir: str,
    config: Config,
    is_main: bool = False,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Encode video in single-pass VBR (extras or fast main).

    Args:
        clip: Clip metadata
        src_path: Source M2TS file path
        encode_dir: Output directory
        config: Config with preset, bitrate, CRF settings
        is_main: If True, use main preset/bitrate; else use extras
        logger: Logger instance

    Returns:
        True if encoding succeeded
    """
    if clip.video is None or clip.video.height == 0:
        return False

    video_ext = "hevc" if config.codec == "hevc" else "h264"
    out_video = os.path.join(encode_dir, f"{clip.clip_id}_video.{video_ext}")

    # Check resumability
    if skip_if_exists(out_file=out_video):
        if logger:
            logger.info(f"Video already encoded: {out_video}")
        return True

    # Build filter graph (scale for extras if > 720p)
    video_filter = []
    if not is_main and clip.video.height > 720:
        video_filter = ["-vf", f"scale={config.extras_scale}"]

    # Codec and bitrate settings
    enc_lib = "libx265" if config.codec == "hevc" else "libx264"
    is_hevc = config.codec == "hevc"

    if is_main:
        # Main movie: use target bitrate
        if not config.main_bitrate:
            raise ValueError(
                "config.main_bitrate is empty; call budget.apply_bitrate_to_config() "
                "before encoding main clips"
            )
        preset = config.main_preset
        crf_or_bitrate = [
            "-b:v",
            config.main_bitrate,
            "-maxrate",
            config.main_maxrate,
            "-bufsize",
            config.main_bufsize,
        ]
    else:
        # Extras: use CRF
        preset = "medium"
        crf_or_bitrate = ["-crf", str(config.extras_crf)]

    for attempt in range(3):
        cmd = (
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-stats",
                "-i",
                src_path,
                "-map",
                "0:v:0",
                "-c:v",
                enc_lib,
                "-preset",
                preset,
            ]
            + crf_or_bitrate
            + video_filter
        )

        if is_hevc:
            if "-b:v" in cmd:
                # HEVC with bitrate: add maxrate/bufsize (already in crf_or_bitrate)
                pass
        else:
            # x264: add bluray-compat option
            cmd += ["-x264opts", "bluray-compat=1"]

        if config.threads > 0:
            cmd += ["-threads", str(config.threads)]

        cmd += ["-an", out_video]

        result = run_managed(cmd, nice=config.nice, logger=logger)

        if result.succeeded or (os.path.isfile(out_video) and os.path.getsize(out_video) > 0):
            if logger:
                logger.info("Single-pass encode succeeded")
            return True

        if attempt < 2:
            if logger:
                logger.warning(f"Attempt {attempt + 1} failed, retrying...")
            # Clean up partial output
            for f in glob.glob(out_video + "*"):
                try:
                    os.remove(f)
                except Exception:
                    pass

    if logger:
        logger.error("Single-pass encode failed after 3 attempts")
    return False


def encode_video_two_pass(
    clip: Clip,
    src_path: str,
    encode_dir: str,
    work_dir: str,
    config: Config,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Encode video in two-pass VBR (high-quality main movie).

    Args:
        clip: Clip metadata
        src_path: Source M2TS file path
        encode_dir: Output directory
        work_dir: Work directory for pass log files
        config: Config with preset, bitrate settings
        logger: Logger instance

    Returns:
        True if encoding succeeded
    """
    if clip.video is None or clip.video.height == 0:
        return False

    video_ext = "hevc" if config.codec == "hevc" else "h264"
    out_video = os.path.join(encode_dir, f"{clip.clip_id}_video.{video_ext}")
    pass_log = os.path.join(work_dir, f"pass_{clip.clip_id}.log")

    # Check resumability
    if skip_if_exists(out_file=out_video, pass_log_base=pass_log):
        if logger:
            logger.info(f"Video already encoded: {out_video}")
        return True

    enc_lib = "libx265" if config.codec == "hevc" else "libx264"
    is_hevc = config.codec == "hevc"
    preset = config.main_preset

    if not config.main_bitrate:
        raise ValueError(
            "config.main_bitrate is empty; call budget.apply_bitrate_to_config() "
            "before encoding main clips"
        )

    # Clean orphaned passlogs
    for f in glob.glob(pass_log + "*"):
        try:
            os.remove(f)
        except Exception:
            pass

    # Pass 1
    if logger:
        logger.info("Pass 1/2...")

    for attempt in range(3):
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-stats",
            "-i",
            src_path,
            "-map",
            "0:v:0",
            "-c:v",
            enc_lib,
            "-preset",
            preset,
            "-b:v",
            config.main_bitrate,
        ]
        if not is_hevc:
            cmd += ["-x264opts", "bluray-compat=1"]
        if config.threads > 0:
            cmd += ["-threads", str(config.threads)]
        cmd += ["-pass", "1", "-passlogfile", pass_log, "-an", "-f", "null", "/dev/null"]

        result = run_managed(cmd, nice=config.nice, logger=logger)

        if result.succeeded or glob.glob(pass_log + "*"):
            break  # Stats file exists = pass 1 finished

        if attempt < 2:
            if logger:
                logger.warning(f"Pass 1 attempt {attempt + 1} failed, retrying...")

    # Check if pass 1 succeeded
    if not glob.glob(pass_log + "*"):
        if logger:
            logger.error("Pass 1 failed")
        return False

    # Pass 2
    if logger:
        logger.info("Pass 2/2...")

    pass2_ok = False
    for attempt in range(3):
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-stats",
            "-i",
            src_path,
            "-map",
            "0:v:0",
            "-c:v",
            enc_lib,
            "-preset",
            preset,
            "-b:v",
            config.main_bitrate,
            "-maxrate",
            config.main_maxrate,
            "-bufsize",
            config.main_bufsize,
        ]
        if not is_hevc:
            cmd += ["-x264opts", "bluray-compat=1"]
        if config.threads > 0:
            cmd += ["-threads", str(config.threads)]
        cmd += ["-pass", "2", "-passlogfile", pass_log, "-an", out_video]

        result = run_managed(cmd, nice=config.nice, logger=logger)

        # Success if exit code 0 or partial output exists
        if result.succeeded or (os.path.isfile(out_video) and os.path.getsize(out_video) > 0):
            pass2_ok = True
            break

        if attempt < 2:
            if logger:
                logger.warning(f"Pass 2 attempt {attempt + 1} failed, retrying...")
            # Clean up partial output and passlogs
            for f in glob.glob(pass_log + "*"):
                try:
                    os.remove(f)
                except Exception:
                    pass

    # Clean up passlogs
    for f in glob.glob(pass_log + "*"):
        try:
            os.remove(f)
        except Exception:
            pass

    if pass2_ok:
        if logger:
            logger.info("Two-pass encode succeeded")
        return True
    else:
        if logger:
            logger.error("Pass 2 failed")
        # Clean up partial output
        if os.path.isfile(out_video):
            try:
                os.remove(out_video)
            except Exception:
                pass
        return False


def encode_clip(
    clip: Clip,
    clip_type: str,  # "extras" or "main"
    source_dir: str,
    encode_dir: str,
    work_dir: str,
    config: Config,
    total_clips: int,
    clip_idx: int,
    logger: Optional[logging.Logger] = None,
) -> EncodeStats:
    """Encode a single clip (audio, subtitles, video).

    Args:
        clip: Clip metadata
        clip_type: "extras" or "main"
        source_dir: Source STREAM directory (for M2TS paths)
        encode_dir: Output directory for encoded files
        work_dir: Work directory for temp files
        config: Config with codec, bitrate, preset settings
        total_clips: Total clips to encode (for progress display)
        clip_idx: Current clip index (for progress display)
        logger: Logger instance

    Returns:
        EncodeStats with results
    """
    src_path = os.path.join(source_dir, f"{clip.clip_id}.m2ts")

    if not os.path.isfile(src_path):
        if logger:
            logger.error(f"Source file not found: {src_path}")
        return EncodeStats(
            clip_name=clip.clip_id,
            clip_type=clip_type,
            audio_tracks=0,
            subtitle_tracks=0,
            video_encoded=False,
            success=False,
        )

    # Check if clip has video
    if clip.video is None or clip.video.height == 0:
        if logger:
            logger.warning(
                f"[{clip_idx}/{total_clips}] {clip_type.upper()}: {clip.clip_id}.m2ts — skipping (no video)"
            )
        return EncodeStats(
            clip_name=clip.clip_id,
            clip_type=clip_type,
            audio_tracks=0,
            subtitle_tracks=0,
            video_encoded=False,
            success=True,
        )

    if logger:
        logger.info(f"[{clip_idx}/{total_clips}] {clip_type.upper()}: {clip.clip_id}.m2ts")

    # Extract audio
    audio_tracks, audio_exts = extract_audio(clip, src_path, encode_dir, logger)
    if audio_tracks == 0:
        if logger:
            logger.info("  (video-only)")

    # Extract subtitles
    subtitle_tracks = extract_subtitles(clip, src_path, encode_dir, logger)

    # Encode video
    is_main = clip_type == "main"

    if config.main_passes == 1:
        video_ok = encode_video_single_pass(clip, src_path, encode_dir, config, is_main, logger)
    else:
        video_ok = encode_video_two_pass(clip, src_path, encode_dir, work_dir, config, logger)

    if video_ok:
        if logger:
            logger.info(f"  done ({audio_tracks} audio, {subtitle_tracks} subtitle)")

    return EncodeStats(
        clip_name=clip.clip_id,
        clip_type=clip_type,
        audio_tracks=audio_tracks,
        subtitle_tracks=subtitle_tracks,
        video_encoded=video_ok,
        success=video_ok,
    )


def encode_all(
    inventory: Inventory,
    extras_clips: list[str],
    main_clips: list[str],
    source_dir: str,
    encode_dir: str,
    work_dir: str,
    config: Config,
    no_extras: bool = False,
    logger: Optional[logging.Logger] = None,
) -> list[EncodeStats]:
    """Encode all extras and main clips.

    Args:
        inventory: Inventory with clip metadata
        extras_clips: List of clip names to encode as extras
        main_clips: List of clip names to encode as main
        source_dir: Source STREAM directory
        encode_dir: Output directory
        work_dir: Work directory
        config: Config with encoding settings
        no_extras: If True, skip extras encoding
        logger: Logger instance

    Returns:
        List of EncodeStats for all clips
    """
    stats = []

    # Build clip lookup (inventory.clips is already a dict)
    clip_map = inventory.clips

    total_clips = len(extras_clips) + len(main_clips)
    if no_extras:
        total_clips = len(main_clips)

    clip_idx = 0

    # Encode extras
    if not no_extras and not config.movie_only:
        for clip_name in extras_clips:
            clip = clip_map.get(clip_name)
            if not clip:
                if logger:
                    logger.warning(f"Clip not found in inventory: {clip_name}")
                continue

            clip_idx += 1
            stat = encode_clip(
                clip,
                "extras",
                source_dir,
                encode_dir,
                work_dir,
                config,
                total_clips,
                clip_idx,
                logger,
            )
            stats.append(stat)

    # Encode main movie
    if main_clips:
        for clip_name in main_clips:
            clip = clip_map.get(clip_name)
            if not clip:
                if logger:
                    logger.warning(f"Clip not found in inventory: {clip_name}")
                continue

            clip_idx += 1
            stat = encode_clip(
                clip,
                "main",
                source_dir,
                encode_dir,
                work_dir,
                config,
                total_clips,
                clip_idx,
                logger,
            )
            stats.append(stat)

    if logger:
        logger.info("Encoding complete")

    return stats
