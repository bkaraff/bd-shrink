"""Tests for MPLS parser."""

import struct
import tempfile
from pathlib import Path

import pytest

from bd_shrink.mpls import (
    parse_mpls,
)


def _create_synthetic_mpls(
    playlist_type: int = 0,
    num_play_items: int = 1,
    num_sub_items: int = 0,
    num_chapters: int = 0,
) -> bytes:
    """Create a minimal synthetic MPLS file for testing.

    Args:
        playlist_type: 0 = main, 1 = menu
        num_play_items: Number of PlayItems
        num_sub_items: Number of SubItems
        num_chapters: Number of chapters (PlayMarks)

    Returns:
        Binary MPLS file data
    """
    mpls = bytearray()

    # === MPLS Header (8 magic + 12 offsets = 20 bytes) ===
    mpls.extend(b"MPLS")  # Magic (4 bytes)
    mpls.extend(b"0200")  # Version "0200" (4 bytes)

    playlist_offset = 0x30  # After AppInfoPlayList (at 0x20) + its 14-byte body
    mpls.extend(struct.pack(">I", playlist_offset))  # PlayList start (4 bytes)
    mpls.extend(struct.pack(">I", 0x80))  # PlayListMark start (4 bytes)
    mpls.extend(struct.pack(">I", 0))  # ExtensionData (4 bytes)

    # Pad to 0x20 (AppInfoPlayList starts at offset 32).
    assert len(mpls) == 20
    mpls.extend(b"\x00" * (0x20 - len(mpls)))

    # === AppInfoPlayList (at 0x20) ===
    assert len(mpls) == 0x20
    # AppInfoPlayList: 4-byte length (excludes itself) + 1 reserved + 1 playback_type + 4 reserved
    app_info = bytearray()
    app_info_len = 6  # 1 (reserved) + 1 (playback_type) + 4 (reserved)
    app_info.extend(struct.pack(">I", app_info_len))
    app_info.extend(b"\x00")  # reserved
    playlist_type_byte = (playlist_type & 0x3) << 6
    app_info.extend(struct.pack("B", playlist_type_byte))
    app_info.extend(b"\x00" * 4)  # remaining reserved
    mpls.extend(app_info)

    # Pad to playlist_offset
    mpls.extend(b"\x00" * (playlist_offset - len(mpls)))

    # === PlayList section (at offset 0x30) ===
    assert len(mpls) == playlist_offset
    pl_data = bytearray()

    # Build PlayItems and SubItems first to get accurate size
    items_data = bytearray()
    for i in range(num_play_items):
        items_data.extend(_create_play_item(clip_id=f"{i:05d}"))
    for i in range(num_sub_items):
        items_data.extend(_create_sub_item(clip_id=f"{1000 + i:05d}"))

    # Build PlayMark data
    marks_data = bytearray()
    marks_data.extend(struct.pack(">H", num_chapters))
    marks_data.extend(struct.pack(">H", 0))  # reserved
    for ch_id in range(num_chapters):
        marks_data.extend(struct.pack("B", 0))  # mark type
        marks_data.extend(struct.pack("B", 0))  # reserved
        marks_data.extend(struct.pack(">H", 0))  # clip ref
        ts = (ch_id + 1) * 1000 * 90
        marks_data.extend(struct.pack(">I", ts))

    # PlayList length excludes itself: reserved(2) + num_items(2) + num_subs(2) + items + marks
    total_length = 6 + len(items_data) + len(marks_data)
    pl_data.extend(struct.pack(">I", total_length))  # uint32 length
    pl_data.extend(struct.pack(">H", 0))  # reserved
    pl_data.extend(struct.pack(">H", num_play_items))
    pl_data.extend(struct.pack(">H", num_sub_items))
    pl_data.extend(items_data)
    pl_data.extend(marks_data)

    mpls.extend(pl_data)

    return bytes(mpls)


def _create_play_item(clip_id: str) -> bytes:
    """Create a synthetic PlayItem block matching the BDMV on-disk layout."""
    # Pos +0: Length (2) + Clip_file_name (5) + Clip_codec_id (4)
    # Pos +11: connection_condition (1) + ref_to_STC_id (1) + reserved (1)
    # Pos +14: IN_time (4, 45kHz ticks)  +  OUT_time (4, 45kHz ticks)
    # Total payload: 5 (clip_id) + 4 (codec_id) + 3 (conn/ref/reserved) + 4 (IN) + 4 (OUT) = 20 bytes
    # item_len field = 20 (does not include the 2-byte length field itself)

    item = bytearray()
    item_len = 20
    item.extend(struct.pack(">H", item_len))

    clip_id_bytes = clip_id.encode("ascii")[:5].ljust(5, b"\x00")
    item.extend(clip_id_bytes)
    item.extend(b"M2TS")  # Clip_codec_identifier

    item.extend(b"\x00")  # connection_condition
    item.extend(b"\x01")  # ref_to_STC_id
    item.extend(b"\x00")  # reserved

    # IN_time = 0 (in 45 kHz ticks)
    item.extend(struct.pack(">I", 0))

    # OUT_time = 1000ms * 45 = 45000 (in 45 kHz ticks)
    item.extend(struct.pack(">I", 1000 * 45))

    return bytes(item)


