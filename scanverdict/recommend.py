"""Verdict -> filter chain, ffmpeg command line and VapourSynth snippet.

Every option spelling here comes from the ffmpeg filter reference (bwdif 11.19,
decimate 11.56, fieldmatch 11.93, telecine 11.252, tinterlace 11.258) and from
the VIVTC and VapourSynth-Bwdif readmes, not from memory.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from fractions import Fraction

from .classify import (
    FIELD_BLENDED,
    INTERLACED_BFF,
    INTERLACED_TFF,
    MIXED,
    PROGRESSIVE,
    PULLDOWN_2_2,
    TELECINE_3_2,
    UNDETERMINED,
    FileVerdict,
    FrameBlend,
    Segment,
)
from .probe import Probe

OUTPUT_NAME = "out.mkv"
ENCODER = ["-c:v", "libx264"]

# out_frames / in_frames for each chain. bwdif in send_field mode emits one
# frame per field, so a true-interlace fix doubles the frame count.
_FRAME_RATIO = {
    PROGRESSIVE: Fraction(1),
    INTERLACED_TFF: Fraction(2),
    INTERLACED_BFF: Fraction(2),
    TELECINE_3_2: Fraction(4, 5),
    PULLDOWN_2_2: Fraction(1, 2),
}


@dataclass
class Recommendation:
    label: str
    filters: list[str]
    ffmpeg: str
    vapoursynth: str
    summary: str
    frame_ratio: Fraction | None
    output_fps: Fraction | None
    caveats: list[str]

    @property
    def is_noop(self) -> bool:
        return not self.filters

    def as_dict(self) -> dict:
        return {
            "filters": ",".join(self.filters),
            "ffmpeg": self.ffmpeg,
            "vapoursynth": self.vapoursynth,
            "summary": self.summary,
            "frame_ratio": None if self.frame_ratio is None else str(self.frame_ratio),
            "output_fps": None if self.output_fps is None else round(float(self.output_fps), 6),
            "caveats": self.caveats,
        }


def fps_text(rate: Fraction | None) -> str:
    if rate is None:
        return "unknown"
    return f"{round(float(rate), 3):g}"


def chain_for(label: str, field_order: str | None) -> list[str]:
    """The -vf pieces for a label, or [] when the right answer is to do nothing."""
    order = field_order or "tff"
    if label == INTERLACED_TFF:
        return ["bwdif=mode=send_field:parity=tff"]
    if label == INTERLACED_BFF:
        return ["bwdif=mode=send_field:parity=bff"]
    if label == TELECINE_3_2:
        return [f"fieldmatch=order={order}", "decimate"]
    if label == PULLDOWN_2_2:
        # decimate defaults to cycle=5; a 2:2 cadence needs one frame dropped
        # in every two, which is what makes this a half-rate chain.
        return [f"fieldmatch=order={order}", "decimate=cycle=2"]
    return []


def _quote(path: str) -> str:
    if os.name == "nt":
        return f'"{path}"' if any(c in path for c in ' \t"&()^%!') else path
    return shlex.quote(path)


def _ffmpeg_command(path: str, filters: list[str], span=None, out=OUTPUT_NAME) -> str:
    argv = ["ffmpeg"]
    if span is not None:
        argv += ["-ss", f"{span[0]:.3f}", "-to", f"{span[1]:.3f}"]
    argv += ["-i", _quote(path)]
    if filters:
        argv += ["-vf", ",".join(filters)]
    argv += ENCODER + [out]
    return " ".join(argv)


def chain_text(label: str, field_order: str | None) -> str:
    """The chain for a label as one printable string, including the empty cases."""
    if label in (UNDETERMINED, MIXED):
        return "-- (nothing conclusive)"
    pieces = chain_for(label, field_order)
    return ",".join(pieces) if pieces else "(no filter)"


def segment_command(path: str, segment: Segment, field_order: str | None, index: int) -> str:
    """The ffmpeg line that extracts one segment and fixes it, numbered out01.mkv on."""
    stem, ext = os.path.splitext(OUTPUT_NAME)
    return _ffmpeg_command(
        path,
        chain_for(segment.label, field_order),
        span=(segment.start, segment.end),
        out=f"{stem}{index:02d}{ext}",
    )


_VS_HEADER = """import vapoursynth as vs
{extra}core = vs.core

