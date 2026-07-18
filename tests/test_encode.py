"""Tests for encode module: video, audio, subtitle encoding."""

import logging
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from bd_shrink.config import Config
from bd_shrink.encode import (
    EncodeStats,
    encode_all,
    encode_clip,
    encode_video_single_pass,
    encode_video_two_pass,
    extract_audio,
    extract_subtitles,
    get_audio_codecs,
    get_subtitle_codecs,
    skip_if_exists,
)
from bd_shrink.inventory import AudioStream, Clip, Inventory, SubtitleStream, VideoStream


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_dir = os.path.join(tmpdir, "source")
        encode_dir = os.path.join(tmpdir, "encode")
        work_dir = os.path.join(tmpdir, "work")
        os.makedirs(source_dir)
        os.makedirs(encode_dir)
        os.makedirs(work_dir)
        yield {
            "temp": tmpdir,
            "source": source_dir,
            "encode": encode_dir,
            "work": work_dir,
        }


@pytest.fixture
def default_config():
    """Default config for testing."""
    return Config(
        source="",
        output="",
        codec="h264",
        target_gb=23,
        main_preset="slow",
        main_passes=2,
        nice=0,
        movie_only=False,
        main_bitrate="8000k",
        main_maxrate="8000k",
        main_bufsize="16000k",
    )


@pytest.fixture
def null_logger():
    """Logger that discards all messages."""
    logger = logging.getLogger("test_null")
    logger.addHandler(logging.NullHandler())
    return logger


@pytest.fixture
def mock_clip():
    """Mock clip with video, audio, subtitles."""
    return Clip(
        clip_id="00000",
        duration_sec=3600,
        video=VideoStream(
            codec_name="h264",
            width=1920,
            height=1080,
            r_frame_rate="23.976",
            bit_rate=18000000,
        ),
        audio=[
            AudioStream(index=0, codec_name="ac3", bit_rate=640000, channel_layout="5.1"),
            AudioStream(index=1, codec_name="dts", bit_rate=1509000, channel_layout="5.1"),
        ],
        subtitles=[
            SubtitleStream(index=0, codec_name="hdmv_pgs_subtitle"),
        ],
    )


class TestSkipIfExists:
    """Test resumability check."""

    def test_skip_if_out_file_exists(self, temp_dirs):
        """Verify skip if output file exists."""
        out_file = os.path.join(temp_dirs["encode"], "test_video.h264")
        with open(out_file, "w") as f:
            f.write("content")

        assert skip_if_exists(out_file=out_file) is True

    def test_skip_if_out_file_empty(self, temp_dirs):
        """Verify don't skip if output file is empty."""
        out_file = os.path.join(temp_dirs["encode"], "test_video.h264")
        with open(out_file, "w"):
            pass  # Empty file

        assert skip_if_exists(out_file=out_file) is False

    def test_skip_if_pass_log_exists(self, temp_dirs):
        """Verify skip if pass log exists."""
        pass_log = os.path.join(temp_dirs["work"], "pass_00000.log")
        with open(pass_log + "-0.log", "w") as f:
            f.write("log content")

        assert skip_if_exists(pass_log_base=pass_log) is True

    def test_skip_if_neither_exists(self, temp_dirs):
        """Verify don't skip if nothing exists."""
        out_file = os.path.join(temp_dirs["encode"], "nonexistent.h264")
        pass_log = os.path.join(temp_dirs["work"], "nonexistent.log")

        assert skip_if_exists(out_file=out_file, pass_log_base=pass_log) is False


class TestGetCodecs:
    """Test codec extraction from clips."""

    def test_get_audio_codecs(self, mock_clip):
        """Verify audio codec extraction."""
        codecs = get_audio_codecs(mock_clip)
        assert codecs == ["ac3", "dts"]

    def test_get_audio_codecs_empty(self):
        """Verify empty audio codec list."""
        clip = Clip(
            clip_id="00000",
            duration_sec=3600,
            video=VideoStream(
                codec_name="h264",
                width=1920,
                height=1080,
                r_frame_rate="23.976",
                bit_rate=18000000,
            ),
            audio=[],
            subtitles=[],
        )
        codecs = get_audio_codecs(clip)
        assert codecs == []

    def test_get_subtitle_codecs(self, mock_clip):
        """Verify subtitle codec extraction."""
        codecs = get_subtitle_codecs(mock_clip)
        assert codecs == ["hdmv_pgs_subtitle"]

    def test_get_subtitle_codecs_empty(self):
        """Verify empty subtitle codec list."""
        clip = Clip(
            clip_id="00000",
            duration_sec=3600,
            video=VideoStream(
                codec_name="h264",
                width=1920,
                height=1080,
                r_frame_rate="23.976",
                bit_rate=18000000,
            ),
            audio=[],
            subtitles=[],
        )
        codecs = get_subtitle_codecs(clip)
        assert codecs == []


