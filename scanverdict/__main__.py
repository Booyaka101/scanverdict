"""scanverdict command line: argument parsing, orchestration and rendering."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass

from . import __version__
from .classify import MIXED, UNDETERMINED, FileVerdict, classify_window, reconcile
from .ffmpeg import ScanverdictError, require
from .probe import Probe, probe
from .recommend import Recommendation, fps_text, recommend, segment_chains
from .sampler import Plan, plan, sample
from .verify import Verification, apply_to_confidence, verify

DEFAULT_WINDOWS = 12
DEFAULT_FRAMES = 120


@dataclass
class Report:
    info: Probe
    layout: Plan
    verdict: FileVerdict
    rec: Recommendation
    checked: Verification

    def as_dict(self) -> dict:
        v = self.verdict
        return {
            "scanverdict": __version__,
            "file": self.info.path,
            "verdict": v.label,
            "confidence": v.confidence,
            "field_order": v.field_order,
            "field_order_assumed": v.field_order_assumed,
            "cadence": v.cadence,
            "phase": v.phase,
            "phase_agreement": v.phase_agreement,
            "agreement": v.agreement,
            "container": {
                "field_order": self.info.field_order,
                "label": v.container_label,
                "agreement": v.container_agreement,
            },
            "probe": self.info.as_dict(),
            "sampling": {
                "windows": len(self.layout.starts),
                "windows_requested": self.layout.requested_windows,
                "frames_per_window": self.layout.frames_per_window,
                "starts": self.layout.starts,
                "note": self.layout.note,
            },
            "recommendation": self.rec.as_dict(),
            "verification": self.checked.as_dict(),
            "windows": [w.as_dict() for w in v.windows],
            "notes": v.notes,
        }

    def as_row(self) -> dict:
        v, c = self.verdict, self.checked
        return {
            "file": self.info.path,
            "verdict": v.label,
            "confidence": v.confidence,
            "field_order": v.field_order or "",
            "cadence": v.cadence or "",
            "phase": "" if v.phase is None else v.phase,
            "agreement": v.agreement,
            "container": v.container_label,
            "container_agreement": v.container_agreement,
            "fps_in": fps_text(self.info.r_frame_rate),
            "fps_out": fps_text(self.rec.output_fps),
            "filters": ",".join(self.rec.filters),
            "comb_before": "" if c.comb_before is None else c.comb_before,
            "comb_after": "" if c.comb_after is None else c.comb_after,
            "reduction": "" if c.reduction is None else f"{c.reduction:.4f}",
            "frames_before": c.frames_before or "",
            "frames_after": c.frames_after or "",
            "verified": "yes" if c.passed else ("no" if c.ran else "skipped"),
        }


CSV_FIELDS = [
    "file", "verdict", "confidence", "field_order", "cadence", "phase",
    "agreement", "container", "container_agreement", "fps_in", "fps_out",
    "filters", "comb_before", "comb_after", "reduction", "frames_before",
    "frames_after", "verified",
]


def analyse(path: str, args: argparse.Namespace) -> Report:
    info = probe(path)
    layout = plan(info, args.windows, args.frames_per_window)
    windows = sample(info, layout)
    verdicts = [classify_window(w, info) for w in windows]
    verdict = reconcile(verdicts, info)
    if layout.note:
        verdict.notes.insert(0, layout.note)

    rec = recommend(verdict, info)
    if args.no_verify:
        checked = Verification(ran=False, reason="skipped (--no-verify)")
    else:
        checked = verify(info, layout, verdict, rec)
        apply_to_confidence(verdict, checked)
        rec = recommend(verdict, info)
    return Report(info, layout, verdict, rec, checked)


def render(report: Report, out) -> None:
    info, v, rec, checked = report.info, report.verdict, report.rec, report.checked
    w = out.write

    w(f"{info.path}\n")
    w(
        f"  {info.width}x{info.height} {info.codec}, {fps_text(info.r_frame_rate)} fps, "
        f"{info.duration:.1f} s, {info.pix_fmt}\n\n"
    )

    w(f"verdict: {v.label} (confidence {v.confidence})\n")
    w(f"container says: {v.container_label} ({v.container_agreement})\n")
    if v.cadence:
        w(f"cadence: {v.cadence}, phase {v.phase}, {v.phase_agreement}\n")
    if v.field_order:
        suffix = " (assumed, not measured)" if v.field_order_assumed else ""
        w(f"field order: {v.field_order}{suffix}\n")
    w(f"sampled: {len(report.layout.starts)} windows of "
      f"{report.layout.frames_per_window} frames, {v.agreement} agree\n")

    w("\nevidence\n")
    for line in _evidence_lines(report):
        w(f"  {line}\n")

    if v.label in (MIXED, UNDETERMINED) or any(win.label != v.label for win in v.windows):
        w("\nper window\n")
        for line in _window_table(report):
            w(f"  {line}\n")

    w(f"\n{rec.summary}\n")
    if rec.filters:
        w(f"\n{rec.ffmpeg}\n")
    if v.label == MIXED:
        w("\nsegments need different chains:\n")
        for label, chain in segment_chains(v):
            w(f"  {label:<16} {chain}\n")

    w("\nvapoursynth\n")
    for line in rec.vapoursynth.rstrip().splitlines():
        w(f"  {line}\n")

    w(f"\nverified: {checked.headline()}\n")

    for note in rec.caveats + v.notes:
        w(f"note: {note}\n")


def _evidence_lines(report: Report) -> list[str]:
    windows = [win for win in report.verdict.windows if win.evidence]
    if not windows:
        return ["no window produced usable measurements"]

    def avg(key: str) -> float:
        return sum(win.evidence[key] for win in windows) / len(windows)

    blocks = windows[0].evidence["total_blocks"]
    lines = [
        f"combing          {avg('comb_mean'):.1%} of the {blocks} blocks in a frame, "
        f"{avg('dirty_share'):.0%} of frames above threshold",
        f"after matching   {avg('best_comb_mean'):.1%} of blocks, "
        f"{avg('clean_share'):.0%} of frames clean",
        f"field matches    {avg('match_c_share'):.0%} keep the frame as shot "
        f"(example: {windows[0].evidence['match_string'][:30]})",
        f"blend solve      fires on {avg('blend_rate'):.0%} of frames",
        f"motion           {avg('motion'):.1f} gray levels between frames (p90)",
        f"idet             {', '.join(sorted({win.evidence['idet'] for win in windows}))}",
    ]
    crop = windows[0].evidence["crop_rows"]
    if crop[0] or crop[1] != report.info.height:
        lines.append(
            f"letterbox        rows {crop[0]}-{crop[1]} carry picture; "
            "the black bars are excluded"
        )
    return lines


def _window_table(report: Report) -> list[str]:
    rows = ["#   start     verdict          why"]
    for win in report.verdict.windows:
        rows.append(f"{win.index:<3} {win.start:>8.1f}  {win.label:<16} {win.reason}")
    return rows


def _quiet_line(report: Report) -> str:
    v = report.verdict
    extra = f" cadence {v.cadence} phase {v.phase}" if v.cadence else ""
    return f"{report.info.path}: {v.label} ({v.confidence}){extra}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scanverdict",
        description=(
            "Tell what scan type and cadence a video file really has, recommend the "
            "filter chain that fixes it, and prove the recommendation by running it."
        ),
        epilog=(
            "Per-scene NTSC inverse telecine by hand is Wobbly's job: "
            "https://github.com/Jaded-Encoding-Thaumaturgy/Wobbly"
        ),
    )
    p.add_argument("files", nargs="+", metavar="FILE", help="video files to analyse")
    p.add_argument("--json", action="store_true", help="emit JSON (an array for several files)")
    p.add_argument("--csv", action="store_true", help="emit one CSV row per file")
    p.add_argument(
        "--windows", type=int, default=DEFAULT_WINDOWS, metavar="N",
        help=f"how many spans to sample across the file (default {DEFAULT_WINDOWS})",
    )
    p.add_argument(
        "--frames-per-window", type=int, default=DEFAULT_FRAMES, metavar="N",
        help=f"frames decoded per span (default {DEFAULT_FRAMES})",
    )
    p.add_argument("--no-verify", action="store_true", help="skip the proving pass")
    p.add_argument("--quiet", action="store_true", help="one line per file")
    p.add_argument("--version", action="version", version=f"scanverdict {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except KeyboardInterrupt:
        print("scanverdict: interrupted", file=sys.stderr)
        return 130


def _run(argv: list[str] | None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.json and args.csv:
        parser.error("--json and --csv are mutually exclusive")
    if args.windows < 1:
        parser.error("--windows must be at least 1")
    if args.frames_per_window < 8:
        parser.error("--frames-per-window must be at least 8")

    try:
        require("ffmpeg", "ffprobe")
    except ScanverdictError as exc:
        print(f"scanverdict: {exc}", file=sys.stderr)
        return 2

    reports: list[Report] = []
    failures = 0
    for index, path in enumerate(args.files):
        try:
            report = analyse(path, args)
        except ScanverdictError as exc:
            print(f"scanverdict: {exc}", file=sys.stderr)
            failures += 1
            continue
        reports.append(report)
        if args.json or args.csv:
            continue
        if args.quiet:
            print(_quiet_line(report))
        else:
            if index:
                print("-" * 72)
            render(report, sys.stdout)

    if args.json:
        payload = [r.as_dict() for r in reports]
        # One file in, one object out, so `| jq -r .verdict` works. Asking for
        # several always gives an array, even when only one of them survived.
        single = len(args.files) == 1 and len(payload) == 1
        json.dump(payload[0] if single else payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif args.csv:
        writer = csv.DictWriter(sys.stdout, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for r in reports:
            writer.writerow(r.as_row())

    if not reports:
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