clip = core.lsmas.LWLibavSource({source})"""

_VS_FOOTER = "clip.set_output()"


def _vs_source(path: str, extra_import: str = "") -> str:
    full = os.path.abspath(path)
    # A raw string cannot end in a backslash and cannot hold its own quote.
    quoted = repr(full) if '"' in full or full.endswith("\\") else f'r"{full}"'
    extra = f"{extra_import}\n" if extra_import else ""
    return _VS_HEADER.format(source=quoted, extra=extra)


def _srestore(path: str, order_bit: int, frate: float) -> str:
    """The one snippet that undoes blending, wherever the blend came from."""
    return (
        "# srestore lives in havsfunc 33. Release 34 dropped it, so pin:\n"
        "#   pip install havsfunc==33\n"
        f"{_vs_source(path, 'import havsfunc as haf')}\n"
        f"clip = haf.srestore(clip, frate={frate:.3f})"
        "  # frate = the rate of the film underneath the blends\n"
        f"{_VS_FOOTER}\n"
        "\n"
        "# Maintained alternative, when field matching leaves 2 blends every 5 frames:\n"
        "#   from vsdeinterlace import deblend\n"
        f"#   clip = deblend(core.vivtc.VFM(clip, order={order_bit}))\n"
    )


def _vapoursynth(
    label: str,
    path: str,
    field_order: str | None,
    out_fps: Fraction | None,
    blend: FrameBlend | None = None,
) -> str:
    order_bit = 0 if field_order == "bff" else 1
    order_note = "0 = bottom field first" if order_bit == 0 else "1 = top field first"
    head = _vs_source(path)

    if label == PROGRESSIVE:
        if blend is not None:
            return _srestore(path, order_bit, blend.source_fps)
        return (
            "# Nothing to run. The frames are already whole; any deinterlacer or\n"
            "# field matcher here would cost you detail and buy you nothing."
        )
    if label in (INTERLACED_TFF, INTERLACED_BFF):
        field = 3 if label == INTERLACED_TFF else 2
        side = "top" if field == 3 else "bottom"
        return (
            f"{head}\n"
            f"clip = core.bwdif.Bwdif(clip, field={field})"
            f"  # {field} = double rate, starts with the {side} field\n"
            f"{_VS_FOOTER}\n"
        )
    if label == TELECINE_3_2:
        return (
            f"{head}\n"
            f"clip = core.vivtc.VFM(clip, order={order_bit})  # {order_note}\n"
            f"clip = core.vivtc.VDecimate(clip)"
            f"  # drops 1 frame in 5 -> {fps_text(out_fps)} fps\n"
            f"{_VS_FOOTER}\n"
        )
    if label == PULLDOWN_2_2:
        return (
            f"{head}\n"
            f"clip = core.vivtc.VFM(clip, order={order_bit})  # {order_note}\n"
            f"clip = core.vivtc.VDecimate(clip, cycle=2)"
            f"  # drops 1 frame in 2 -> {fps_text(out_fps)} fps\n"
            f"{_VS_FOOTER}\n"
        )
    if label == FIELD_BLENDED:
        return _srestore(path, order_bit, 23.976)
    return "# No snippet: scanverdict will not guess at a chain it cannot justify."


def recommend(verdict: FileVerdict, probe_info: Probe) -> Recommendation:
    label = verdict.label
    filters = chain_for(label, verdict.field_order)
    ratio = _FRAME_RATIO.get(label)
    in_fps = Fraction(probe_info.r_frame_rate)
    out_fps = in_fps * ratio if ratio is not None else None
    caveats: list[str] = []

    if label == PROGRESSIVE and verdict.frame_blend is not None:
        blend = verdict.frame_blend
        summary = (
            "whole frames, but each one is a mixture of the two around it on a "
            f"{blend.period}-frame cycle: the rate was changed by blending. No ffmpeg "
            "deinterlacer touches this. Use srestore, below."
        )
        caveats.append(
            "A blend cannot be fully undone. srestore rebuilds the "
            f"{blend.source_fps:.1f} fps source where clean frames survive between the "
            "blends and interpolates where they do not."
        )
    elif label == PROGRESSIVE:
        summary = "no filter needed -- the frames are already whole"
    elif label in (INTERLACED_TFF, INTERLACED_BFF):
        side = "top" if label == INTERLACED_TFF else "bottom"
        summary = (
            f"true interlace, {side} field first: bwdif in send_field mode keeps both "
            f"fields as frames, so {fps_text(in_fps)} fps becomes {fps_text(out_fps)} fps"
        )
        caveats.append(
            "send_field doubles the frame rate to keep the motion you paid for. "
            "Use mode=send_frame instead if you need the original rate."
        )
    elif label == TELECINE_3_2:
        summary = (
            f"3:2 telecine: field matching rebuilds the film frames and decimate drops "
            f"the duplicate, {fps_text(in_fps)} -> {fps_text(out_fps)} fps"
        )
    elif label == PULLDOWN_2_2:
        summary = (
            f"2:2 cadence: every second frame is a repeat, so decimate at cycle 2 halves "
            f"the rate, {fps_text(in_fps)} -> {fps_text(out_fps)} fps"
        )
    elif label == FIELD_BLENDED:
        summary = (
            "fields have been blended together. ffmpeg has no good answer here: every "
            "one of its deinterlacers assumes the fields are still separate, and none of "
            "them can unmix an average of two moments. Use srestore in VapourSynth."
        )
        caveats.append(
            "A blend cannot be fully undone. srestore recovers the source cadence where "
            "clean frames survive between the blends and interpolates where they do not."
        )
    elif label == MIXED:
        summary = (
            "the file is not one thing: its parts need different chains. Cut it on the "
            "boundaries listed here and treat each part separately, or do the whole "
            "thing in VapourSynth and splice."
        )
    else:
        summary = (
            "no recommendation. scanverdict could not tell what this is, and a wrong "
            "filter here is a lossy re-encode of content that may not need one."
        )

    if verdict.field_order_assumed and label in (TELECINE_3_2, PULLDOWN_2_2):
        caveats.append(
            f"order={verdict.field_order} is an assumption, not a measurement. "
            "If the output still combs, run it again with order="
            f"{'bff' if verdict.field_order == 'tff' else 'tff'}."
        )
    if probe_info.is_vfr and filters:
        caveats.append(
            "decimate and fieldmatch both want constant frame rate input. This file is "
            "variable, so prefix the chain with dejudder,fps="
            f"{probe_info.r_frame_rate}."
        )

    return Recommendation(
        label=label,
        filters=filters,
        ffmpeg=_ffmpeg_command(probe_info.path, filters),
        vapoursynth=_vapoursynth(
            label, probe_info.path, verdict.field_order, out_fps, verdict.frame_blend
        ),
        summary=summary,
        frame_ratio=ratio,
        output_fps=out_fps,
        caveats=caveats,
    )


def segment_chains(verdict: FileVerdict) -> list[tuple[str, str]]:
    """For a mixed file: the distinct labels present and the chain each needs."""
    seen: dict[str, str] = {}
    for window in verdict.windows:
        seen.setdefault(
            window.label,
            chain_text(window.label, window.field_order or verdict.field_order),
        )
    return sorted(seen.items())
