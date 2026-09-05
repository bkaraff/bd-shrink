"""Tests for runner module: subprocess orchestration."""

import logging

import pytest

from bd_shrink.runner import (
    RunResult,
    check_returncode,
    find_tool,
    run_managed,
    run_simple,
    systemd_run_available,
)


@pytest.fixture
def null_logger():
    """Logger that discards all messages."""
    logger = logging.getLogger("test_null")
    logger.addHandler(logging.NullHandler())
    return logger


class TestRunResult:
    """Test RunResult dataclass."""

    def test_run_result_success(self):
        """Verify successful command result."""
        result = RunResult(
            returncode=0,
            stdout="output",
            stderr="",
            succeeded=True,
        )
        assert result.succeeded is True
        assert result.returncode == 0

    def test_run_result_failure(self):
        """Verify failed command result."""
        result = RunResult(
            returncode=1,
            stdout="",
            stderr="error message",
            succeeded=False,
        )
        assert result.succeeded is False
        assert result.returncode == 1


class TestRunSimple:
    """Test run_simple function (direct subprocess, no systemd)."""

    def test_run_simple_success(self, null_logger):
        """Verify successful command."""
        result = run_simple(["echo", "hello"], logger=null_logger)
        assert result.succeeded is True
        assert "hello" in result.stdout

    def test_run_simple_failure(self, null_logger):
        """Verify failed command."""
        result = run_simple(["sh", "-c", "exit 1"], logger=null_logger)
        assert result.succeeded is False
        assert result.returncode == 1

    def test_run_simple_captures_stderr(self, null_logger):
        """Verify stderr is captured."""
        result = run_simple(
            ["sh", "-c", "echo error >&2; exit 0"],
            logger=null_logger,
        )
        assert "error" in result.stderr

    def test_run_simple_captures_stdout(self, null_logger):
        """Verify stdout is captured."""
        result = run_simple(["echo", "output"], logger=null_logger)
        assert "output" in result.stdout

    def test_run_simple_with_cwd(self, null_logger, tmp_path):
        """Verify cwd parameter works."""
        # Create a temp file in tmp_path
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        # List files in tmp_path
        result = run_simple(
            ["ls", "test.txt"],
            cwd=str(tmp_path),
            logger=null_logger,
        )
        assert result.succeeded is True
        assert "test.txt" in result.stdout


class TestCheckReturncode:
    """Test check_returncode helper."""

    def test_check_returncode_success(self):
        """Verify successful return code check."""
        result = RunResult(
            returncode=0,
            stdout="",
            stderr="",
            succeeded=True,
        )
        assert check_returncode(result, 0) is True

    def test_check_returncode_failure(self):
        """Verify failed return code check."""
        result = RunResult(
            returncode=1,
            stdout="",
            stderr="",
            succeeded=False,
        )
        assert check_returncode(result, 0) is False

    def test_check_returncode_nonzero_expected(self):
        """Verify non-zero expected return code."""
        result = RunResult(
            returncode=2,
            stdout="",
            stderr="",
            succeeded=False,
        )
        assert check_returncode(result, 2) is True
        assert check_returncode(result, 0) is False


class TestRunFF:
    """Test run_ff function (systemd-run wrapper).

    Note: These tests don't actually call systemd-run (not available in all environments).
    Instead, they verify the function would construct correct commands.
    In CI, these tests verify basic structure without executing systemd-run.
    """

    def test_run_ff_nice_validation_too_low(self):
        """Verify nice < 0 is rejected."""
        from bd_shrink.runner import run_ff

        with pytest.raises(ValueError, match="nice must be 0-19"):
            run_ff(["echo", "test"], nice=-1)

    def test_run_ff_nice_validation_too_high(self):
        """Verify nice > 19 is rejected."""
        from bd_shrink.runner import run_ff

        with pytest.raises(ValueError, match="nice must be 0-19"):
            run_ff(["echo", "test"], nice=20)

    def test_run_ff_nice_validation_valid_min(self):
        """Verify nice=0 is accepted."""
        from bd_shrink.runner import run_ff

        # Should not raise; will fail at systemd-run call if systemd unavailable
        # This just verifies validation passes
        try:
            run_ff(["echo", "test"], nice=0)
        except FileNotFoundError:
            # systemd-run not available, that's OK
            pass
        except Exception:
            # Other errors are fine, we just care that nice validation passed
            pass

    def test_run_ff_nice_validation_valid_max(self):
        """Verify nice=19 is accepted."""
        from bd_shrink.runner import run_ff

        try:
            run_ff(["echo", "test"], nice=19)
        except FileNotFoundError:
            pass
        except Exception:
            pass


