"""Tests for audio module: codecs, extensions, bitrate estimation."""

import os
import tempfile

import pytest

from bd_shrink import audio


class TestCodecToExtension:
    """Test codec to file extension mapping."""

    def test_ac3_extension(self):
        """Verify AC-3 maps to .ac3."""
        assert audio.audio_ext("ac3") == ".ac3"

    def test_eac3_extension(self):
        """Verify E-AC-3 maps to .eac3."""
        assert audio.audio_ext("eac3") == ".eac3"

    def test_dts_extension(self):
        """Verify DTS maps to .dts."""
        assert audio.audio_ext("dts") == ".dts"

    def test_truehd_extension(self):
        """Verify TrueHD maps to .thd."""
        assert audio.audio_ext("truehd") == ".thd"

    def test_pcm_bluray_extension(self):
        """Verify PCM Blu-ray maps to .wav."""
        assert audio.audio_ext("pcm_bluray") == ".w64"

    def test_pcm_variants_extension(self):
        """Verify PCM variants map to .wav."""
        assert audio.audio_ext("pcm_s16be") == ".wav"
        assert audio.audio_ext("pcm_s24be") == ".wav"
        assert audio.audio_ext("pcm_s16le") == ".wav"

    def test_unknown_codec_defaults_to_ac3(self):
        """Verify unknown codec defaults to .ac3."""
        assert audio.audio_ext("unknown_codec") == ".ac3"


class TestExtensionToTsMuxerType:
    """Test extension to tsMuxeR audio type mapping."""

    def test_ac3_tsmuxer_type(self):
        """Verify .ac3 maps to A_AC3."""
        assert audio.tsmuxer_type(".ac3") == "A_AC3"

    def test_eac3_tsmuxer_type(self):
        """Verify .eac3 maps to A_EAC3."""
        assert audio.tsmuxer_type(".eac3") == "A_EAC3"

    def test_dts_tsmuxer_type(self):
        """Verify .dts maps to A_DTS."""
        assert audio.tsmuxer_type(".dts") == "A_DTS"

    def test_truehd_tsmuxer_type(self):
        """Verify .thd maps to A_TRUEHD."""
        assert audio.tsmuxer_type(".thd") == "A_TRUEHD"

    def test_wav_tsmuxer_type(self):
        """Verify .wav maps to A_LPCM."""
        assert audio.tsmuxer_type(".wav") == "A_LPCM"

    def test_tsmuxer_type_with_codec_name(self):
        """Verify tsmuxer_type() converts codec name to type."""
        assert audio.tsmuxer_type("ac3") == "A_AC3"
        assert audio.tsmuxer_type("dts") == "A_DTS"
        assert audio.tsmuxer_type("truehd") == "A_TRUEHD"

    def test_w64_tsmuxer_type(self):
        """Verify .w64 maps to A_LPCM."""
        assert audio.tsmuxer_type(".w64") == "A_LPCM"


class TestFormatOverride:
    """Test AUDIO_FORMAT_OVERRIDE and AUDIO_TRANSCODE maps."""

    def test_pcm_bluray_transcode_little_endian(self):
        """pcm_bluray must transcode to little-endian PCM for W64 container."""
        assert audio.AUDIO_TRANSCODE["pcm_bluray"] == "pcm_s24le"
        assert audio.AUDIO_FORMAT_OVERRIDE["pcm_bluray"] == "w64"

    def test_truehd_format_override(self):
        """TrueHD needs explicit -f truehd for ffmpeg."""
        assert audio.AUDIO_FORMAT_OVERRIDE["truehd"] == "truehd"

    def test_w64_only_little_endian(self):
        """W64/WAV containers reject big-endian PCM; ensure we never pair w64 with s24be."""
        from bd_shrink.audio import AUDIO_FORMAT_OVERRIDE, AUDIO_TRANSCODE

        for codec, fmt in AUDIO_FORMAT_OVERRIDE.items():
            if fmt in ("wav", "w64"):
                transcode_codec = AUDIO_TRANSCODE.get(codec, "")
                if transcode_codec:
                    assert transcode_codec.endswith("le"), (
                        f"{codec}: {fmt} requires little-endian PCM, got {transcode_codec}"
                    )


