"""Rebuild phase: tsMuxeR authoring and surgical clip replacement."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bd_shrink.config import Config
from bd_shrink.runner import find_tool, run_managed, run_simple


@dataclass
class RebuildStats:
    """Statistics for rebuild operation."""
    mode: str  # "movie_only" or "surgical"
    video_tracks: int
    audio_tracks_max: int
    subtitle_tracks_max: int
    remuxed_clips: int
    copied_clips: int
    success: bool


def audio_tsmuxer_type(audio_file: str) -> str:
    """Map audio file extension to tsMuxeR audio type.
    
    Args:
        audio_file: Path or filename with extension (e.g., "clip_audio_0.ac3")
    
    Returns:
        tsMuxeR audio type (e.g., "A_AC3", "A_DTS", "A_TRUEHD")
    """
    ext = Path(audio_file).suffix.lower().lstrip(".")
    
    ext_to_type = {
        "ac3": "A_AC3",
        "eac3": "A_EAC3",
        "dts": "A_DTS",
        "thd": "A_TRUEHD",
        "wav": "A_LPCM",
    }
    
    return ext_to_type.get(ext, "A_AC3")  # Default to AC3


def video_tsmuxer_type(codec: str, is_hevc: bool) -> str:
    """Map video codec to tsMuxeR video type.
    
    Args:
        codec: Codec name (h264 or hevc)
        is_hevc: If True, use HEVC; else H.264
    
    Returns:
        tsMuxeR video type
    """
    return "V_MPEGH/ISO/HEVC" if is_hevc else "V_MPEG4/ISO/AVC"


def count_audio_in_clip(encode_dir: str, clip_id: str) -> int:
    """Count audio tracks for a clip in encode directory.
    
    Args:
        encode_dir: Path to encode directory
        clip_id: Clip ID (e.g., "00000")
    
    Returns:
        Number of audio files found
    """
    count = 0
    for idx in range(10):  # Max 10 audio tracks
        # Check for common audio extensions
        for ext in [".ac3", ".eac3", ".dts", ".thd", ".wav"]:
            if os.path.isfile(os.path.join(encode_dir, f"{clip_id}_audio_{idx}{ext}")):
                count += 1
                break
    return count


def count_subtitles_in_clip(encode_dir: str, clip_id: str) -> int:
    """Count subtitle tracks for a clip in encode directory.
    
    Args:
        encode_dir: Path to encode directory
        clip_id: Clip ID (e.g., "00000")
    
    Returns:
        Number of subtitle files found
    """
    count = 0
    for idx in range(10):  # Max 10 subtitle tracks
        if os.path.isfile(os.path.join(encode_dir, f"{clip_id}_sub_{idx}.sup")):
            count += 1
        elif os.path.isfile(os.path.join(encode_dir, f"{clip_id}_sub_{idx}.srt")):
            # SRT skipped in tsMuxeR (no font rendering on Linux)
            break
    return count


def find_audio_file(encode_dir: str, clip_id: str, audio_idx: int) -> Optional[str]:
    """Find audio file for a specific track index.
    
    Args:
        encode_dir: Path to encode directory
        clip_id: Clip ID
        audio_idx: Audio track index
    
    Returns:
        Full path to audio file, or None if not found
    """
    for ext in [".ac3", ".eac3", ".dts", ".thd", ".wav"]:
        path = os.path.join(encode_dir, f"{clip_id}_audio_{audio_idx}{ext}")
        if os.path.isfile(path):
            return path
    return None


def write_tsmuxer_meta_movie_only(
    meta_file: str,
    encode_dir: str,
    main_clips: list[str],
    main_fps: str,
    video_ext: str,
    is_hevc: bool,
    logger: Optional[logging.Logger] = None,
) -> tuple[bool, int, int]:
    """Write tsMuxeR metafile for movie-only mode.
    
    Args:
        meta_file: Path to write metafile
        encode_dir: Encode directory with video/audio/subtitle files
        main_clips: List of main clip IDs
        main_fps: Frame rate string (e.g., "23.976")
        video_ext: Video extension (h264 or hevc)
        is_hevc: If True, use HEVC codec
        logger: Logger instance
    
    Returns:
        Tuple (success, max_audio_tracks, max_subtitle_tracks)
    """
    video_type = video_tsmuxer_type("hevc" if is_hevc else "h264", is_hevc)
    
    # Count max audio/subtitle tracks
    max_audio = 0
    max_subs = 0
    for clip_id in main_clips:
        a = count_audio_in_clip(encode_dir, clip_id)
        s = count_subtitles_in_clip(encode_dir, clip_id)
        if a > max_audio:
            max_audio = a
        if s > max_subs:
            max_subs = s
    
    try:
        with open(meta_file, "w") as f:
            # MUXOPT header
            f.write("MUXOPT --no-pcr-on-video-pid --new-audio-pes --vbr --blu-ray\n")
            
            # Video tracks
            for i, clip_id in enumerate(main_clips):
                video_path = os.path.join(encode_dir, f"{clip_id}_video.{video_ext}")
                if not os.path.isfile(video_path):
                    if logger:
                        logger.warning(f"Video file not found: {video_path}")
                    continue
                
                if i == 0:
                    f.write(f'{video_type}, "{video_path}", fps={main_fps}, insertSEI, contSPS\n')
                else:
                    f.write(f'+{video_type}, "{video_path}", fps={main_fps}, insertSEI, contSPS\n')
            
            # Audio tracks (grouped by track index)
            for audio_idx in range(max_audio):
                first_in_group = True
                for clip_id in main_clips:
                    audio_path = find_audio_file(encode_dir, clip_id, audio_idx)
                    if not audio_path:
                        continue
                    
                    audio_type = audio_tsmuxer_type(audio_path)
                    prefix = "" if first_in_group else "+"
                    f.write(f'{prefix}{audio_type}, "{audio_path}"\n')
                    first_in_group = False
            
            # Subtitle tracks (grouped by track index)
            for sub_idx in range(max_subs):
                first_in_group = True
                for clip_id in main_clips:
                    sub_path = os.path.join(encode_dir, f"{clip_id}_sub_{sub_idx}.sup")
                    if not os.path.isfile(sub_path):
                        continue
                    
                    prefix = "" if first_in_group else "+"
                    f.write(f'{prefix}S_HDMV/PGS, "{sub_path}"\n')
                    first_in_group = False
        
        return True, max_audio, max_subs
    
    except Exception as e:
        if logger:
            logger.error(f"Failed to write tsMuxeR metafile: {e}")
        return False, 0, 0


def write_tsmuxer_meta_surgical(
    meta_file: str,
    encode_dir: str,
    clip_id: str,
    main_fps: str,
    video_ext: str,
    is_hevc: bool,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Write tsMuxeR metafile for surgical remux of a single clip.
    
    Args:
        meta_file: Path to write metafile
        encode_dir: Encode directory
        clip_id: Clip ID to remux
        main_fps: Frame rate string
        video_ext: Video extension
        is_hevc: If True, use HEVC codec
        logger: Logger instance
    
    Returns:
        True if metafile written successfully
    """
    video_type = video_tsmuxer_type("hevc" if is_hevc else "h264", is_hevc)
    
    try:
        with open(meta_file, "w") as f:
            # MUXOPT header
            f.write("MUXOPT --no-pcr-on-video-pid --new-audio-pes --vbr --blu-ray\n")
            
            # Video track
            video_path = os.path.join(encode_dir, f"{clip_id}_video.{video_ext}")
            f.write(f'{video_type}, "{video_path}", fps={main_fps}, insertSEI, contSPS\n')
            
            # Audio tracks
            audio_idx = 0
            while True:
                audio_path = find_audio_file(encode_dir, clip_id, audio_idx)
                if not audio_path:
                    break
                audio_type = audio_tsmuxer_type(audio_path)
                f.write(f'{audio_type}, "{audio_path}"\n')
                audio_idx += 1
            
            # Subtitle tracks
            sub_idx = 0
            while True:
                sub_path = os.path.join(encode_dir, f"{clip_id}_sub_{sub_idx}.sup")
                if not os.path.isfile(sub_path):
                    break
                f.write(f'S_HDMV/PGS, "{sub_path}"\n')
                sub_idx += 1
        
        return True
    
    except Exception as e:
        if logger:
            logger.error(f"Failed to write tsMuxeR metafile: {e}")
        return False


