"""Audio codec handling: passthrough, extension maps, bitrate estimation."""

# Map audio codec names to file extensions (for passthrough storage)
CODEC_TO_EXT = {
    "ac3": ".ac3",
    "eac3": ".eac3",
    "dts": ".dts",
    "truehd": ".thd",
    "pcm_bluray": ".w64",
    "pcm_s16be": ".wav",
    "pcm_s24be": ".wav",
    "pcm_s16le": ".wav",
    "pcm_s24le": ".wav",
}

# Audio codecs that need explicit -f format flag for ffmpeg extraction
AUDIO_FORMAT_OVERRIDE = {
    "truehd": "truehd",
    "pcm_bluray": "w64",
}

# Audio codecs that can't be stream-copied — must be transcoded
AUDIO_TRANSCODE = {
    "pcm_bluray": "pcm_s24be",
}

# Map subtitle codec names to file extensions
SUBTITLE_CODEC_TO_EXT = {
    "hdmv_pgs_subtitle": ".sup",
    "pgs_subtitle": ".sup",
    "subrip": ".srt",
}

# Map subtitle codec names to ffmpeg -f format specifier
SUBTITLE_CODEC_TO_FORMAT = {
    "hdmv_pgs_subtitle": "sup",
    "pgs_subtitle": "sup",
    "subrip": "srt",
}
EXT_TO_TSMUXER_TYPE = {
    ".ac3": "A_AC3",
    ".eac3": "A_EAC3",
    ".dts": "A_DTS",
    ".thd": "A_TRUEHD",
    ".wav": "A_LPCM",
    ".w64": "A_LPCM",
}

# Fallback bitrates (bytes/sec) for codecs when ffprobe returns 0
BITRATE_FALLBACKS = {
    "dts": 1_509_000,
    "truehd": 2_000_000,
    "eac3": 1_536_000,
    "pcm_bluray": 4_608_000,
    # Extras default when bitrate unknown
    "default_extras": 256_000,
    # Main default when bitrate unknown
    "default_main": 640_000,
}

# MPEG audio codecs are skipped (not encoded/passed through)
SKIP_CODECS = {"mp3", "mp3float", "mp2", "mp2float"}


def audio_ext(codec: str) -> str:
    """Return file extension for an audio codec.

    Args:
        codec: Codec name from ffprobe (e.g., 'ac3', 'dts', 'truehd')

    Returns:
        File extension including dot (e.g., '.ac3', '.dts', '.wav')
    """
    return CODEC_TO_EXT.get(codec, ".ac3")


def tsmuxer_type(ext_or_codec: str) -> str:
    """Return tsMuxeR audio track type for a codec or extension.

    Args:
        ext_or_codec: Either a file extension (e.g., '.ac3') or codec name

    Returns:
        tsMuxeR audio type (e.g., 'A_AC3', 'A_DTS', 'A_TRUEHD')
    """
    # If it looks like an extension, use it directly
    if ext_or_codec.startswith("."):
        return EXT_TO_TSMUXER_TYPE.get(ext_or_codec, "A_AC3")

    # Otherwise, convert codec to extension first, then to type
    ext = audio_ext(ext_or_codec)
    return EXT_TO_TSMUXER_TYPE.get(ext, "A_AC3")


def subtitle_ext(codec: str) -> str:
    """Return file extension for a subtitle codec.

    Args:
        codec: Codec name from ffprobe (e.g., 'hdmv_pgs_subtitle')

    Returns:
        File extension including dot (e.g., '.sup', '.srt')
    """
    return SUBTITLE_CODEC_TO_EXT.get(codec, ".sup")


def subtitle_format(codec: str) -> str:
    """Return ffmpeg -f format specifier for a subtitle codec.

    Args:
        codec: Codec name from ffprobe

    Returns:
        ffmpeg format string (e.g., 'sup', 'srt') or empty string if unknown
    """
    return SUBTITLE_CODEC_TO_FORMAT.get(codec, "")


