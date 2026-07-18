"""Tests for inventory module: clip probing and playlist indexing."""

import json

import pytest

from bd_shrink.inventory import (
    AudioStream,
    Clip,
    Inventory,
    PlaylistMetadata,
    SubtitleStream,
    VideoStream,
    from_json,
    to_json,
)


@pytest.fixture
def sample_clip():
    """Create a sample Clip for testing."""
    return Clip(
        clip_id="00000",
        duration_sec=3600.0,
        video=VideoStream(
            codec_name="h264",
            width=1920,
            height=1080,
            r_frame_rate="59.94",
            bit_rate=15_000_000,
        ),
        audio=[
            AudioStream(
                index=0,
                codec_name="ac3",
                bit_rate=640_000,
                channel_layout="5.1",
            ),
            AudioStream(
                index=1,
                codec_name="dts",
                bit_rate=1_509_000,
                channel_layout="5.1",
            ),
        ],
        subtitles=[
            SubtitleStream(index=0, codec_name="hdmv_pgs_subtitle"),
        ],
    )


@pytest.fixture
def sample_inventory(sample_clip):
    """Create a sample Inventory for testing."""
    clip2 = Clip(
        clip_id="00001",
        duration_sec=1800.0,
        video=VideoStream(
            codec_name="h264",
            width=1920,
            height=1080,
            r_frame_rate="23.976",
            bit_rate=8_000_000,
        ),
        audio=[
            AudioStream(
                index=0,
                codec_name="ac3",
                bit_rate=640_000,
                channel_layout="5.1",
            ),
        ],
        subtitles=[],
    )

    return Inventory(
        clips={"00000": sample_clip, "00001": clip2},
        playlists={
            "00000": PlaylistMetadata(
                playlist_id="00000",
                playlist_type=0,  # main
                duration_sec=3600.0,
                num_chapters=10,
                clips=["00000"],
            ),
            "00001": PlaylistMetadata(
                playlist_id="00001",
                playlist_type=0,
                duration_sec=1800.0,
                num_chapters=5,
                clips=["00001"],
            ),
        },
    )


class TestClipStructure:
    """Test Clip dataclass."""

    def test_clip_creation(self, sample_clip):
        """Verify Clip can be created with all fields."""
        assert sample_clip.clip_id == "00000"
        assert sample_clip.duration_sec == 3600.0
        assert sample_clip.video is not None
        assert len(sample_clip.audio) == 2
        assert len(sample_clip.subtitles) == 1

    def test_clip_no_video(self):
        """Verify Clip can be created without video (menu/audio-only)."""
        clip = Clip(
            clip_id="menu",
            duration_sec=30.0,
            video=None,
            audio=[],
            subtitles=[],
        )
        assert clip.video is None
        assert len(clip.audio) == 0

    def test_clip_no_subtitles(self, sample_clip):
        """Verify Clip with no subtitles is valid."""
        clip = Clip(
            clip_id="00002",
            duration_sec=2000.0,
            video=sample_clip.video,
            audio=sample_clip.audio,
            subtitles=[],
        )
        assert len(clip.subtitles) == 0


class TestPlaylistMetadata:
    """Test PlaylistMetadata dataclass."""

    def test_main_movie_playlist(self):
        """Verify main movie playlist type."""
        pl = PlaylistMetadata(
            playlist_id="00000",
            playlist_type=0,
            duration_sec=7200.0,
            num_chapters=20,
            clips=["00000", "00001"],
        )
        assert pl.playlist_type == 0

    def test_menu_playlist(self):
        """Verify menu playlist type."""
        pl = PlaylistMetadata(
            playlist_id="90000",
            playlist_type=1,
            duration_sec=60.0,
            num_chapters=0,
            clips=["90000"],
        )
        assert pl.playlist_type == 1

    def test_playlist_duration_from_clips(self):
        """Verify playlist can compute duration from clip list."""
        pl = PlaylistMetadata(
            playlist_id="00000",
            playlist_type=0,
            duration_sec=5400.0,  # sum of two 1.5h clips
            num_chapters=0,
            clips=["00000", "00001"],
        )
        assert pl.duration_sec == 5400.0


class TestInventoryStructure:
    """Test Inventory dataclass and operations."""

    def test_inventory_creation(self, sample_inventory):
        """Verify Inventory is created correctly."""
        assert len(sample_inventory.clips) == 2
        assert len(sample_inventory.playlists) == 2
        assert "00000" in sample_inventory.clips
        assert "00001" in sample_inventory.playlists

    def test_inventory_access_clip(self, sample_inventory):
        """Verify clips can be accessed by ID."""
        clip = sample_inventory.clips["00000"]
        assert clip.clip_id == "00000"
        assert clip.duration_sec == 3600.0

    def test_inventory_access_playlist(self, sample_inventory):
        """Verify playlists can be accessed by ID."""
        pl = sample_inventory.playlists["00000"]
        assert pl.playlist_type == 0
        assert pl.num_chapters == 10


