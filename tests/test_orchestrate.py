"""Tests for the __main__ orchestrator and source/output resolution."""

import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bd_shrink import __main__ as orchestrator
from bd_shrink.__main__ import (
    PipelineError,
    SourceError,
    apply_overrides,
    build_clip_fps_map,
    dedup_clips,
    detect_burn_device,
    first_main_fps,
    has_resume_state,
    main,
    normalize_fps,
    parse_playlist_csv,
    resolve_output_work,
    resolve_source,
)
from bd_shrink.classify import Classification
from bd_shrink.config import Config
from bd_shrink.encode import EncodeStats
from bd_shrink.inventory import Clip, Inventory, PlaylistMetadata, VideoStream

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def null_logger():
    logger = logging.getLogger("test_orchestrate_null")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.CRITICAL)
    return logger


@pytest.fixture
def bdmv_source(tmp_path):
    """Create a minimal BDMV folder with STREAM/ and PLAYLIST/."""
    root = tmp_path / "MyDisc"
    bdmv = root / "BDMV"
    (bdmv / "STREAM").mkdir(parents=True)
    (bdmv / "PLAYLIST").mkdir(parents=True)
    (bdmv / "index.bdmv").touch()
    return {"parent": str(root), "bdmv": str(bdmv)}


@pytest.fixture
def sample_inventory():
    """Inventory with two clips and one playlist referencing both."""
    clips = {
        "00000": Clip(
            clip_id="00000",
            duration_sec=5400.0,
            video=VideoStream(
                codec_name="h264",
                width=1920,
                height=1080,
                r_frame_rate="24000/1001",
                bit_rate=20000000,
            ),
            audio=[],
            subtitles=[],
        ),
        "00001": Clip(
            clip_id="00001",
            duration_sec=600.0,
            video=VideoStream(
                codec_name="h264",
                width=1920,
                height=1080,
                r_frame_rate="24000/1001",
                bit_rate=15000000,
            ),
            audio=[],
            subtitles=[],
        ),
    }
    playlists = {
        "00000.mpls": PlaylistMetadata(
            playlist_id="00000.mpls",
            playlist_type=0,
            duration_sec=6000.0,
            num_chapters=1,
            clips=["00000", "00001"],
        ),
    }
    return Inventory(clips=clips, playlists=playlists)


# ---------------------------------------------------------------------------
# resolve_source
# ---------------------------------------------------------------------------


class TestResolveSource:
    def test_parent_of_bdmv(self, bdmv_source):
        bdmv_root, parent = resolve_source(bdmv_source["parent"])
        assert bdmv_root == bdmv_source["bdmv"]
        assert parent == bdmv_source["parent"]

    def test_bdmv_folder_directly(self, bdmv_source):
        bdmv_root, parent = resolve_source(bdmv_source["bdmv"])
        assert bdmv_root == bdmv_source["bdmv"]
        assert parent == bdmv_source["parent"]

    def test_missing_source_raises(self, tmp_path):
        with pytest.raises(SourceError, match="source not found"):
            resolve_source(str(tmp_path / "nope"))

    def test_single_video_file_rejected(self, tmp_path):
        f = tmp_path / "movie.mkv"
        f.touch()
        with pytest.raises(SourceError, match="Single video-file input is not yet supported"):
            resolve_source(str(f))

    def test_iso_rejected(self, tmp_path):
        f = tmp_path / "disc.iso"
        f.touch()
        with pytest.raises(SourceError, match="ISO image input is not yet supported"):
            resolve_source(str(f))

    def test_unsupported_file_ext(self, tmp_path):
        f = tmp_path / "x.txt"
        f.touch()
        with pytest.raises(SourceError, match="unsupported source file type"):
            resolve_source(str(f))

    def test_non_bdmv_directory(self, tmp_path):
        (tmp_path / "random").mkdir()
        with pytest.raises(SourceError, match="not a BDMV folder"):
            resolve_source(str(tmp_path / "random"))

    def test_bdmv_without_stream_raises(self, tmp_path):
        bdmv = tmp_path / "BDMV"
        (bdmv / "PLAYLIST").mkdir(parents=True)
        (bdmv / "index.bdmv").touch()
        with pytest.raises(SourceError, match="no STREAM/"):
            resolve_source(str(tmp_path))

    def test_bdmv_without_playlist_raises(self, tmp_path):
        bdmv = tmp_path / "BDMV"
        (bdmv / "STREAM").mkdir(parents=True)
        (bdmv / "index.bdmv").touch()
        with pytest.raises(SourceError, match="no PLAYLIST/"):
            resolve_source(str(tmp_path))


