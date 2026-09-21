"""Turn window measurements into a label, then reconcile the windows.

The reconciliation is the point of the tool: a single window near a fade or a
studio logo is exactly what makes idet-based detectors deinterlace progressive
films (ptr727/PlexCleaner#749), so a verdict only survives here if the windows
agree with each other, and the container's own claim is always printed beside
the pixel evidence rather than trusted or silently overridden.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from .metrics import BlendPeriod, window_metrics
from .probe import Probe
from .sampler import Window

PROGRESSIVE = "progressive"
INTERLACED_TFF = "interlaced_tff"
INTERLACED_BFF = "interlaced_bff"
TELECINE_3_2 = "telecine_3_2"
PULLDOWN_2_2 = "pulldown_2_2"
FIELD_BLENDED = "field_blended"
MIXED = "mixed"
UNDETERMINED = "undetermined"

# Fractions of a frame's total 8x16 blocks. Detailed or grainy progressive
# content trips the comb metric on a few percent of its blocks with no
# interlacing present at all, which is the headroom COMB_CLEAN allows for.
COMB_CLEAN = 0.08
COMB_DIRTY = 0.12

MOTION_MIN = 0.6        # 90th-percentile frame difference, in gray levels
DUP_REL = 0.25          # a duplicate sits below this fraction of the median
DUP_ABS = 0.35
CADENCE_HITS = 0.75     # of the expected duplicate slots
CADENCE_EXTRA = 0.15    # of the remaining slots
BLEND_RATE = 0.08
MOSTLY = 0.80
RARELY = 0.20
MATCH_C_MIN = 0.60      # "best_match almost always the current-frame pairing"

MIN_FRAMES = 8


def cadence_name(period: int) -> str:
    return "3:2" if period == 5 else "2:2"


@dataclass
class Cadence:
    period: int
    phase: int          # position of the duplicate within the cycle, absolute
    hit_rate: float
    extra_rate: float


@dataclass
class FrameBlend:
    """A blended frame rate conversion: whole frames mixed, no fields involved."""

    period: int
    source_fps: float
    strength: float

    def as_dict(self) -> dict:
        return {
            "period": self.period,
            "source_fps": round(self.source_fps, 3),
            "strength": round(self.strength, 3),
        }


@dataclass
class WindowVerdict:
    index: int
    start: float
    start_frame: int
    label: str
    reason: str
    field_order: str | None = None
    cadence: Cadence | None = None
    blend_period: BlendPeriod | None = None
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = {
            "window": self.index,
            "start": round(self.start, 3),
            "label": self.label,
            "reason": self.reason,
            "field_order": self.field_order,
            "cadence": None if self.cadence is None else cadence_name(self.cadence.period),
            "phase": None if self.cadence is None else self.cadence.phase,
        }
        out.update(self.evidence)
        return out


@dataclass
class FileVerdict:
    label: str
    confidence: str
    field_order: str | None
    field_order_assumed: bool
    cadence: str | None
    phase: int | None
    phase_agreement: str
    agreement: str
    windows: list[WindowVerdict]
    notes: list[str]
    container_label: str
    container_agreement: str
    frame_blend: FrameBlend | None = None


def _find_cadence(duplicates: np.ndarray, period: int) -> tuple[int, float, float] | None:
    """Is every `period`-th frame a duplicate, and no other frame?"""
    n = len(duplicates)
    if n < period * 3:
        return None
    best = None
    for phase in range(period):
        slots = np.arange(phase, n, period)
        others = np.setdiff1d(np.arange(n), slots, assume_unique=True)
        hits = float(duplicates[slots].mean())
        extra = float(duplicates[others].mean()) if others.size else 0.0
        if hits >= CADENCE_HITS and extra <= CADENCE_EXTRA:
            if best is None or hits - extra > best[1] - best[2]:
                best = (phase, hits, extra)
    return best


def classify_window(window: Window, probe_info: Probe) -> WindowVerdict:
    frames = window.frames
    base = {"index": window.index, "start": window.start, "start_frame": window.start_frame}
    if len(frames) < MIN_FRAMES:
        return WindowVerdict(
            **base,
            label=UNDETERMINED,
            reason=f"only {len(frames)} frame{'' if len(frames) == 1 else 's'} decoded here",
        )

    m = window_metrics(frames)
    total = m["total_blocks"] or 1
    comb = m["comb"] / total
    best = m["match"].best_comb / total
    match_string = m["match"].match_string
    dup = m["dup"]
    matched_dup = m["matched_dup"]
    blend = m["blend"]
    idet = m["idet"]

    clean = float((best <= COMB_CLEAN).mean())
    dirty = float((comb >= COMB_DIRTY).mean())
    c_share = match_string.count("c") / len(match_string) if match_string else 0.0
    blend_rate = float(blend.blended.mean()) if blend.blended.size else 0.0
    motion = float(np.percentile(dup, 90)) if dup.size else 0.0

    evidence = {
        "comb_mean": round(float(comb.mean()), 4),
        "comb_p90": round(float(np.percentile(comb, 90)), 4),
        "best_comb_mean": round(float(best.mean()), 4),
        "clean_share": round(clean, 3),
        "dirty_share": round(dirty, 3),
        "match_c_share": round(c_share, 3),
        "blend_rate": round(blend_rate, 3),
        "blend_period": None if m["blend_period"] is None else m["blend_period"].period,
        "motion": round(motion, 3),
        "idet": idet.verdict,
        "match_string": match_string[:60],
        "total_blocks": total,
        "crop_rows": list(m["crop_rows"]),
    }

    def verdict(label, reason, field_order=None, cadence=None):
        return WindowVerdict(
            **base, label=label, reason=reason, field_order=field_order,
            cadence=cadence, blend_period=m["blend_period"], evidence=evidence,
        )

    if motion < MOTION_MIN:
        return verdict(
            UNDETERMINED,
            f"almost no motion here (p90 frame difference {motion:.2f}), nothing to measure",
        )

    if matched_dup.size:
        scale = max(DUP_ABS, DUP_REL * float(np.median(matched_dup)))
        duplicates = matched_dup < scale
    else:
        duplicates = np.zeros(0, dtype=bool)

    for period, label, name in ((5, TELECINE_3_2, "3:2"), (2, PULLDOWN_2_2, "2:2")):
        found = _find_cadence(duplicates, period)
        if found is None or clean < MOSTLY:
            continue
        phase_local, hits, extra = found
        # duplicates[j] compares matched frames j and j+1, so the redundant
        # frame is window frame j+2.
        phase = (window.start_frame + phase_local + 2) % period
        return verdict(
            label,
            f"{name} cadence: one duplicate every {period} frames "
            f"({hits:.0%} of slots), {clean:.0%} of frames clean after field matching",
            field_order=_field_order(idet, probe_info),
            cadence=Cadence(period=period, phase=phase, hit_rate=hits, extra_rate=extra),
        )

    if clean <= RARELY and blend_rate >= BLEND_RATE:
        return verdict(
            FIELD_BLENDED,
            f"no field match cleans these frames ({best.mean():.1%} of blocks still combed) "
            f"and {blend_rate:.0%} of them solve as a blend of their neighbours",
            field_order=_field_order(idet, probe_info),
        )

    if dirty >= MOSTLY and clean <= RARELY and c_share >= MATCH_C_MIN:
        if idet.verdict in ("tff", "bff"):
            label = INTERLACED_TFF if idet.verdict == "tff" else INTERLACED_BFF
            return verdict(
                label,
                f"{dirty:.0%} of frames combed, no field match improves them, "
                f"field order {idet.verdict} throughout",
                field_order=idet.verdict,
            )
        return verdict(
            UNDETERMINED,
            f"{dirty:.0%} of frames combed and unmatched, but the field order does not "
            f"resolve (idet says {idet.verdict}) -- could be heavy grain or noise",
        )

    if dirty <= 0.10 and clean >= 0.90:
        return verdict(
            PROGRESSIVE,
            f"no combing worth the name ({comb.mean():.1%} of blocks, "
            f"{dirty:.0%} of frames above threshold) and no cadence",
            field_order=None,
        )

    return verdict(
        UNDETERMINED,
        f"metrics do not agree: {dirty:.0%} of frames combed, {clean:.0%} clean after "
        f"matching, blend fires on {blend_rate:.0%}, no cadence found",
    )


def _field_order(idet, probe_info: Probe) -> str | None:
    if idet.verdict in ("tff", "bff"):
        return idet.verdict
    container = probe_info.container_verdict
    if container == INTERLACED_TFF:
        return "tff"
    if container == INTERLACED_BFF:
        return "bff"
    return None


def reconcile(verdicts: list[WindowVerdict], probe_info: Probe) -> FileVerdict:
    notes: list[str] = []
    total = len(verdicts)
    decisive = [v for v in verdicts if v.label != UNDETERMINED]
    counts = Counter(v.label for v in decisive)

    if not decisive:
        label, confidence = UNDETERMINED, "low"
        agreement = f"0/{total} windows"
        notes.append("every window was ambiguous; the reasons are in the window table")
    else:
        top, n = counts.most_common(1)[0]
        share = n / total
        if n == total:
            label, confidence = top, "high"
        elif share >= MOSTLY and len(counts) == 1:
            label, confidence = top, "medium"
            notes.append(
                f"{total - n} of the windows could not be read either way, "
                "rather than contradicting this"
            )
        elif share >= MOSTLY:
            label, confidence = top, "medium"
            notes.append(
                "dissenting windows: "
                + ", ".join(
                    f"#{v.index} {v.label}" for v in decisive if v.label != top
                )
            )
        else:
            label, confidence = MIXED, "low"
            notes.append(
                "windows disagree: "
                + ", ".join(f"{k} x{c}" for k, c in counts.most_common())
            )
        agreement = f"{n}/{total} windows"

    orders = Counter(v.field_order for v in verdicts if v.field_order)
    field_order = orders.most_common(1)[0][0] if orders else None
    assumed = False
    if field_order is None and label in (TELECINE_3_2, PULLDOWN_2_2):
        field_order, assumed = "tff", True
        notes.append(
            "field order could not be measured (the cadence leaves no combing to "
            "read); tff assumed -- swap to bff if the output still combs"
        )
    elif len(orders) > 1:
        notes.append(
            "field order is not consistent: "
            + ", ".join(f"{k} x{c}" for k, c in orders.most_common())
        )
        if confidence == "high":
            confidence = "medium"

    cadence = phase = None
    phase_agreement = ""
    cadences = [v.cadence for v in verdicts if v.cadence is not None]
    if cadences and label in (TELECINE_3_2, PULLDOWN_2_2):
        cadence = cadence_name(cadences[0].period)
        phases = Counter(c.phase for c in cadences)
        phase, hits = phases.most_common(1)[0]
        phase_agreement = f"consistent across {hits}/{total} windows"
        if hits < len(cadences):
            phase_agreement = f"phase moves: {dict(phases)} across {total} windows"
            notes.append(
                "the cadence phase is not the same in every window, which is normal "
                "for a disc assembled from several sources; fieldmatch relocks per scene"
            )

    frame_blend = _frame_blend(verdicts, label, probe_info)
    if frame_blend is not None:
        notes.append(
            f"the blend cycle repeats every {frame_blend.period} frames: about "
            f"{frame_blend.source_fps:.1f} fps of source resampled to "
            f"{probe_info.fps:.3g} by mixing whole frames, not by interlacing them"
        )

    container_label = probe_info.container_verdict
    if container_label == "unknown":
        container_agreement = "no claim"
    elif label == UNDETERMINED:
        container_agreement = "nothing to compare it to"
    elif container_label == label:
        container_agreement = "agrees"
    else:
        container_agreement = "disagrees"

    if probe_info.is_vfr:
        notes.append(
            f"variable frame rate: r_frame_rate {probe_info.r_frame_rate} but "
            f"avg_frame_rate {probe_info.avg_frame_rate}; cadence detection assumes "
            "constant frame rate, so treat the phase with suspicion"
        )
        if confidence == "high":
            confidence = "medium"

    return FileVerdict(
        label=label,
        confidence=confidence,
        field_order=field_order,
        field_order_assumed=assumed,
        cadence=cadence,
        phase=phase,
        phase_agreement=phase_agreement,
        agreement=agreement,
        windows=verdicts,
        notes=notes,
        container_label=container_label,
        container_agreement=container_agreement,
        frame_blend=frame_blend,
    )


def _frame_blend(
    verdicts: list[WindowVerdict], label: str, probe_info: Probe
) -> FrameBlend | None:
    """A blend cycle only counts when most of the progressive windows see one.

    Restricted to a progressive verdict: a file already called field_blended or
    interlaced ripples for reasons this test cannot tell apart.
    """
    if label != PROGRESSIVE:
        return None
    windows = [v for v in verdicts if v.label == PROGRESSIVE]
    found = [v.blend_period for v in windows if v.blend_period is not None]
    if not windows or len(found) * 2 <= len(windows):
        return None
    period = Counter(b.period for b in found).most_common(1)[0][0]
    return FrameBlend(
        period=period,
        # The conversions this band catches move between neighbouring rates, so
        # the cycle covers one fewer source frame than output frames.
        source_fps=probe_info.fps * (period - 1) / period,
        strength=max(b.strength for b in found if b.period == period),
    )


@dataclass
class Segment:
    start: float
    end: float
    label: str
    windows: int

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "label": self.label,
            "windows": self.windows,
        }


def segments(verdict: FileVerdict, window_seconds: float, duration: float) -> list[Segment]:
    """Runs of consecutive same-label windows, as time spans.

    Only meaningful when the windows abut, which is what --full arranges. The
    boundaries are therefore accurate to one window, not to the frame.
    """
    out: list[Segment] = []
    for win in verdict.windows:
        end = min(duration, win.start + window_seconds)
        if out and out[-1].label == win.label:
            out[-1].end = end
            out[-1].windows += 1
        else:
            out.append(Segment(start=win.start, end=end, label=win.label, windows=1))
    return out
