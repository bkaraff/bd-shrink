#!/usr/bin/env python3
"""Fixture tests for the binary parsers embedded in bd_shrink.sh."""

import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bd_shrink.sh"


def heredoc_after(source, marker, terminator):
    start = source.index(marker) + len(marker)
    end = source.index("\n" + terminator, start)
    return source[start:end].lstrip("\n")


def build_mpls():
    data = bytearray(b"MPLS0200")
    playlist_start = 58
    data += playlist_start.to_bytes(4, "big")
    mark_start_pos = len(data)
    data += b"\0\0\0\0"  # patched below
    data += b"\0\0\0\0"  # extension address
    data += b"\0" * 20

    # AppInfoPlayList at 0x28: length excludes the four length bytes.
    data += (14).to_bytes(4, "big")
    data += b"\0\x01\0\0"  # reserved, standard playback, count
    data += b"\0" * 8       # UO mask
    data += b"\0\0"        # flags/reserved

    assert len(data) == playlist_start
    playlist_start_pos = playlist_start
    playlist = bytearray(b"\0" * 10)
    item = bytearray(b"00001M2TS")
    item += b"\0\x01\0"                 # reserved/connection/STC
    item += (0).to_bytes(4, "big")
    item += (45000 * 600).to_bytes(4, "big")
    item += b"\0" * 12               # UO mask, misc, still time
    assert len(item) == 32
    playlist += len(item).to_bytes(2, "big") + item
    playlist[0:4] = (len(playlist) - 4).to_bytes(4, "big")
    playlist[6:8] = (1).to_bytes(2, "big")
    playlist[8:10] = (0).to_bytes(2, "big")
    data += playlist

    mark_start = len(data)
    data[mark_start_pos:mark_start_pos + 4] = mark_start.to_bytes(4, "big")
    marks = bytearray((2 + 3 * 14).to_bytes(4, "big"))
    marks += (3).to_bytes(2, "big")
    for mark_type, timestamp in ((1, 0), (1, 45000 * 300), (2, 45000 * 400)):
        marks += bytes((0, mark_type))
        marks += (0).to_bytes(2, "big")
        marks += timestamp.to_bytes(4, "big")
        marks += b"\0" * 6
    data += marks
    return bytes(data)


def build_clpi(coding_type):
    data = bytearray(b"CLPI0200")
    data += (28).to_bytes(4, "big")  # SequenceInfoStartAddress
    data += (40).to_bytes(4, "big")  # ProgramInfoStartAddress
    data += (0).to_bytes(4, "big")
    data += (0).to_bytes(4, "big")
    data += (0).to_bytes(4, "big")
    data += b"\0" * 12
    data += b"\0\0\0\0\0\x01"  # ProgramInfo length/reserved/program count
    data += b"\0" * 4            # SPN
    data += (0x100).to_bytes(2, "big")
    data += b"\x01\0"             # stream count/reserved
    data += (0x1100).to_bytes(2, "big")
    data += b"\x01" + bytes((coding_type,))
    return bytes(data)


def main():
    source = SCRIPT.read_text(encoding="utf-8")
    mpls_py = heredoc_after(source, "python3 - \"$1\" << 'PYEOF'", "PYEOF")
    clpi_py = heredoc_after(source, "<< 'CLPI_DIFF'", "CLPI_DIFF")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        playlist_dir = tmp_path / "PLAYLIST"
        playlist_dir.mkdir()
        (playlist_dir / "00001.mpls").write_bytes(build_mpls())
        result = subprocess.run(
            ["python3", "-c", mpls_py, str(playlist_dir)],
            check=True, capture_output=True, text=True,
        )
        parsed = json.loads(result.stdout)["00001.mpls"]
        assert parsed["playitems"][0]["clip"] == "00001"
        assert parsed["chapters"] == 2
        assert parsed["chapter_times"] == [0.0, 300.0]
        assert parsed["playlist_type"] == 1

        src = tmp_path / "src.clpi"
        dst = tmp_path / "dst.clpi"
        unsafe = tmp_path / "unsafe.txt"
        src.write_bytes(build_clpi(0x83))
        dst.write_bytes(build_clpi(0x81))
        subprocess.run(
            ["python3", "-c", clpi_py, str(src), str(dst), str(unsafe), "00001"],
            check=True,
        )
        assert unsafe.read_text(encoding="utf-8") == "00001\n"

    print("parser fixtures: OK")


if __name__ == "__main__":
    main()