def should_skip(codec: str) -> bool:
    """Check if an audio codec should be skipped (MPEG audio).

    Args:
        codec: Codec name from ffprobe

    Returns:
        True if codec should be skipped, False otherwise
    """
    return codec in SKIP_CODECS


def estimate_bitrate(codec: str, source_bitrate: int = 0, is_main: bool = False) -> int:
    """Estimate audio bitrate for budget calculation.

    When ffprobe reports a source bitrate > 0, use it (passthrough uses source bitrate).
    When ffprobe reports 0, use codec-specific fallback.
    MPEG audio should not be passed to this function; they're skipped before budget calc.

    Args:
        codec: Codec name from ffprobe
        source_bitrate: Bitrate in bits/sec from ffprobe (0 if unknown)
        is_main: If True, use main default fallback; if False, use extras default

    Returns:
        Bitrate in bits/sec for budget calculations
    """
    if source_bitrate > 0:
        return source_bitrate

    # Use codec-specific fallback if available
    if codec in BITRATE_FALLBACKS:
        return BITRATE_FALLBACKS[codec]

    # Use default based on context (main vs extras)
    if is_main:
        return BITRATE_FALLBACKS["default_main"]
    else:
        return BITRATE_FALLBACKS["default_extras"]


def count_audio(encode_dir: str, clip_id: str) -> int:
    """Count audio tracks for a clip by checking for multiple extensions.

    Scans encode_dir for files matching <clip_id>_audio_<N>.<ext>
    where <ext> is one of: ac3, eac3, dts, thd, wav

    Args:
        encode_dir: Path to directory containing encoded clips
        clip_id: Clip ID (e.g., '00000')

    Returns:
        Number of audio tracks found (0 if none)
    """
    import os

    count = 0
    while True:
        found = False
        for ext in ["ac3", "eac3", "dts", "thd", "wav", "w64"]:
            candidate = os.path.join(encode_dir, f"{clip_id}_audio_{count}.{ext}")
            if os.path.isfile(candidate):
                found = True
                break
        if not found:
            break
        count += 1

    return count


def find_audio(encode_dir: str, clip_id: str, audio_idx: int) -> str:
    """Find audio file for a clip + track index.

    Args:
        encode_dir: Path to directory containing encoded clips
        clip_id: Clip ID (e.g., '00000')
        audio_idx: Audio track index (0, 1, 2, ...)

    Returns:
        Full path to the audio file if found

    Raises:
        FileNotFoundError if no matching audio file exists
    """
    import os

    for ext in ["ac3", "eac3", "dts", "thd", "wav", "w64"]:
        candidate = os.path.join(encode_dir, f"{clip_id}_audio_{audio_idx}.{ext}")
        if os.path.isfile(candidate):
            return candidate

    raise FileNotFoundError(f"Audio track not found: {clip_id}_audio_{audio_idx}.<ext>")


def get_audio_tracks_from_clip_data(
    clip_audio_list: list, is_main: bool = False
) -> tuple[int, int]:
    """Count audio tracks and estimate total bitrate for a clip.

    Counts non-MPEG audio tracks and calculates total bitrate from source rates.
    Used in budget calculation.

    Args:
        clip_audio_list: List of audio track dicts from inventory (each has
                        'codec_name'/'codec' and optional 'bit_rate' fields)
        is_main: If True, use main default fallback; if False, use extras

    Returns:
        Tuple of (audio_count, total_bitrate_bits_per_sec)
    """
    count = 0
    total_bitrate = 0

    for audio_track in clip_audio_list:
        codec = audio_track.get("codec_name") or audio_track.get("codec", "")

        if should_skip(codec):
            continue

        count += 1
        source_bitrate = audio_track.get("bit_rate", 0) or 0
        bitrate = estimate_bitrate(codec, source_bitrate, is_main)
        total_bitrate += bitrate

    return count, total_bitrate
