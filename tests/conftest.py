"""Fixtures for pytest tests."""

import pytest


@pytest.fixture
def sample_inventory():
    """Sample inventory.json structure for testing."""
    return {
        "clips": {
            "00000": {
                "size_bytes": 25_000_000_000,
                "duration_sec": 5400,
                "video": [{"height": 1080, "width": 1920}],
                "audio": [
                    {"codec_name": "ac3", "bit_rate": 640000},
                    {"codec_name": "dts", "bit_rate": 1509000},
                ],
                "subtitles": [{"codec_name": "hdmv_pgs_subtitle"}],
            }
        },
        "disc_size_mb": 47000,
    }


@pytest.fixture
def sample_classify():
    """Sample classify.json structure for testing."""
    return {
        "main_movie": ["00000"],
        "extras": [],
        "menus": [],
        "orphans": [],
        "details": {
            "00000": {
                "duration": 5400,
                "size_mb": 25000,
                "clips": ["00000"],
                "is_menu": False,
            }
        },
    }
