"""What the container claims, and what happens when the file is not a video."""

from __future__ import annotations

from fractions import Fraction

import pytest

from scanverdict.ffmpeg import ScanverdictError
from scanverdict.probe import Probe, probe


def make(**over) -> Probe:
    settings = {
        "path": "x.mkv",
        "codec": "h264",
        "width": 720,
        "height": 576,
        "duration": 60.0,
        "r_frame_rate": Fraction(25),
        "avg_frame_rate": Fraction(25),
        "field_order": "progressive",
        "nb_frames": 1500,
        "pix_fmt": "yuv420p",
    }
    settings.update(over)
    return Probe(**settings)


@pytest.mark.parametrize(
    "token,label",
    [
        ("progressive", "progressive"),
        ("tt", "interlaced_tff"),
        ("bb", "interlaced_bff"),
        # ffprobe reports tb for content built with tinterlace=interleave_top,
        # which is top field first. See the note in probe.py.
        ("tb", "interlaced_tff"),
        ("bt", "interlaced_bff"),
        ("unknown", "unknown"),
        ("nonsense", "unknown"),
    ],
)
def test_container_verdict(token, label):
    assert make(field_order=token).container_verdict == label


def test_constant_rate_is_not_vfr():
    assert not make().is_vfr


def test_wildly_different_rates_are_vfr():
    assert make(r_frame_rate=Fraction(30000, 1001), avg_frame_rate=Fraction(24)).is_vfr


def test_a_rate_of_zero_is_not_called_vfr():
    assert not make(avg_frame_rate=Fraction(0)).is_vfr


def test_missing_file_is_a_clear_message(tmp_path):
    with pytest.raises(ScanverdictError, match="no such file"):
        probe(str(tmp_path / "nope.mkv"))


def test_a_directory_is_a_clear_message(tmp_path):
    with pytest.raises(ScanverdictError, match="directory"):
        probe(str(tmp_path))


def test_an_empty_file_is_a_clear_message(tmp_path):
    empty = tmp_path / "empty.mkv"
    empty.write_bytes(b"")
    with pytest.raises(ScanverdictError, match="empty"):
        probe(str(empty))


def test_a_text_file_is_not_a_video(tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("this is not a video", encoding="utf-8")
    with pytest.raises(ScanverdictError):
        probe(str(text))