# ---------------------------------------------------------------------------
# resolve_output_work
# ---------------------------------------------------------------------------


class TestResolveOutputWork:
    def test_default_work_dir(self, tmp_path):
        out = tmp_path / "out"
        config = Config(source="/x", output=str(out))
        o, w = resolve_output_work(config, "/source/parent/MyDisc")
        assert o == str(out)
        assert w == str(out) + ".work"

    def test_explicit_work_dir(self, tmp_path):
        out = tmp_path / "out"
        work = tmp_path / "alt.work"
        config = Config(source="/x", output=str(out), work_dir=str(work))
        o, w = resolve_output_work(config, "/source/parent/MyDisc")
        assert w == str(work)

    def test_existing_parent_creates_subdir(self, tmp_path):
        """If output is an existing dir without BDMV/, create a source-named subdir."""
        existing = tmp_path / "Movies"
        existing.mkdir()
        config = Config(source="/x", output=str(existing))
        o, w = resolve_output_work(config, "/source/parent/MyDisc")
        assert o == str(existing / "MyDisc")
        assert w == str(existing / "MyDisc") + ".work"

    def test_existing_output_with_bdmv_keeps(self, tmp_path):
        """If output already has BDMV/, use it directly (recording into it)."""
        existing = tmp_path / "Movies"
        (existing / "BDMV").mkdir(parents=True)
        config = Config(source="/x", output=str(existing))
        o, _ = resolve_output_work(config, "/source/parent/MyDisc")
        assert o == str(existing)


# ---------------------------------------------------------------------------
# Clip helpers
# ---------------------------------------------------------------------------


