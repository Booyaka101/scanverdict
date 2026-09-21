"""Shared test setup: build the real fixtures once, analyse each one once.

Decoding is the slow part, so the analysis is session scoped and every test
reads the same Report objects.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_fixtures import FixtureError, build

from scanverdict.__main__ import analyse

TEST_WINDOWS = 4


def cli_args(**overrides) -> argparse.Namespace:
    settings = {
        "windows": TEST_WINDOWS,
        "frames_per_window": 120,
        "no_verify": True,
        "json": False,
        "csv": False,
        "quiet": False,
    }
    settings.update(overrides)
    return argparse.Namespace(**settings)


@pytest.fixture(scope="session")
def fixtures() -> dict[str, Path]:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not on PATH, so the fixtures cannot be built")
    try:
        return build()
    except FixtureError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="session")
def reports(fixtures):
    return {name: analyse(str(path), cli_args()) for name, path in fixtures.items()}
