"""Per-frame measurements taken on a decoded gray8 window.

Everything here works on a (frames, H, W) uint8 array and returns plain numpy
arrays. Nothing in this module knows what a verdict is.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

COMB_THRESHOLD = 9          # "CT" -- a pixel is combed when d1*d2 > CT^2
BLOCK_H, BLOCK_W = 8, 16
BLOCK_MIN_COMBED = 12       # a block is combed when it holds more than this
BLEND_RESIDUAL_MAX = 4.0
BLEND_ALPHA_MIN, BLEND_ALPHA_MAX = 0.2, 0.8
BLACK_LEVEL = 16            # letterbox bars are never brighter than this

# idet's own constants, read from libavfilter/vf_idet.c.
IDET_INTL_THRES = 1.04
IDET_PROG_THRES = 1.5

# Five ways to pair frame i's kept field with a complementary field, named after
# TFM's match letters. Each entry is (source of even rows, source of odd rows)
# where "cur"/"prev"/"next" are frames i, i-1, i+1. The order is also the
# tie-break preference: on a telecined frame two different matches reconstruct
# equally clean output, and picking between them at random scatters the
# duplicates so no cadence is visible. TFM has the same rule.
MATCH_KINDS = ("c", "p", "n", "b", "u")
MATCH_TIE_ABS = 6        # blocks
MATCH_TIE_FRAC = 0.15    # of the winning count
MATCH_TIE_NOISE = 0.02   # of the frame's total blocks -- detailed content combs
                         # a few percent of its blocks with no interlacing at all
_MATCH_SOURCES = {
    "c": ("cur", "cur"),    # frame i untouched
    "p": ("cur", "prev"),   # top of i    + bottom of i-1
    "n": ("cur", "next"),   # top of i    + bottom of i+1
    "b": ("prev", "cur"),   # bottom of i + top of i-1
    "u": ("next", "cur"),   # bottom of i + top of i+1
}

# Frames are processed in slabs so a 1080-line window never allocates a
# gigabyte of int32 intermediates.
_CHUNK = 8


def block_grid(height: int, width: int) -> tuple[int, int]:
    return height // BLOCK_H, width // BLOCK_W


def total_blocks(height: int, width: int) -> int:
    rows, cols = block_grid(height, width)
    return rows * cols


def comb(frames: np.ndarray) -> np.ndarray:
    """Combed-block count per frame.

    For each interior row, d1 = p[y-1]-p[y] and d2 = p[y+1]-p[y]; the pixel is
    combed when d1*d2 > CT^2, i.e. the row sits outside both its neighbours by
    more than CT in the same direction.
    """
    frames = np.asarray(frames)
    if frames.ndim == 2:
        frames = frames[None]
    n, h, w = frames.shape
    rows, cols = block_grid(h, w)
    if n == 0 or rows == 0 or cols == 0:
        return np.zeros(n, dtype=np.int32)

    out = np.empty(n, dtype=np.int32)
    limit = COMB_THRESHOLD * COMB_THRESHOLD
    for start in range(0, n, _CHUNK):
        slab = frames[start : start + _CHUNK].astype(np.int32, copy=False)
        d1 = slab[:, :-2] - slab[:, 1:-1]
        d2 = slab[:, 2:] - slab[:, 1:-1]
        combed = (d1 * d2) > limit
        # combed[:, j] describes row j+1, so row 0 and row h-1 are never combed.
        padded = np.zeros((slab.shape[0], h, w), dtype=np.uint8)
        padded[:, 1 : h - 1] = combed
        tiles = padded[:, : rows * BLOCK_H, : cols * BLOCK_W].reshape(
            slab.shape[0], rows, BLOCK_H, cols, BLOCK_W
        )
        per_block = tiles.sum(axis=(2, 4), dtype=np.int32)
        out[start : start + _CHUNK] = (per_block > BLOCK_MIN_COMBED).sum(axis=(1, 2))
    return out


def weave(top_src: np.ndarray, bottom_src: np.ndarray) -> np.ndarray:
    """Interleave two frame stacks: even rows from the first, odd from the second."""
    out = np.array(top_src, dtype=np.uint8, copy=True)
    out[:, 1::2] = bottom_src[:, 1::2]
    return out


@dataclass
class FieldMatch:
    """Per-interior-frame field-match result. Index 0 is frame 1 of the window."""

    comb_by_kind: np.ndarray  # (kinds, interior) combed-block counts
    best_comb: np.ndarray     # (interior,)
    best_kind: list[str]      # (interior,)

    @property
    def match_string(self) -> str:
        return "".join(self.best_kind)


def field_match(frames: np.ndarray) -> FieldMatch:
    n = len(frames)
    if n < 3:
        empty = np.zeros((len(MATCH_KINDS), 0), dtype=np.int32)
        return FieldMatch(empty, np.zeros(0, dtype=np.int32), [])

    sources = {"prev": frames[:-2], "cur": frames[1:-1], "next": frames[2:]}
    counts = np.empty((len(MATCH_KINDS), n - 2), dtype=np.int32)
    for idx, kind in enumerate(MATCH_KINDS):
        top, bottom = _MATCH_SOURCES[kind]
        counts[idx] = comb(weave(sources[top], sources[bottom]))

    floor = counts.min(axis=0)
    noise = max(MATCH_TIE_ABS, int(MATCH_TIE_NOISE * total_blocks(*frames.shape[1:])))
    tolerance = np.maximum(noise, (floor * MATCH_TIE_FRAC).astype(np.int32))
    acceptable = counts <= (floor + tolerance)
    winner = acceptable.argmax(axis=0)  # first kind in preference order that qualifies
    return FieldMatch(
        comb_by_kind=counts,
        best_comb=floor,
        best_kind=[MATCH_KINDS[i] for i in winner],
    )


def frame_diff(frames: np.ndarray) -> np.ndarray:
    """dup[i]: mean absolute difference between frames i and i-1, for i >= 1."""
    n = len(frames)
    if n < 2:
        return np.zeros(0, dtype=np.float64)
    out = np.empty(n - 1, dtype=np.float64)
    for start in range(0, n - 1, _CHUNK):
        stop = min(start + _CHUNK, n - 1)
        a = frames[start + 1 : stop + 1].astype(np.int16, copy=False)
        b = frames[start:stop].astype(np.int16, copy=False)
        out[start:stop] = np.abs(a - b).mean(axis=(1, 2))
    return out


@dataclass
class Blend:
    """Least-squares solve of f_i = a*f_{i-1} + (1-a)*f_{i+1} per interior frame."""

    alpha: np.ndarray
    residual: np.ndarray
    blended: np.ndarray  # bool


def blend_solve(frames: np.ndarray) -> Blend:
    n = len(frames)
    if n < 3:
        z = np.zeros(0)
        return Blend(z, z, np.zeros(0, dtype=bool))

    alpha = np.zeros(n - 2)
    residual = np.full(n - 2, np.inf)
    for start in range(0, n - 2, _CHUNK):
        stop = min(start + _CHUNK, n - 2)
        prev = frames[start:stop].astype(np.float32, copy=False)
        cur = frames[start + 1 : stop + 1].astype(np.float32, copy=False)
        nxt = frames[start + 2 : stop + 2].astype(np.float32, copy=False)
        x = prev - nxt
        y = cur - nxt
        xx = np.einsum("ijk,ijk->i", x, x)
        xy = np.einsum("ijk,ijk->i", x, y)
        moving = xx > 1e-6
        a = np.zeros(stop - start)
        a[moving] = xy[moving] / xx[moving]
        res = y - a[:, None, None].astype(np.float32) * x
        rms = np.sqrt(np.einsum("ijk,ijk->i", res, res) / res[0].size)
        alpha[start:stop] = a
        # A frozen triple has no blend to solve for; leave its residual at inf.
        residual[start:stop] = np.where(moving, rms, np.inf)

    blended = (
        (residual < BLEND_RESIDUAL_MAX)
        & (alpha > BLEND_ALPHA_MIN)
        & (alpha < BLEND_ALPHA_MAX)
    )
    return Blend(alpha, residual, blended)


# A blended frame-rate conversion mixes each output frame from a different pair
# of source frames, so the three-frame fit above matches well at some phases and
# badly at others. The cycle length is the number of output frames the
# conversion repeats over: 25 for 24 -> 25. Measured on four unrelated sources,
# blended files ripple 0.26 to 0.32 and progressive ones 0.07 to 0.11.
BLEND_PERIOD_MIN, BLEND_PERIOD_MAX = 8, 40
BLEND_RIPPLE_MIN = 0.20
BLEND_PERIOD_AC = 0.50
_BLEND_PERIOD_SAMPLES = 90


@dataclass
class BlendPeriod:
    period: int      # output frames per cycle
    strength: float  # residual autocorrelation at that lag
    ripple: float    # residual coefficient of variation


def blend_period(blend: Blend) -> BlendPeriod | None:
    """Find a repeating cycle in the blend residual, or None if there is none."""
    r = blend.residual[np.isfinite(blend.residual)]
    if len(r) < _BLEND_PERIOD_SAMPLES:
        return None
    mean = float(r.mean())
    if mean <= 0:
        return None
    ripple = float(r.std()) / mean
    if ripple < BLEND_RIPPLE_MIN:
        return None
    centred = r - mean
    energy = float(centred @ centred)
    if energy <= 0:
        return None
    lags = np.arange(BLEND_PERIOD_MIN, BLEND_PERIOD_MAX + 1)
    scores = np.array([float(centred[: len(r) - k] @ centred[k:]) / energy for k in lags])
    best = int(scores.argmax())
    if scores[best] < BLEND_PERIOD_AC:
        return None
    return BlendPeriod(
        period=int(lags[best]), strength=float(scores[best]), ripple=ripple
    )


@dataclass
class IdetScore:
    """idet's alpha/delta comparison, reimplemented from libavfilter/vf_idet.c."""

    alpha0: float
    alpha1: float
    delta: float
    verdict: str  # tff | bff | progressive | undetermined


