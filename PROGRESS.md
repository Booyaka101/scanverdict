# PROGRESS

Version 1.1.0, released 2026-09-21. The brief is delivered. Nothing is stubbed and nothing
is mocked in the shipped package.

https://github.com/Booyaka101/scanverdict/releases/tag/v1.1.0
https://pypi.org/project/scanverdict/1.1.0/

## State

Complete and verified locally on Windows 11 with Python 3.11.9, numpy 2.4.4, ffmpeg 8.1
and ruff 0.16.8.

```
D:\Repos\ideas\scanverdict
  scanverdict/       __main__.py probe.py sampler.py metrics.py classify.py
                     recommend.py verify.py ffmpeg.py __init__.py
  tests/             make_fixtures.py conftest.py test_classify.py test_verify.py
                     test_metrics.py test_probe.py test_cli.py test_recommend.py
                     test_sampler.py
  packaging/         exe_entry.py
  .github/workflows/ ci.yml
  pyproject.toml MANIFEST.in scanverdict.spec LICENSE README.md CHANGELOG.md .gitignore
```

## Verified working

Everything below was run, not assumed.

- `ruff check . && python -m pytest tests -q` builds nine real ffmpeg fixtures and passes,
  120 tests in 2m39s with fixtures cached. Full output is in the build summary.
- `ruff check .` clean with `E,F,W,I,B,UP,C4,RUF` selected.
- Every fixture classifies to the exact expected string: progressive, telecine_3_2,
  interlaced_tff, pulldown_2_2, field_blended, mixed, undetermined, plus the container
  disagreement path. The 1.1.0 changes moved none of them.
- Verification measured on real files: telecine `combed blocks 238 -> 4 (-98.3%), 29.97 ->
  23.976 fps, frame count 360 -> 288 as expected for 3:2`; interlace `combed blocks 560 -> 6 (-98.9%), 30 -> 60
  fps, frame count 360 -> 720`.
- `--full` on the spliced fixture returns `0.0 - 20.0 telecine_3_2` and
  `20.0 - 40.0 interlaced_tff` from ten back-to-back windows, plus the two `-ss/-to`
  commands that extract and fix each half. The splice really is at 20.0s.
- Blend cycle detection checked against eight files built in a scratch directory from four
  unrelated ffmpeg sources (`testsrc2`, `mandelbrot`, `smptebars` with `scroll`, `life`),
  four run through `framerate=25` from 24 and four clean at 25. The two blended sources
  with enough motion to classify at all reported cycles of 24 and 25 frames with residual
  autocorrelation 0.66 to 0.90 and ripple 0.25 to 0.32. Nothing fired on any of the four
  clean files, and nothing fired on the eight non-blended fixtures.
- Windows are classified as they are decoded, so memory is flat in the window count.
  Measured with `tracemalloc` on the spliced fixture at 40 windows: 68 MB peak streaming
  against 341 MB holding them all. Without this `--full` on a feature would have wanted
  tens of gigabytes, which is the one real bug this review turned up.
- `scanverdict tests/fixtures/telecine.mkv --json | jq -r .verdict` prints `telecine_3_2`.
- A filename outside the Windows ANSI code page runs to completion. Reverting the stdout
  reconfigure makes the new subprocess test fail with the exact `UnicodeEncodeError` it
  was written to catch, so the test is doing work.
- Wheel and sdist build. The sdist carries `tests/conftest.py` and `tests/make_fixtures.py`,
  and PKG-INFO carries the README body.
- Clean venv at `D:\tmp\cleanenv`: `pip install dist/scanverdict-1.1.0-py3-none-any.whl`
  then `scanverdict --version` prints `scanverdict 1.1.0`, a real analysis runs, and a
  missing file prints `scanverdict: nope.mkv: no such file` with exit 1.
- `pipx install ./dist/scanverdict-1.1.0-py3-none-any.whl` with `PIPX_HOME` and
  `PIPX_BIN_DIR` pointed at a scratch directory installs and runs. pipx was not already on
  this box, so it went into the throwaway venv rather than the user's Python.
- The sdist unpacked into that venv runs its own test suite from scratch.
- The emitted command run by hand, `ffmpeg -i tests/fixtures/telecine.mkv -vf
  fieldmatch=order=tff,decimate -c:v libx264 out.mkv`, gives 480 frames at 23.976 fps, and
  scanverdict reads that output back as `progressive (high)` with combing at 0.2% of 1920
  blocks, which is the 4 blocks per frame the verify pass predicted.
- `pyinstaller scanverdict.spec` produces a single-file `dist/scanverdict.exe` that runs
  `--version`, a real analysis and the missing-file path.
- CI is green on the released commit `463db04` across Python 3.11, 3.12 and 3.13, plus the
  packaging job that proves the sdist still carries `conftest.py` and that the README
  reaches PKG-INFO. Checked through the commit check-runs API, not the run-level status.
- `pip install scanverdict` from the live index into a fresh venv gives 1.1.0, classifies
  all three sample fixtures and exits 1 on a missing file.
- `scanverdict.exe` downloaded back from the release page is byte for byte the local build
  (sha256 `2ffb96ce...`) and runs.

## Not done

- The exe is Windows-only, built on this machine. A macOS or Linux binary needs a runner on
  that platform, and the release workflow that would build one does not exist yet.
- 1.0.0 was never published. It is in the history as commit `4dea8a9` and the changelog
  keeps its entry, but the first public artefacts are 1.1.0.
- Nobody has been told about it. No forum post, no Reddit thread, no VideoHelp reply. The
  owner owns that wording.
- Blend cycle detection has never seen a real capture. Every file it was tuned and checked
  against came out of ffmpeg's own synthetic sources. The thresholds separated blends from
  clean material by a wide margin there, but a noisy VHS transfer is a different animal and
  the README says so.

## Next steps, in order

1. Post the release link where the audience is. VideoHelp and doom9 download exes from a
   release page, not wheels from PyPI, so the exe link is the one that travels. r/ffmpeg
   and r/DataHoarder are the other two places people argue about whether a rip is
   interlaced.
2. Get real captures through the blend cycle detector. It has only ever seen synthetic
   material and that is the weakest claim in the README.
3. Tighten segment boundaries below one window.
4. A release workflow that builds the exe on a runner, so the binary is reproducible by
   someone other than this machine.

## Features considered and not built

Judged against the brief's non-goals and against what would need real material to tune.

- **Frame-accurate segment boundaries.** `--full` puts them on a window edge, so they are
  good to `frames_per_window / fps`, four seconds at the defaults. Tightening that means
  reclassifying near each boundary at a finer grain, which is worth doing and is the
  obvious next piece of work.
- **Per-scene cadence output.** A cadence that breaks scene by scene needs scene detection
  and per-frame field match decisions, not windows. Wobbly already owns this job and the
  README says so.
- **`--exec` to run the recommended chain on the whole file.** Deliberately excluded by the
  brief. The command is printed for the user to inspect and run.
- **A seventh verdict for frame blending.** Rejected on purpose. A blended conversion is
  progressive, and that is what a consumer switching on `verdict` should keep seeing. The
  cycle goes in `frame_blend` in JSON and `blend_period` in CSV instead.
- **`field_blended` verification.** There is no ffmpeg chain to run, so there is nothing to
  measure. Proving an `srestore` result would mean requiring VapourSynth at runtime, which
  breaks the one-dependency rule.
- **Bundling ffmpeg in the exe.** Would take the binary from 21 MB to well over 100 MB and
  puts scanverdict in the position of shipping someone else's GPL build. The exe checks for
  ffmpeg and prints install instructions instead.
