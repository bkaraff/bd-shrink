"""Tests for TUI module."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from bd_shrink.config import Config
from bd_shrink.tui import (
    ColorTheme,
    select_codec,
    select_mode,
    select_output_format,
    select_source,
    select_speed_profile,
    show_summary,
    show_welcome,
)


@pytest.fixture
def console():
    """Create a test console."""
    return Console()


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield {
            "temp": tmpdir,
            "bdmv": os.path.join(tmpdir, "BDMV"),
        }


@pytest.fixture
def default_config():
    """Default config for testing."""
    return Config(
        source="",
        output="",
    )


class TestColorTheme:
    """Test color theme constants."""

    def test_color_theme_constants(self):
        """Verify color theme has required colors."""
        assert ColorTheme.BLUE == "#89b4fa"
        assert ColorTheme.GREEN == "#a6e3a1"
        assert ColorTheme.RED == "#f38ba8"
        assert ColorTheme.TEXT == "#cdd6f4"


class TestUIComponents:
    """Test UI component functions."""

    def test_show_welcome(self, console):
        """Verify welcome message renders without error."""
        # Just verify it doesn't crash
        try:
            show_welcome(console)
        except Exception as e:
            pytest.fail(f"show_welcome crashed: {e}")

    def test_show_summary(self, console, default_config):
        """Verify summary renders without error."""
        default_config.source = "/tmp/test"
        default_config.output = "/tmp/output"

        try:
            show_summary(console, default_config, "25.00 GB")
        except Exception as e:
            pytest.fail(f"show_summary crashed: {e}")

    def test_select_codec_returns_valid_codec(self):
        """Verify select_codec returns valid codec."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "h264 (AVC, BD-compatible)"

            result = select_codec(MagicMock(), "h264")

            assert result == "h264"

    def test_select_codec_returns_hevc(self):
        """Verify select_codec returns hevc."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "hevc (x265, slower)"

            result = select_codec(MagicMock(), "h264")

            assert result == "hevc"

    def test_select_codec_returns_none_on_cancel(self):
        """Verify select_codec returns None on cancel."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = None

            result = select_codec(MagicMock(), "h264")

            assert result is None

    def test_select_mode_returns_movie_only(self):
        """Verify select_mode returns True for movie-only."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "Movie-only (no menus, fresh BD)"

            result = select_mode(MagicMock(), False)

            assert result is True

    def test_select_mode_returns_full_disc(self):
        """Verify select_mode returns False for full disc."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "Full disc (keep menus, extras)"

            result = select_mode(MagicMock(), True)

            assert result is False

    def test_select_mode_returns_none_on_cancel(self):
        """Verify select_mode returns None on cancel."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = None

            result = select_mode(MagicMock(), False)

            assert result is None

    def test_select_output_format_returns_folder(self):
        """Verify select_output_format returns False for folder."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "Folder (BDMV)"

            result = select_output_format(MagicMock(), False)

            assert result is False

    def test_select_output_format_returns_iso(self):
        """Verify select_output_format returns True for ISO."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "ISO (.iso file)"

            result = select_output_format(MagicMock(), False)

            assert result is True

    def test_select_speed_profile_quality(self):
        """Verify select_speed_profile returns quality settings."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "Quality (slow, 2-pass)"

            result = select_speed_profile(MagicMock(), "slow", 2)

            assert result == ("slow", 2)

    def test_select_speed_profile_fast(self):
        """Verify select_speed_profile returns fast settings."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "Fast (medium, 1-pass)"

            result = select_speed_profile(MagicMock(), "slow", 2)

            assert result == ("medium", 1)

    def test_select_speed_profile_extreme(self):
        """Verify select_speed_profile returns extreme settings."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "Extreme (veryslow, 2-pass)"

            result = select_speed_profile(MagicMock(), "slow", 2)

            assert result == ("veryslow", 2)

    def test_select_speed_profile_returns_none_on_cancel(self):
        """Verify select_speed_profile returns None on cancel."""
        with patch("bd_shrink.tui.questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = None

            result = select_speed_profile(MagicMock(), "slow", 2)

            assert result is None


class TestSourceSelection:
    """Test source selection logic."""

    def test_select_source_returns_file_with_valid_extension(self, temp_dirs):
        """Verify source selection accepts valid video files."""
        # Create a test video file
        test_file = os.path.join(temp_dirs["temp"], "movie.mkv")
        with open(test_file, "w") as f:
            f.write("dummy")

        with (
            patch("bd_shrink.tui.questionary.confirm") as mock_confirm,
            patch("bd_shrink.tui.questionary.path") as mock_path,
        ):
            mock_confirm.return_value.ask.return_value = True
            mock_path.return_value.ask.return_value = test_file

            result = select_source(MagicMock(), "")

            assert result == test_file

    def test_select_source_rejects_invalid_extension(self, temp_dirs):
        """Verify source selection rejects invalid file extensions."""
        test_file = os.path.join(temp_dirs["temp"], "movie.txt")
        with open(test_file, "w") as f:
            f.write("dummy")

        with (
            patch("bd_shrink.tui.questionary.confirm") as mock_confirm,
            patch("bd_shrink.tui.questionary.path") as mock_path,
        ):
            mock_confirm.return_value.ask.return_value = True
            # First call returns invalid file, second returns None (cancel)
            mock_path.return_value.ask.side_effect = [test_file, None]

            result = select_source(MagicMock(), "")

            assert result is None

    def test_select_source_cancelled(self):
        """Verify source selection returns None on cancel."""
        with patch("bd_shrink.tui.questionary.confirm") as mock_confirm:
            mock_confirm.return_value.ask.return_value = False

            result = select_source(MagicMock(), "")

            assert result is None


class TestInteractiveTUI:
    """Test interactive TUI flow."""

    def test_interactive_tui_cancelled_at_source(self, console, default_config):
        """Verify TUI returns None when cancelled at source."""
        with patch("bd_shrink.tui.select_source") as mock_source:
            mock_source.return_value = None

            from bd_shrink.tui import interactive_tui

            result = interactive_tui(console, default_config, "")

            assert result is None

    def test_interactive_tui_flow_complete(self, console, default_config, temp_dirs):
        """Verify TUI completes with valid inputs."""
        # Create test BDMV structure
        os.makedirs(temp_dirs["bdmv"], exist_ok=True)
        with open(os.path.join(temp_dirs["bdmv"], "index.bdmv"), "w") as f:
            f.write("dummy")

        with (
            patch("bd_shrink.tui.select_source") as mock_source,
            patch("bd_shrink.tui.select_output") as mock_output,
            patch("bd_shrink.tui.select_mode") as mock_mode,
            patch("bd_shrink.tui.select_output_format") as mock_format,
            patch("bd_shrink.tui.select_codec") as mock_codec,
            patch("bd_shrink.tui.select_speed_profile") as mock_speed,
            patch("bd_shrink.tui.questionary.select") as mock_action,
            patch("bd_shrink.tui.questionary.confirm") as mock_confirm,
        ):
            mock_source.return_value = temp_dirs["bdmv"]
            mock_output.return_value = os.path.join(temp_dirs["temp"], "output")
            mock_mode.return_value = False
            mock_format.return_value = False
            mock_codec.return_value = "h264"
            mock_speed.return_value = ("slow", 2)
            mock_action.return_value.ask.return_value = "Start"
            mock_confirm.return_value.ask.return_value = False

            from bd_shrink.tui import interactive_tui

            result = interactive_tui(console, default_config, "")

            assert result is not None
            assert result.source == temp_dirs["bdmv"]