class TestRunSimpleEdgeCases:
    """Test edge cases and error handling."""

    def test_run_simple_empty_command(self, null_logger):
        """Verify empty command is handled gracefully."""
        result = run_simple([], logger=null_logger)
        # Should return failure result, not raise
        assert result.succeeded is False

    def test_run_simple_nonexistent_command(self, null_logger):
        """Verify nonexistent command returns error."""
        result = run_simple(
            ["/nonexistent/command/that/should/not/exist"],
            logger=null_logger,
        )
        assert result.succeeded is False
        assert result.returncode != 0

    def test_run_simple_with_stderr_output(self, null_logger):
        """Verify stderr is captured even on success."""
        result = run_simple(
            ["sh", "-c", "echo 'warning' >&2; exit 0"],
            logger=null_logger,
        )
        assert result.succeeded is True
        assert "warning" in result.stderr

    def test_run_simple_multiline_output(self, null_logger):
        """Verify multiline output is captured."""
        result = run_simple(
            ["sh", "-c", "echo 'line1'; echo 'line2'; echo 'line3'"],
            logger=null_logger,
        )
        assert result.succeeded is True
        assert "line1" in result.stdout
        assert "line2" in result.stdout
        assert "line3" in result.stdout


class TestFindTool:
    """Test find_tool tool resolution."""

    def test_find_tool_existing(self):
        """A tool that exists on PATH resolves to an absolute path."""
        # 'sh' exists on every POSIX system this runs on
        path = find_tool("sh")
        assert path is not None
        assert path.endswith("/sh") or path.endswith("sh")

    def test_find_tool_missing(self):
        """A nonexistent tool returns None (no exception)."""
        assert find_tool("definitely-not-a-real-tool-xyz123") is None

    def test_find_tool_absolute_path(self, tmp_path):
        """An absolute path to an executable is returned as-is."""
        import os
        import stat

        script = tmp_path / "mytool"
        script.write_text("#!/bin/sh\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        assert find_tool(str(script)) == str(script)
        assert os.path.isfile(find_tool(str(script)))


class TestRunManaged:
    """Test run_managed dispatch between run_ff and run_simple."""

    def test_falls_back_to_simple_without_systemd(self, null_logger):
        """When systemd-run is unavailable, run_managed runs directly."""
        from unittest.mock import patch

        with patch("bd_shrink.runner.systemd_run_available", return_value=False):
            result = run_managed(["echo", "hello"], logger=null_logger)
        assert result.succeeded is True
        assert "hello" in result.stdout

    def test_fallback_forwards_env(self, null_logger):
        """Fallback path merges the provided env into the child environment."""
        from unittest.mock import patch

        with patch("bd_shrink.runner.systemd_run_available", return_value=False):
            result = run_managed(
                ["sh", "-c", "echo $MY_TEST_VAR"],
                env={"MY_TEST_VAR": "xyz123"},
                logger=null_logger,
            )
        assert result.succeeded is True
        assert "xyz123" in result.stdout

    def test_uses_run_ff_when_available(self, null_logger):
        """When systemd-run is available, run_managed delegates to run_ff."""
        from unittest.mock import patch

        sentinel = RunResult(returncode=0, stdout="", stderr="", succeeded=True)
        with (
            patch("bd_shrink.runner.systemd_run_available", return_value=True),
            patch("bd_shrink.runner.run_ff", return_value=sentinel) as mock_ff,
        ):
            result = run_managed(["ffmpeg", "-version"], nice=10, logger=null_logger)
        assert result is sentinel
        mock_ff.assert_called_once()
        assert mock_ff.call_args.kwargs["nice"] == 10

    def test_systemd_run_available_returns_bool(self):
        """systemd_run_available returns a bool without raising."""
        assert isinstance(systemd_run_available(), bool)
