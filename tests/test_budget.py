"""Tests for budget module: bitrate and space calculations."""

import pytest

from bd_shrink.budget import (
    calculate_budget,
    estimate_audio_size,
    estimate_subtitle_size,
)
from bd_shrink.inventory import (
    AudioStream,
    Clip,
    Inventory,
    PlaylistMetadata,
    SubtitleStream,
    VideoStream,
)


@pytest.fixture
def hd_video():
    """1080p HD video."""
    return VideoStream(
        codec_name="h264",
        width=1920,
        height=1080,
        r_frame_rate="59.94",
        bit_rate=15_000_000,
    )


@pytest.fixture
def main_movie_clip(hd_video):
    """2-hour main movie clip with AC-3 + DTS audio."""
    return Clip(
        clip_id="00000",
        duration_sec=7200.0,
        video=hd_video,
        audio=[
            AudioStream(0, "ac3", 640_000, "5.1"),
            AudioStream(1, "dts", 1_509_000, "5.1"),
        ],
        subtitles=[
            SubtitleStream(0, "hdmv_pgs_subtitle"),
        ],
    )


@pytest.fixture
def extras_clip(hd_video):
    """1-hour extras clip."""
    return Clip(
        clip_id="00001",
        duration_sec=3600.0,
        video=hd_video,
        audio=[
            AudioStream(0, "ac3", 640_000, "5.1"),
        ],
        subtitles=[],
    )


@pytest.fixture
def menu_clip(hd_video):
    """30-second menu clip."""
    return Clip(
        clip_id="90000",
        duration_sec=30.0,
        video=hd_video,
        audio=[],
        subtitles=[],
    )


class TestAudioSizeEstimation:
    """Test audio stream size estimation."""

    def test_estimate_audio_size_single_track(self):
        """Verify single audio track size."""
        inventory = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=3600.0,  # 1 hour
                    video=None,
                    audio=[
                        AudioStream(0, "ac3", 640_000, "5.1"),
                    ],
                    subtitles=[],
                )
            },
            playlists={},
        )
        
        size = estimate_audio_size(inventory, ["00000"], is_main=False)
        
        # 3600 seconds * 640,000 bits/sec / 8 = 288,000,000 bytes = ~274 MB
        expected = 3600 * 640_000 // 8
        assert size == expected

    def test_estimate_audio_size_multiple_tracks(self, main_movie_clip):
        """Verify multiple audio tracks summed."""
        inventory = Inventory(
            clips={"00000": main_movie_clip},
            playlists={},
        )
        
        size = estimate_audio_size(inventory, ["00000"], is_main=True)
        
        # AC-3: 7200 * 640,000 / 8
        # DTS: 7200 * 1_509_000 / 8
        # Total: 7200 * (640_000 + 1_509_000) / 8
        expected = 7200 * (640_000 + 1_509_000) // 8
        assert size == expected

    def test_estimate_audio_size_skips_mpeg(self):
        """Verify MPEG audio is skipped."""
        inventory = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=3600.0,
                    video=None,
                    audio=[
                        AudioStream(0, "mp3", 192_000, "2.0"),  # Should be skipped
                        AudioStream(1, "ac3", 640_000, "5.1"),
                    ],
                    subtitles=[],
                )
            },
            playlists={},
        )
        
        size = estimate_audio_size(inventory, ["00000"], is_main=False)
        
        # Only AC-3 counted
        expected = 3600 * 640_000 // 8
        assert size == expected

    def test_estimate_audio_size_zero_bitrate_uses_fallback(self):
        """Verify zero bitrate triggers fallback."""
        inventory = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=3600.0,
                    video=None,
                    audio=[
                        AudioStream(0, "dts", 0, "5.1"),  # Unknown bitrate
                    ],
                    subtitles=[],
                )
            },
            playlists={},
        )
        
        size = estimate_audio_size(inventory, ["00000"], is_main=False)
        
        # Should use DTS fallback (1509 kbps)
        expected = 3600 * 1_509_000 // 8
        assert size == expected

    def test_estimate_audio_size_empty(self):
        """Verify empty clip list."""
        inventory = Inventory(clips={}, playlists={})
        size = estimate_audio_size(inventory, [], is_main=False)
        assert size == 0


class TestSubtitleSizeEstimation:
    """Test subtitle stream size estimation."""

    def test_estimate_subtitle_size_pgs(self):
        """Verify PGS subtitle size estimate."""
        inventory = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=3600.0,
                    video=None,
                    audio=[],
                    subtitles=[
                        SubtitleStream(0, "hdmv_pgs_subtitle"),
                    ],
                )
            },
            playlists={},
        )
        
        size = estimate_subtitle_size(inventory, ["00000"])
        
        # 3600 seconds * 50,000 bits/sec / 8 = 22,500,000 bytes
        expected = 3600 * 50_000 // 8
        assert size == expected

    def test_estimate_subtitle_size_dvb(self):
        """Verify DVB subtitle size estimate."""
        inventory = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=3600.0,
                    video=None,
                    audio=[],
                    subtitles=[
                        SubtitleStream(0, "dvb_subtitle"),
                    ],
                )
            },
            playlists={},
        )
        
        size = estimate_subtitle_size(inventory, ["00000"])
        
        # 3600 seconds * 10,000 bits/sec / 8
        expected = 3600 * 10_000 // 8
        assert size == expected

    def test_estimate_subtitle_size_multiple_tracks(self):
        """Verify multiple subtitle tracks summed."""
        inventory = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=3600.0,
                    video=None,
                    audio=[],
                    subtitles=[
                        SubtitleStream(0, "hdmv_pgs_subtitle"),
                        SubtitleStream(1, "hdmv_pgs_subtitle"),
                    ],
                )
            },
            playlists={},
        )
        
        size = estimate_subtitle_size(inventory, ["00000"])
        
        # 2 tracks * 3600 * 50,000 / 8
        expected = 2 * 3600 * 50_000 // 8
        assert size == expected