class TestSkipCodecs:
    """Test MPEG audio skip logic."""

    def test_skip_mp3(self):
        """Verify mp3 is skipped."""
        assert audio.should_skip("mp3") is True

    def test_skip_mp3float(self):
        """Verify mp3float is skipped."""
        assert audio.should_skip("mp3float") is True

    def test_skip_mp2(self):
        """Verify mp2 is skipped."""
        assert audio.should_skip("mp2") is True

    def test_skip_mp2float(self):
        """Verify mp2float is skipped."""
        assert audio.should_skip("mp2float") is True

    def test_dont_skip_ac3(self):
        """Verify ac3 is not skipped."""
        assert audio.should_skip("ac3") is False

    def test_dont_skip_dts(self):
        """Verify dts is not skipped."""
        assert audio.should_skip("dts") is False


class TestBitrateEstimation:
    """Test bitrate estimation for budget calculations."""

    def test_estimate_with_source_bitrate(self):
        """Verify source bitrate is used when available."""
        result = audio.estimate_bitrate("ac3", source_bitrate=640_000)
        assert result == 640_000

    def test_estimate_dts_fallback(self):
        """Verify DTS fallback when source bitrate is 0."""
        result = audio.estimate_bitrate("dts", source_bitrate=0)
        assert result == 1_509_000

    def test_estimate_truehd_fallback(self):
        """Verify TrueHD fallback when source bitrate is 0."""
        result = audio.estimate_bitrate("truehd", source_bitrate=0)
        assert result == 2_000_000

    def test_estimate_eac3_fallback(self):
        """Verify E-AC-3 fallback when source bitrate is 0."""
        result = audio.estimate_bitrate("eac3", source_bitrate=0)
        assert result == 1_536_000

    def test_estimate_pcm_fallback(self):
        """Verify PCM Blu-ray fallback when source bitrate is 0."""
        result = audio.estimate_bitrate("pcm_bluray", source_bitrate=0)
        assert result == 4_608_000

    def test_estimate_unknown_codec_extras_default(self):
        """Verify unknown codec uses extras default (256k) when is_main=False."""
        result = audio.estimate_bitrate("unknown", source_bitrate=0, is_main=False)
        assert result == 256_000

    def test_estimate_unknown_codec_main_default(self):
        """Verify unknown codec uses main default (640k) when is_main=True."""
        result = audio.estimate_bitrate("unknown", source_bitrate=0, is_main=True)
        assert result == 640_000


class TestCountAudio:
    """Test audio track counting."""

    def test_count_audio_no_tracks(self):
        """Verify count_audio returns 0 when no tracks exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            count = audio.count_audio(tmpdir, "00000")
            assert count == 0

    def test_count_audio_single_track(self):
        """Verify count_audio finds single AC-3 track."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a dummy audio file
            open(os.path.join(tmpdir, "00000_audio_0.ac3"), "w").close()
            count = audio.count_audio(tmpdir, "00000")
            assert count == 1

    def test_count_audio_multiple_tracks(self):
        """Verify count_audio counts multiple tracks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "00000_audio_0.ac3"), "w").close()
            open(os.path.join(tmpdir, "00000_audio_1.dts"), "w").close()
            open(os.path.join(tmpdir, "00000_audio_2.eac3"), "w").close()
            count = audio.count_audio(tmpdir, "00000")
            assert count == 3

    def test_count_audio_with_extensions(self):
        """Verify count_audio recognizes all supported extensions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exts = ["ac3", "eac3", "dts", "thd", "wav"]
            for i, ext in enumerate(exts):
                open(os.path.join(tmpdir, f"00000_audio_{i}.{ext}"), "w").close()
            count = audio.count_audio(tmpdir, "00000")
            assert count == len(exts)