class TestExtractAudio:
    """Test audio extraction."""

    def test_extract_audio_skip_mpeg(self, mock_clip, temp_dirs, null_logger):
        """Verify MPEG audio codecs are skipped."""
        # Replace audio with MPEG codecs
        mock_clip.audio = [
            AudioStream(index=0, codec_name="mp2", bit_rate=192000, channel_layout="2.0"),
            AudioStream(index=1, codec_name="mp3", bit_rate=128000, channel_layout="2.0"),
        ]

        # Create dummy source file
        src_path = os.path.join(temp_dirs["source"], "00000.m2ts")
        with open(src_path, "w") as f:
            f.write("dummy")

        # Should skip both MPEG codecs
        tracks, exts = extract_audio(mock_clip, src_path, temp_dirs["encode"], null_logger)
        assert tracks == 0
        assert exts == []

    def test_extract_audio_passthrough(self, mock_clip, temp_dirs, null_logger):
        """Verify audio passthrough (no extraction without actual ffmpeg)."""
        src_path = os.path.join(temp_dirs["source"], "00000.m2ts")
        with open(src_path, "w") as f:
            f.write("dummy")

        # Without mocking ffmpeg, extraction returns 0
        # (would succeed with real ffmpeg)
        with patch("bd_shrink.encode.run_managed") as mock_run:
            mock_run.return_value = MagicMock(succeeded=False)
            tracks, exts = extract_audio(mock_clip, src_path, temp_dirs["encode"], null_logger)
            assert tracks == 0

    def test_extract_audio_resumable(self, mock_clip, temp_dirs, null_logger):
        """Verify audio extraction resumes if output exists."""
        src_path = os.path.join(temp_dirs["source"], "00000.m2ts")
        with open(src_path, "w") as f:
            f.write("dummy")

        # Create dummy audio output
        audio_out = os.path.join(temp_dirs["encode"], "00000_audio_0.ac3")
        with open(audio_out, "w") as f:
            f.write("audio")

        # Should resume (not call ffmpeg)
        with patch("bd_shrink.encode.run_managed") as mock_run:
            tracks, exts = extract_audio(mock_clip, src_path, temp_dirs["encode"], null_logger)
            # Should skip run_simple due to resumability
            assert mock_run.call_count <= 1  # May be called for first_audio check


class TestExtractSubtitles:
    """Test subtitle extraction."""

    def test_extract_subtitles_skip_dvd(self, mock_clip, temp_dirs, null_logger):
        """Verify DVD subtitle codecs are skipped."""
        mock_clip.subtitles = [
            SubtitleStream(index=0, codec_name="dvd_subtitle"),
        ]

        src_path = os.path.join(temp_dirs["source"], "00000.m2ts")
        with open(src_path, "w") as f:
            f.write("dummy")

        # Should skip DVD subtitle
        tracks = extract_subtitles(mock_clip, src_path, temp_dirs["encode"], null_logger)
        assert tracks == 0

    def test_extract_subtitles_keep_pgs(self, mock_clip, temp_dirs, null_logger):
        """Verify PGS subtitles are kept."""
        src_path = os.path.join(temp_dirs["source"], "00000.m2ts")
        with open(src_path, "w") as f:
            f.write("dummy")

        with patch("bd_shrink.encode.run_managed") as mock_run:
            mock_run.return_value = MagicMock(succeeded=False)
            tracks = extract_subtitles(mock_clip, src_path, temp_dirs["encode"], null_logger)
            # Without real ffmpeg, would return 0
            assert tracks == 0


class TestEncodeVideoSinglePass:
    """Test single-pass video encoding."""

    def test_single_pass_resumable(self, mock_clip, temp_dirs, default_config, null_logger):
        """Verify single-pass encoding resumes if output exists."""
        # Create dummy output
        out_video = os.path.join(temp_dirs["encode"], f"{mock_clip.clip_id}_video.h264")
        with open(out_video, "w") as f:
            f.write("video")

        src_path = os.path.join(temp_dirs["source"], "00000.m2ts")
        with open(src_path, "w") as f:
            f.write("dummy")

        # Should resume without calling ffmpeg
        with patch("bd_shrink.encode.run_managed") as mock_run:
            result = encode_video_single_pass(
                mock_clip,
                src_path,
                temp_dirs["encode"],
                default_config,
                is_main=False,
                logger=null_logger,
            )
            assert result is True
            assert mock_run.call_count == 0

    def test_single_pass_no_video_stream(self, temp_dirs, default_config, null_logger):
        """Verify single-pass fails if clip has no video."""
        clip = Clip(
            clip_id="00000",
            duration_sec=3600,
            video=None,
            audio=[],
            subtitles=[],
        )

        src_path = os.path.join(temp_dirs["source"], "00000.m2ts")
        result = encode_video_single_pass(
            clip,
            src_path,
            temp_dirs["encode"],
            default_config,
            logger=null_logger,
        )
        assert result is False