def idet_score(frames: np.ndarray) -> IdetScore:
    """Sum idet's per-frame metrics over the window, then apply its thresholds."""
    n, h, _ = frames.shape
    if n < 3 or h < 6:
        return IdetScore(0.0, 0.0, 0.0, "undetermined")

    ys = np.arange(2, h - 2)
    even = (ys & 1) == 0
    a0 = a1 = dsum = 0.0
    for i in range(1, n - 1):
        cur = frames[i].astype(np.int32, copy=False)
        prev = frames[i - 1].astype(np.int32, copy=False)
        nxt = frames[i + 1].astype(np.int32, copy=False)
        spatial = cur[ys - 1] + cur[ys + 1]
        vp = np.abs(spatial - 2 * prev[ys]).sum(axis=1, dtype=np.int64)
        vn = np.abs(spatial - 2 * nxt[ys]).sum(axis=1, dtype=np.int64)
        vd = np.abs(spatial - 2 * cur[ys]).sum(axis=1, dtype=np.int64)
        a0 += float(vp[even].sum() + vn[~even].sum())
        a1 += float(vp[~even].sum() + vn[even].sum())
        dsum += float(vd.sum())

    if a0 > IDET_INTL_THRES * a1:
        verdict = "tff"
    elif a1 > IDET_INTL_THRES * a0:
        verdict = "bff"
    elif a1 > IDET_PROG_THRES * dsum:
        verdict = "progressive"
    else:
        verdict = "undetermined"
    return IdetScore(a0, a1, dsum, verdict)