def rebuild_movie_only(
    encode_dir: str,
    output_dir: str,
    work_dir: str,
    main_clips: list[str],
    config: Config,
    main_fps: str = "23.976",
    logger: Optional[logging.Logger] = None,
) -> RebuildStats:
    """Rebuild in movie-only mode: fresh BD with tsMuxeR authoring.
    
    Args:
        encode_dir: Directory with encoded clips
        output_dir: Output BDMV directory
        work_dir: Work directory for metafiles
        main_clips: List of main clip IDs
        config: Config with codec settings
        main_fps: Frame rate
        logger: Logger instance
    
    Returns:
        RebuildStats with results
    """
    if logger:
        logger.info("Movie-only mode: authoring fresh BD...")
    
    is_hevc = config.codec == "hevc"
    video_ext = "hevc" if is_hevc else "h264"
    
    # Create metafile
    meta_dir = os.path.join(work_dir, "meta")
    os.makedirs(meta_dir, exist_ok=True)
    meta_file = os.path.join(meta_dir, "movie.meta")
    
    success, max_audio, max_subs = write_tsmuxer_meta_movie_only(
        meta_file,
        encode_dir,
        main_clips,
        main_fps,
        video_ext,
        is_hevc,
        logger,
    )
    
    if not success:
        return RebuildStats(
            mode="movie_only",
            video_tracks=0,
            audio_tracks_max=0,
            subtitle_tracks_max=0,
            remuxed_clips=0,
            copied_clips=0,
            success=False,
        )
    
    if logger:
        logger.info("Running tsMuxeR...")

    # Resolve tsMuxeR full path (systemd-run uses a restricted PATH).
    tsmuxer = find_tool("tsMuxeR")
    if tsmuxer is None:
        if logger:
            logger.error("tsMuxeR not found on PATH")
        return RebuildStats(
            mode="movie_only",
            video_tracks=len(main_clips),
            audio_tracks_max=max_audio,
            subtitle_tracks_max=max_subs,
            remuxed_clips=0,
            copied_clips=0,
            success=False,
        )

    # Run tsMuxeR
    result = run_managed(
        [tsmuxer, meta_file, output_dir],
        nice=config.nice,
        logger=logger,
    )
    
    if not result.succeeded:
        if logger:
            logger.error(f"tsMuxeR failed: {result.stderr}")
        return RebuildStats(
            mode="movie_only",
            video_tracks=len(main_clips),
            audio_tracks_max=max_audio,
            subtitle_tracks_max=max_subs,
            remuxed_clips=0,
            copied_clips=0,
            success=False,
        )
    
    if logger:
        logger.info(f"BDMV folder created: {output_dir}")
    
    return RebuildStats(
        mode="movie_only",
        video_tracks=len(main_clips),
        audio_tracks_max=max_audio,
        subtitle_tracks_max=max_subs,
        remuxed_clips=0,
        copied_clips=0,
        success=True,
    )


