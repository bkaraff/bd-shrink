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
    codec: str  # 'h264', 'h265', 'mpeg2'
    duration: int  # milliseconds
    in_time: int  # entry point (milliseconds)
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
    pts: int  # presentation timestamp


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


def _parse_timestamp(ts_bytes: bytes, khz: int = 90) -> int:
    """Parse a 4-byte BD timestamp to milliseconds.

    BD uses two clock rates: 45 kHz for PlayItem/SubItem IN_time/OUT_time
    (STC/600), and 90 kHz for PTS values like chapter marks (STC/300).

    Args:
        ts_bytes: 4 raw bytes from the file.
        khz: Clock rate — 45 for IN_time/OUT_time, 90 for PTS.
    """
    ts_raw = struct.unpack(">I", ts_bytes)[0]
    ts_clean = ts_raw & 0x3FFFFFFF  # remove top 2 reserved bits
    return ts_clean // khz


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
        # Read MPLS header (8 bytes magic + 12 bytes of section offsets = 20 bytes)
        magic = f.read(4)
        if magic != b"MPLS":
            raise IOError(f"Not a valid MPLS file (magic: {magic})")

        _read_u32(f)  # version ("0200")
        playlist_start = _read_u32(f)  # Offset to PlayList section
        # The 3rd u32 is PlayListMark_start, not PlayListInfo.
        _playmark_start = _read_u32(f)
        _ext_start = _read_u32(f)  # ExtensionData start (0 if absent)

        # AppInfoPlayList is at a fixed offset 0x20 (32 bytes) per BDMV spec.
        # It contains PlayList_type among other metadata.
        f.seek(0x20)
        _read_u32(f)  # length of AppInfoPlayList block
        _read_u8(f)  # reserved
        playlist_type_byte = _read_u8(f)
        playlist_type = (playlist_type_byte >> 6) & 0x3  # Bits 6-7

        # Skip to PlayList section
        f.seek(playlist_start)
        _read_u32(f)  # length (uint32, not uint16!)
        _read_u16(f)  # reserved
        num_items = _read_u16(f)
        num_sub_items = _read_u16(f)

        # === Parse PlayItems ===
        for _ in range(num_items):
            try:
                play_item = _parse_play_item(f)
                if play_item is not None:
                    play_items.append(play_item)
            except (EOFError, struct.error):
                break  # truncated file

        # === Parse SubItems ===
        for _ in range(num_sub_items):
            try:
                sub_item = _parse_sub_item(f)
                if sub_item is not None:
                    sub_items.append(sub_item)
            except (EOFError, struct.error):
                break  # truncated file

        # === Parse PlayMark (chapters) ===
        # PlayMark table starts after PlayItem + SubItem data
        # (seek is implicit after parsing above)
        try:
            num_marks = _read_u16(f)
            _read_u16(f)  # reserved

            for mark_id in range(num_marks):
                _read_u8(f)
                _read_u8(f)  # reserved
                _read_u16(f)
                pts = _read_u32(f)
                # Convert to milliseconds
                pts_ms = pts // 90
                chapters.append(Chapter(mark_id=mark_id, pts=pts_ms))
        except (EOFError, struct.error):
            pass  # chapters are non-critical; carry on with what we have

    return MPLSPlaylist(
        playlist_id=playlist_id,
        playlist_type=playlist_type,
        play_items=play_items,
        sub_items=sub_items,
        chapters=chapters,
    )


