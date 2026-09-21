"""Every fixture has to come back with the exact verdict string it was built for."""

from __future__ import annotations

import pytest

from scanverdict.classify import (
    FIELD_BLENDED,
    INTERLACED_TFF,
    MIXED,
    PROGRESSIVE,
    PULLDOWN_2_2,
    TELECINE_3_2,
    UNDETERMINED,
)
from scanverdict.recommend import segment_chains

EXPECTED = {
    "progressive.mkv": PROGRESSIVE,
    "telecine.mkv": TELECINE_3_2,
    "interlaced.mkv": INTERLACED_TFF,
    "pulldown22.mkv": PULLDOWN_2_2,
    "blended.mkv": FIELD_BLENDED,
    "flagged.mkv": PROGRESSIVE,
    # 24 -> 25 blending mixes whole frames, not fields, so the scan really is
    # progressive. The blend itself is reported separately, see below.
    "blend2425.mkv": PROGRESSIVE,
}


@pytest.mark.parametrize("name,label", sorted(EXPECTED.items()))
def test_verdict(reports, name, label):
    assert reports[name].verdict.label == label


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_confidence_is_high(reports, name):
    assert reports[name].verdict.confidence == "high"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_window_agrees(reports, name):
    verdict = reports[name].verdict
    assert [w.label for w in verdict.windows] == [verdict.label] * len(verdict.windows)


def test_telecine_cadence(reports):
    verdict = reports["telecine.mkv"].verdict
    assert verdict.cadence == "3:2"
    assert verdict.phase == 2
    assert verdict.field_order == "tff"
    assert not verdict.field_order_assumed


def test_pulldown22_cadence(reports):
    verdict = reports["pulldown22.mkv"].verdict
    assert verdict.cadence == "2:2"
    assert verdict.phase is not None


def test_interlaced_field_order(reports):
    verdict = reports["interlaced.mkv"].verdict
    assert verdict.field_order == "tff"
    assert verdict.container_agreement == "agrees"


def test_progressive_has_no_cadence(reports):
    verdict = reports["progressive.mkv"].verdict
    assert verdict.cadence is None
    assert verdict.container_agreement == "agrees"


def test_container_disagreement_is_called_out(reports):
    """Progressive pixels behind a tt flag. The whole point of reconciling."""
    verdict = reports["flagged.mkv"].verdict
    assert verdict.label == PROGRESSIVE
    assert verdict.container_label == INTERLACED_TFF
    assert verdict.container_agreement == "disagrees"


def test_telecine_container_lies_too(reports):
    verdict = reports["telecine.mkv"].verdict
    assert verdict.container_label == PROGRESSIVE
    assert verdict.container_agreement == "disagrees"


def test_a_spliced_file_is_called_mixed(reports):
    """Telecined film joined to true interlace. One chain cannot serve both."""
    verdict = reports["mixed.mkv"].verdict
    assert verdict.label == MIXED
    assert verdict.confidence == "low"
    assert {w.label for w in verdict.windows} == {TELECINE_3_2, INTERLACED_TFF}
    assert not reports["mixed.mkv"].rec.filters


def test_mixed_names_a_chain_per_segment(reports):
    chains = dict(segment_chains(reports["mixed.mkv"].verdict))
    assert chains[TELECINE_3_2] == "fieldmatch=order=tff,decimate"
    assert chains[INTERLACED_TFF] == "bwdif=mode=send_field:parity=tff"


def test_a_frozen_file_refuses_to_guess(reports):
    """Nothing moves, so nothing can be measured. Never a guess."""
    report = reports["frozen.mkv"]
    assert report.verdict.label == UNDETERMINED
    assert report.verdict.container_agreement == "nothing to compare it to"
    assert not report.rec.filters
    assert all("no motion" in w.reason for w in report.verdict.windows)


def test_a_blended_rate_conversion_is_named(reports):
    """24 -> 25 by blending: progressive frames that are still mixtures."""
    verdict = reports["blend2425.mkv"].verdict
    blend = verdict.frame_blend
    assert blend is not None
    assert blend.period in (24, 25)
    assert blend.source_fps == pytest.approx(24.0, abs=0.1)
    assert any("blend cycle repeats every" in note for note in verdict.notes)


def test_clean_progressive_reports_no_blend(reports):
    for name in ("progressive.mkv", "flagged.mkv"):
        assert reports[name].verdict.frame_blend is None


def test_a_blended_file_is_sent_to_srestore(reports):
    report = reports["blend2425.mkv"]
    assert not report.rec.filters
    assert "srestore" in report.rec.vapoursynth
    assert "frate=24.000" in report.rec.vapoursynth
