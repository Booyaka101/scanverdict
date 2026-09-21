"""Window layout: where the spans land, and when the plan refuses."""

from __future__ import annotations

from fractions import Fraction

import pytest

from scanverdict.ffmpeg import ScanverdictError
from scanverdict.probe import Probe
from scanverdict.sampler import Plan, plan, sample

NTSC = Fraction(30000, 1001)


def a_probe(duration: float = 600.0, height: int = 480, fps: Fraction = NTSC) -> Probe:
    return Probe(
        path="x.mkv", codec="ffv1", width=640, height=height, duration=duration,
        r_frame_rate=fps, avg_frame_rate=fps, field_order="progressive",
        nb_frames=round(duration * float(fps)), pix_fmt="yuv420p",
    )


def test_sampled_windows_do_not_overlap():
    layout = plan(a_probe(), windows=12, frames_per_window=120)
    assert len(layout.starts) == 12
    gaps = [b - a for a, b in zip(layout.starts, layout.starts[1:], strict=False)]
    assert min(gaps) >= layout.window_seconds
    assert not layout.contiguous


def test_sampled_windows_stay_inside_the_middle_of_the_file():
    layout = plan(a_probe(), windows=12, frames_per_window=120)
    assert layout.starts[0] >= 600.0 * 0.05
    assert layout.starts[-1] + layout.window_seconds <= 600.0 * 0.95 + 0.01


def test_full_windows_abut():
    layout = plan(a_probe(), windows=12, frames_per_window=120, full=True)
    assert layout.contiguous
    assert layout.starts[0] == 0.0
    gaps = {round(b - a, 2) for a, b in zip(layout.starts, layout.starts[1:], strict=False)}
    assert gaps == {round(layout.window_seconds, 2)}


def test_full_covers_the_whole_file():
    layout = plan(a_probe(duration=600.0), windows=1, frames_per_window=120, full=True)
    covered = layout.starts[-1] + layout.window_seconds
    assert covered == pytest.approx(600.0, abs=layout.window_seconds)
    assert "full scan" in layout.note


def test_full_ignores_the_window_count():
    few = plan(a_probe(), windows=2, frames_per_window=120, full=True)
    many = plan(a_probe(), windows=99, frames_per_window=120, full=True)
    assert few.starts == many.starts


def test_a_short_file_gets_shorter_windows():
    layout = plan(a_probe(duration=2.0), windows=12, frames_per_window=120)
    assert layout.frames_per_window < 120
    assert len(layout.starts) == 1
    assert "only 2.0s" in layout.note


def test_an_absurd_window_is_refused_rather_than_swallowing_memory():
    with pytest.raises(ScanverdictError) as caught:
        plan(a_probe(duration=90000.0, height=2160), windows=1, frames_per_window=900000)
    assert "--frames-per-window" in str(caught.value)


def test_windows_are_decoded_one_at_a_time():
    """--full plans thousands of windows, so holding them all would cost tens of GB."""
    info = a_probe()
    layout = Plan(starts=[0.0, 1.0], frames_per_window=10, requested_windows=2)
    # x.mkv does not exist, so a list-returning sample() would raise on this line.
    stream = sample(info, layout)
    with pytest.raises(ScanverdictError):
        next(stream)
