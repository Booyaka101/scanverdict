"""Metric unit tests on synthetic arrays. No ffmpeg, no decoding."""

from __future__ import annotations

import numpy as np
import pytest

from scanverdict.metrics import (
    active_rows,
    blend_period,
    blend_solve,
    comb,
    field_match,
    frame_diff,
    idet_score,
    total_blocks,
    weave,
)

HEIGHT, WIDTH = 96, 128


def ramp(shift: int = 0) -> np.ndarray:
    """A smooth diagonal wave. Nothing here should read as combed.

    Sine rather than a modulo gradient: a wrap discontinuity is a hard vertical
    edge and the comb metric is right to flag it.
    """
    y = np.arange(HEIGHT)[:, None] / HEIGHT
    x = (np.arange(WIDTH)[None, :] + shift * 3) / WIDTH
    return (128 + 100 * np.sin(2 * np.pi * (0.7 * y + 1.3 * x))).astype(np.uint8)


def moving(count: int, step: int = 4) -> np.ndarray:
    return np.stack([ramp(i * step) for i in range(count)])


def test_total_blocks():
    assert total_blocks(96, 128) == (96 // 8) * (128 // 16)


def test_smooth_frames_are_not_combed():
    assert comb(moving(4)).max() == 0


def test_weaving_two_moments_combs():
    frames = moving(2, step=12)
    woven = weave(frames[:1], frames[1:2])
    assert comb(woven)[0] > 0.5 * total_blocks(HEIGHT, WIDTH)


def test_comb_ignores_the_outer_rows():
    """d1 and d2 need a row either side, so rows 0 and h-1 can never be combed."""
    frame = np.zeros((1, 16, 16), dtype=np.uint8)
    frame[0, 0] = 255
    frame[0, -1] = 255
    assert comb(frame)[0] == 0


def test_progressive_run_matches_itself():
    match = field_match(moving(10))
    assert match.match_string == "c" * 8
    assert match.best_comb.max() == 0


def test_telecined_run_matches_a_neighbour():
    """Split one moment's fields across two frames, the way pulldown does.

    Frame 3 keeps its own top field but carries frame 4's bottom. The whole
    frame lives in frame 3's top plus frame 2's bottom, which is match "p".
    """
    frames = moving(6, step=8)
    dirty = frames.copy()
    dirty[2] = weave(frames[2:3], frames[3:4])[0]
    dirty[3] = weave(frames[3:4], frames[4:5])[0]
    match = field_match(dirty)
    assert match.best_kind[2] == "p"       # interior index 2 is frame 3
    assert match.best_comb[2] == 0


def test_frame_diff_sees_a_duplicate():
    frames = moving(4)
    frames[2] = frames[1]
    diff = frame_diff(frames)
    assert diff[1] == pytest.approx(0.0)
    assert diff[0] > 1.0


def test_blend_solve_recovers_alpha():
    frames = moving(3, step=6).astype(np.float32)
    frames[1] = 0.35 * frames[0] + 0.65 * frames[2]
    result = blend_solve(frames.astype(np.uint8))
    assert result.alpha[0] == pytest.approx(0.35, abs=0.05)
    assert result.blended[0]


def test_blend_solve_leaves_clean_frames_alone():
    assert not blend_solve(moving(5, step=9)).blended.any()


def test_blend_solve_ignores_a_frozen_triple():
    frozen = np.repeat(ramp()[None], 3, axis=0)
    result = blend_solve(frozen)
    assert np.isinf(result.residual[0])
    assert not result.blended[0]


def test_idet_calls_a_progressive_run_progressive():
    assert idet_score(moving(8)).verdict == "progressive"


def test_idet_finds_the_field_order():
    """Fields alternating between two moments, top field leading."""
    frames = moving(9, step=10)
    woven = np.stack([weave(frames[i : i + 1], frames[i + 1 : i + 2])[0] for i in range(8)])
    assert idet_score(woven).verdict in ("tff", "bff")


def test_active_rows_strips_the_letterbox():
    frames = moving(4)
    frames[:, :12] = 0
    frames[:, -12:] = 0
    top, bottom = active_rows(frames)
    assert top == 12
    assert bottom == HEIGHT - 12


def test_active_rows_keeps_field_parity():
    frames = moving(4)
    frames[:, :11] = 0      # odd boundary: cropping there would swap the fields
    top, bottom = active_rows(frames)
    assert top % 2 == 0
    assert (bottom - top) % 2 == 0


def test_active_rows_survives_an_all_black_window():
    black = np.zeros((4, HEIGHT, WIDTH), dtype=np.uint8)
    assert active_rows(black) == (0, HEIGHT)


def test_short_windows_do_not_crash():
    short = moving(2)
    assert field_match(short).match_string == ""
    assert blend_solve(short).alpha.size == 0


def _panning(count: int, rate: float) -> np.ndarray:
    """A textured field panning at `rate` pixels per frame."""
    rng = np.random.default_rng(7)
    canvas = rng.integers(0, 255, size=(HEIGHT, WIDTH + 4 * count), dtype=np.uint8)
    out = np.empty((count, HEIGHT, WIDTH), dtype=np.uint8)
    for i in range(count):
        x = i * rate
        left, weight = int(x), x - int(x)
        a = canvas[:, left : left + WIDTH].astype(np.float32)
        b = canvas[:, left + 1 : left + 1 + WIDTH].astype(np.float32)
        out[i] = (a * (1 - weight) + b * weight).round().astype(np.uint8)
    return out


def test_blend_period_finds_a_24_to_25_cycle():
    """Resampling 24 source frames onto 25 output frames cycles every 25."""
    source = _panning(200, rate=3.0)
    picked = [i * 24 / 25 for i in range(150)]
    frames = np.empty((150, HEIGHT, WIDTH), dtype=np.uint8)
    for i, pos in enumerate(picked):
        lo, weight = int(pos), pos - int(pos)
        frames[i] = (
            source[lo].astype(np.float32) * (1 - weight)
            + source[lo + 1].astype(np.float32) * weight
        ).round()
    found = blend_period(blend_solve(frames))
    assert found is not None
    assert found.period == 25


def test_blend_period_ignores_clean_motion():
    assert blend_period(blend_solve(_panning(150, rate=3.0))) is None


def test_blend_period_needs_enough_frames():
    assert blend_period(blend_solve(_panning(40, rate=3.0))) is None