class TestClipHelpers:
    def test_dedup_clips_preserves_order(self, sample_inventory):
        clips = dedup_clips(sample_inventory, ["00000.mpls"])
        assert clips == ["00000", "00001"]

    def test_dedup_clips_skips_unknown(self, sample_inventory):
        clips = dedup_clips(sample_inventory, ["nope.mpls"])
        assert clips == []

    def test_dedup_clips_dedupes_across_playlists(self, sample_inventory):
        # Add a second playlist sharing clip 00000.
        sample_inventory.playlists["00001.mpls"] = PlaylistMetadata(
            playlist_id="00001.mpls",
            playlist_type=0,
            duration_sec=5400.0,
            num_chapters=0,
            clips=["00000"],
        )
        clips = dedup_clips(sample_inventory, ["00000.mpls", "00001.mpls"])
        assert clips == ["00000", "00001"]  # 00000 not repeated

    def test_parse_playlist_csv_adds_mpls(self):
        assert parse_playlist_csv("00000, 00001.mpls") == ["00000.mpls", "00001.mpls"]

    def test_parse_playlist_csv_empty(self):
        assert parse_playlist_csv("") == []
        assert parse_playlist_csv(" , ") == []

    def test_apply_overrides_main(self, sample_inventory):
        sample_inventory.playlists["00001.mpls"] = PlaylistMetadata(
            playlist_id="00001.mpls",
            playlist_type=0,
            duration_sec=1.0,
            num_chapters=0,
            clips=["00001"],
        )
        base = Classification(
            main_playlists=["00000.mpls"],
            extras_playlists=[],
            menu_playlists=[],
        )
        config = Config(source="/x", output="/y", override_main_playlists="00001")
        result = apply_overrides(config, sample_inventory, base)
        assert result.main_playlists == ["00001.mpls"]

    def test_apply_overrides_not_extras(self, sample_inventory):
        sample_inventory.playlists["00001.mpls"] = PlaylistMetadata(
            playlist_id="00001.mpls",
            playlist_type=0,
            duration_sec=1.0,
            num_chapters=0,
            clips=["00001"],
        )
        base = Classification(
            main_playlists=["00000.mpls"],
            extras_playlists=["00000.mpls", "00001.mpls"],
            menu_playlists=[],
        )
        config = Config(source="/x", output="/y", override_not_extras="00000.mpls")
        result = apply_overrides(config, sample_inventory, base)
        assert result.extras_playlists == ["00001.mpls"]

    def test_apply_overrides_no_change_when_empty(self, sample_inventory):
        base = Classification(
            main_playlists=["00000.mpls"],
            extras_playlists=["e"],
            menu_playlists=["m"],
        )
        config = Config(source="/x", output="/y")
        result = apply_overrides(config, sample_inventory, base)
        assert result == base

    def test_apply_overrides_main_removes_from_extras_and_menus(self, sample_inventory):
        """Forcing a playlist into main must remove it from extras/menus.

        Regression test: previously a clip forced into ``main`` via
        ``--main-playlist`` stayed in ``extras_playlists`` too, so
        ``encode_all`` encoded it at 720p extras quality first and the main
        pass then silently skipped it via ``skip_if_exists``.
        """
        sample_inventory.playlists["00001.mpls"] = PlaylistMetadata(
            playlist_id="00001.mpls",
            playlist_type=0,
            duration_sec=1.0,
            num_chapters=0,
            clips=["00001"],
        )
        base = Classification(
            main_playlists=["00000.mpls"],
            extras_playlists=["00001.mpls"],
            menu_playlists=["00001.mpls"],
        )
        config = Config(source="/x", output="/y", override_main_playlists="00001")
        result = apply_overrides(config, sample_inventory, base)
        assert result.main_playlists == ["00001.mpls"]
        assert "00001.mpls" not in result.extras_playlists
        assert "00001.mpls" not in result.menu_playlists

    def test_apply_overrides_extras_removes_from_main_and_menus(self, sample_inventory):
        sample_inventory.playlists["00001.mpls"] = PlaylistMetadata(
            playlist_id="00001.mpls",
            playlist_type=0,
            duration_sec=1.0,
            num_chapters=0,
            clips=["00001"],
        )
        base = Classification(
            main_playlists=["00000.mpls", "00001.mpls"],
            extras_playlists=[],
            menu_playlists=["00001.mpls"],
        )
        config = Config(source="/x", output="/y", override_extras="00001")
        result = apply_overrides(config, sample_inventory, base)
        assert result.extras_playlists == ["00001.mpls"]
        assert "00001.mpls" not in result.main_playlists
        assert "00001.mpls" not in result.menu_playlists

    def test_apply_overrides_menus_removes_from_main_and_extras(self, sample_inventory):
        sample_inventory.playlists["00001.mpls"] = PlaylistMetadata(
            playlist_id="00001.mpls",
            playlist_type=0,
            duration_sec=1.0,
            num_chapters=0,
            clips=["00001"],
        )
        base = Classification(
            main_playlists=["00000.mpls", "00001.mpls"],
            extras_playlists=["00001.mpls"],
            menu_playlists=[],
        )
        config = Config(source="/x", output="/y", override_menus="00001")
        result = apply_overrides(config, sample_inventory, base)
        assert result.menu_playlists == ["00001.mpls"]
        assert "00001.mpls" not in result.main_playlists
        assert "00001.mpls" not in result.extras_playlists


# ---------------------------------------------------------------------------
# FPS helpers
# ---------------------------------------------------------------------------


class TestFpsHelpers:
    @pytest.mark.parametrize(
        "rate,expected",
        [
            ("24000/1001", "23.976"),
            ("24/1", "24"),
            ("25/1", "25"),
            ("30000/1001", "29.97"),
            ("50/1", "50"),
            ("60000/1001", "59.94"),
            ("garbage", "23.976"),
            ("0/0", "23.976"),
        ],
    )
    def test_normalize_fps(self, rate, expected):
        assert normalize_fps(rate) == expected

    def test_first_main_fps_from_first_clip(self, sample_inventory):
        assert first_main_fps(sample_inventory, ["00000", "00001"]) == "23.976"

    def test_first_main_fps_falls_back_when_no_clips(self, sample_inventory):
        assert first_main_fps(sample_inventory, ["nope"]) == "23.976"

    def test_first_main_fps_falls_back_when_no_video(self):
        inv = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=1.0,
                    video=None,
                    audio=[],
                    subtitles=[],
                )
            },
            playlists={},
        )
        assert first_main_fps(inv, ["00000"]) == "23.976"

    def test_build_clip_fps_map_skips_clips_without_video(self, sample_inventory):
        m = build_clip_fps_map(sample_inventory, ["00000", "missing"])
        assert m == {"00000": "23.976"}


