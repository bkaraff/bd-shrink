"""Tests for rebuild, validate, and iso modules."""

import logging
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from bd_shrink.iso import ISOResult, burn_iso, cleanup_iso_mounts, create_iso
from bd_shrink.rebuild import (
    RebuildStats,
    audio_tsmuxer_type,
    count_audio_in_clip,
    count_subtitles_in_clip,
    find_audio_file,
    rebuild_surgical,
    video_tsmuxer_type,
)
from bd_shrink.validate import (
    ValidationResult,
    check_output_size,
    validate_bdmv_structure,
    validate_clpi_file,
    validate_m2ts_file,
)


@pytest.fixture
def null_logger():
    """Logger that discards all messages."""
    logger = logging.getLogger("test_null")
    logger.addHandler(logging.NullHandler())
    return logger


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield {
            "temp": tmpdir,
            "encode": os.path.join(tmpdir, "encode"),
            "output": os.path.join(tmpdir, "output"),
            "work": os.path.join(tmpdir, "work"),
        }


class TestRebuildHelpers:
    """Test rebuild helper functions."""

    def test_audio_tsmuxer_type_ac3(self):
        """Verify AC3 audio type."""
        assert audio_tsmuxer_type("clip_audio_0.ac3") == "A_AC3"

    def test_audio_tsmuxer_type_dts(self):
        """Verify DTS audio type."""
        assert audio_tsmuxer_type("clip_audio_1.dts") == "A_DTS"

    def test_audio_tsmuxer_type_truehd(self):
        """Verify TrueHD audio type."""
        assert audio_tsmuxer_type("clip_audio_2.thd") == "A_TRUEHD"

    def test_audio_tsmuxer_type_default(self):
        """Verify default audio type."""
        assert audio_tsmuxer_type("clip_audio_0.unknown") == "A_AC3"

    def test_video_tsmuxer_type_h264(self):
        """Verify H.264 video type."""
        assert video_tsmuxer_type("h264", False) == "V_MPEG4/ISO/AVC"

    def test_video_tsmuxer_type_hevc(self):
        """Verify HEVC video type."""
        assert video_tsmuxer_type("hevc", True) == "V_MPEGH/ISO/HEVC"

    def test_count_audio_in_clip_empty(self, temp_dirs, null_logger):
        """Verify count returns 0 for no audio."""
        os.makedirs(temp_dirs["encode"], exist_ok=True)
        count = count_audio_in_clip(temp_dirs["encode"], "00000")
        assert count == 0

    def test_count_audio_in_clip_multiple(self, temp_dirs, null_logger):
        """Verify count returns correct number of audio tracks."""
        os.makedirs(temp_dirs["encode"], exist_ok=True)

        # Create dummy audio files
        for i in range(3):
            with open(os.path.join(temp_dirs["encode"], f"00000_audio_{i}.ac3"), "w") as f:
                f.write("dummy")

        count = count_audio_in_clip(temp_dirs["encode"], "00000")
        assert count == 3

    def test_count_subtitles_in_clip_empty(self, temp_dirs):
        """Verify count returns 0 for no subtitles."""
        os.makedirs(temp_dirs["encode"], exist_ok=True)
        count = count_subtitles_in_clip(temp_dirs["encode"], "00000")
        assert count == 0

    def test_count_subtitles_in_clip_multiple(self, temp_dirs):
        """Verify count returns correct number of subtitle tracks."""
        os.makedirs(temp_dirs["encode"], exist_ok=True)

        # Create dummy subtitle files
        for i in range(2):
            with open(os.path.join(temp_dirs["encode"], f"00000_sub_{i}.sup"), "w") as f:
                f.write("dummy")

        count = count_subtitles_in_clip(temp_dirs["encode"], "00000")
        assert count == 2

    def test_find_audio_file_exists(self, temp_dirs):
        """Verify find_audio_file locates existing file."""
        os.makedirs(temp_dirs["encode"], exist_ok=True)
        audio_path = os.path.join(temp_dirs["encode"], "00000_audio_0.ac3")
        with open(audio_path, "w") as f:
            f.write("dummy")

        found = find_audio_file(temp_dirs["encode"], "00000", 0)
        assert found == audio_path

    def test_find_audio_file_missing(self, temp_dirs):
        """Verify find_audio_file returns None for missing file."""
        os.makedirs(temp_dirs["encode"], exist_ok=True)
        found = find_audio_file(temp_dirs["encode"], "00000", 0)
        assert found is None


