# Changelog

## 1.1.0 - 2026-09-21

### Added

- `--full` scans the whole file in back-to-back windows instead of sampling twelve spans
  of it. Consecutive windows that agree become a timed segment, so a spliced file now
  reports `0.0 - 20.0 telecine_3_2` and `20.0 - 40.0 interlaced_tff` plus a numbered
  ffmpeg cut list, rather than a per-window table. Boundaries are good to one window.
- Blended frame rate conversions are detected and named. A 24 to 25 conversion done by
  mixing whole frames leaves a repeating cycle in the blend residual; scanverdict finds
  the cycle length, reports the source rate it implies and points at `srestore` with the
  right `frate`. This was a documented gap in 1.0.0.
- JSON carries `frame_blend` and `segments`. CSV gains a `blend_period` column.

### Fixed

- A filename outside the Windows ANSI code page no longer ends the run with a
  `UnicodeEncodeError` traceback. Output is UTF-8 on every platform.
- Piping into a command that exits early, `| head` for instance, exits 141 quietly
  instead of printing a `BrokenPipeError`.
- A span that decodes no frames names the file and the timestamp it failed at.
- `--frames-per-window` large enough to exhaust memory is refused with the figure it
  would have needed, rather than raising `MemoryError` partway through.
- Windows are classified as they are decoded rather than all decoded first, so memory
  stays flat in the number of windows. Measured on the spliced fixture at 40 windows:
  68 MB peak instead of 341 MB. This is what makes `--full` usable on a feature.
- A scan of more than 24 windows prints `window n/total` to stderr while it works, so a
  long `--full` run does not look like a hang. Nothing is printed when stderr is not a
  terminal, so pipes and redirects are unaffected.

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
