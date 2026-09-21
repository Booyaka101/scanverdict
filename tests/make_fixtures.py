"""Build the test fixtures with ffmpeg. Real video, no checked-in binaries.

Most fixtures start from the same synthetic clip: testsrc2 at double size,
scrolled diagonally so there is motion in both axes, scaled down and blurred
slightly. The blur matters. Unfiltered testsrc2 is pixel sharp, and single-pixel
vertical detail trips the comb metric on its own: a progressive testsrc2 frame
combs about 4.6% of its blocks with no interlacing anywhere near it. That floor
puts a ceiling of roughly -70% on any measured comb reduction and makes the
fixtures less like the sources this tool is for, which are film or tape through
a lens, an analogue chain and a lossy encoder. sigma 0.8 drops the floor to 0.2%
and the telecine fixture then measures 238 -> 4 combed blocks through the
recommended chain.

Run directly to build them by hand:

    python tests/make_fixtures.py [directory] [--rebuild]
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

WIDTH, HEIGHT = 640, 480
DURATION = 20
_PREPARE = (
    "scroll=horizontal=0.008:vertical=0.004,"
    f"scale={WIDTH}:{HEIGHT}:flags=bicubic,"
    "gblur=sigma=0.8"
)
_LOSSLESS = ["-c:v", "ffv1", "-level", "3", "-g", "1", "-pix_fmt", "yuv420p"]


class FixtureError(RuntimeError):
    pass


@dataclass(frozen=True)
class Generated:
    """A fixture synthesised straight out of lavfi."""

    rate: str
    after: str = ""          # filters applied after _PREPARE
    duration: int = DURATION
    prepare: str = _PREPARE

    @property
    def chain(self) -> str:
        return f"{self.prepare},{self.after}" if self.after else self.prepare


GENERATED = {
    # 24p film, never touched. The control.
    "progressive.mkv": Generated("24"),
    # 3:2 pulldown exactly as a telecine house would lay it down.
    "telecine.mkv": Generated("24000/1001", "telecine=first_field=top:pattern=23"),
    # True interlace: 60 distinct moments woven into 30 frames, nothing repeats.
    "interlaced.mkv": Generated("60", "tinterlace=mode=interleave_top"),
    # 2:2. telecine=pattern=22 on 25p is a no-op (it rebuilds the frame it just
    # split), so the repeat has to come from duplicating frames at 50.
    "pulldown22.mkv": Generated("25", "fps=50"),
    # Fields interlaced and then averaged together, the PAL-speedup signature.
    "blended.mkv": Generated("50", "tinterlace=mode=interleave_top,framerate=50"),
    # 24 -> 25 by blending. See test_classify.py: scanverdict calls this
    # progressive, and the README says why.
    "blend2425.mkv": Generated("24", "framerate=25"),
    # One frame held for 10 seconds. Nothing moves, so nothing can be measured
    # and the only honest answer is undetermined.
    "frozen.mkv": Generated(
        "25",
        "trim=end_frame=1,loop=loop=249:size=1:start=0,setpts=N/25/TB",
        duration=2,
    ),
}

# Progressive content carrying an interlaced flag. This is the disagreement the
# whole reconciliation exists for, so it gets a fixture of its own.
FLAGGED = "flagged.mkv"
FLAGGED_SOURCE = "progressive.mkv"

# Telecined film spliced onto true interlace, the way a bonus feature lands on a
# disc next to the main programme.
MIXED = "mixed.mkv"
MIXED_PARTS = ("telecine.mkv", "interlaced.mkv")

NAMES = tuple(GENERATED) + (FLAGGED, MIXED)


def _run(argv: list[str]) -> None:
    try:
        done = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError:
        raise FixtureError("ffmpeg is not on PATH, so the fixtures cannot be built") from None
    if done.returncode != 0:
        tail = "\n".join(done.stderr.strip().splitlines()[-6:])
        raise FixtureError(f"ffmpeg failed building fixtures:\n{tail}")


def _generate(path: Path, spec: Generated) -> None:
    source = f"testsrc2=size={WIDTH * 2}x{HEIGHT * 2}:duration={spec.duration}:rate={spec.rate}"
    _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", source,
        "-vf", spec.chain, *_LOSSLESS, str(path),
    ])


def _flag(path: Path, source: Path) -> None:
    _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-c:v", "copy", "-field_order", "tt", str(path),
    ])


def _splice(path: Path, parts: list[Path]) -> None:
    """Join the parts without re-encoding, so each keeps its own cadence.

    The concat demuxer resolves list entries against the list file's directory,
    which is why the names go in bare.
    """
    listing = path.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    try:
        _run([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(path),
        ])
    finally:
        listing.unlink(missing_ok=True)


def _stale(path: Path, rebuild: bool) -> bool:
    return rebuild or not (path.exists() and path.stat().st_size > 0)


def build(dest: Path = FIXTURE_DIR, rebuild: bool = False) -> dict[str, Path]:
    """Build every fixture into `dest`, skipping any that are already there."""
    dest.mkdir(parents=True, exist_ok=True)
    made = {}
    for name, spec in GENERATED.items():
        path = dest / name
        if _stale(path, rebuild):
            _generate(path, spec)
        made[name] = path

    flagged = dest / FLAGGED
    if _stale(flagged, rebuild):
        _flag(flagged, made[FLAGGED_SOURCE])
    made[FLAGGED] = flagged

    mixed = dest / MIXED
    if _stale(mixed, rebuild):
        _splice(mixed, [made[name] for name in MIXED_PARTS])
    made[MIXED] = mixed
    return made


def main(argv: list[str]) -> int:
    positional = [a for a in argv[1:] if not a.startswith("-")]
    dest = Path(positional[0]).resolve() if positional else FIXTURE_DIR
    try:
        made = build(dest, rebuild="--rebuild" in argv)
    except FixtureError as exc:
        print(f"make_fixtures: {exc}", file=sys.stderr)
        return 1
    for name, path in sorted(made.items()):
        print(f"{name:18} {path.stat().st_size / 1e6:7.1f} MB  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
