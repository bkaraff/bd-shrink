"""Tests for CLI argument parsing and validation."""

import pytest

from bd_shrink.cli import args_to_config, create_parser, parse_args, validate_args


class TestParser:
    """Test argument parser creation."""

    def test_parser_created(self):
        """Verify parser is created successfully."""
        parser = create_parser()
        assert parser is not None
        assert parser.prog == "bd-shrink"

    def test_parse_minimal_args(self):
        """Verify minimal args parse without error."""
        args, remaining = parse_args([])
        assert args.source == ""
        assert args.output == ""
        assert args.target == 23
        assert args.codec == "h264"

    def test_parse_with_source_output(self):
        """Verify source and output parsing."""
        args, _ = parse_args(["-s", "/path/to/BDMV", "-o", "/output"])
        assert args.source == "/path/to/BDMV"
        assert args.output == "/output"

    def test_parse_target_flag(self):
        """Verify --target parsing."""
        args, _ = parse_args(["--target", "50"])
        assert args.target == 50

    def test_parse_codec_h264(self):
        """Verify h264 codec choice."""
        args, _ = parse_args(["--codec", "h264"])
        assert args.codec == "h264"

    def test_parse_codec_hevc(self):
        """Verify hevc codec choice."""
        args, _ = parse_args(["--codec", "hevc"])
        assert args.codec == "hevc"

    def test_parse_main_passes(self):
        """Verify main_passes parsing."""
        args, _ = parse_args(["--main-passes", "1"])
        assert args.main_passes == 1

    def test_parse_movie_only(self):
        """Verify --movie-only flag."""
        args, _ = parse_args(["--movie-only"])
        assert args.movie_only is True

    def test_parse_nice_with_value(self):
        """Verify --nice with explicit value."""
        args, _ = parse_args(["--nice", "10"])
        assert args.nice == 10

    def test_parse_nice_without_value(self):
        """Verify --nice without value defaults to 19."""
        args, _ = parse_args(["--nice"])
        assert args.nice == 19

    def test_parse_install_deps(self):
        """Verify --install-deps flag."""
        args, _ = parse_args(["--install-deps"])
        assert args.install_deps is True


class TestValidation:
    """Test argument validation."""

    def test_validate_target_floor(self):
        """Verify --target must be >= 1 GB."""
        args, _ = parse_args(["--target", "0"])
        with pytest.raises(ValueError, match="target must be >= 1 GB"):
            validate_args(args)

    def test_validate_target_negative(self):
        """Verify --target rejects negative values."""
        args, _ = parse_args(["--target", "-5"])
        with pytest.raises(ValueError, match="target must be >= 1 GB"):
            validate_args(args)

    def test_validate_nice_range(self):
        """Verify --nice must be 0-19."""
        args, _ = parse_args(["--nice", "20"])
        with pytest.raises(ValueError, match="nice must be 0-19"):
            validate_args(args)

    def test_validate_nice_negative(self):
        """Verify --nice rejects negative."""
        args, _ = parse_args(["--nice", "-1"])
        with pytest.raises(ValueError, match="nice must be 0-19"):
            validate_args(args)

    def test_validate_burn_speed_negative(self):
        """Verify --burn-speed rejects negative."""
        args, _ = parse_args(["--burn-speed", "-1"])
        with pytest.raises(ValueError, match="burn-speed must be >= 0"):
            validate_args(args)


class TestConfigConversion:
    """Test conversion from args to Config."""

    def test_args_to_config_defaults(self):
        """Verify Config creation with defaults."""
        args, _ = parse_args([])
        config = args_to_config(args)
        assert config.target_gb == 23
        assert config.codec == "h264"
        assert config.main_passes == 2
        assert config.movie_only is False
        assert config.nice == 0

    def test_args_to_config_movie_only_implies_keep_one(self):
        """Verify --movie-only implies --keep-one."""
        args, _ = parse_args(["--movie-only"])
        config = args_to_config(args)
        assert config.movie_only is True
        assert config.keep_one is True

    def test_args_to_config_explicit_keep_one(self):
        """Verify explicit --keep-one sets flag."""
        args, _ = parse_args(["--keep-one"])
        config = args_to_config(args)
        assert config.keep_one is True

    def test_args_to_config_with_custom_values(self):
        """Verify Config with custom argument values."""
        args, _ = parse_args([
            "-s", "/src",
            "-o", "/out",
            "--target", "30",
            "--codec", "hevc",
            "--main-passes", "1",
            "--nice", "15",
        ])
        config = args_to_config(args)
        assert config.source == "/src"
        assert config.output == "/out"
        assert config.target_gb == 30
        assert config.codec == "hevc"
        assert config.main_passes == 1
        assert config.nice == 15

    def test_args_to_config_burn_flags(self):
        """Verify burn-related flags."""
        args, _ = parse_args([
            "--burn",
            "--burn-device", "/dev/sr0",
            "--burn-speed", "8",
        ])
        config = args_to_config(args)
        assert config.burn is True
        assert config.burn_device == "/dev/sr0"
        assert config.burn_speed == 8


class TestPlaylistOverrides:
    """Test playlist name override validation."""

    def test_playlist_override_valid_decimal(self):
        """Verify valid 5-digit decimal playlist names."""
        args, _ = parse_args(["--main-playlist", "00000"])
        config = args_to_config(args)
        assert config.override_main_playlists == "00000"

    def test_playlist_override_with_extension(self):
        """Verify playlist names can include .mpls extension."""
        args, _ = parse_args(["--extra", "00001.mpls"])
        config = args_to_config(args)
        assert config.override_extras == "00001.mpls"

    def test_playlist_override_csv(self):
        """Verify CSV-separated playlist names."""
        args, _ = parse_args(["--main-playlist", "00000,00001,00002"])
        config = args_to_config(args)
        assert config.override_main_playlists == "00000,00001,00002"

    def test_playlist_override_invalid_hex(self):
        """Verify playlist names reject hex characters."""
        args, _ = parse_args(["--extra", "ABCDE"])
        with pytest.raises(ValueError, match="Invalid playlist name"):
            args_to_config(args)

    def test_playlist_override_too_short(self):
        """Verify playlist names must be exactly 5 digits."""
        args, _ = parse_args(["--menu", "0001"])
        with pytest.raises(ValueError, match="Invalid playlist name"):
            args_to_config(args)

    def test_playlist_override_too_long(self):
        """Verify playlist names must be exactly 5 digits."""
        args, _ = parse_args(["--menu", "000000"])
        with pytest.raises(ValueError, match="Invalid playlist name"):
            args_to_config(args)
