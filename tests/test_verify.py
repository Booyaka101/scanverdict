"""The proving pass: run the recommended chain and measure that it worked."""

from __future__ import annotations

from fractions import Fraction

import pytest
from conftest import cli_args

from scanverdict.__main__ import analyse


@pytest.fixture(scope="module")
def proven(fixtures):
    return analyse(str(fixtures["telecine.mkv"]), cli_args(no_verify=False))


def test_chain_removes_the_combing(proven):
    checked = proven.checked
    assert checked.ran
    assert checked.reduction < -0.90, checked.headline()


def test_frame_count_matches_the_cadence(proven):
    checked = proven.checked
    assert checked.count_ok
    assert checked.frames_after == round(checked.frames_before * 4 / 5)


def test_output_rate_is_film_rate(proven):
    assert proven.checked.fps_after == Fraction(24000, 1001)


def test_verification_passes_and_keeps_confidence(proven):
    assert proven.checked.passed
    assert proven.verdict.confidence == "high"
    assert "as expected" in proven.checked.headline()


def test_nothing_to_verify_on_progressive(fixtures):
    report = analyse(str(fixtures["progressive.mkv"]), cli_args(no_verify=False))
    assert not report.checked.ran
    assert "no filter" in report.checked.reason


def test_skipping_says_so(fixtures):
    report = analyse(str(fixtures["telecine.mkv"]), cli_args(no_verify=True))
    assert not report.checked.ran
    assert report.checked.headline() == "skipped (--no-verify)"