class TestValidation:
    """Test validation functions."""

    def test_validate_m2ts_file_missing(self):
        """Verify validation fails for missing file."""
        assert validate_m2ts_file("/nonexistent/file.m2ts") is False

    def test_validate_m2ts_file_empty(self, temp_dirs):
        """Verify validation fails for empty file."""
        os.makedirs(temp_dirs["temp"], exist_ok=True)
        empty_file = os.path.join(temp_dirs["temp"], "empty.m2ts")
        with open(empty_file, "w"):
            pass

        assert validate_m2ts_file(empty_file) is False

    def test_validate_clpi_file_missing(self):
        """Verify validation fails for missing CLPI."""
        assert validate_clpi_file("/nonexistent/file.clpi") is False

    def test_validate_clpi_file_empty(self, temp_dirs):
        """Verify validation fails for empty CLPI."""
        os.makedirs(temp_dirs["temp"], exist_ok=True)
        empty_file = os.path.join(temp_dirs["temp"], "empty.clpi")
        with open(empty_file, "w"):
            pass

        assert validate_clpi_file(empty_file) is False

    def test_validate_clpi_file_real_header(self, temp_dirs):
        """Verify validation accepts real HDMV/CLPI 0100/0200 headers."""
        os.makedirs(temp_dirs["temp"], exist_ok=True)
        real_file = os.path.join(temp_dirs["temp"], "real.clpi")

        with open(real_file, "wb") as f:
            f.write(b"HDMV0200")
            f.write(b"\x00" * 24)

        assert validate_clpi_file(real_file) is True

    def test_validate_clpi_file_bad_header(self, temp_dirs):
        """Verify validation rejects CLPI with wrong magic bytes."""
        os.makedirs(temp_dirs["temp"], exist_ok=True)
        bad_file = os.path.join(temp_dirs["temp"], "bad.clpi")

        with open(bad_file, "wb") as f:
            f.write(b"CORRUPTED")

        assert validate_clpi_file(bad_file) is False

    def test_validate_bdmv_structure_missing_dirs(self, temp_dirs, null_logger):
        """Verify structure validation detects missing directories."""
        os.makedirs(temp_dirs["output"], exist_ok=True)

        result = validate_bdmv_structure(temp_dirs["output"], null_logger)

        assert result.valid is False
        assert len(result.missing_files) > 0

    def test_validate_bdmv_structure_complete(self, temp_dirs, null_logger):
        """Verify structure validation passes for complete BDMV."""
        # Create minimal BDMV structure
        os.makedirs(os.path.join(temp_dirs["output"], "BDMV/STREAM"), exist_ok=True)
        os.makedirs(os.path.join(temp_dirs["output"], "BDMV/CLIPINF"), exist_ok=True)
        os.makedirs(os.path.join(temp_dirs["output"], "BDMV/PLAYLIST"), exist_ok=True)

        # Create index.bdmv
        with open(os.path.join(temp_dirs["output"], "BDMV/index.bdmv"), "w") as f:
            f.write("dummy")

        result = validate_bdmv_structure(temp_dirs["output"], null_logger)

        # Should have no missing/corrupted required files
        assert "BDMV" not in result.missing_files
        assert "BDMV/index.bdmv" not in result.missing_files

    def test_check_output_size_within_limit(self, temp_dirs, null_logger):
        """Verify size check passes within limit."""
        os.makedirs(temp_dirs["temp"], exist_ok=True)
        test_file = os.path.join(temp_dirs["temp"], "test.bin")
        with open(test_file, "wb") as f:
            f.write(b"x" * 1000)

        fits, size_gb = check_output_size(test_file, 50, null_logger)
        assert fits is True
        assert size_gb > 0

    def test_check_output_size_exceeds_limit(self, temp_dirs, null_logger):
        """Verify size check detects oversized output."""
        os.makedirs(temp_dirs["temp"], exist_ok=True)
        test_file = os.path.join(temp_dirs["temp"], "test.bin")
        # Create a very large file (1 MB)
        with open(test_file, "wb") as f:
            f.write(b"x" * (1024 * 1024))

        fits, size_gb = check_output_size(test_file, 0, null_logger)  # 0 GB target
        assert fits is False


