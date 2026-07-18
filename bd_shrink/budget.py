"""Budget: calculate target bitrates and space estimates.

Determines how much bitrate to allocate to the main movie based on:
  - Target disc size (BD25 = 23 GB)
  - Overhead for menus/extras/subtitles
  - Audio stream sizes (passthrough, using source bitrates or fallbacks)
  - Unique main movie clips (deduped for seamless branching per B9 fix)
"""

from bd_shrink import audio
from bd_shrink.classify import count_main_clips_unique
from bd_shrink.inventory import Inventory


def estimate_audio_size(inventory: Inventory, clip_ids: list[str], is_main: bool = False) -> int:
    """Estimate total audio data size in bytes.

    Sums audio bitrate * duration for all clips and tracks.
    Uses source bitrates (passthrough) or fallback table estimates.

    Args:
        inventory: Full inventory
        clip_ids: List of clip IDs to sum over
        is_main: If True, use main audio defaults; else extras defaults

    Returns:
        Estimated audio size in bytes
    """
    total_bits = 0

    for clip_id in clip_ids:
        if clip_id not in inventory.clips:
            continue

        clip = inventory.clips[clip_id]

        for audio_track in clip.audio:
            codec = audio_track.codec_name
            source_bitrate = audio_track.bit_rate

            # Skip MPEG audio
            if audio.should_skip(codec):
                continue

            # Estimate bitrate for this track
            track_bitrate = audio.estimate_bitrate(codec, source_bitrate, is_main)

            # Duration comes from clip
            duration_sec = clip.duration_sec

            # Add: duration * bitrate
            total_bits += int(duration_sec * track_bitrate)

    return total_bits // 8  # Convert bits to bytes


def estimate_subtitle_size(inventory: Inventory, clip_ids: list[str]) -> int:
    """Estimate total subtitle data size in bytes.

    Rough estimate: PGS subtitles ~50 kbit/s, DVB ~10 kbit/s.

    Args:
        inventory: Full inventory
        clip_ids: List of clip IDs to sum over

    Returns:
        Estimated subtitle size in bytes
    """
    total_bits = 0

    for clip_id in clip_ids:
        if clip_id not in inventory.clips:
            continue

        clip = inventory.clips[clip_id]
        duration_sec = clip.duration_sec

        for sub_track in clip.subtitles:
            codec = sub_track.codec_name

            # Estimate bitrate by codec
            if "pgs" in codec.lower():
                bitrate = 50_000  # 50 kbit/s for PGS
            elif "dvb" in codec.lower():
                bitrate = 10_000  # 10 kbit/s for DVB
            else:
                bitrate = 20_000  # Default 20 kbit/s

            total_bits += int(duration_sec * bitrate)

    return total_bits // 8


