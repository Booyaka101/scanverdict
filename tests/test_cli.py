"""The command line: output shapes, exit codes and the failure paths."""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from pathlib import Path

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


def test_full_scan_turns_a_mixed_file_into_segments(capsys, fixtures):
    """mixed.mkv is 20s of telecine spliced to 20s of interlace. Find the seam."""
    code, out, _ = run(
        capsys, str(fixtures["mixed.mkv"]), "--full", "--no-verify", "--json"
    )
    payload = json.loads(out)
    assert code == 0
    assert payload["sampling"]["full"] is True
    labels = [seg["label"] for seg in payload["segments"]]
    assert labels == ["telecine_3_2", "interlaced_tff"]
    seam = payload["segments"][0]["end"]
    assert seam == pytest.approx(20.0, abs=payload["sampling"]["window_seconds"])


def test_full_scan_prints_a_cut_list(capsys, fixtures):
    code, out, _ = run(capsys, str(fixtures["mixed.mkv"]), "--full", "--no-verify")
    assert code == 0
    assert "segments" in out
    assert "-vf fieldmatch=order=tff,decimate -c:v libx264 out01.mkv" in out
    assert "-vf bwdif=mode=send_field:parity=tff -c:v libx264 out02.mkv" in out


def test_sampling_reports_no_segments(capsys, fixtures):
    code, out, _ = run(capsys, str(fixtures["mixed.mkv"]), "--json", *FAST)
    payload = json.loads(out)
    assert code == 0
    assert payload["segments"] == []
    assert any("--full" in note for note in payload["notes"])


def test_a_non_latin1_filename_survives_stdout(fixtures, tmp_path):
    """Windows picks the ANSI code page for stdout, which cannot hold this name.

    Runs out of process on purpose: capsys would substitute its own encoder and
    hide the very thing being tested.
    """
    target = tmp_path / "映画.mkv"
    target.write_bytes(fixtures["progressive.mkv"].read_bytes())
    done = subprocess.run(
        [sys.executable, "-m", "scanverdict", str(target), "--quiet", *FAST],
        capture_output=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert "映画.mkv: progressive" in done.stdout.decode("utf-8")