def _parse_play_item(f: BinaryIO) -> PlayItem | None:
    """Parse a single PlayItem block from file.

    On-disk layout (BDMV spec):
      - Length (2 bytes, uint16)
      - Clip_information_file_name (5 bytes, ASCII digits)
      - Clip_codec_identifier (4 bytes, usually "M2TS")
      - connection_condition + reserved (1 byte)
      - ref_to_STC_id (1 byte)
      - reserved (1 byte)
      - IN_time (4 bytes, 45 kHz ticks)
      - OUT_time (4 bytes, 45 kHz ticks)
      - ... (UO_mask_table, STN_table, etc. — skipped)

    Codec is left as "mpeg2" fallback — ffprobe picks up the real codec
    during the inventory phase. Duration is OUT_time - IN_time.
    """
    item_start = f.tell()
    item_len = _read_u16(f)

    clip_id_bytes = _read_exact(f, 5)
    try:
        clip_id = clip_id_bytes.decode("ascii").strip("\x00")
        if not clip_id.isdigit() or len(clip_id) != 5:
            f.seek(item_start + 2 + item_len)
            return None
    except UnicodeDecodeError:
        f.seek(item_start + 2 + item_len)
        return None

    _read_exact(f, 4)  # Clip_codec_identifier (usually "M2TS")

    _read_u8(f)  # connection_condition + reserved bits
    _read_u8(f)  # ref_to_STC_id
    _read_u8(f)  # reserved

    in_time_ts = _read_exact(f, 4)
    in_time = _parse_timestamp(in_time_ts, khz=45)

    out_time_ts = _read_exact(f, 4)
    out_time = _parse_timestamp(out_time_ts, khz=45)

    # Skip to end of this PlayItem (past UO_mask_table, STN_table, etc.)
    f.seek(item_start + 2 + item_len)

    duration = out_time - in_time

    return PlayItem(
        clip_id=clip_id,
        codec="mpeg2",  # fallback; ffprobe provides real codec
        duration=duration,
        in_time=in_time,
        out_time=out_time,
    )


def _parse_sub_item(f: BinaryIO) -> SubItem | None:
    """Parse a single SubItem block (seamless branching, PiP).

    SubItem structure:
      - length (2 bytes)
      - clip_id (5 bytes string)
      - entry point (4 bytes timestamp)
      - (no out_time in SubItem)

    Returns None if the block cannot be parsed (non-ASCII clip_id, truncated,
    or other structural difference between BDMV versions).
    """
    item_start = f.tell()
    item_len = _read_u16(f)

    clip_id_bytes = _read_exact(f, 5)
    try:
        clip_id = clip_id_bytes.decode("ascii").strip("\x00")
        if not clip_id.isdigit() or len(clip_id) != 5:
            f.seek(item_start + 2 + item_len)
            return None
    except UnicodeDecodeError:
        f.seek(item_start + 2 + item_len)
        return None

    _read_exact(f, 4)  # Clip_codec_identifier (usually "M2TS")

    _read_u8(f)  # connection_condition
    _read_u8(f)  # ref_to_STC_id
    _read_u8(f)  # reserved

    # SubPath duration (IN_time to OUT_time in 45 kHz ticks)
    in_ts = _read_exact(f, 4)
    in_time = _parse_timestamp(in_ts, khz=45)
    out_ts = _read_exact(f, 4)
    out_time = _parse_timestamp(out_ts, khz=45)

    # Skip to end
    f.seek(item_start + 2 + item_len)

    return SubItem(clip_id=clip_id, duration=out_time - in_time)


def _parse_stn_table(f: BinaryIO, stn_start: int) -> str:
    """Parse STN_table to extract video codec.

    Returns:
        Codec string: 'h264', 'h265' (hevc), or 'mpeg2'
    """
    current_pos = f.tell()
    f.seek(stn_start)

    try:
        _read_u16(f)
        _read_u16(f)  # reserved

        num_video = _read_u8(f)
        _read_u8(f)
        _read_u8(f)
        _read_u8(f)
        _read_u8(f)
        _read_u8(f)  # reserved
        _read_u8(f)
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
    codec_id = (codec_byte >> 4) & 0xF  # Upper nibble

    # Map codec ID to name
    codec_map = {
        0x01: "mpeg2",
        0x02: "h264",
        0x0A: "h265",
    }
    codec = codec_map.get(codec_id, "mpeg2")

    # Skip to end of this stream entry
    f.seek(entry_start + entry_len)

    return codec