# ---------------------------------------------------------------------------
# Resume / burn detection helpers
# ---------------------------------------------------------------------------


class TestResumeAndBurn:
    def test_has_resume_state_false_when_empty(self, tmp_path):
        assert has_resume_state(str(tmp_path)) is False

    def test_has_resume_state_false_when_work_dir_missing(self, tmp_path):
        assert has_resume_state(str(tmp_path / "nope")) is False

    def test_has_resume_state_true_with_video_output(self, tmp_path):
        encode = tmp_path / "encode"
        encode.mkdir()
        (encode / "00000_video.h264").write_bytes(b"x")
        assert has_resume_state(str(tmp_path)) is True

    def test_has_resume_state_true_with_inventory_checkpoint(self, tmp_path):
        (tmp_path / "inventory.json").write_text("{}")
        assert has_resume_state(str(tmp_path)) is True

    def test_detect_burn_device_returns_str(self):
        assert isinstance(detect_burn_device(), str)


# ---------------------------------------------------------------------------
# main() entry behavior
# ---------------------------------------------------------------------------


class TestMainEntry:
    def test_install_deps_exits_zero(self, capsys):
        main(["--install-deps"])
        out = capsys.readouterr().out
        assert "REQUIRED TOOLS" in out

    def test_invalid_target_exits_one(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["-s", "/x", "-o", "/y", "--target", "0"])
        err = capsys.readouterr().err
        assert exc.value.code == 1
        assert "target" in err

    def test_no_source_after_tui_skipped_exits_two(self, capsys, monkeypatch):
        # Force TUI to be skipped (stdin not a tty), no -s/-o.
        monkeypatch.setattr(sys, "stdin", MagicMock(isatty=lambda: False))
        with pytest.raises(SystemExit) as exc:
            main(["--tui"])
        assert exc.value.code == 2
        assert "TUI requires an interactive terminal" in capsys.readouterr().err

    def test_single_video_file_rejected_exits_two(self, tmp_path, capsys):
        f = tmp_path / "movie.mkv"
        f.touch()
        with pytest.raises(SystemExit) as exc:
            main(["-s", str(f), "-o", str(tmp_path / "out"), "-f"])
        assert exc.value.code == 2
        assert "not yet supported" in capsys.readouterr().err

    def test_output_exists_no_force_exits_one(self, bdmv_source, tmp_path, capsys):
        out = tmp_path / "out"
        (out / "BDMV").mkdir(parents=True)
        with pytest.raises(SystemExit) as exc:
            main(["-s", bdmv_source["parent"], "-o", str(out), "--dry-run"])
        err = capsys.readouterr().err
        assert exc.value.code == 1
        assert "output exists" in err

    def test_dry_run_exits_zero(self, bdmv_source, tmp_path, capsys):
        out = tmp_path / "out"
        main(
            [
                "-s",
                bdmv_source["parent"],
                "-o",
                str(out),
                "-f",
                "--dry-run",
            ]
        )
        err = capsys.readouterr().err
        assert "DRY RUN" in err


# ---------------------------------------------------------------------------
# run_pipeline (full happy path, mocked phases)
# ---------------------------------------------------------------------------


