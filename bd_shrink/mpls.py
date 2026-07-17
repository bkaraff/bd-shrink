"""MPLS playlist file parser for Blu-ray discs.

Reads binary MPLS files to extract:
  - PlayList type (0 = main movie, 1 = menu/interactive)
  - PlayItem clips (stream files to play)
  - SubPath items (seamless branching, picture-in-picture, etc.)
  - Chapter marks (navigation points)
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass
class PlayItem:
    """A single clip in a playlist."""
    clip_id: str  # e.g., '00000' (without .m2ts)
    codec: str    # 'h264', 'h265', 'mpeg2'
    duration: int  # milliseconds
    in_time: int   # entry point (milliseconds)
    out_time: int  # exit point (milliseconds)


@dataclass
class SubItem:
    """SubPath item (seamless branching, PiP, etc.)."""
    clip_id: str
    duration: int  # milliseconds (usually 0 for PiP/branching)


@dataclass
class Chapter:
    """Chapter mark (navigation point) in a playlist."""
    mark_id: int  # chapter index
    pts: int      # presentation timestamp


@dataclass
class MPLSPlaylist:
    """Parsed MPLS playlist object."""
    playlist_id: str  # e.g., '00000' (from filename)
    playlist_type: int  # 0 = main, 1 = menu
    play_items: list[PlayItem]
    sub_items: list[SubItem]
    chapters: list[Chapter]


def _read_exact(f: BinaryIO, n: int) -> bytes:
    """Read exactly n bytes or raise EOFError."""
    data = f.read(n)
    if len(data) < n:
        raise EOFError(f"Expected {n} bytes, got {len(data)}")
    return data


def _read_u8(f: BinaryIO) -> int:
    """Read an 8-bit unsigned integer."""
    return struct.unpack(">B", _read_exact(f, 1))[0]


def _read_u16(f: BinaryIO) -> int:
    """Read a 16-bit big-endian unsigned integer."""
    return struct.unpack(">H", _read_exact(f, 2))[0]


def _read_u32(f: BinaryIO) -> int:
    """Read a 32-bit big-endian unsigned integer."""
    return struct.unpack(">I", _read_exact(f, 4))[0]


def _read_u16_be(f: BinaryIO) -> int:
    """Alias for _read_u16 (big-endian)."""
    return _read_u16(f)


def _parse_timestamp(ts_bytes: bytes) -> int:
    """Parse a 4-byte BD timestamp to milliseconds.
    
    BD timestamps are in units of 90 kHz (11.111... microseconds).
    Milliseconds = ts_bytes / 90.
    """
    ts_raw = struct.unpack(">I", ts_bytes)[0]
    # Remove top 2 bits (reserved) and convert to milliseconds
    ts_clean = ts_raw & 0x3FFFFFFF
    return ts_clean // 90


def parse_mpls(mpls_path: str) -> MPLSPlaylist:
    """Parse a Blu-ray MPLS playlist file.
    
    MPLS structure (BDMV spec):
      - Header: 'MPLS' magic + offsets
      - PlayListInfo: PlayList_type, num chapters, etc.
      - PlayItem[] + STN_table
      - SubPath[] (optional)
      - PlayMark[] (chapters)
    
    Args:
        mpls_path: Path to .mpls file (e.g., 'BDMV/PLAYLIST/00000.mpls')
    
    Returns:
        MPLSPlaylist with extracted metadata
    
    Raises:
        IOError: if file not found or corrupt
        EOFError: if file is truncated
    """
    path = Path(mpls_path)
    playlist_id = path.stem  # filename without .mpls
    
    play_items = []
    sub_items = []
    chapters = []
    playlist_type = 0  # default: main
    
    with open(mpls_path, "rb") as f:
        # Read MPLS header
        magic = f.read(4)
        if magic != b"MPLS":
            raise IOError(f"Not a valid MPLS file (magic: {magic})")
        
        # Read offsets
        _read_u32(f)  # version (usually 0x00020000)
        playlist_start = _read_u32(f)           # Offset to PlayList
        playlist_info_start = _read_u32(f)      # Offset to PlayListInfo
        _read_u32(f)  # chunklist_start (unused)
        _read_u32(f)  # extension_start (unused)
        
        # === PlayListInfo ===
        f.seek(playlist_info_start)
        pli_length = _read_u16(f)  # length
        _read_u8(f)   # reserved
        playlist_type_byte = _read_u8(f)
        playlist_type = (playlist_type_byte >> 6) & 0x3  # Bits 6-7
        
        # Skip to PlayList section
        f.seek(playlist_start)
        pl_length = _read_u16(f)  # length
        _read_u16(f)  # reserved
        num_items = _read_u16(f)
        num_sub_items = _read_u16(f)
        
        # === Parse PlayItems ===
        for _ in range(num_items):
            play_item = _parse_play_item(f)
            play_items.append(play_item)
        
        # === Parse SubItems ===
        for _ in range(num_sub_items):
            sub_item = _parse_sub_item(f)
            sub_items.append(sub_item)
        
        # === Parse PlayMark (chapters) ===
        # PlayMark table starts after PlayItem + SubItem data
        # (seek is implicit after parsing above)
        num_marks = _read_u16(f)
        _read_u16(f)  # reserved
        
        for mark_id in range(num_marks):
            mark_type = _read_u8(f)
            _read_u8(f)  # reserved
            clip_ref = _read_u16(f)
            pts = _read_u32(f)
            # Convert to milliseconds
            pts_ms = pts // 90
            chapters.append(Chapter(mark_id=mark_id, pts=pts_ms))
    
    return MPLSPlaylist(
        playlist_id=playlist_id,
        playlist_type=playlist_type,
        play_items=play_items,
        sub_items=sub_items,
        chapters=chapters,
    )


def _parse_play_item(f: BinaryIO) -> PlayItem:
    """Parse a single PlayItem block from file.
    
    PlayItem structure:
      - length (2 bytes)
      - clip_id (5 bytes string)
      - codec info in STN_table
      - duration (4 bytes timestamp)
      - in_time (4 bytes timestamp)
      - out_time (4 bytes timestamp)
    """
    item_start = f.tell()
    item_len = _read_u16(f)
    
    clip_id_bytes = _read_exact(f, 5)
    clip_id = clip_id_bytes.decode("ascii").strip('\x00')
    
    _read_u8(f)  # connection condition
    
    # STN_table offset (relative to item start)
    stn_offset = _read_u16(f)
    
    _read_u8(f)  # reserved
    _read_u8(f)  # is_multi_angle + connection condition
    
    duration_ts = _read_exact(f, 4)
    duration = _parse_timestamp(duration_ts)
    
    in_time_ts = _read_exact(f, 4)
    in_time = _parse_timestamp(in_time_ts)
    
    out_time_ts = _read_exact(f, 4)
    out_time = _parse_timestamp(out_time_ts)
    
    # Parse STN_table for codec info if offset is nonzero
    if stn_offset > 0:
        codec = _parse_stn_table(f, item_start + stn_offset)
    else:
        codec = "mpeg2"  # fallback
    
    # Skip to end of this PlayItem
    f.seek(item_start + item_len)
    
    return PlayItem(
        clip_id=clip_id,
        codec=codec,
        duration=duration,
        in_time=in_time,
        out_time=out_time,
    )


def _parse_sub_item(f: BinaryIO) -> SubItem:
    """Parse a single SubItem block (seamless branching, PiP).
    
    SubItem structure:
      - length (2 bytes)
      - clip_id (5 bytes string)
      - entry point (4 bytes timestamp)
      - (no out_time in SubItem)
    """
    item_start = f.tell()
    item_len = _read_u16(f)
    
    clip_id_bytes = _read_exact(f, 5)
    clip_id = clip_id_bytes.decode("ascii").strip('\x00')
    
    _read_u8(f)  # connection condition
    _read_u16(f)  # STN_table offset (usually 0 for SubItem)
    _read_u8(f)  # reserved
    _read_u8(f)  # multi_angle byte
    
    # SubPath duration (often 0)
    duration_ts = _read_exact(f, 4)
    duration = _parse_timestamp(duration_ts)
    
    # Skip to end
    f.seek(item_start + item_len)
    
    return SubItem(clip_id=clip_id, duration=duration)


def _parse_stn_table(f: BinaryIO, stn_start: int) -> str:
    """Parse STN_table to extract video codec.
    
    Returns:
        Codec string: 'h264', 'h265' (hevc), or 'mpeg2'
    """
    current_pos = f.tell()
    f.seek(stn_start)
    
    try:
        stn_len = _read_u16(f)
        _read_u16(f)  # reserved
        
        num_video = _read_u8(f)
        num_audio = _read_u8(f)
        num_pg = _read_u8(f)
        num_ig = _read_u8(f)
        num_secondary_audio = _read_u8(f)
        _read_u8(f)  # reserved
        num_secondary_video = _read_u8(f)
        _read_u8(f)  # reserved
        
        # Parse first video stream (index 0)
        if num_video > 0:
            codec = _parse_video_stream_entry(f)
        else:
            codec = "mpeg2"  # Default fallback
    finally:
        f.seek(current_pos)
    
    return codec


def _parse_video_stream_entry(f: BinaryIO) -> str:
    """Parse a video stream entry to extract codec.
    
    Video stream structure:
      - length (1 byte)
      - codec/resolution in first bits
    
    Codec values (from BDMV spec):
      - 0x01: MPEG-2
      - 0x02: AVC/H.264
      - 0x0a: HEVC/H.265
    """
    entry_len = _read_u8(f)
    entry_start = f.tell()
    
    codec_byte = _read_u8(f)
    codec_id = (codec_byte >> 4) & 0xf  # Upper nibble
    
    # Map codec ID to name
    codec_map = {
        0x01: "mpeg2",
        0x02: "h264",
        0x0a: "h265",
    }
    codec = codec_map.get(codec_id, "mpeg2")
    
    # Skip to end of this stream entry
    f.seek(entry_start + entry_len)
    
    return codec
