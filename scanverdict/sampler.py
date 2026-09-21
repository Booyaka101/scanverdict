"""Pick windows across the file and decode each one to gray8.

Nothing is scaled vertically: field parity has to survive to metrics.py, so the
only geometry change is a centre crop in the horizontal direction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .ffmpeg import gray_frames, require
from .probe import Probe

MAX_COLUMNS = 512
_ANALYSIS_SPAN = 0.90   # middle 90% of the duration
_BLOCK_W = 16


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
    note: str | None = None


def crop_for(p: Probe) -> Crop:
    width = min(p.width, MAX_COLUMNS)
    trimmed = width - (width % _BLOCK_W)
    if trimmed >= _BLOCK_W:
        width = trimmed
    x = (p.width - width) // 2
    return Crop(width=width, height=p.height, x=x - (x & 1))


def plan(p: Probe, windows: int, frames_per_window: int) -> Plan:
    """Evenly spaced, non-overlapping starts across the middle 90% of the file."""
    fps = p.fps
    span = p.duration * _ANALYSIS_SPAN
    lead = p.duration * (1.0 - _ANALYSIS_SPAN) / 2.0

    frames = frames_per_window
    note = None
    window_seconds = frames / fps
    if window_seconds > span:
        frames = max(12, int(span * fps) - 1)
        window_seconds = frames / fps
        note = (
            f"file is only {p.duration:.1f}s, so each window holds "
            f"{frames} frames instead of {frames_per_window}"
        )

    fits = max(1, int(span // window_seconds))
    count = min(windows, fits)
    if count < windows and note is None:
        note = (
            f"file holds only {count} non-overlapping window"
            f"{'' if count == 1 else 's'} of {frames} frames, not {windows}"
        )
    elif count < windows:
        note += f"; and only {count} of them fit, not {windows}"

    if count == 1:
        starts = [lead + max(0.0, (span - window_seconds) / 2.0)]
    else:
        usable = max(0.0, span - window_seconds)
        step = usable / (count - 1)
        starts = [lead + i * step for i in range(count)]

    return Plan(
        starts=[round(s, 3) for s in starts],
        frames_per_window=frames,
        requested_windows=windows,
        note=note,
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
    return gray_frames(argv, height, width)


def sample(p: Probe, layout: Plan) -> list[Window]:
    crop = crop_for(p)
    out = []
    for index, start in enumerate(layout.starts):
        frames = decode(
            p.path,
            start,
            crop.height,
            crop.width,
            filters=[crop.filter],
            frames=layout.frames_per_window,
        )
        out.append(
            Window(
                index=index,
                start=start,
                start_frame=round(start * p.fps),
                frames=frames,
            )
        )
    return out