class TestRunPipeline:
    def _make_bdmv_empty_workdir(self, tmp_path):
        # Ensure build_inventory finds at least an empty STREAM + PLAYLIST.
        root = tmp_path / "disc"
        (root / "BDMV" / "STREAM").mkdir(parents=True)
        (root / "BDMV" / "PLAYLIST").mkdir(parents=True)
        (root / "BDMV" / "index.bdmv").touch()
        out = tmp_path / "out"
        work = tmp_path / "out.work"
        return str(root), str(out), str(work)

    def test_happy_path_invokes_all_phases_and_writes_checkpoints(
        self,
        tmp_path,
        null_logger,
        monkeypatch,
    ):
        src_root, output_dir, work_dir = self._make_bdmv_empty_workdir(tmp_path)

        fake_inv = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=5400.0,
                    video=VideoStream(
                        codec_name="h264",
                        width=1920,
                        height=1080,
                        r_frame_rate="24000/1001",
                        bit_rate=0,
                    ),
                    audio=[],
                    subtitles=[],
                )
            },
            playlists={
                "00000.mpls": PlaylistMetadata(
                    playlist_id="00000.mpls",
                    playlist_type=0,
                    duration_sec=5400.0,
                    num_chapters=1,
                    clips=["00000"],
                ),
            },
        )
        config = Config(
            source=src_root,
            output=output_dir,
            movie_only=True,
            target_gb=23,
            overhead_mb=200,
            force=True,
        )

        calls: list[str] = []

        def fake_build_inventory(bdmv_root):
            calls.append("inventory")
            return fake_inv

        def fake_classify(inv):
            calls.append("classify")
            return Classification(
                main_playlists=["00000.mpls"],
                extras_playlists=[],
                menu_playlists=[],
            )

        def fake_calculate_budget(inv, main, exc, menu, target_gb, overhead_mb):
            calls.append("budget")
            return {
                "main_duration_sec": 5400.0,
                "main_clip_count": 1,
                "main_bitrate_kbps": 8000,
                "audio_size_mb": 0.0,
                "subtitle_size_mb": 0.0,
                "menu_and_extras_video_mb": 0.0,
                "available_video_mb": 23000.0,
                "target_gb": target_gb,
                "overhead_mb": overhead_mb,
            }

        def fake_encode_all(
            inv, exc, main, source_dir, encode_dir, work_dir, config, no_extras, logger
        ):
            calls.append("encode")
            assert main == ["00000"]
            assert exc == []
            return [
                EncodeStats(
                    clip_name="00000",
                    clip_type="main",
                    audio_tracks=0,
                    subtitle_tracks=0,
                    video_encoded=True,
                    success=True,
                )
            ]

        def fake_rebuild_movie_only(
            encode_dir, output_dir, work_dir, main_clips, config, main_fps, logger
        ):
            calls.append("rebuild")
            assert main_fps == "23.976"
            # Create a minimal valid BDMV output structure.
            os.makedirs(os.path.join(output_dir, "BDMV/STREAM"), exist_ok=True)
            os.makedirs(os.path.join(output_dir, "BDMV/CLIPINF"), exist_ok=True)
            os.makedirs(os.path.join(output_dir, "BDMV/PLAYLIST"), exist_ok=True)
            Path(os.path.join(output_dir, "BDMV/index.bdmv")).touch()
            from bd_shrink.rebuild import RebuildStats

            return RebuildStats(
                mode="movie_only",
                video_tracks=1,
                audio_tracks_max=0,
                subtitle_tracks_max=0,
                remuxed_clips=1,
                copied_clips=0,
                success=True,
            )

        def fake_validate_bdmv_structure(output_dir, logger):
            calls.append("validate")
            from bd_shrink.validate import ValidationResult

            return ValidationResult(
                valid=True,
                missing_files=[],
                corrupted_files=[],
                warnings=[],
                output_bytes=1000,
            )

        def fake_check_output_size(output_path, target_gb, logger):
            return True, 0.001

        import bd_shrink.budget as budmod
        import bd_shrink.classify as clfmod
        import bd_shrink.encode as encmod
        import bd_shrink.inventory as invmod
        import bd_shrink.rebuild as rebmod
        import bd_shrink.validate as valmod

        monkeypatch.setattr(invmod, "build_inventory", fake_build_inventory)
        monkeypatch.setattr(clfmod, "classify_playlists", fake_classify)
        monkeypatch.setattr(budmod, "calculate_budget", fake_calculate_budget)
        monkeypatch.setattr(encmod, "encode_all", fake_encode_all)
        monkeypatch.setattr(rebmod, "rebuild_movie_only", fake_rebuild_movie_only)
        monkeypatch.setattr(valmod, "validate_bdmv_structure", fake_validate_bdmv_structure)
        monkeypatch.setattr(valmod, "check_output_size", fake_check_output_size)

        orchestrator.run_pipeline(
            config, os.path.join(src_root, "BDMV"), output_dir, work_dir, null_logger
        )

        assert calls == ["inventory", "classify", "budget", "encode", "rebuild", "validate"]
        # Checkpoints were written.
        for name in (
            "inventory.json",
            "classify.json",
            "budget.json",
            "encode.json",
            "rebuild.json",
            "validate.json",
        ):
            assert os.path.isfile(os.path.join(work_dir, name)), name

    def test_pipeline_raises_on_no_main_clips(self, tmp_path, null_logger, monkeypatch):
        src_root, output_dir, work_dir = self._make_bdmv_empty_workdir(tmp_path)
        config = Config(source=src_root, output=output_dir, force=True)

        empty_inv = Inventory(clips={}, playlists={})
        import bd_shrink.classify as clfmod
        import bd_shrink.inventory as invmod

        monkeypatch.setattr(invmod, "build_inventory", lambda root: empty_inv)
        monkeypatch.setattr(clfmod, "classify_playlists", lambda inv: Classification([], [], []))

        with pytest.raises(PipelineError, match="no main clips"):
            orchestrator.run_pipeline(
                config,
                os.path.join(src_root, "BDMV"),
                output_dir,
                work_dir,
                null_logger,
            )

    def test_pipeline_raises_on_encode_failure(self, tmp_path, null_logger, monkeypatch):
        src_root, output_dir, work_dir = self._make_bdmv_empty_workdir(tmp_path)
        config = Config(source=src_root, output=output_dir, movie_only=True, force=True)

        fake_inv = Inventory(
            clips={
                "00000": Clip(
                    clip_id="00000",
                    duration_sec=1.0,
                    video=VideoStream(
                        codec_name="h264",
                        width=1920,
                        height=1080,
                        r_frame_rate="24/1",
                        bit_rate=0,
                    ),
                    audio=[],
                    subtitles=[],
                )
            },
            playlists={
                "00000.mpls": PlaylistMetadata(
                    playlist_id="00000.mpls",
                    playlist_type=0,
                    duration_sec=1.0,
                    num_chapters=0,
                    clips=["00000"],
                )
            },
        )
        import bd_shrink.budget as budmod
        import bd_shrink.classify as clfmod
        import bd_shrink.encode as encmod
        import bd_shrink.inventory as invmod

        monkeypatch.setattr(invmod, "build_inventory", lambda root: fake_inv)
        monkeypatch.setattr(
            clfmod, "classify_playlists", lambda inv: Classification(["00000.mpls"], [], [])
        )
        monkeypatch.setattr(
            budmod,
            "calculate_budget",
            lambda inv, main, exc, menu, target_gb, overhead_mb: {
                "main_duration_sec": 1.0,
                "main_clip_count": 1,
                "main_bitrate_kbps": 1000,
                "audio_size_mb": 0.0,
                "subtitle_size_mb": 0.0,
                "menu_and_extras_video_mb": 0.0,
                "available_video_mb": 100.0,
                "target_gb": target_gb,
                "overhead_mb": overhead_mb,
            },
        )
        monkeypatch.setattr(
            encmod,
            "encode_all",
            lambda *a, **k: [
                EncodeStats(
                    clip_name="00000",
                    clip_type="main",
                    audio_tracks=0,
                    subtitle_tracks=0,
                    video_encoded=False,
                    success=False,
                )
            ],
        )

        with pytest.raises(PipelineError, match="encoding failed for 1 clip"):
            orchestrator.run_pipeline(
                config,
                os.path.join(src_root, "BDMV"),
                output_dir,
                work_dir,
                null_logger,
            )


# ---------------------------------------------------------------------------
# logging_setup
# ---------------------------------------------------------------------------


class TestLoggingSetup:
    def test_setup_logging_returns_logger_with_handlers(self, tmp_path):
        from bd_shrink.logging_setup import LOGGER_NAME, setup_logging

        logger = setup_logging(str(tmp_path), level=logging.DEBUG)
        assert logger.name == LOGGER_NAME
        assert len(logger.handlers) >= 1
        # Work-dir mirror file exists.
        assert (tmp_path / "bd_shrink.log").exists()
        # Logging does not raise.
        logger.info("test message")

    def test_setup_logging_resets_handlers_on_recall(self, tmp_path):
        from bd_shrink.logging_setup import setup_logging

        logger = setup_logging(str(tmp_path))
        first_count = len(logger.handlers)
        logger = setup_logging(str(tmp_path))
        assert len(logger.handlers) == first_count