def calculate_budget(
    inventory: Inventory,
    main_playlist_ids: list[str],
    extras_playlist_ids: list[str],
    menu_playlist_ids: list[str],
    target_gb: float = 23.0,
    overhead_mb: float = 200.0,
) -> dict:
    """Calculate bitrate budget for main movie.

    Algorithm (from AGENTS.md B9):
    1. Sum unique main clips by duration (dedup for seamless branching)
    2. Estimate menu + extras + audio sizes
    3. Calculate target bitrate = (target_gb - overhead - menu/extras/audio) / main_duration

    Args:
        inventory: Full inventory
        main_playlist_ids: List of main movie playlist IDs
        extras_playlist_ids: List of extras playlist IDs
        menu_playlist_ids: List of menu playlist IDs
        target_gb: Target disc size (default 23 for BD25)
        overhead_mb: Overhead for container/filesystem (default 200 MB)

    Returns:
        Dict with:
          - main_duration_sec: total duration of unique main clips
          - main_bitrate_kbps: target bitrate for main movie
          - audio_size_mb: estimated audio size
          - subtitle_size_mb: estimated subtitle size
          - menu_size_mb: estimated menu + extras size
          - available_video_mb: available for main video
    """
    # Convert target to bytes
    target_bytes = target_gb * (1024**3)
    overhead_bytes = overhead_mb * (1024**2)

    # Collect all clip IDs
    main_clips = []
    extras_clips = []
    menu_clips = []

    for pl_id in main_playlist_ids:
        if pl_id in inventory.playlists:
            main_clips.extend(inventory.playlists[pl_id].clips)

    for pl_id in extras_playlist_ids:
        if pl_id in inventory.playlists:
            extras_clips.extend(inventory.playlists[pl_id].clips)

    for pl_id in menu_playlist_ids:
        if pl_id in inventory.playlists:
            menu_clips.extend(inventory.playlists[pl_id].clips)

    # Get unique main clips + duration (per B9 fix)
    main_clip_count, main_duration_sec = count_main_clips_unique(inventory, main_playlist_ids)

    # Estimate component sizes
    main_audio_bytes = estimate_audio_size(inventory, main_clips, is_main=True)
    extras_audio_bytes = estimate_audio_size(inventory, extras_clips, is_main=False)
    menu_audio_bytes = estimate_audio_size(inventory, menu_clips, is_main=False)

    main_subtitle_bytes = estimate_subtitle_size(inventory, main_clips)
    extras_subtitle_bytes = estimate_subtitle_size(inventory, extras_clips)
    menu_subtitle_bytes = estimate_subtitle_size(inventory, menu_clips)

    # Video for extras/menus (assume source bitrates, typically 4-8 Mbps)
    # This is rough; actual varies, but let's use 6 Mbps as default
    extras_video_bitrate = 6_000_000  # 6 Mbps
    menu_video_bitrate = 4_000_000  # 4 Mbps

    extras_duration = (
        sum(
            inventory.clips[cid].duration_sec
            for pl_id in extras_playlist_ids
            for cid in inventory.playlists.get(pl_id, {}).clips
            if cid in inventory.clips
        )
        if extras_clips
        else 0.0
    )

    menu_duration = (
        sum(
            inventory.clips[cid].duration_sec
            for pl_id in menu_playlist_ids
            for cid in inventory.playlists.get(pl_id, {}).clips
            if cid in inventory.clips
        )
        if menu_clips
        else 0.0
    )

    extras_video_bytes = int(extras_duration * extras_video_bitrate / 8)
    menu_video_bytes = int(menu_duration * menu_video_bitrate / 8)

    # Total non-main size
    non_main_bytes = (
        main_audio_bytes
        + main_subtitle_bytes
        + extras_video_bytes
        + extras_audio_bytes
        + extras_subtitle_bytes
        + menu_video_bytes
        + menu_audio_bytes
        + menu_subtitle_bytes
        + overhead_bytes
    )

    available_video_bytes = target_bytes - non_main_bytes

    # Warn if negative budget
    if available_video_bytes < 0:
        available_video_bytes = max(available_video_bytes, 1_000_000_000)  # At least 1 GB

    # Calculate target bitrate for main video
    if main_duration_sec > 0:
        main_bitrate_bits = (available_video_bytes * 8) / main_duration_sec
        main_bitrate_kbps = int(main_bitrate_bits / 1000)
    else:
        main_bitrate_kbps = 0

    return {
        "main_duration_sec": main_duration_sec,
        "main_clip_count": main_clip_count,
        "main_bitrate_kbps": main_bitrate_kbps,
        "audio_size_mb": (main_audio_bytes + extras_audio_bytes + menu_audio_bytes) / (1024**2),
        "subtitle_size_mb": (main_subtitle_bytes + extras_subtitle_bytes + menu_subtitle_bytes)
        / (1024**2),
        "menu_and_extras_video_mb": (extras_video_bytes + menu_video_bytes) / (1024**2),
        "available_video_mb": available_video_bytes / (1024**2),
        "target_gb": target_gb,
        "overhead_mb": overhead_mb,
    }


def apply_bitrate_to_config(config, main_bitrate_kbps: int) -> None:
    """Populate config.main_bitrate/maxrate/bufsize from the budgeted kbps.

    ffmpeg needs bitrate/maxrate/bufsize as strings (e.g. "8000k"). maxrate is
    set to the target bitrate (constrained VBR to stay within the disc budget);
    bufsize is 2x the bitrate, a standard VBV buffer for Blu-ray-compatible VBR.

    Args:
        config: Config object to mutate
        main_bitrate_kbps: target main-movie bitrate in kbps from calculate_budget
    """
    kbps = max(int(main_bitrate_kbps), 1)
    config.main_bitrate = f"{kbps}k"
    config.main_maxrate = f"{kbps}k"
    config.main_bufsize = f"{kbps * 2}k"
