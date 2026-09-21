"""scanverdict command line: argument parsing, orchestration and rendering."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass

from . import __version__
from .classify import (
    MIXED,
    UNDETERMINED,
    FileVerdict,
    Segment,
    classify_window,
    reconcile,
    segments,
)
from .ffmpeg import ScanverdictError, require
from .probe import Probe, probe
from .recommend import (
    Recommendation,
    chain_text,
    fps_text,
    recommend,
    segment_chains,
    segment_command,
)
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

    @property
    def segments(self) -> list[Segment]:
        """Timed spans, or [] when there is nothing to cut on.

        One span covering the whole file says no more than the verdict already does.
        """
        if not self.layout.contiguous:
            return []
        runs = segments(self.verdict, self.layout.window_seconds, self.info.duration)
        return runs if len(runs) > 1 else []

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
            "frame_blend": None if v.frame_blend is None else v.frame_blend.as_dict(),
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
                "full": self.layout.contiguous,
                "window_seconds": round(self.layout.window_seconds, 3),
            },
            "recommendation": self.rec.as_dict(),
            "verification": self.checked.as_dict(),
            "windows": [w.as_dict() for w in v.windows],
            "segments": [s.as_dict() for s in self.segments],
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
            "blend_period": "" if v.frame_blend is None else v.frame_blend.period,
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
    "blend_period", "agreement", "container", "container_agreement", "fps_in", "fps_out",
    "filters", "comb_before", "comb_after", "reduction", "frames_before",
    "frames_after", "verified",
]


PROGRESS_FROM = 24   # windows, above which a silent scan looks like a hang


def _progress(done: int, total: int) -> None:
    if total < PROGRESS_FROM or not sys.stderr.isatty():
        return
    print(
        f"\r  window {done}/{total}",
        end="\n" if done == total else "",
        file=sys.stderr,
        flush=True,
    )


def analyse(path: str, args: argparse.Namespace) -> Report:
    info = probe(path)
    layout = plan(info, args.windows, args.frames_per_window, full=args.full)
    verdicts = []
    for window in sample(info, layout):
        verdicts.append(classify_window(window, info))
        _progress(len(verdicts), len(layout.starts))
    verdict = reconcile(verdicts, info)
    if layout.note:
        verdict.notes.insert(0, layout.note)
    if verdict.label == MIXED and not layout.contiguous:
        verdict.notes.append(
            "run it again with --full to turn these windows into timed segments "
            "and a cut list"
        )

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
    how = "scanned" if report.layout.contiguous else "sampled"
    w(f"{how}: {len(report.layout.starts)} windows of "
      f"{report.layout.frames_per_window} frames, {v.agreement} agree\n")

    w("\nevidence\n")
    for line in _evidence_lines(report):
        w(f"  {line}\n")

    runs = report.segments
    if not runs and (
        v.label in (MIXED, UNDETERMINED) or any(win.label != v.label for win in v.windows)
    ):
        w("\nper window\n")
        for line in _window_table(report):
            w(f"  {line}\n")

    w(f"\n{rec.summary}\n")
    if rec.filters:
        w(f"\n{rec.ffmpeg}\n")
    if runs:
        w("\nsegments\n")
        for line in _segment_table(report, runs):
            w(f"  {line}\n")
        if len({seg.label for seg in runs}) > 1:
            w("\ncut there and run each part through its own chain:\n")
            for i, seg in enumerate(runs, 1):
                w(f"  {segment_command(info.path, seg, v.field_order, i)}\n")
    elif v.label == MIXED:
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
        f"blend per frame  fires on {avg('blend_rate'):.0%} of frames",
        f"motion           {avg('motion'):.1f} gray levels between frames (p90)",
        f"idet             {', '.join(sorted({win.evidence['idet'] for win in windows}))}",
    ]
    blend = report.verdict.frame_blend
    if blend is not None:
        lines.append(
            f"blend cycle      repeats every {blend.period} frames, so the source ran "
            f"at about {blend.source_fps:.1f} fps"
        )
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


def _segment_table(report: Report, runs: list[Segment]) -> list[str]:
    rows = ["start      end       verdict          chain"]
    for seg in runs:
        chain = chain_text(seg.label, report.verdict.field_order)
        rows.append(f"{seg.start:>8.1f}  {seg.end:>8.1f}  {seg.label:<16} {chain}")
    rows.append(
        "(boundaries land on a window edge, so they are good to "
        f"{report.layout.window_seconds:.1f}s)"
    )
    return rows


def _quiet_line(report: Report) -> str:
    v = report.verdict
    extra = f" cadence {v.cadence} phase {v.phase}" if v.cadence else ""
    if v.frame_blend is not None:
        extra += f" frame-blended on a {v.frame_blend.period}-frame cycle"
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
    p.add_argument(
        "--full", action="store_true",
        help="scan the whole file in back-to-back windows instead of sampling, "
             "which turns a mixed verdict into timed segments (--windows is ignored)",
    )
    p.add_argument("--no-verify", action="store_true", help="skip the proving pass")
    p.add_argument("--quiet", action="store_true", help="one line per file")
    p.add_argument("--version", action="version", version=f"scanverdict {__version__}")
    return p


def _utf8_output() -> None:
    """Stop a non-Latin-1 filename from killing the run on a Windows console.

    Python picks the ANSI code page for stdout on Windows, so a Japanese title
    raises UnicodeEncodeError halfway through printing a verdict.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    try:
        return _run(argv)
    except KeyboardInterrupt:
        print("scanverdict: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # `scanverdict ... | head` closes the pipe under us. Retiring the fd
        # keeps the interpreter from reporting the same failure again at exit.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141


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
