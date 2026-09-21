"""ffprobe wrapper: what the container claims about the stream."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from fractions import Fraction

from .ffmpeg import FFmpegFailed, ScanverdictError, require, text

# ffprobe's field_order vocabulary. All four of tt/bb/tb/bt are interlaced. The
# headers read the second letter as the field displayed first, but ffmpeg's own
# encoders write "tb" for material made with tinterlace=interleave_top, which is
# top field first, so tb is mapped that way here. Measured, not assumed. This
# only ever seeds the fallback: a measured field order always wins.
_FIELD_ORDER = {
    "progressive": "progressive",
    "tt": "interlaced_tff",
    "bb": "interlaced_bff",
    "tb": "interlaced_tff",
    "bt": "interlaced_bff",
    "unknown": "unknown",
}


@dataclass
class Probe:
    path: str
    codec: str
    width: int
    height: int
    duration: float
    r_frame_rate: Fraction
    avg_frame_rate: Fraction
    field_order: str  # raw ffprobe token
    nb_frames: int | None
    pix_fmt: str

    @property
    def fps(self) -> float:
        return float(self.r_frame_rate) or float(self.avg_frame_rate)

    @property
    def container_verdict(self) -> str:
        """The raw field_order token mapped onto scanverdict's own labels."""
        return _FIELD_ORDER.get(self.field_order, "unknown")

    @property
    def is_vfr(self) -> bool:
        r, a = float(self.r_frame_rate), float(self.avg_frame_rate)
        if r <= 0 or a <= 0:
            return False
        return abs(r - a) / max(r, a) > 0.02

    def as_dict(self) -> dict:
        d = asdict(self)
        d["r_frame_rate"] = str(self.r_frame_rate)
        d["avg_frame_rate"] = str(self.avg_frame_rate)
        d["fps"] = round(self.fps, 6)
        d["container_verdict"] = self.container_verdict
        d["vfr"] = self.is_vfr
        return d


def _fraction(value: str | None) -> Fraction:
    if not value or "/" not in value:
        try:
            return Fraction(value or 0).limit_denominator(1000000)
        except (ValueError, ZeroDivisionError):
            return Fraction(0)
    num, den = value.split("/", 1)
    try:
        if int(den) == 0:
            return Fraction(0)
        return Fraction(int(num), int(den))
    except ValueError:
        return Fraction(0)


def probe(path: str) -> Probe:
    if not os.path.exists(path):
        raise ScanverdictError(f"{path}: no such file")
    if os.path.isdir(path):
        raise ScanverdictError(f"{path}: is a directory")
    if os.path.getsize(path) == 0:
        raise ScanverdictError(f"{path}: file is empty")
    require("ffprobe")

    raw = text(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_streams", "-show_format",
            "-of", "json", path,
        ]
    )
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FFmpegFailed(f"{path}: ffprobe returned unparseable JSON") from exc

    streams = info.get("streams") or []
    if not streams:
        raise ScanverdictError(f"{path}: no video stream found")
    st = streams[0]
    fmt = info.get("format") or {}

    width, height = int(st.get("width") or 0), int(st.get("height") or 0)
    if width < 32 or height < 32:
        raise ScanverdictError(f"{path}: video is {width}x{height}, too small to analyse")

    duration = 0.0
    for candidate in (st.get("duration"), fmt.get("duration")):
        try:
            duration = float(candidate)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            break

    nb_frames = None
    try:
        nb_frames = int(st["nb_frames"])
    except (KeyError, TypeError, ValueError):
        pass

    r = _fraction(st.get("r_frame_rate"))
    a = _fraction(st.get("avg_frame_rate"))
    if r == 0 and a == 0:
        raise ScanverdictError(f"{path}: ffprobe reports no frame rate; cannot sample")
    if duration <= 0 and nb_frames and float(r or a) > 0:
        duration = nb_frames / float(r or a)
    if duration <= 0:
        raise ScanverdictError(f"{path}: ffprobe reports no duration; cannot sample")

    return Probe(
        path=path,
        codec=st.get("codec_name") or "unknown",
        width=width,
        height=height,
        duration=duration,
        r_frame_rate=r or a,
        avg_frame_rate=a or r,
        field_order=st.get("field_order") or "unknown",
        nb_frames=nb_frames,
        pix_fmt=st.get("pix_fmt") or "unknown",
    )
