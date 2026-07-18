"""Inventory: probe source clips and MPLS playlists to collect metadata.

Produces a structured inventory of all clips, their streams, durations, and codecs.
Uses ffprobe for video probing and the MPLS parser for playlist structure.
"""

import json
import struct
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from bd_shrink.mpls import parse_mpls


@dataclass
class AudioStream:
    """Audio track metadata."""

    index: int
    codec_name: str
    bit_rate: int  # bits/sec, 0 if unknown
    channel_layout: str


@dataclass
class SubtitleStream:
    """Subtitle track metadata."""

    index: int
    codec_name: str


@dataclass
class VideoStream:
    """Video stream metadata."""

    codec_name: str
    width: int
    height: int
    r_frame_rate: str  # e.g., "59.94" or "24000/1001"
    bit_rate: int  # bits/sec, 0 if unknown


@dataclass
class Clip:
    """A single M2TS clip with all its streams."""

    clip_id: str  # e.g., "00000"
    duration_sec: float  # seconds (from ffprobe)
    video: Optional[VideoStream]
    audio: list[AudioStream]
    subtitles: list[SubtitleStream]


@dataclass
class PlaylistMetadata:
    """Parsed MPLS playlist with enriched metadata."""

    playlist_id: str
    playlist_type: int  # 0 = main, 1 = menu
    duration_sec: float  # total duration of all unique clips
    num_chapters: int
    clips: list[str]  # List of clip_ids in playback order (may have duplicates for branching)


@dataclass
class Inventory:
    """Complete inventory of source disc structure."""

    clips: dict[str, Clip]  # clip_id -> Clip
    playlists: dict[str, PlaylistMetadata]  # playlist_id -> PlaylistMetadata


def probe_clip(m2ts_path: str) -> Clip:
    """Probe an M2TS file to extract video/audio/subtitle metadata.

    Args:
        m2ts_path: Path to .m2ts file

    Returns:
        Clip with populated streams

    Raises:
        subprocess.CalledProcessError: if ffprobe fails
    """
    clip_id = Path(m2ts_path).stem  # filename without extension

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,bit_rate,channel_layout",
        "-of",
        "json",
        m2ts_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)

    duration_sec = float(data.get("format", {}).get("duration", 0))

    video = None
    audio = []
    subtitles = []

    for stream in data.get("streams", []):
        stream_type = stream.get("codec_type")
        index = stream.get("index", 0)

        if stream_type == "video":
            video = VideoStream(
                codec_name=stream.get("codec_name", "unknown"),
                width=stream.get("width", 0),
                height=stream.get("height", 0),
                r_frame_rate=stream.get("r_frame_rate", "0/1"),
                bit_rate=int(stream.get("bit_rate", 0) or 0),
            )
        elif stream_type == "audio":
            bit_rate_str = stream.get("bit_rate", "0")
            bit_rate = int(bit_rate_str) if bit_rate_str else 0
            audio.append(
                AudioStream(
                    index=index,
                    codec_name=stream.get("codec_name", "unknown"),
                    bit_rate=bit_rate,
                    channel_layout=stream.get("channel_layout", "unknown"),
                )
            )
        elif stream_type == "subtitle":
            subtitles.append(
                SubtitleStream(
                    index=index,
                    codec_name=stream.get("codec_name", "unknown"),
                )
            )

    return Clip(
        clip_id=clip_id,
        duration_sec=duration_sec,
        video=video,
        audio=audio,
        subtitles=subtitles,
    )


def build_inventory(bdmv_root: str) -> Inventory:
    """Build complete inventory from a BDMV folder.

    Scans BDMV/STREAM/ for .m2ts clips and BDMV/PLAYLIST/ for .mpls files.
    Probes each clip with ffprobe and parses each playlist.

    Args:
        bdmv_root: Path to BDMV folder (must contain STREAM/ and PLAYLIST/)

    Returns:
        Inventory with all clips and playlists

    Raises:
        FileNotFoundError: if STREAM or PLAYLIST directories don't exist
    """
    bdmv_path = Path(bdmv_root)
    stream_dir = bdmv_path / "STREAM"
    playlist_dir = bdmv_path / "PLAYLIST"

    if not stream_dir.exists():
        raise FileNotFoundError(f"STREAM directory not found: {stream_dir}")
    if not playlist_dir.exists():
        raise FileNotFoundError(f"PLAYLIST directory not found: {playlist_dir}")

    # Probe all clips
    clips_dict = {}
    for m2ts_file in sorted(stream_dir.glob("*.m2ts")):
        clip = probe_clip(str(m2ts_file))
        clips_dict[clip.clip_id] = clip

    # Parse all playlists
    playlists_dict = {}
    for mpls_file in sorted(playlist_dir.glob("*.mpls")):
        try:
            mpls = parse_mpls(str(mpls_file))
        except (EOFError, struct.error, IOError):
            continue

        # Collect unique clips from PlayItems + SubItems
        clip_ids = set()
        duration = 0.0

        for item in mpls.play_items:
            clip_ids.add(item.clip_id)
            if item.clip_id in clips_dict:
                duration += clips_dict[item.clip_id].duration_sec

        # SubItems have duration 0 (they're overlays/branching), don't add to duration
        for sub_item in mpls.sub_items:
            clip_ids.add(sub_item.clip_id)

        playlist_meta = PlaylistMetadata(
            playlist_id=mpls.playlist_id,
            playlist_type=mpls.playlist_type,
            duration_sec=duration,
            num_chapters=len(mpls.chapters),
            clips=[item.clip_id for item in mpls.play_items]  # main clips first
            + [item.clip_id for item in mpls.sub_items],  # then subs
        )
        playlists_dict[mpls.playlist_id] = playlist_meta

    return Inventory(clips=clips_dict, playlists=playlists_dict)


def to_json(inventory: Inventory) -> str:
    """Serialize inventory to JSON (for checkpointing).

    Args:
        inventory: Inventory object

    Returns:
        JSON string
    """
    data = {
        "clips": {clip_id: asdict(clip) for clip_id, clip in inventory.clips.items()},
        "playlists": {pl_id: asdict(pl) for pl_id, pl in inventory.playlists.items()},
    }
    return json.dumps(data, indent=2)


def from_json(json_str: str) -> Inventory:
    """Deserialize inventory from JSON.

    Args:
        json_str: JSON string

    Returns:
        Inventory object
    """
    data = json.loads(json_str)

    clips_dict = {}
    for clip_id, clip_data in data.get("clips", {}).items():
        video_data = clip_data.get("video")
        video = None
        if video_data:
            video = VideoStream(**video_data)

        audio = [AudioStream(**a) for a in clip_data.get("audio", [])]

        subtitles = [SubtitleStream(**s) for s in clip_data.get("subtitles", [])]

        clips_dict[clip_id] = Clip(
            clip_id=clip_data["clip_id"],
            duration_sec=clip_data["duration_sec"],
            video=video,
            audio=audio,
            subtitles=subtitles,
        )

    playlists_dict = {}
    for pl_id, pl_data in data.get("playlists", {}).items():
        playlists_dict[pl_id] = PlaylistMetadata(**pl_data)

    return Inventory(clips=clips_dict, playlists=playlists_dict)
