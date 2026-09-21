"""Verdict to filter chain. Pure mapping, no decoding."""

from __future__ import annotations

import ast
from fractions import Fraction

import pytest

from scanverdict.classify import (
    FIELD_BLENDED,
    INTERLACED_BFF,
    INTERLACED_TFF,
    MIXED,
    PROGRESSIVE,
    PULLDOWN_2_2,
    TELECINE_3_2,
    UNDETERMINED,
    FileVerdict,
    WindowVerdict,
)
from scanverdict.probe import Probe
from scanverdict.recommend import chain_for, fps_text, recommend, segment_chains


def make_probe(rate="30000/1001", avg=None, **over) -> Probe:
    settings = {
        "path": "dvd_rip.mkv",
        "codec": "mpeg2video",
        "width": 720,
        "height": 480,
        "duration": 600.0,
        "r_frame_rate": Fraction(rate),
        "avg_frame_rate": Fraction(avg or rate),
        "field_order": "progressive",
        "nb_frames": 17982,
        "pix_fmt": "yuv420p",
    }
    settings.update(over)
    return Probe(**settings)


def make_verdict(label, field_order="tff", assumed=False, windows=()) -> FileVerdict:
    return FileVerdict(
        label=label,
        confidence="high",
        field_order=field_order,
        field_order_assumed=assumed,
        cadence=None,
        phase=None,
        phase_agreement="",
        agreement="12/12 windows",
        windows=list(windows),
        notes=[],
        container_label=PROGRESSIVE,
        container_agreement="disagrees",
    )


@pytest.mark.parametrize(
    "label,expected",
    [
        (PROGRESSIVE, []),
        (INTERLACED_TFF, ["bwdif=mode=send_field:parity=tff"]),
        (INTERLACED_BFF, ["bwdif=mode=send_field:parity=bff"]),
        (TELECINE_3_2, ["fieldmatch=order=tff", "decimate"]),
        (PULLDOWN_2_2, ["fieldmatch=order=tff", "decimate=cycle=2"]),
        (FIELD_BLENDED, []),
        (UNDETERMINED, []),
        (MIXED, []),
    ],
)
def test_chain_for(label, expected):
    assert chain_for(label, "tff") == expected


def test_bff_telecine_carries_the_order_through():
    assert chain_for(TELECINE_3_2, "bff")[0] == "fieldmatch=order=bff"


@pytest.mark.parametrize(
    "rate,text",
    [("30000/1001", "29.97"), ("24000/1001", "23.976"), ("25", "25"), ("50", "50")],
)
def test_fps_text(rate, text):
    assert fps_text(Fraction(rate)) == text


def test_telecine_recommendation():
    rec = recommend(make_verdict(TELECINE_3_2), make_probe())
    assert rec.ffmpeg == (
        "ffmpeg -i dvd_rip.mkv -vf fieldmatch=order=tff,decimate -c:v libx264 out.mkv"
    )
    assert rec.frame_ratio == Fraction(4, 5)
    assert rec.output_fps == Fraction(24000, 1001)
    assert "core.vivtc.VFM(clip, order=1)" in rec.vapoursynth
    assert "core.vivtc.VDecimate(clip)" in rec.vapoursynth


def test_bff_snippet_uses_order_zero():
    rec = recommend(make_verdict(TELECINE_3_2, field_order="bff"), make_probe())
    assert "core.vivtc.VFM(clip, order=0)" in rec.vapoursynth


def test_interlace_doubles_the_rate():
    rec = recommend(make_verdict(INTERLACED_TFF), make_probe())
    assert rec.frame_ratio == Fraction(2)
    assert rec.output_fps == Fraction(60000, 1001)
    assert any("send_field" in c for c in rec.caveats)


def test_pulldown22_halves_the_rate():
    rec = recommend(make_verdict(PULLDOWN_2_2), make_probe(rate="50"))
    assert rec.output_fps == Fraction(25)
    assert "decimate=cycle=2" in rec.ffmpeg


def test_field_blended_admits_ffmpeg_cannot_do_it():
    rec = recommend(make_verdict(FIELD_BLENDED), make_probe())
    assert rec.is_noop
    assert "no good answer" in rec.summary
    assert "srestore" in rec.vapoursynth
    # srestore was dropped in havsfunc 34, so the snippet has to pin the version.
    assert "havsfunc==33" in rec.vapoursynth


def test_progressive_recommends_nothing():
    rec = recommend(make_verdict(PROGRESSIVE, field_order=None), make_probe())
    assert rec.is_noop
    assert rec.frame_ratio == Fraction(1)


def test_undetermined_refuses_to_guess():
    rec = recommend(make_verdict(UNDETERMINED, field_order=None), make_probe())
    assert rec.is_noop
    assert rec.frame_ratio is None
    assert "will not guess" in rec.vapoursynth


def test_assumed_field_order_is_flagged():
    rec = recommend(make_verdict(TELECINE_3_2, assumed=True), make_probe())
    assert any("order=bff" in c for c in rec.caveats)


def test_vfr_input_gets_a_warning():
    probe = make_probe(rate="30000/1001", avg="24000/1001")
    rec = recommend(make_verdict(TELECINE_3_2), probe)
    assert any("dejudder" in c for c in rec.caveats)


def test_mixed_lists_a_chain_per_segment():
    windows = [
        WindowVerdict(index=0, start=0.0, start_frame=0, label=TELECINE_3_2, reason=""),
        WindowVerdict(index=1, start=10.0, start_frame=300, label=INTERLACED_TFF, reason=""),
        WindowVerdict(index=2, start=20.0, start_frame=600, label=TELECINE_3_2, reason=""),
    ]
    chains = dict(segment_chains(make_verdict(MIXED, windows=windows)))
    assert chains[TELECINE_3_2] == "fieldmatch=order=tff,decimate"
    assert chains[INTERLACED_TFF] == "bwdif=mode=send_field:parity=tff"


def test_windows_path_survives_quoting():
    """Backslashes have to reach VapourSynth intact and a space must not split the argv."""
    rec = recommend(make_verdict(TELECINE_3_2), make_probe(path=r"D:\rips\my show.mkv"))
    source = next(ln for ln in rec.vapoursynth.splitlines() if "LWLibavSource" in ln)
    literal = source.split("LWLibavSource(", 1)[1].rsplit(")", 1)[0]
    assert ast.literal_eval(literal).endswith("my show.mkv")
    assert "my show.mkv" in rec.ffmpeg
