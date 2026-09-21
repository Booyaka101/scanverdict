"""The command line: output shapes, exit codes and the failure paths."""

from __future__ import annotations

import csv
import io
import json

import pytest

from scanverdict import __version__
from scanverdict.__main__ import main

FAST = ["--no-verify", "--windows", "2", "--frames-per-window", "60"]


def run(capsys, *argv) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_json_of_one_file_is_an_object(capsys, fixtures):
    """`scanverdict FILE --json | jq -r .verdict` has to work."""
    code, out, _ = run(capsys, str(fixtures["telecine.mkv"]), "--json", *FAST)
    payload = json.loads(out)
    assert code == 0
    assert payload["verdict"] == "telecine_3_2"
    assert payload["cadence"] == "3:2"
    assert payload["scanverdict"] == __version__


def test_json_of_several_files_is_an_array(capsys, fixtures):
    code, out, _ = run(
        capsys,
        str(fixtures["telecine.mkv"]),
        str(fixtures["progressive.mkv"]),
        "--json",
        *FAST,
    )
    payload = json.loads(out)
    assert code == 0
    assert [entry["verdict"] for entry in payload] == ["telecine_3_2", "progressive"]


def test_csv_has_a_header_and_a_row(capsys, fixtures):
    code, out, _ = run(capsys, str(fixtures["telecine.mkv"]), "--csv", *FAST)
    rows = list(csv.DictReader(io.StringIO(out)))
    assert code == 0
    assert len(rows) == 1
    assert rows[0]["verdict"] == "telecine_3_2"
    assert rows[0]["filters"] == "fieldmatch=order=tff,decimate"
    assert rows[0]["verified"] == "skipped"


def test_quiet_is_one_line_per_file(capsys, fixtures):
    code, out, _ = run(
        capsys,
        str(fixtures["telecine.mkv"]),
        str(fixtures["progressive.mkv"]),
        "--quiet",
        *FAST,
    )
    lines = out.strip().splitlines()
    assert code == 0
    assert len(lines) == 2
    assert "telecine_3_2 (high)" in lines[0]


def test_human_output_leads_with_the_verdict(capsys, fixtures):
    code, out, _ = run(capsys, str(fixtures["telecine.mkv"]), *FAST)
    assert code == 0
    assert "verdict: telecine_3_2 (confidence high)" in out
    assert "container says: progressive (disagrees)" in out
    assert "field order: tff" in out
    assert "-vf fieldmatch=order=tff,decimate -c:v libx264 out.mkv" in out
    assert "core.vivtc.VFM" in out
    assert "evidence" in out


def test_missing_file_is_a_message_not_a_traceback(capsys, tmp_path):
    code, out, err = run(capsys, str(tmp_path / "gone.mkv"), *FAST)
    assert code == 1
    assert "Traceback" not in err
    assert err.startswith("scanverdict: ")
    assert "no such file" in err
    assert out == ""


def test_one_bad_file_does_not_stop_the_others(capsys, fixtures, tmp_path):
    code, out, err = run(
        capsys, str(tmp_path / "gone.mkv"), str(fixtures["progressive.mkv"]), "--quiet", *FAST
    )
    assert code == 1               # something failed
    assert "progressive" in out    # but the good file still reported
    assert "gone.mkv" in err


def test_version():
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0


def test_json_and_csv_together_is_refused():
    with pytest.raises(SystemExit) as exit_info:
        main(["x.mkv", "--json", "--csv"])
    assert exit_info.value.code == 2


def test_zero_windows_is_refused():
    with pytest.raises(SystemExit) as exit_info:
        main(["x.mkv", "--windows", "0"])
    assert exit_info.value.code == 2


def test_a_tiny_window_is_refused():
    with pytest.raises(SystemExit) as exit_info:
        main(["x.mkv", "--frames-per-window", "3"])
    assert exit_info.value.code == 2


def test_asking_for_more_windows_than_fit_says_so(capsys, fixtures):
    code, out, _ = run(capsys, str(fixtures["telecine.mkv"]), "--no-verify", "--windows", "99")
    assert code == 0
    assert "not 99" in out