class TestFindAudio:
    """Test audio file discovery."""

    def test_find_audio_ac3(self):
        """Verify find_audio locates AC-3 track."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "00000_audio_0.ac3")
            open(path, "w").close()
            found = audio.find_audio(tmpdir, "00000", 0)
            assert found == path

    def test_find_audio_dts(self):
        """Verify find_audio locates DTS track."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "00000_audio_1.dts")
            open(path, "w").close()
            found = audio.find_audio(tmpdir, "00000", 1)
            assert found == path

    def test_find_audio_not_found(self):
        """Verify find_audio raises FileNotFoundError when track missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                audio.find_audio(tmpdir, "00000", 0)

    def test_find_audio_multiple_tracks(self):
        """Verify find_audio finds correct track by index."""
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "00000_audio_0.ac3"), "w").close()
            open(os.path.join(tmpdir, "00000_audio_1.dts"), "w").close()

            path0 = audio.find_audio(tmpdir, "00000", 0)
            assert "audio_0.ac3" in path0

            path1 = audio.find_audio(tmpdir, "00000", 1)
            assert "audio_1.dts" in path1


class TestGetAudioTracksFromClipData:
    """Test audio track extraction from clip data."""

    def test_audio_tracks_single_ac3(self):
        """Verify single AC-3 track counts correctly."""
        clip_audio = [{"codec_name": "ac3", "bit_rate": 640_000}]
        count, bitrate = audio.get_audio_tracks_from_clip_data(clip_audio, is_main=True)
        assert count == 1
        assert bitrate == 640_000

    def test_audio_tracks_multiple_codecs(self):
        """Verify multiple audio tracks with different codecs."""
        clip_audio = [
            {"codec_name": "ac3", "bit_rate": 640_000},
            {"codec_name": "dts", "bit_rate": 1_509_000},
        ]
        count, bitrate = audio.get_audio_tracks_from_clip_data(clip_audio, is_main=True)
        assert count == 2
        assert bitrate == 640_000 + 1_509_000

    def test_audio_tracks_skip_mpeg(self):
        """Verify MPEG audio is skipped."""
        clip_audio = [
            {"codec_name": "ac3", "bit_rate": 640_000},
            {"codec_name": "mp3", "bit_rate": 192_000},  # Should be skipped
            {"codec_name": "dts", "bit_rate": 1_509_000},
        ]
        count, bitrate = audio.get_audio_tracks_from_clip_data(clip_audio, is_main=True)
        assert count == 2  # mp3 not counted
        assert bitrate == 640_000 + 1_509_000  # mp3 not included

    def test_audio_tracks_zero_bitrate_with_fallback(self):
        """Verify fallback bitrate when source bitrate is 0."""
        clip_audio = [
            {"codec_name": "ac3", "bit_rate": 640_000},
            {"codec_name": "dts", "bit_rate": 0},  # Use fallback
        ]
        count, bitrate = audio.get_audio_tracks_from_clip_data(clip_audio, is_main=True)
        assert count == 2
        # 640k for AC-3 + 1509k fallback for DTS
        assert bitrate == 640_000 + 1_509_000

    def test_audio_tracks_main_vs_extras_default(self):
        """Verify different defaults for main vs extras."""
        clip_audio = [{"codec_name": "unknown_codec", "bit_rate": 0}]

        # Extras: should use 256k default
        _, bitrate_extras = audio.get_audio_tracks_from_clip_data(clip_audio, is_main=False)
        assert bitrate_extras == 256_000

        # Main: should use 640k default
        _, bitrate_main = audio.get_audio_tracks_from_clip_data(clip_audio, is_main=True)
        assert bitrate_main == 640_000

    def test_audio_tracks_empty_list(self):
        """Verify empty audio list returns 0,0."""
        clip_audio = []
        count, bitrate = audio.get_audio_tracks_from_clip_data(clip_audio, is_main=True)
        assert count == 0
        assert bitrate == 0
