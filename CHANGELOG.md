# Changelog

## 1.0.0 - 2026-09-21

First release.

- `scanverdict FILE [FILE ...]` classifies each file as `progressive`, `interlaced_tff`,
  `interlaced_bff`, `telecine_3_2`, `pulldown_2_2`, `field_blended`, `mixed` or
  `undetermined`, from decoded pixels rather than the container flag.
- The container's claim is always printed next to the pixel verdict, with `agrees` or
  `disagrees` spelled out.
- Recommends an ffmpeg filter chain and a VapourSynth snippet per verdict, then proves it:
  the chain is run over three sampled spans and the combing measured before and after, with
  the output frame count checked against the cadence. Confidence drops to `low` when the
  reduction is under 60% or the count is wrong, and the reason is printed.
- 3:2 cadence detection reports the phase and whether it holds across every window.
- `field_blended` says plainly that ffmpeg has no chain for it and emits an `srestore`
  snippet, with `vsdeinterlace.deblend` named as the maintained alternative.
- `mixed` prints a per-window table and the chain each segment needs instead of one answer.
- Letterbox bars are excluded before measuring. Variable frame rate input is flagged.
  Files too short for the requested window count get fewer, and say so.
- Output as text, `--json` (object for one file, array for several), `--csv`, or `--quiet`.
- Missing ffmpeg, missing files, unreadable files and failed verification runs all produce a
  message and an exit code, never a traceback.
- Ships as a wheel on PyPI and as a single-file `scanverdict.exe` that does not bundle
  ffmpeg.