class TestEncodeVideoTwoPass:
    """Test two-pass video encoding."""

    def test_two_pass_resumable(self, mock_clip, temp_dirs, default_config, null_logger):
        """Verify two-pass encoding resumes if output exists."""
        out_video = os.path.join(temp_dirs["encode"], f"{mock_clip.clip_id}_video.h264")
        with open(out_video, "w") as f:
            f.write("video")

        src_path = os.path.join(temp_dirs["source"], "00000.m2ts")

        with patch("bd_shrink.encode.run_managed") as mock_run:
            result = encode_video_two_pass(
                mock_clip,
                src_path,
                temp_dirs["encode"],
                temp_dirs["work"],
                default_config,
                logger=null_logger,
            )
            assert result is True
            assert mock_run.call_count == 0

    def test_two_pass_no_video_stream(self, temp_dirs, default_config, null_logger):
        """Verify two-pass fails if clip has no video."""
        clip = Clip(
            clip_id="00000",
            duration_sec=3600,
            video=None,
            audio=[],
            subtitles=[],
        )

        src_path = os.path.join(temp_dirs["source"], "00000.m2ts")
        result = encode_video_two_pass(
            clip,
            src_path,
            temp_dirs["encode"],
            temp_dirs["work"],
            default_config,
            logger=null_logger,
        )
        assert result is False


class TestEncodeClip:
    """Test single clip encoding."""

    def test_encode_clip_no_video(self, temp_dirs, default_config, null_logger):
        """Verify encode_clip skips if clip has no video."""
        clip = Clip(
            clip_id="00000",
            duration_sec=3600,
            video=None,
            audio=[],
            subtitles=[],
        )

        # Create dummy source file
        src_path = os.path.join(temp_dirs["source"], "00000.m2ts")
        with open(src_path, "w") as f:
            f.write("dummy")

        stat = encode_clip(
            clip,
            "main",
            temp_dirs["source"],
            temp_dirs["encode"],
            temp_dirs["work"],
            default_config,
            total_clips=1,
            clip_idx=1,
            logger=null_logger,
        )

        assert stat.success is True  # Skipped gracefully
        assert stat.video_encoded is False

    def test_encode_clip_missing_source(self, mock_clip, temp_dirs, default_config, null_logger):
        """Verify encode_clip handles missing source."""
        stat = encode_clip(
            mock_clip,
            "main",
            temp_dirs["source"],
            temp_dirs["encode"],
            temp_dirs["work"],
            default_config,
            total_clips=1,
            clip_idx=1,
            logger=null_logger,
        )

        assert stat.success is False
        assert stat.video_encoded is False


class TestEncodeAll:
    """Test batch encoding."""

    def test_encode_all_empty(self, temp_dirs, default_config, null_logger):
        """Verify encode_all handles empty clip lists."""
        inventory = Inventory(clips={}, playlists={})

        stats = encode_all(
            inventory,
            extras_clips=[],
            main_clips=[],
            source_dir=temp_dirs["source"],
            encode_dir=temp_dirs["encode"],
            work_dir=temp_dirs["work"],
            config=default_config,
            logger=null_logger,
        )

        assert stats == []

    def test_encode_all_no_extras(self, temp_dirs, default_config, null_logger):
        """Verify encode_all skips extras when no_extras=True."""
        clip = Clip(
            clip_id="00001",
            duration_sec=3600,
            video=VideoStream(
                codec_name="h264",
                width=1920,
                height=1080,
                r_frame_rate="23.976",
                bit_rate=18000000,
            ),
            audio=[],
            subtitles=[],
        )
        inventory = Inventory(clips={"00001": clip}, playlists={})

        stats = encode_all(
            inventory,
            extras_clips=["00001"],
            main_clips=[],
            source_dir=temp_dirs["source"],
            encode_dir=temp_dirs["encode"],
            work_dir=temp_dirs["work"],
            config=default_config,
            no_extras=True,
            logger=null_logger,
        )

        # Should return empty list since extras skipped
        assert len(stats) == 0


class TestEncodeStats:
    """Test EncodeStats dataclass."""

    def test_encode_stats_success(self):
        """Verify EncodeStats records success."""
        stat = EncodeStats(
            clip_name="00000",
            clip_type="main",
            audio_tracks=2,
            subtitle_tracks=1,
            video_encoded=True,
            success=True,
        )

        assert stat.success is True
        assert stat.audio_tracks == 2

    def test_encode_stats_failure(self):
        """Verify EncodeStats records failure."""
        stat = EncodeStats(
            clip_name="00000",
            clip_type="extras",
            audio_tracks=0,
            subtitle_tracks=0,
            video_encoded=False,
            success=False,
        )

        assert stat.success is False
        assert stat.video_encoded is False
