"""Pick windows across the file and decode each one to gray8.

Nothing is scaled vertically: field parity has to survive to metrics.py, so the
only geometry change is a centre crop in the horizontal direction.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import numpy as np

from .ffmpeg import FFmpegFailed, ScanverdictError, gray_frames, require
from .probe import Probe

MAX_COLUMNS = 512
_ANALYSIS_SPAN = 0.90   # middle 90% of the duration
_BLOCK_W = 16
MIN_WINDOW_FRAMES = 12
WINDOW_BUDGET = 2 * 1024**3   # bytes held for one decoded window


@dataclass(frozen=True)
class Crop:
    width: int
    height: int
    x: int

    @property
    def filter(self) -> str:
        return f"crop={self.width}:{self.height}:{self.x}:0"


@dataclass
class Window:
    index: int
    start: float
    start_frame: int
    frames: np.ndarray

    def __len__(self) -> int:
        return len(self.frames)


@dataclass
class Plan:
    starts: list[float]
    frames_per_window: int
    requested_windows: int
    window_seconds: float = 0.0
    contiguous: bool = False   # full scan: windows abut, so runs are segments
    note: str | None = None


def crop_for(p: Probe) -> Crop:
    width = min(p.width, MAX_COLUMNS)
    trimmed = width - (width % _BLOCK_W)
    if trimmed >= _BLOCK_W:
        width = trimmed
    x = (p.width - width) // 2
    return Crop(width=width, height=p.height, x=x - (x & 1))


def _budget(p: Probe, frames: int) -> None:
    cost = frames * p.height * crop_for(p).width
    if cost > WINDOW_BUDGET:
        raise ScanverdictError(
            f"{frames} frames of {p.height} lines needs {cost / 1024**3:.1f} GB in memory "
            f"for one window. Lower --frames-per-window."
        )


def plan(p: Probe, windows: int, frames_per_window: int, full: bool = False) -> Plan:
    """Where to sample.

    The default spreads non-overlapping windows evenly across the middle 90% of
    the file. `full` tiles them back to back over the whole of it instead, so
    consecutive windows with the same verdict are a real segment.
    """
    fps = p.fps
    span = p.duration if full else p.duration * _ANALYSIS_SPAN
    lead = 0.0 if full else p.duration * (1.0 - _ANALYSIS_SPAN) / 2.0

    frames = frames_per_window
    notes: list[str] = []
    window_seconds = frames / fps
    if window_seconds > span:
        frames = max(MIN_WINDOW_FRAMES, int(span * fps) - 1)
        window_seconds = frames / fps
        notes.append(
            f"file is only {p.duration:.1f}s, so each window holds "
            f"{frames} frames instead of {frames_per_window}"
        )
    _budget(p, frames)

    if full:
        # round, not ceil: a runt tail window decodes too few frames to classify
        # and would only add an undetermined vote.
        count = max(1, round(span / window_seconds))
        starts = [i * window_seconds for i in range(count)]
        notes.append(
            f"full scan: {count} back-to-back windows of {frames} frames "
            f"across all {p.duration:.1f}s"
        )
    else:
        fits = max(1, int(span // window_seconds))
        count = min(windows, fits)
        if count < windows:
            notes.append(
                f"file holds only {count} non-overlapping window"
                f"{'' if count == 1 else 's'} of {frames} frames, not {windows}"
            )
        if count == 1:
            starts = [lead + max(0.0, (span - window_seconds) / 2.0)]
        else:
            step = max(0.0, span - window_seconds) / (count - 1)
            starts = [lead + i * step for i in range(count)]

    return Plan(
        starts=[round(s, 3) for s in starts],
        frames_per_window=frames,
        requested_windows=count if full else windows,
        window_seconds=window_seconds,
        contiguous=full,
        note="; ".join(notes) or None,
    )


def decode(
    path: str,
    start: float,
    height: int,
    width: int,
    filters: Sequence[str] = (),
    frames: int | None = None,
    duration: float | None = None,
) -> np.ndarray:
    """Decode one span of `path` to a (frames, height, width) uint8 array.

    `duration` limits how much input is read, not how much output is written, so
    a decimating filter chain can be measured against the span that fed it.
    """
    require("ffmpeg")
    argv = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}"]
    if duration is not None:
        argv += ["-t", f"{duration:.3f}"]
    argv += ["-i", path, "-map", "0:v:0", "-an", "-sn", "-dn"]
    if filters:
        argv += ["-vf", ",".join(filters)]
    if frames is not None:
        argv += ["-frames:v", str(frames)]
    argv += ["-f", "rawvideo", "-pix_fmt", "gray", "-"]
    try:
        return gray_frames(argv, height, width)
    except FFmpegFailed as exc:
        raise FFmpegFailed(f"{path}: at {start:.3f}s, {exc}") from exc


def sample(p: Probe, layout: Plan) -> Iterator[Window]:
    """Decode each planned window in turn.

    A generator, not a list: --full can plan thousands of windows on a feature
    length file, and holding every decoded one would cost tens of gigabytes.
    """
    crop = crop_for(p)
    for index, start in enumerate(layout.starts):
        frames = decode(
            p.path,
            start,
            crop.height,
            crop.width,
            filters=[crop.filter],
            frames=layout.frames_per_window,
        )
        yield Window(
            index=index,
            start=start,
            start_frame=round(start * p.fps),
            frames=frames,
        )
