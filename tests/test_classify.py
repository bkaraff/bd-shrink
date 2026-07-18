"""Tests for classification module: playlist categorization."""

import pytest

from bd_shrink.classify import (
    classify_playlists,
    count_main_clips_unique,
    has_video,
    is_low_res,
    is_menu_type,
    is_seamless_branching,
    is_short_clip,
)
from bd_shrink.inventory import (
    AudioStream,
    Clip,
    Inventory,
    PlaylistMetadata,
    VideoStream,
)


@pytest.fixture
def hd_1080p_video():
    """1080p HD video."""
    return VideoStream(
        codec_name="h264",
        width=1920,
        height=1080,
        r_frame_rate="59.94",
        bit_rate=15_000_000,
    )


@pytest.fixture
def sd_480p_video():
    """480p SD video."""
    return VideoStream(
        codec_name="h264",
        width=720,
        height=480,
        r_frame_rate="29.97",
        bit_rate=6_000_000,
    )


@pytest.fixture
def movie_clip_hd(hd_1080p_video):
    """2-hour HD movie clip."""
    return Clip(
        clip_id="00000",
        duration_sec=7200.0,
        video=hd_1080p_video,
        audio=[AudioStream(0, "ac3", 640_000, "5.1")],
        subtitles=[],
    )


@pytest.fixture
def movie_clip_sd(sd_480p_video):
    """1.5-hour SD movie clip."""
    return Clip(
        clip_id="00001",
        duration_sec=5400.0,
        video=sd_480p_video,
        audio=[AudioStream(0, "ac3", 640_000, "5.1")],
        subtitles=[],
    )


@pytest.fixture
def menu_clip_hd(hd_1080p_video):
    """30-second menu clip (short HD)."""
    return Clip(
        clip_id="90000",
        duration_sec=30.0,
        video=hd_1080p_video,
        audio=[],
        subtitles=[],
    )


@pytest.fixture
def menu_clip_no_video():
    """Audio-only menu clip."""
    return Clip(
        clip_id="90001",
        duration_sec=60.0,
        video=None,
        audio=[AudioStream(0, "ac3", 192_000, "2.0")],
        subtitles=[],
    )


class TestMenuDetection:
    """Test menu/interactive detection."""

    def test_is_menu_type_true(self):
        """Verify MPLS type 1 is detected as menu."""
        pl = PlaylistMetadata(
            playlist_id="90000",
            playlist_type=1,
            duration_sec=60.0,
            num_chapters=0,
            clips=["90000"],
        )
        assert is_menu_type(pl) is True

    def test_is_menu_type_false(self):
        """Verify MPLS type 0 is not menu type."""
        pl = PlaylistMetadata(
            playlist_id="00000",
            playlist_type=0,
            duration_sec=7200.0,
            num_chapters=10,
            clips=["00000"],
        )
        assert is_menu_type(pl) is False

    def test_is_short_clip_true(self):
        """Verify <120s clip is short."""
        pl = PlaylistMetadata(
            playlist_id="menu",
            playlist_type=1,
            duration_sec=60.0,
            num_chapters=0,
            clips=["90000"],
        )
        assert is_short_clip(pl, 120.0) is True

    def test_is_short_clip_boundary(self):
        """Verify 120s clip is at boundary."""
        pl = PlaylistMetadata(
            playlist_id="intro",
            playlist_type=0,
            duration_sec=120.0,
            num_chapters=0,
            clips=["intro"],
        )
        assert is_short_clip(pl, 120.0) is True

    def test_is_short_clip_long_film(self):
        """Verify >120s clip is not short."""
        pl = PlaylistMetadata(
            playlist_id="00000",
            playlist_type=0,
            duration_sec=7200.0,
            num_chapters=10,
            clips=["00000"],
        )
        assert is_short_clip(pl, 120.0) is False