class TestISO:
    """Test ISO creation and burning."""

    def test_create_iso_missing_source(self, temp_dirs, null_logger):
        """Verify create_iso handles missing source."""
        result = create_iso(
            "/nonexistent/bdmv",
            os.path.join(temp_dirs["temp"], "output.iso"),
            null_logger,
        )

        # Should fail due to missing genisoimage or source
        # (depends on test environment)
        assert isinstance(result, ISOResult)

    def test_burn_iso_missing_file(self, temp_dirs, null_logger):
        """Verify burn_iso rejects missing ISO."""
        result = burn_iso(
            "/nonexistent/file.iso",
            "/dev/sr0",
            null_logger,
        )

        assert result.success is False
        assert "not found" in result.error_message

    def test_cleanup_iso_mounts_empty_list(self, null_logger):
        """Verify cleanup handles empty list."""
        result = cleanup_iso_mounts([], null_logger)
        assert result is True

    def test_cleanup_iso_mounts_nonexistent(self, null_logger):
        """Verify cleanup handles nonexistent paths."""
        result = cleanup_iso_mounts(["/nonexistent/path"], null_logger)
        # Should still return True (graceful)
        assert result is True


class TestRebuildStats:
    """Test RebuildStats dataclass."""

    def test_rebuild_stats_movie_only(self):
        """Verify movie-only rebuild stats."""
        stat = RebuildStats(
            mode="movie_only",
            video_tracks=1,
            audio_tracks_max=2,
            subtitle_tracks_max=1,
            remuxed_clips=0,
            copied_clips=0,
            success=True,
        )

        assert stat.mode == "movie_only"
        assert stat.success is True

    def test_rebuild_stats_surgical(self):
        """Verify surgical rebuild stats."""
        stat = RebuildStats(
            mode="surgical",
            video_tracks=5,
            audio_tracks_max=2,
            subtitle_tracks_max=1,
            remuxed_clips=3,
            copied_clips=2,
            success=True,
        )

        assert stat.mode == "surgical"
        assert stat.remuxed_clips == 3
        assert stat.copied_clips == 2


class TestValidationResult:
    """Test ValidationResult dataclass."""

    def test_validation_result_valid(self):
        """Verify valid result."""
        result = ValidationResult(
            valid=True,
            missing_files=[],
            corrupted_files=[],
            warnings=[],
            output_bytes=25000000000,
        )

        assert result.valid is True
        assert len(result.missing_files) == 0

    def test_validation_result_invalid(self):
        """Verify invalid result."""
        result = ValidationResult(
            valid=False,
            missing_files=["BDMV/index.bdmv"],
            corrupted_files=["BDMV/STREAM/00000.m2ts"],
            warnings=["Orphan CLPI without M2TS"],
            output_bytes=0,
        )

        assert result.valid is False
        assert len(result.missing_files) == 1
        assert len(result.corrupted_files) == 1


