"""Run the recommended chain on a few windows and measure whether it worked.

A verdict nobody checked is a guess with a confident voice. This re-decodes the
same spans twice, once clean and once through the chain, and compares the comb
metric per frame and the frame count against what the claimed cadence predicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np

from .classify import FIELD_BLENDED, PROGRESSIVE, FileVerdict
from .ffmpeg import ScanverdictError
from .metrics import active_rows, comb, total_blocks
from .probe import Probe
from .recommend import Recommendation, fps_text
from .sampler import Plan, crop_for, decode

VERIFY_WINDOWS = 3
MIN_REDUCTION = 0.60     # below this the chain has not earned the verdict
COMB_FLOOR = 0.05        # of total blocks; under this there was nothing to remove
COUNT_TOLERANCE = 3      # frames per window, for cycle boundaries at the seams


@dataclass
class Verification:
    ran: bool
    reason: str = ""
    comb_before: int | None = None
    comb_after: int | None = None
    reduction: float | None = None
    frames_before: int = 0
    frames_after: int = 0
    frames_expected: int | None = None
    fps_before: Fraction | None = None
    fps_after: Fraction | None = None
    count_ok: bool | None = None
    cadence: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.ran and not self.problems

    def headline(self) -> str:
        if not self.ran:
            return self.reason
        parts = [
            f"combed blocks {self.comb_before} -> {self.comb_after} "
            f"({self.reduction * 100:+.1f}%)"
        ]
        if self.fps_before is not None:
            parts.append(f"{fps_text(self.fps_before)} -> {fps_text(self.fps_after)} fps")
        count = f"frame count {self.frames_before} -> {self.frames_after}"
        if self.frames_expected is not None:
            if not self.count_ok:
                count += f", expected {self.frames_expected}"
            elif self.cadence:
                count += f" as expected for {self.cadence}"
            else:
                count += " as expected"
        parts.append(count)
        return ", ".join(parts)

    def as_dict(self) -> dict:
        return {
            "ran": self.ran,
            "reason": self.reason,
            "comb_before": self.comb_before,
            "comb_after": self.comb_after,
            "reduction": None if self.reduction is None else round(self.reduction, 4),
            "frames_before": self.frames_before,
            "frames_after": self.frames_after,
            "frames_expected": self.frames_expected,
            "fps_before": None if self.fps_before is None else round(float(self.fps_before), 6),
            "fps_after": None if self.fps_after is None else round(float(self.fps_after), 6),
            "count_ok": self.count_ok,
            "cadence": self.cadence,
            "problems": self.problems,
        }


def _pick(starts: list[float], count: int) -> list[float]:
    if count <= 1:
        return list(starts[:1])
    if len(starts) <= count:
        return list(starts)
    step = (len(starts) - 1) / (count - 1)
    return [starts[round(i * step)] for i in range(count)]


def _mean_comb(frames: np.ndarray, rows: tuple[int, int]) -> tuple[float, int]:
    top, bottom = rows
    active = frames[:, top:bottom]
    if not len(active):
        return 0.0, 0
    return float(comb(active).mean()), total_blocks(active.shape[1], active.shape[2])


def _why_no_chain(label: str) -> str:
    if label == PROGRESSIVE:
        return "the recommendation is to run no filter at all"
    if label == FIELD_BLENDED:
        return "ffmpeg has no chain that undoes blending, so there is nothing to measure"
    return "no chain was recommended"


def verify(
    probe_info: Probe,
    layout: Plan,
    verdict: FileVerdict,
    rec: Recommendation,
) -> Verification:
    if rec.is_noop:
        return Verification(ran=False, reason=f"nothing to verify -- {_why_no_chain(rec.label)}")

    crop = crop_for(probe_info)
    span = layout.frames_per_window / probe_info.fps
    starts = _pick(layout.starts, VERIFY_WINDOWS)

    before_sum = after_sum = 0.0
    frames_before = frames_after = 0
    blocks = 1
    try:
        for start in starts:
            clean = decode(
                probe_info.path, start, crop.height, crop.width,
                filters=[crop.filter], duration=span,
            )
            rows = active_rows(clean)
            fixed = decode(
                probe_info.path, start, crop.height, crop.width,
                filters=list(rec.filters) + [crop.filter], duration=span,
            )
            b_mean, blocks = _mean_comb(clean, rows)
            a_mean, _ = _mean_comb(fixed, rows)
            before_sum += b_mean
            after_sum += a_mean
            frames_before += len(clean)
            frames_after += len(fixed)
    except ScanverdictError as exc:
        return Verification(
            ran=False,
            reason=f"could not run the chain: {exc}",
            problems=["the verification pass failed, so this verdict is unproven"],
        )

    n = len(starts)
    before = before_sum / n
    after = after_sum / n
    reduction = (after - before) / before if before > 0 else 0.0

    expected = None
    count_ok = None
    if rec.frame_ratio is not None:
        expected = round(frames_before * float(rec.frame_ratio))
        count_ok = abs(frames_after - expected) <= COUNT_TOLERANCE * n

    problems = []
    if before >= COMB_FLOOR * blocks:
        if -reduction < MIN_REDUCTION:
            problems.append(
                f"the chain only removed {-reduction:.0%} of the combing "
                f"(wanted at least {MIN_REDUCTION:.0%}), so it is not fixing what was measured"
            )
    elif count_ok:
        pass  # no combing to remove; the frame count is what proves the cadence
    else:
        problems.append(
            f"there was almost no combing to remove ({before:.0f} of {blocks} blocks per "
            "frame), so the comb reduction proves nothing here"
        )

    if count_ok is False:
        problems.append(
            f"frame count came out {frames_after}, not the {expected} a "
            f"{rec.label} cadence predicts"
        )

    return Verification(
        ran=True,
        comb_before=round(before),
        comb_after=round(after),
        reduction=reduction,
        frames_before=frames_before,
        frames_after=frames_after,
        frames_expected=expected,
        fps_before=Fraction(probe_info.r_frame_rate),
        fps_after=rec.output_fps,
        count_ok=count_ok,
        cadence=verdict.cadence or "",
        problems=problems,
    )


def apply_to_confidence(verdict: FileVerdict, result: Verification) -> None:
    """A failed verification drops the confidence and says why, in place."""
    if not result.ran or not result.problems:
        return
    verdict.confidence = "low"
    verdict.notes.extend(result.problems)