class TestResolutionDetection:
    """Test resolution-based classification."""

    def test_is_low_res_true_480p(self, sd_480p_video):
        """Verify 480p clip is low-res."""
        inventory = Inventory(
            clips={
                "00001": Clip(
                    clip_id="00001",
                    duration_sec=5400.0,
                    video=sd_480p_video,
                    audio=[],
                    subtitles=[],
                )
            },
            playlists={},
        )
        pl = PlaylistMetadata(
            playlist_id="00001",
            playlist_type=0,
            duration_sec=5400.0,
            num_chapters=0,
            clips=["00001"],
        )
        assert is_low_res(inventory, pl) is True

    def test_is_low_res_false_1080p(self, hd_1080p_video):
        """Verify 1080p clip is not low-res."""
        inventory = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=7200.0,
                    video=hd_1080p_video,
                    audio=[],
                    subtitles=[],
                )
            },
            playlists={},
        )
        pl = PlaylistMetadata(
            playlist_id="00000",
            playlist_type=0,
            duration_sec=7200.0,
            num_chapters=10,
            clips=["00000"],
        )
        assert is_low_res(inventory, pl) is False

    def test_is_low_res_mixed_clips(self, hd_1080p_video, sd_480p_video):
        """Verify mixed resolution is not low-res (has HD)."""
        inventory = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=7200.0,
                    video=hd_1080p_video,
                    audio=[],
                    subtitles=[],
                ),
                "00001": Clip(
                    clip_id="00001",
                    duration_sec=1800.0,
                    video=sd_480p_video,
                    audio=[],
                    subtitles=[],
                ),
            },
            playlists={},
        )
        pl = PlaylistMetadata(
            playlist_id="mixed",
            playlist_type=0,
            duration_sec=9000.0,
            num_chapters=0,
            clips=["00000", "00001"],
        )
        assert is_low_res(inventory, pl) is False

    def test_is_low_res_missing_clip(self):
        """Verify missing clip doesn't crash is_low_res."""
        inventory = Inventory(clips={}, playlists={})
        pl = PlaylistMetadata(
            playlist_id="missing",
            playlist_type=0,
            duration_sec=60.0,
            num_chapters=0,
            clips=["nonexistent"],
        )
        # Should not crash, treats missing as non-HD
        result = is_low_res(inventory, pl)
        assert result is True


class TestVideoDetection:
    """Test video stream detection."""

    def test_has_video_true(self, hd_1080p_video):
        """Verify clip with video is detected."""
        inventory = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=7200.0,
                    video=hd_1080p_video,
                    audio=[],
                    subtitles=[],
                )
            },
            playlists={},
        )
        pl = PlaylistMetadata(
            playlist_id="00000",
            playlist_type=0,
            duration_sec=7200.0,
            num_chapters=0,
            clips=["00000"],
        )
        assert has_video(inventory, pl) is True

    def test_has_video_false_audio_only(self):
        """Verify audio-only clip is detected as no video."""
        inventory = Inventory(
            clips={
                "audio": Clip(
                    clip_id="audio",
                    duration_sec=3600.0,
                    video=None,
                    audio=[AudioStream(0, "ac3", 192_000, "2.0")],
                    subtitles=[],
                )
            },
            playlists={},
        )
        pl = PlaylistMetadata(
            playlist_id="audio",
            playlist_type=0,
            duration_sec=3600.0,
            num_chapters=0,
            clips=["audio"],
        )
        assert has_video(inventory, pl) is False


class TestClassification:
    """Test playlist classification logic."""

    def test_classify_simple_single_main(self, movie_clip_hd):
        """Verify single HD movie classified as main."""
        inventory = Inventory(
            clips={"00000": movie_clip_hd},
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

        result = classify_playlists(inventory)

        assert "00000" in result.main_playlists
        assert len(result.menu_playlists) == 0
        assert len(result.extras_playlists) == 0

    def test_classify_main_and_menu(self, movie_clip_hd, sd_480p_video):
        """Verify HD movie + short low-res clip classified correctly."""
        menu_clip_lowres = Clip(
            clip_id="90000",
            duration_sec=30.0,
            video=sd_480p_video,
            audio=[],
            subtitles=[],
        )

        inventory = Inventory(
            clips={"00000": movie_clip_hd, "90000": menu_clip_lowres},
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
                    playlist_type=0,
                    duration_sec=30.0,
                    num_chapters=0,
                    clips=["90000"],
                ),
            },
        )

        result = classify_playlists(inventory)

        assert "00000" in result.main_playlists
        assert "90000" in result.menu_playlists

    def test_classify_type1_always_menu(self, movie_clip_hd):
        """Verify MPLS type 1 is always classified as menu."""
        inventory = Inventory(
            clips={"00000": movie_clip_hd},
            playlists={
                "90000": PlaylistMetadata(
                    playlist_id="90000",
                    playlist_type=1,  # Interactive/menu
                    duration_sec=7200.0,  # Even if long
                    num_chapters=0,
                    clips=["00000"],
                )
            },
        )

        result = classify_playlists(inventory)

        # Type 1 should be menu regardless of duration
        assert "90000" in result.menu_playlists
        assert "90000" not in result.main_playlists

    def test_classify_main_and_extras(self, movie_clip_hd, movie_clip_sd):
        """Verify HD + SD movies classified as main + extras."""
        inventory = Inventory(
            clips={"00000": movie_clip_hd, "00001": movie_clip_sd},
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
                    duration_sec=5400.0,
                    num_chapters=15,
                    clips=["00001"],
                ),
            },
        )

        result = classify_playlists(inventory)

        assert "00000" in result.main_playlists
        assert "00001" in result.extras_playlists

    def test_classify_no_video_is_menu(self, menu_clip_no_video, movie_clip_hd):
        """Verify short audio-only clip classified as menu."""
        inventory = Inventory(
            clips={"00000": movie_clip_hd, "90001": menu_clip_no_video},
            playlists={
                "00000": PlaylistMetadata(
                    playlist_id="00000",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=20,
                    clips=["00000"],
                ),
                "90001": PlaylistMetadata(
                    playlist_id="90001",
                    playlist_type=0,
                    duration_sec=60.0,
                    num_chapters=0,
                    clips=["90001"],
                ),
            },
        )

        result = classify_playlists(inventory)

        assert "00000" in result.main_playlists
        assert "90001" in result.menu_playlists