class TestInventorySerialization:
    """Test JSON serialization/deserialization."""

    def test_to_json_produces_valid_json(self, sample_inventory):
        """Verify to_json produces parseable JSON."""
        json_str = to_json(sample_inventory)
        data = json.loads(json_str)
        assert "clips" in data
        assert "playlists" in data

    def test_to_json_preserves_clips(self, sample_inventory):
        """Verify all clips are serialized."""
        json_str = to_json(sample_inventory)
        data = json.loads(json_str)
        assert len(data["clips"]) == 2
        assert "00000" in data["clips"]
        assert "00001" in data["clips"]

    def test_to_json_preserves_playlists(self, sample_inventory):
        """Verify all playlists are serialized."""
        json_str = to_json(sample_inventory)
        data = json.loads(json_str)
        assert len(data["playlists"]) == 2
        assert "00000" in data["playlists"]

    def test_from_json_restores_inventory(self, sample_inventory):
        """Verify from_json reconstructs equivalent inventory."""
        json_str = to_json(sample_inventory)
        restored = from_json(json_str)

        assert len(restored.clips) == len(sample_inventory.clips)
        assert len(restored.playlists) == len(sample_inventory.playlists)

    def test_from_json_preserves_clip_metadata(self, sample_inventory):
        """Verify clip fields are preserved through JSON round-trip."""
        json_str = to_json(sample_inventory)
        restored = from_json(json_str)

        clip = restored.clips["00000"]
        assert clip.clip_id == "00000"
        assert clip.duration_sec == 3600.0
        assert clip.video.codec_name == "h264"
        assert clip.video.width == 1920
        assert len(clip.audio) == 2

    def test_from_json_preserves_audio_streams(self, sample_inventory):
        """Verify audio streams survive round-trip."""
        json_str = to_json(sample_inventory)
        restored = from_json(json_str)

        clip = restored.clips["00000"]
        assert len(clip.audio) == 2
        assert clip.audio[0].codec_name == "ac3"
        assert clip.audio[0].bit_rate == 640_000
        assert clip.audio[1].codec_name == "dts"

    def test_from_json_handles_no_subtitles(self, sample_inventory):
        """Verify clips without subtitles deserialize correctly."""
        json_str = to_json(sample_inventory)
        restored = from_json(json_str)

        clip = restored.clips["00001"]
        assert len(clip.subtitles) == 0

    def test_from_json_preserves_playlist_metadata(self, sample_inventory):
        """Verify playlist fields survive round-trip."""
        json_str = to_json(sample_inventory)
        restored = from_json(json_str)

        pl = restored.playlists["00000"]
        assert pl.playlist_id == "00000"
        assert pl.playlist_type == 0
        assert pl.duration_sec == 3600.0
        assert pl.num_chapters == 10
        assert pl.clips == ["00000"]

    def test_round_trip_idempotent(self, sample_inventory):
        """Verify multiple round-trips produce identical results."""
        json1 = to_json(sample_inventory)
        inv2 = from_json(json1)
        json2 = to_json(inv2)

        # Should serialize identically
        assert json.loads(json1) == json.loads(json2)


class TestVideoStream:
    """Test VideoStream dataclass."""

    def test_video_stream_creation(self):
        """Verify VideoStream can be created."""
        video = VideoStream(
            codec_name="h264",
            width=1920,
            height=1080,
            r_frame_rate="59.94",
            bit_rate=15_000_000,
        )
        assert video.codec_name == "h264"
        assert video.width == 1920

    def test_video_stream_zero_bitrate(self):
        """Verify VideoStream with unknown bitrate."""
        video = VideoStream(
            codec_name="h264",
            width=1920,
            height=1080,
            r_frame_rate="23.976",
            bit_rate=0,
        )
        assert video.bit_rate == 0


class TestAudioStream:
    """Test AudioStream dataclass."""

    def test_audio_stream_creation(self):
        """Verify AudioStream can be created."""
        audio = AudioStream(
            index=0,
            codec_name="ac3",
            bit_rate=640_000,
            channel_layout="5.1",
        )
        assert audio.codec_name == "ac3"
        assert audio.bit_rate == 640_000

    def test_audio_stream_multiple_languages(self):
        """Verify multiple audio tracks with different codecs."""
        audio1 = AudioStream(index=0, codec_name="ac3", bit_rate=640_000, channel_layout="5.1")
        audio2 = AudioStream(index=1, codec_name="dts", bit_rate=1_509_000, channel_layout="5.1")

        assert audio1.codec_name != audio2.codec_name
        assert audio1.bit_rate != audio2.bit_rate


class TestSubtitleStream:
    """Test SubtitleStream dataclass."""

    def test_subtitle_stream_creation(self):
        """Verify SubtitleStream can be created."""
        sub = SubtitleStream(index=0, codec_name="hdmv_pgs_subtitle")
        assert sub.codec_name == "hdmv_pgs_subtitle"

    def test_subtitle_stream_dvb(self):
        """Verify DVB subtitle codec."""
        sub = SubtitleStream(index=0, codec_name="dvb_subtitle")
        assert sub.codec_name == "dvb_subtitle"
