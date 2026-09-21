"""Subprocess plumbing shared by probe, sampler and verify."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence

import numpy as np

_INSTALL_HINT = (
    "scanverdict needs ffmpeg and ffprobe on PATH.\n"
    "  Windows : winget install Gyan.FFmpeg   (or grab a build from https://www.gyan.dev/ffmpeg/builds/)\n"
    "  macOS   : brew install ffmpeg\n"
    "  Debian  : sudo apt install ffmpeg"
)


class ScanverdictError(Exception):
    """Anything the user can act on. __main__ prints these without a traceback."""


class FFmpegNotFound(ScanverdictError):
    pass


class FFmpegFailed(ScanverdictError):
    pass


def require(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        raise FFmpegNotFound(f"{' and '.join(missing)} not found.\n{_INSTALL_HINT}")


def _run(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise FFmpegNotFound(f"{argv[0]} not found.\n{_INSTALL_HINT}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegFailed(f"{argv[0]} timed out after {timeout:.0f}s") from exc


def text(argv: Sequence[str], timeout: float = 120.0) -> str:
    proc = _run(argv, timeout)
    if proc.returncode != 0:
        raise FFmpegFailed(_tail(argv, proc.stderr))
    return proc.stdout.decode("utf-8", "replace")


def gray_frames(argv: Sequence[str], height: int, width: int, timeout: float = 600.0):
    """Run a command whose stdout is `-f rawvideo -pix_fmt gray` and reshape it.

    Returns (frames, height, width) uint8. A short read is not an error: seeking
    past a truncated stream or hitting EOF mid-window just yields fewer frames.
    """
    proc = _run(argv, timeout)
    stride = height * width
    count = len(proc.stdout) // stride
    if count == 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise FFmpegFailed(
            "ffmpeg decoded no frames here"
            + (f": {detail.splitlines()[-1]}" if detail else "")
        )
    buf = np.frombuffer(proc.stdout[: count * stride], dtype=np.uint8)
    return buf.reshape(count, height, width)


def _tail(argv: Sequence[str], stderr: bytes, lines: int = 6) -> str:
    msg = stderr.decode("utf-8", "replace").strip().splitlines()
    joined = "\n".join(msg[-lines:]) if msg else "(no stderr)"
    return f"{argv[0]} failed: {joined}"