def _create_sub_item(clip_id: str) -> bytes:
    """Create a synthetic SubItem block matching the BDMV on-disk layout."""
    # Same layout as PlayItem: 5 (clip_id) + 4 (codec_id) + 3 + 4 (IN) + 4 (OUT) = 20 bytes
    item = bytearray()
    item_len = 20
    item.extend(struct.pack(">H", item_len))

    clip_id_bytes = clip_id.encode("ascii")[:5].ljust(5, b"\x00")
    item.extend(clip_id_bytes)
    item.extend(b"M2TS")

    item.extend(b"\x00")  # connection_condition
    item.extend(b"\x01")  # ref_to_STC_id
    item.extend(b"\x00")  # reserved

    item.extend(struct.pack(">I", 0))  # IN_time = 0
    item.extend(struct.pack(">I", 500 * 45))  # OUT_time = 500ms

    return bytes(item)


class TestMPLSParser:
    """Test MPLS parsing."""

    def test_parse_main_movie_single_clip(self):
        """Verify parsing a simple main movie playlist."""
        mpls_data = _create_synthetic_mpls(playlist_type=0, num_play_items=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            mpls_path = Path(tmpdir) / "00000.mpls"
            mpls_path.write_bytes(mpls_data)

            result = parse_mpls(str(mpls_path))

            assert result.playlist_id == "00000"
            assert result.playlist_type == 0  # main
            assert len(result.play_items) == 1
            assert result.play_items[0].clip_id == "00000"
            assert result.play_items[0].duration == 1000  # 1000 ms
            assert result.play_items[0].codec == "mpeg2"  # default when no STN

    def test_parse_menu_playlist(self):
        """Verify parsing a menu playlist."""
        mpls_data = _create_synthetic_mpls(playlist_type=1, num_play_items=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            mpls_path = Path(tmpdir) / "90000.mpls"
            mpls_path.write_bytes(mpls_data)

            result = parse_mpls(str(mpls_path))

            assert result.playlist_type == 1  # menu

    def test_parse_multiple_play_items(self):
        """Verify parsing playlist with multiple clips."""
        mpls_data = _create_synthetic_mpls(playlist_type=0, num_play_items=3)

        with tempfile.TemporaryDirectory() as tmpdir:
            mpls_path = Path(tmpdir) / "00001.mpls"
            mpls_path.write_bytes(mpls_data)

            result = parse_mpls(str(mpls_path))

            assert len(result.play_items) == 3
            assert result.play_items[0].clip_id == "00000"
            assert result.play_items[1].clip_id == "00001"
            assert result.play_items[2].clip_id == "00002"

    def test_parse_with_sub_items(self):
        """Verify parsing playlist with SubItems (seamless branching)."""
        mpls_data = _create_synthetic_mpls(
            playlist_type=0,
            num_play_items=1,
            num_sub_items=2,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mpls_path = Path(tmpdir) / "00002.mpls"
            mpls_path.write_bytes(mpls_data)

            result = parse_mpls(str(mpls_path))

            assert len(result.play_items) == 1
            assert len(result.sub_items) == 2
            assert result.sub_items[0].clip_id == "01000"
            assert result.sub_items[1].clip_id == "01001"

    def test_parse_with_chapters(self):
        """Verify parsing playlist with chapters."""
        mpls_data = _create_synthetic_mpls(
            playlist_type=0,
            num_play_items=1,
            num_chapters=3,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mpls_path = Path(tmpdir) / "00003.mpls"
            mpls_path.write_bytes(mpls_data)

            result = parse_mpls(str(mpls_path))

            assert len(result.chapters) == 3
            # Chapters: mark_id 0 @ 1000ms, 1 @ 2000ms, 2 @ 3000ms
            assert result.chapters[0].mark_id == 0
            assert result.chapters[0].pts == 1000
            assert result.chapters[1].pts == 2000
            assert result.chapters[2].pts == 3000

    def test_parse_invalid_magic(self):
        """Verify error on invalid MPLS magic."""
        bad_mpls = b"XXXX" + b"\x00" * 100

        with tempfile.TemporaryDirectory() as tmpdir:
            mpls_path = Path(tmpdir) / "00000.mpls"
            mpls_path.write_bytes(bad_mpls)

            with pytest.raises(IOError, match="Not a valid MPLS file"):
                parse_mpls(str(mpls_path))

    def test_parse_file_not_found(self):
        """Verify error on missing file."""
        with pytest.raises(FileNotFoundError):
            parse_mpls("/nonexistent/path/00000.mpls")

    def test_parse_empty_playlist(self):
        """Verify parsing playlist with no items."""
        mpls_data = _create_synthetic_mpls(
            playlist_type=0,
            num_play_items=0,
            num_sub_items=0,
            num_chapters=0,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mpls_path = Path(tmpdir) / "00004.mpls"
            mpls_path.write_bytes(mpls_data)

            result = parse_mpls(str(mpls_path))

            assert len(result.play_items) == 0
            assert len(result.sub_items) == 0
            assert len(result.chapters) == 0

    def test_playlist_id_from_filename(self):
        """Verify playlist ID extracted from filename."""
        mpls_data = _create_synthetic_mpls()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Use a specific filename
            mpls_path = Path(tmpdir) / "12345.mpls"
            mpls_path.write_bytes(mpls_data)

            result = parse_mpls(str(mpls_path))

            assert result.playlist_id == "12345"

    def test_play_item_timestamps(self):
        """Verify PlayItem timestamp parsing."""
        # A PlayItem with 1000 ms duration encodes as 90000 ticks (1000 * 90 kHz)
        mpls_data = _create_synthetic_mpls(num_play_items=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            mpls_path = Path(tmpdir) / "00005.mpls"
            mpls_path.write_bytes(mpls_data)

            result = parse_mpls(str(mpls_path))

            item = result.play_items[0]
            assert item.duration == 1000
            assert item.in_time == 0
            assert item.out_time == 1000