class TestSurgicalNoExtras:
    """Test that rebuild_surgical honors no_extras in the verbatim-copy pass."""

    def _make_config(self):
        from bd_shrink.config import Config

        return Config(source="s", output="o", codec="h264", nice=0)

    def test_no_extras_skips_extra_clip_copy(self, temp_dirs, null_logger):
        """With no_extras=True, an un-encoded extras clip is NOT copied out."""
        source_dir = os.path.join(temp_dirs["temp"], "src", "BDMV")
        os.makedirs(os.path.join(source_dir, "STREAM"), exist_ok=True)
        os.makedirs(os.path.join(source_dir, "CLIPINF"), exist_ok=True)
        os.makedirs(os.path.join(source_dir, "PLAYLIST"), exist_ok=True)

        # Source has a main clip and an extras clip in STREAM (un-encoded).
        for cid in ("00001", "00002"):
            with open(os.path.join(source_dir, "STREAM", f"{cid}.m2ts"), "wb") as f:
                f.write(b"\x00" * 16)
            with open(os.path.join(source_dir, "CLIPINF", f"{cid}.clpi"), "wb") as f:
                f.write(b"\x00" * 16)

        output_dir = temp_dirs["output"]
        os.makedirs(temp_dirs["encode"], exist_ok=True)
        os.makedirs(temp_dirs["work"], exist_ok=True)

        # tsMuxeR resolved but never actually needed (no encoded videos present).
        with (
            patch("bd_shrink.rebuild.find_tool", return_value="/usr/bin/tsMuxeR"),
            patch("bd_shrink.rebuild.run_simple") as mock_simple,
            patch("bd_shrink.rebuild.run_managed") as mock_managed,
        ):
            mock_simple.return_value = MagicMock(succeeded=True)
            mock_managed.return_value = MagicMock(succeeded=True)

            rebuild_surgical(
                source_dir=source_dir,
                encode_dir=temp_dirs["encode"],
                output_dir=output_dir,
                work_dir=temp_dirs["work"],
                main_clips=["00001"],
                extras_clips=["00002"],
                config=self._make_config(),
                clip_fps_map={"00001": "23.976", "00002": "23.976"},
                no_extras=True,
                logger=null_logger,
            )

        # Verify which clips were copied via run_simple cp calls.
        copied = [
            call.args[0]
            for call in mock_simple.call_args_list
            if call.args and call.args[0][:1] == ["cp"]
        ]
        copied_targets = " ".join(" ".join(c) for c in copied)
        assert "00001.m2ts" in copied_targets  # main copied
        assert "00002.m2ts" not in copied_targets  # extra NOT copied

    def test_extras_included_when_not_no_extras(self, temp_dirs, null_logger):
        """With no_extras=False, the extras clip IS copied out."""
        source_dir = os.path.join(temp_dirs["temp"], "src2", "BDMV")
        os.makedirs(os.path.join(source_dir, "STREAM"), exist_ok=True)
        os.makedirs(os.path.join(source_dir, "CLIPINF"), exist_ok=True)
        os.makedirs(os.path.join(source_dir, "PLAYLIST"), exist_ok=True)

        for cid in ("00001", "00002"):
            with open(os.path.join(source_dir, "STREAM", f"{cid}.m2ts"), "wb") as f:
                f.write(b"\x00" * 16)
            with open(os.path.join(source_dir, "CLIPINF", f"{cid}.clpi"), "wb") as f:
                f.write(b"\x00" * 16)

        output_dir = temp_dirs["output"]
        os.makedirs(temp_dirs["encode"], exist_ok=True)
        os.makedirs(temp_dirs["work"], exist_ok=True)

        with (
            patch("bd_shrink.rebuild.find_tool", return_value="/usr/bin/tsMuxeR"),
            patch("bd_shrink.rebuild.run_simple") as mock_simple,
            patch("bd_shrink.rebuild.run_managed") as mock_managed,
        ):
            mock_simple.return_value = MagicMock(succeeded=True)
            mock_managed.return_value = MagicMock(succeeded=True)

            rebuild_surgical(
                source_dir=source_dir,
                encode_dir=temp_dirs["encode"],
                output_dir=output_dir,
                work_dir=temp_dirs["work"],
                main_clips=["00001"],
                extras_clips=["00002"],
                config=self._make_config(),
                clip_fps_map={"00001": "23.976", "00002": "23.976"},
                no_extras=False,
                logger=null_logger,
            )

        copied = [
            call.args[0]
            for call in mock_simple.call_args_list
            if call.args and call.args[0][:1] == ["cp"]
        ]
        copied_targets = " ".join(" ".join(c) for c in copied)
        assert "00001.m2ts" in copied_targets
        assert "00002.m2ts" in copied_targets  # extra copied when not no_extras

    def test_orphan_clips_copied_when_not_no_extras(self, temp_dirs, null_logger):
        """With no_extras=False, orphan clips not in MPLS are also copied."""
        source_dir = os.path.join(temp_dirs["temp"], "src2", "BDMV")
        os.makedirs(os.path.join(source_dir, "STREAM"), exist_ok=True)
        os.makedirs(os.path.join(source_dir, "CLIPINF"), exist_ok=True)
        os.makedirs(os.path.join(source_dir, "PLAYLIST"), exist_ok=True)

        for cid in ("00001", "00002", "00003"):
            with open(os.path.join(source_dir, "STREAM", f"{cid}.m2ts"), "wb") as f:
                f.write(b"\x00" * 16)
            with open(os.path.join(source_dir, "CLIPINF", f"{cid}.clpi"), "wb") as f:
                f.write(b"\x00" * 16)

        output_dir = temp_dirs["output"]
        os.makedirs(temp_dirs["encode"], exist_ok=True)
        os.makedirs(temp_dirs["work"], exist_ok=True)

        with (
            patch("bd_shrink.rebuild.find_tool", return_value="/usr/bin/tsMuxeR"),
            patch("bd_shrink.rebuild.run_simple") as mock_simple,
            patch("bd_shrink.rebuild.run_managed") as mock_managed,
        ):
            mock_simple.return_value = MagicMock(succeeded=True)
            mock_managed.return_value = MagicMock(succeeded=True)

            rebuild_surgical(
                source_dir=source_dir,
                encode_dir=temp_dirs["encode"],
                output_dir=output_dir,
                work_dir=temp_dirs["work"],
                main_clips=["00001"],
                extras_clips=["00002"],
                config=self._make_config(),
                clip_fps_map={"00001": "23.976", "00002": "23.976"},
                no_extras=False,
                logger=null_logger,
            )

        copied = [
            call.args[0]
            for call in mock_simple.call_args_list
            if call.args and call.args[0][:1] == ["cp"]
        ]
        copied_targets = " ".join(" ".join(c) for c in copied)
        assert "00001.m2ts" in copied_targets
        assert "00002.m2ts" in copied_targets
        assert "00003.m2ts" in copied_targets  # orphan copied when not no_extras

    def test_orphan_clips_skipped_when_no_extras(self, temp_dirs, null_logger):
        """With no_extras=True, orphan clips are also skipped."""
        source_dir = os.path.join(temp_dirs["temp"], "src3", "BDMV")
        os.makedirs(os.path.join(source_dir, "STREAM"), exist_ok=True)
        os.makedirs(os.path.join(source_dir, "CLIPINF"), exist_ok=True)
        os.makedirs(os.path.join(source_dir, "PLAYLIST"), exist_ok=True)

        for cid in ("00001", "00003"):
            with open(os.path.join(source_dir, "STREAM", f"{cid}.m2ts"), "wb") as f:
                f.write(b"\x00" * 16)
            with open(os.path.join(source_dir, "CLIPINF", f"{cid}.clpi"), "wb") as f:
                f.write(b"\x00" * 16)

        output_dir = temp_dirs["output"]
        os.makedirs(temp_dirs["encode"], exist_ok=True)
        os.makedirs(temp_dirs["work"], exist_ok=True)

        with (
            patch("bd_shrink.rebuild.find_tool", return_value="/usr/bin/tsMuxeR"),
            patch("bd_shrink.rebuild.run_simple") as mock_simple,
            patch("bd_shrink.rebuild.run_managed") as mock_managed,
        ):
            mock_simple.return_value = MagicMock(succeeded=True)
            mock_managed.return_value = MagicMock(succeeded=True)

            rebuild_surgical(
                source_dir=source_dir,
                encode_dir=temp_dirs["encode"],
                output_dir=output_dir,
                work_dir=temp_dirs["work"],
                main_clips=["00001"],
                extras_clips=["00003"],
                config=self._make_config(),
                clip_fps_map={"00001": "23.976", "00003": "23.976"},
                no_extras=True,
                logger=null_logger,
            )

        copied = [
            call.args[0]
            for call in mock_simple.call_args_list
            if call.args and call.args[0][:1] == ["cp"]
        ]
        copied_targets = " ".join(" ".join(c) for c in copied)
        assert "00001.m2ts" in copied_targets
        assert "00003.m2ts" not in copied_targets  # orphan and extra skipped

    def test_tsmuxer_rename_output(self, temp_dirs, null_logger):
        """tsMuxeR outputs 00000.m2ts/00000.clpi; rebuild renames to clip_id."""
        source_dir = os.path.join(temp_dirs["temp"], "src3", "BDMV")
        os.makedirs(os.path.join(source_dir, "STREAM"), exist_ok=True)
        os.makedirs(os.path.join(source_dir, "CLIPINF"), exist_ok=True)
        os.makedirs(os.path.join(source_dir, "PLAYLIST"), exist_ok=True)

        # Only the encoded clip exists in source STREAM
        with open(os.path.join(source_dir, "STREAM", "00005.m2ts"), "wb") as f:
            f.write(b"\x00" * 16)
        with open(os.path.join(source_dir, "CLIPINF", "00005.clpi"), "wb") as f:
            f.write(b"\x00" * 16)

        output_dir = temp_dirs["output"]
        encode_dir = temp_dirs["encode"]
        work_dir = temp_dirs["work"]
        os.makedirs(encode_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)

        # Create a fake encoded video for clip 00005
        with open(os.path.join(encode_dir, "00005_video.h264"), "wb") as f:
            f.write(b"h264data")

        def fake_run_managed(cmd, **kwargs):
            # Simulate tsMuxeR writing 00000.m2ts and 00000.clpi in the requested output dir
            out = cmd[-1]
            os.makedirs(os.path.join(out, "BDMV", "STREAM"), exist_ok=True)
            os.makedirs(os.path.join(out, "BDMV", "CLIPINF"), exist_ok=True)
            with open(os.path.join(out, "BDMV", "STREAM", "00000.m2ts"), "wb") as f:
                f.write(b"muxed00005")
            with open(os.path.join(out, "BDMV", "CLIPINF", "00000.clpi"), "wb") as f:
                f.write(b"HDMV0200")
            return MagicMock(succeeded=True)

        def fake_run_simple(cmd, **kwargs):
            # Just copy manually so the assertion works even if run_simple is mocked
            if cmd[0] == "cp" and len(cmd) == 3:
                import shutil

                shutil.copy(cmd[1], cmd[2])
            return MagicMock(succeeded=True)

        with (
            patch("bd_shrink.rebuild.find_tool", return_value="/usr/bin/tsMuxeR"),
            patch("bd_shrink.rebuild.run_simple", side_effect=fake_run_simple),
            patch("bd_shrink.rebuild.run_managed", side_effect=fake_run_managed),
        ):
            rebuild_surgical(
                source_dir=source_dir,
                encode_dir=encode_dir,
                output_dir=output_dir,
                work_dir=work_dir,
                main_clips=["00005"],
                extras_clips=[],
                config=self._make_config(),
                clip_fps_map={"00005": "23.976"},
                no_extras=False,
                logger=null_logger,
            )

        assert os.path.isfile(os.path.join(output_dir, "BDMV", "STREAM", "00005.m2ts"))
        assert os.path.isfile(os.path.join(output_dir, "BDMV", "CLIPINF", "00005.clpi"))
        # Verify the file was copied from the renamed tsMuxeR output
        with open(os.path.join(output_dir, "BDMV", "STREAM", "00005.m2ts"), "rb") as f:
            assert f.read() == b"muxed00005"

    def test_tsmuxer_missing_fails_gracefully(self, temp_dirs, null_logger):
        """If tsMuxeR isn't found, rebuild_surgical returns a failed result."""
        source_dir = os.path.join(temp_dirs["temp"], "src3", "BDMV")
        os.makedirs(os.path.join(source_dir, "STREAM"), exist_ok=True)

        with patch("bd_shrink.rebuild.find_tool", return_value=None):
            result = rebuild_surgical(
                source_dir=source_dir,
                encode_dir=temp_dirs["encode"],
                output_dir=temp_dirs["output"],
                work_dir=temp_dirs["work"],
                main_clips=["00001"],
                extras_clips=[],
                config=self._make_config(),
                clip_fps_map={},
                no_extras=False,
                logger=null_logger,
            )
        assert result.success is False