class TestBudgetCalculation:
    """Test budget calculation."""

    def test_calculate_budget_simple(self, main_movie_clip):
        """Verify basic budget calculation."""
        inventory = Inventory(
            clips={"00000": main_movie_clip},
            playlists={
                "00000": PlaylistMetadata(
                    playlist_id="00000",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=20,
                    clips=["00000"],
                )
            },
        )
        
        budget = calculate_budget(
            inventory,
            main_playlist_ids=["00000"],
            extras_playlist_ids=[],
            menu_playlist_ids=[],
            target_gb=23.0,
            overhead_mb=200.0,
        )
        
        assert budget["main_duration_sec"] == 7200.0
        assert budget["main_bitrate_kbps"] > 0
        assert budget["target_gb"] == 23.0
        assert budget["overhead_mb"] == 200.0

    def test_calculate_budget_main_and_extras(
        self, main_movie_clip, extras_clip
    ):
        """Verify budget with extras."""
        inventory = Inventory(
            clips={"00000": main_movie_clip, "00001": extras_clip},
            playlists={
                "00000": PlaylistMetadata(
                    playlist_id="00000",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=20,
                    clips=["00000"],
                ),
                "00001": PlaylistMetadata(
                    playlist_id="00001",
                    playlist_type=0,
                    duration_sec=3600.0,
                    num_chapters=10,
                    clips=["00001"],
                ),
            },
        )
        
        budget = calculate_budget(
            inventory,
            main_playlist_ids=["00000"],
            extras_playlist_ids=["00001"],
            menu_playlist_ids=[],
            target_gb=23.0,
        )
        
        assert budget["main_duration_sec"] == 7200.0
        assert budget["main_bitrate_kbps"] > 0

    def test_calculate_budget_deduplicates_seamless_branching(
        self, main_movie_clip
    ):
        """Verify seamless branching (shared clips) are deduplicated."""
        inventory = Inventory(
            clips={"00000": main_movie_clip},
            playlists={
                "00000": PlaylistMetadata(
                    playlist_id="00000",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=20,
                    clips=["00000"],
                ),
                "00001": PlaylistMetadata(
                    playlist_id="00001",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=20,
                    clips=["00000"],  # Same clip
                ),
            },
        )
        
        budget = calculate_budget(
            inventory,
            main_playlist_ids=["00000", "00001"],
            extras_playlist_ids=[],
            menu_playlist_ids=[],
        )
        
        # Duration should count unique clip only once
        assert budget["main_duration_sec"] == 7200.0
        assert budget["main_clip_count"] == 1

    def test_calculate_budget_respects_target_size(self, main_movie_clip):
        """Verify different target sizes affect bitrate."""
        inventory = Inventory(
            clips={"00000": main_movie_clip},
            playlists={
                "00000": PlaylistMetadata(
                    playlist_id="00000",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=0,
                    clips=["00000"],
                )
            },
        )
        
        budget_bd25 = calculate_budget(
            inventory,
            main_playlist_ids=["00000"],
            extras_playlist_ids=[],
            menu_playlist_ids=[],
            target_gb=23.0,
        )
        
        budget_bd50 = calculate_budget(
            inventory,
            main_playlist_ids=["00000"],
            extras_playlist_ids=[],
            menu_playlist_ids=[],
            target_gb=46.0,
        )
        
        # Larger target = higher bitrate
        assert budget_bd50["main_bitrate_kbps"] > budget_bd25["main_bitrate_kbps"]

    def test_calculate_budget_with_menus(
        self, main_movie_clip, menu_clip
    ):
        """Verify budget accounts for menus."""
        inventory = Inventory(
            clips={"00000": main_movie_clip, "90000": menu_clip},
            playlists={
                "00000": PlaylistMetadata(
                    playlist_id="00000",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=20,
                    clips=["00000"],
                ),
                "90000": PlaylistMetadata(
                    playlist_id="90000",
                    playlist_type=1,
                    duration_sec=30.0,
                    num_chapters=0,
                    clips=["90000"],
                ),
            },
        )
        
        budget = calculate_budget(
            inventory,
            main_playlist_ids=["00000"],
            extras_playlist_ids=[],
            menu_playlist_ids=["90000"],
            target_gb=23.0,
        )
        
        assert budget["main_bitrate_kbps"] > 0

    def test_calculate_budget_returns_dict_keys(self, main_movie_clip):
        """Verify budget dict has all required keys."""
        inventory = Inventory(
            clips={"00000": main_movie_clip},
            playlists={
                "00000": PlaylistMetadata(
                    playlist_id="00000",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=0,
                    clips=["00000"],
                )
            },
        )
        
        budget = calculate_budget(
            inventory,
            main_playlist_ids=["00000"],
            extras_playlist_ids=[],
            menu_playlist_ids=[],
        )
        
        required_keys = [
            "main_duration_sec",
            "main_clip_count",
            "main_bitrate_kbps",
            "audio_size_mb",
            "subtitle_size_mb",
            "menu_and_extras_video_mb",
            "available_video_mb",
            "target_gb",
            "overhead_mb",
        ]
        
        for key in required_keys:
            assert key in budget