class TestSeamlessBranching:
    """Test seamless branching detection."""

    def test_seamless_branching_detected(self, movie_clip_hd):
        """Verify overlapping clips detected as seamless branching."""
        inventory = Inventory(
            clips={"00000": movie_clip_hd},
            playlists={
                "00000": PlaylistMetadata(
                    playlist_id="00000",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=0,
                    clips=["00000"],
                ),
                "00001": PlaylistMetadata(
                    playlist_id="00001",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=0,
                    clips=["00000"],  # Same clip
                ),
            },
        )

        # Both playlists share the same clip
        assert is_seamless_branching(inventory, "00000") is True
        assert is_seamless_branching(inventory, "00001") is True

    def test_seamless_branching_not_detected(self, movie_clip_hd, movie_clip_sd):
        """Verify non-overlapping clips are not seamless branching."""
        inventory = Inventory(
            clips={"00000": movie_clip_hd, "00001": movie_clip_sd},
            playlists={
                "00000": PlaylistMetadata(
                    playlist_id="00000",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=0,
                    clips=["00000"],
                ),
                "00001": PlaylistMetadata(
                    playlist_id="00001",
                    playlist_type=0,
                    duration_sec=5400.0,
                    num_chapters=0,
                    clips=["00001"],
                ),
            },
        )

        assert is_seamless_branching(inventory, "00000") is False
        assert is_seamless_branching(inventory, "00001") is False


class TestMainClipCounting:
    """Test main clip counting with deduplication."""

    def test_count_main_clips_unique_simple(self, movie_clip_hd):
        """Verify single-playlist clip count."""
        inventory = Inventory(
            clips={"00000": movie_clip_hd},
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

        count, duration = count_main_clips_unique(inventory, ["00000"])

        assert count == 1
        assert duration == 7200.0

    def test_count_main_clips_unique_dedup(self, movie_clip_hd):
        """Verify seamless branching deduplicates shared clips."""
        inventory = Inventory(
            clips={"00000": movie_clip_hd},
            playlists={
                "00000": PlaylistMetadata(
                    playlist_id="00000",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=0,
                    clips=["00000"],
                ),
                "00001": PlaylistMetadata(
                    playlist_id="00001",
                    playlist_type=0,
                    duration_sec=7200.0,
                    num_chapters=0,
                    clips=["00000"],  # Same clip
                ),
            },
        )

        # Both playlists reference the same clip
        count, duration = count_main_clips_unique(inventory, ["00000", "00001"])

        # Should count unique clips only once
        assert count == 1
        assert duration == 7200.0

    def test_count_main_clips_multiple(self, movie_clip_hd, movie_clip_sd):
        """Verify counting multiple unique clips."""
        inventory = Inventory(
            clips={"00000": movie_clip_hd, "00001": movie_clip_sd},
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

        count, duration = count_main_clips_unique(inventory, ["00000"])

        assert count == 1
        assert duration == 7200.0