def active_rows(frames: np.ndarray, sample: int = 16) -> tuple[int, int]:
    """Row range with the letterbox bars stripped, snapped to even field parity.

    Cropping at an odd offset would swap top and bottom fields, so the returned
    start is always even and the span always has even length.
    """
    n, h, _ = frames.shape
    if h < 4:
        return 0, h
    step = max(1, n // sample)
    peak = frames[::step].max(axis=(0, 2))
    lit = np.flatnonzero(peak > BLACK_LEVEL)
    if lit.size == 0:
        return 0, h
    top, bottom = int(lit[0]), int(lit[-1]) + 1
    top -= top & 1
    if (bottom - top) & 1:
        bottom -= 1
    if bottom - top < BLOCK_H * 2:
        return 0, h
    return top, bottom


def matched_sequence(frames: np.ndarray, match: FieldMatch) -> np.ndarray:
    """Rebuild the interior frames using each one's winning field match."""
    sources = {"prev": frames[:-2], "cur": frames[1:-1], "next": frames[2:]}
    out = np.empty_like(frames[1:-1])
    for idx, kind in enumerate(match.best_kind):
        top, bottom = _MATCH_SOURCES[kind]
        out[idx, 0::2] = sources[top][idx, 0::2]
        out[idx, 1::2] = sources[bottom][idx, 1::2]
    return out


def window_metrics(frames: np.ndarray) -> dict:
    """Everything above, run once over one window, on its active rows only."""
    top, bottom = active_rows(frames)
    active = frames[:, top:bottom]
    match = field_match(active)
    blended = blend_solve(active)
    if len(match.best_kind) >= 2:
        matched_dup = frame_diff(matched_sequence(active, match))
    else:
        matched_dup = np.zeros(0, dtype=np.float64)
    return {
        "crop_rows": (top, bottom),
        "total_blocks": total_blocks(active.shape[1], active.shape[2]),
        "comb": comb(active),
        "match": match,
        "dup": frame_diff(active),
        # Telecine duplicates only become identical once the fields have been
        # re-paired, so the duplicate `decimate` drops is invisible in raw dup.
        "matched_dup": matched_dup,
        "blend": blended,
        "blend_period": blend_period(blended),
        "idet": idet_score(active),
    }