def rebuild_surgical(
    source_dir: str,
    encode_dir: str,
    output_dir: str,
    work_dir: str,
    main_clips: list[str],
    extras_clips: list[str],
    config: Config,
    clip_fps_map: dict[str, str],
    no_extras: bool = False,
    logger: Optional[logging.Logger] = None,
) -> RebuildStats:
    """Rebuild in surgical mode: preserve menus, replace encoded clips.
    
    Args:
        source_dir: Source BDMV directory
        encode_dir: Directory with encoded clips
        output_dir: Output BDMV directory
        work_dir: Work directory
        main_clips: List of main clip IDs
        extras_clips: List of extra clip IDs
        config: Config with codec settings
        clip_fps_map: Map of clip_id -> fps string
        no_extras: If True, skip extras
        logger: Logger instance
    
    Returns:
        RebuildStats with results
    """
    if logger:
        logger.info("Surgical mode: preserving menus, replacing clips...")
    
    is_hevc = config.codec == "hevc"
    video_ext = "hevc" if is_hevc else "h264"

    # When no_extras is set, extras are neither remuxed nor copied — only the
    # main movie clips are replaced. Compute the effective remux set once so the
    # loop, the verbatim-copy pass, and the orphan pass all agree.
    if no_extras:
        remux_clips = list(main_clips)
    else:
        # Preserve order, dedup (a clip may appear in both lists).
        remux_clips = list(dict.fromkeys(main_clips + extras_clips))

    # Resolve tsMuxeR full path once (systemd-run uses a restricted PATH).
    tsmuxer = find_tool("tsMuxeR")
    if tsmuxer is None:
        if logger:
            logger.error("tsMuxeR not found on PATH")
        return RebuildStats(
            mode="surgical",
            video_tracks=0,
            audio_tracks_max=0,
            subtitle_tracks_max=0,
            remuxed_clips=0,
            copied_clips=0,
            success=False,
        )
    
    # Create output directories
    os.makedirs(os.path.join(output_dir, "BDMV/PLAYLIST"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "BDMV/CLIPINF"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "BDMV/STREAM"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "BDMV/BACKUP/PLAYLIST"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "BDMV/BACKUP/CLIPINF"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "CERTIFICATE"), exist_ok=True)
    
    # Copy source BD metadata
    for meta_file in ["index.bdmv", "MovieObject.bdmv"]:
        src = os.path.join(source_dir, meta_file)
        dst = os.path.join(output_dir, "BDMV", meta_file)
        if os.path.isfile(src):
            result = run_simple(["cp", src, dst], logger=logger)
    
    # Copy certificates
    cert_src = os.path.join(source_dir, "..", "CERTIFICATE")
    if os.path.isdir(cert_src):
        run_simple(["cp", "-r", cert_src, os.path.join(output_dir, "CERTIFICATE")], logger=logger)
    
    # Remux encoded clips
    rebuild_dir = os.path.join(work_dir, "rebuild")
    os.makedirs(rebuild_dir, exist_ok=True)
    remuxed_count = 0
    
    for clip_id in remux_clips:
        video_path = os.path.join(encode_dir, f"{clip_id}_video.{video_ext}")
        if not os.path.isfile(video_path):
            continue  # Not re-encoded
        
        if logger:
            logger.info(f"Remuxing: {clip_id}.m2ts")
        
        fps = clip_fps_map.get(clip_id, "23.976")
        meta_file = os.path.join(rebuild_dir, f"{clip_id}.meta")
        
        if not write_tsmuxer_meta_surgical(
            meta_file,
            encode_dir,
            clip_id,
            fps,
            video_ext,
            is_hevc,
            logger,
        ):
            if logger:
                logger.warning(f"Failed to write metafile for {clip_id}")
            continue
        
        # Create temp output directory
        tmpout = os.path.join(rebuild_dir, f"{clip_id}_output")
        os.makedirs(tmpout, exist_ok=True)
        
        # Run tsMuxeR
        result = run_managed([tsmuxer, meta_file, tmpout], nice=config.nice, logger=logger)
        
        if not result.succeeded:
            if logger:
                logger.warning(f"tsMuxeR failed for {clip_id}")
            continue
        
        # Copy remuxed output to destination
        new_m2ts_path = os.path.join(tmpout, "BDMV/STREAM", f"{clip_id}.m2ts")
        new_clpi_path = os.path.join(tmpout, "BDMV/CLIPINF", f"{clip_id}.clpi")
        
        if os.path.isfile(new_m2ts_path) and os.path.isfile(new_clpi_path):
            dst_m2ts = os.path.join(output_dir, "BDMV/STREAM", f"{clip_id}.m2ts")
            dst_clpi = os.path.join(output_dir, "BDMV/CLIPINF", f"{clip_id}.clpi")
            run_simple(["cp", new_m2ts_path, dst_m2ts], logger=logger)
            run_simple(["cp", new_clpi_path, dst_clpi], logger=logger)
            remuxed_count += 1
            if logger:
                logger.info(f"  done: {clip_id}.m2ts")
    
    # Copy un-encoded clips verbatim
    copied_count = 0
    if logger:
        logger.info("Copying un-encoded clips...")

    # Clips that belong on the output disc. When no_extras is set, extras are
    # excluded entirely (not remuxed above, not copied here).
    wanted_clips = set(remux_clips)

    # Copy from source
    source_stream = os.path.join(source_dir, "STREAM")
    if os.path.isdir(source_stream):
        for m2ts_file in os.listdir(source_stream):
            if not m2ts_file.endswith(".m2ts"):
                continue
            
            clip_id = m2ts_file[:-5]  # Remove .m2ts
            if clip_id not in wanted_clips:
                continue
            
            # Skip if already remuxed
            if os.path.isfile(os.path.join(output_dir, "BDMV/STREAM", m2ts_file)):
                continue
            
            src = os.path.join(source_stream, m2ts_file)
            dst = os.path.join(output_dir, "BDMV/STREAM", m2ts_file)
            src_clpi = os.path.join(source_dir, "CLIPINF", f"{clip_id}.clpi")
            dst_clpi = os.path.join(output_dir, "BDMV/CLIPINF", f"{clip_id}.clpi")
            
            run_simple(["cp", src, dst], logger=logger)
            if os.path.isfile(src_clpi):
                run_simple(["cp", src_clpi, dst_clpi], logger=logger)
            copied_count += 1
    
    # Copy MPLS files
    source_mpls = os.path.join(source_dir, "PLAYLIST")
    if os.path.isdir(source_mpls):
        for mpls_file in os.listdir(source_mpls):
            if mpls_file.endswith(".mpls"):
                src = os.path.join(source_mpls, mpls_file)
                dst = os.path.join(output_dir, "BDMV/PLAYLIST", mpls_file)
                run_simple(["cp", src, dst], logger=logger)
    
    if logger:
        logger.info(f"Surgical rebuild complete: {remuxed_count} remuxed, {copied_count} copied")
    
    return RebuildStats(
        mode="surgical",
        video_tracks=len(main_clips),
        audio_tracks_max=0,  # Would need per-clip stats
        subtitle_tracks_max=0,
        remuxed_clips=remuxed_count,
        copied_clips=copied_count,
        success=True,
    )
