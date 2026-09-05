"""Tests for external-tool and Python-module dependency checks."""

from unittest.mock import patch

from bd_shrink.deps import check_python_dependencies, format_install_deps_output


def test_python_dependency_check_reports_missing_module():
    with patch("bd_shrink.deps.find_python_module", side_effect=lambda name: name != "wcwidth"):
        found, missing = check_python_dependencies()

    assert "questionary" in found
    assert "rich" in found
    assert missing == ["wcwidth"]


def test_install_deps_output_includes_fedora_tui_command():
    with patch("bd_shrink.deps.check_python_dependencies", return_value=([], ["wcwidth"])):
        output = format_install_deps_output()

    assert "MISSING TUI DEPENDENCIES" in output
    assert "sudo dnf install python3-wcwidth" in output
