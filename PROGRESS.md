# PROGRESS

Version 1.1.1, released 2026-09-21. The brief is delivered. Nothing is stubbed and nothing
is mocked in the shipped package.

https://github.com/Booyaka101/scanverdict/releases/tag/v1.1.1
https://pypi.org/project/scanverdict/1.1.1/

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
  .github/workflows/ ci.yml release.yml
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
- Blend cycle detection then checked against real material, which is what 1.1.1 is for. See
  the section below. The short version: it found two false positives that were reaching
  users and they are fixed.
- `--full` throughput measured, which had never been done. 640x480 runs at 2.1x realtime
  (40s fixture in 18.9s, 20s fixture in 9.2s). 1080p30 runs at 0.76x: a 542s file took 716s
  across 136 back-to-back windows and completed, which also exercises the streaming
  classifier at a window count nothing else has hit. The README now carries both figures.
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
- `scanverdict.exe` for 1.1.0 downloaded back from the release page was byte for byte the
  local build (sha256 `2ffb96ce...`) and ran. 1.1.1 is not built locally at all.
- The 1.1.1 exe is built by `release.yml` on the tag. `gh attestation verify
  scanverdict.exe --repo Booyaka101/scanverdict` on the downloaded asset exits 0, and the
  statement names workflow `.github/workflows/release.yml`, ref `refs/tags/v1.1.1`, commit
  `8ea72304e00d30eb6331cb145dfd2e227c4730fa`, subject sha256 `c361156b...`, which is the
  sha256 of the file on disk and of the published `.sha256` sidecar. The downloaded exe
  runs `--version`, classifies `telecine.mkv` with the verify line intact, and exits 1 on a
  missing file.
- CI green on `8ea7230` across 3.11, 3.12 and 3.13 plus packaging, checked through the
  commit check-runs API before the tag was cut.
- `pip install --no-cache-dir scanverdict==1.1.1` from the live index into a fresh venv
  gives `scanverdict 1.1.1`.

## Not done

- The exe is Windows-only. It is built by `release.yml` on a `windows-latest` runner now,
  not on this machine, and carries an `actions/attest-build-provenance` attestation that
  `gh attestation verify` checks against the workflow run and the commit. It is still not
  code signed, so Windows SmartScreen still warns on first run, and the README says so.
  A macOS or Linux binary needs a runner on that platform and is not built.
- 1.0.0 was never published. It is in the history as commit `4dea8a9` and the changelog
  keeps its entry, but the first public artefacts are 1.1.0.
- Nobody has been told about it. No forum post, no Reddit thread, no VideoHelp reply. The
  owner owns that wording.
- Blend cycle detection still has not seen a VHS capture or a film transfer, which is the
  material it exists for. It has now seen real h264 with real motion and real cuts, and it
  does not work on that. The README says so plainly and the file level agreement rule stops
  it reaching a user. See below.
- The `field_blended` fix has no unit test of its own. I could not build a synthetic window
  that lands in the narrow comb band the real failure sat in: every signal I could generate
  either has no vertical detail and combs at zero, or saturates at 1.0. The fixture suite
  proves the genuine blend still classifies, and the real file that triggered it now reads
  `undetermined`. Recording that rather than shipping a fixture tuned to a four point band
  that any ffmpeg version bump would break.

## What real material showed

Run against 156 real video files on this box, plus a purpose-built set of conversions. The
material is AI generated anime clips and one essay video with static parallax stills. It is
real h264 with real motion, real cuts and real encoder noise, so it is valid evidence about
false positives. It is not a VHS capture and must not be described as one.

Two bugs came out of it, both now fixed and both user visible.

- A real progressive 24 fps clip was being called `field_blended` and told to run
  `srestore` in VapourSynth. Its combing floor sits at 11.4% of blocks, in the dead band
  between `COMB_CLEAN` and `COMB_DIRTY`, so it had no clean frames without having dirty
  ones either. The blend rate half of the gate turned out to discriminate nothing: the real
  blended fixture sits at 0.50 and this false positive at 0.90, because slow motion solves
  as a blend of its neighbours whether or not anything was mixed. The gate now also
  requires most frames to be dirty. The file reads `undetermined` now, which is honest.
- Two files in a 66 file partial survey reported file level blend cycles of period 8,
  implying source rates of 52.5 and 21 fps. The autocorrelation was normalised by the whole
  series energy while summing only the overlapping terms, which makes the score decay with
  the lag on its own, and the test on top was a bare height threshold, so the answer was
  always the shortest lag searched. On synthetic progressive only the ripple gate hid this,
  and on real footage ripple runs 0.63 to 1.25 so that gate does nothing at all. Rewritten
  to normalise each lag by the energy it overlaps, to require topographic prominence rather
  than height, and to reject a peak at a multiple of a shorter strong cycle. Over cached
  residuals: false positives 32 to 16 out of 167, recall 3 to 4 out of 19. Both files now
  report no cycle.

The decisive negative result. A 175 second master built only from clips scanverdict already
calls progressive, then converted six ways (untouched at 24, retimed to 25, blended both
directions, duplicated and dropped). Windows 2 and 6 fire on all six versions regardless of
what was done, with the period wandering: 15/22/26 on the untouched one, 23/27 blended,
24/27 duplicated, 21/25 dropped. The detector is reading the content of those windows, not
the cadence. At file level all six correctly report no cycle, so the `_frame_blend`
majority rule is what is carrying this feature on real footage.

A fix that did not work, recorded so nobody tries it twice. Dividing the residual by a
median filtered envelope to remove the motion, windows 9, 15, 25 and 41, at three
prominence thresholds. Strictly worse every way: recall 3/43 against 4/43, false positives
41 to 57 out of 215 against 26.

## The survey numbers

156 files, no errors, `--json --no-verify`. The same 66 files were also run against 1.1.0
before the fixes, so this is a real head to head rather than two different samples.

| verdict | 1.1.0 | 1.1.1 |
| --- | --- | --- |
| undetermined | 37 | 40 |
| progressive | 17 | 17 |
| mixed | 5 | 5 |
| field_blended | 7 | 4 |

Exactly three files changed verdict, all `field_blended` to `undetermined`, and nothing
else moved at all. The fix is surgical rather than a threshold nudge that drags everything
with it. Both blend cycle false positives are gone (`rin_hq_final.mp4` was claiming a
52.5 fps source, `rin_shift2.mp4` a 21 fps one) and no new ones appeared anywhere in the
156.

Across the full 156: undetermined 71%, progressive 13%, mixed 12%, field_blended 4%. That
undetermined figure needs unpacking, because the headline number is misleading.

- 38 of the files are one essay video's parallax stills, which are 10 second slideshows of
  static images. They are 100% undetermined and that is the right answer, there is no
  cadence in them to find.
- Of the remaining 118, 108 are so short that the sampler can only get 1 to 3 windows out
  of them instead of 12. Undetermined runs 76% on the 1-window files and 53% on the 2 to 3
  window ones.
- Only 5 files in the whole set are long enough to give the full 12 windows. Three come
  back undetermined and two come back mixed, and all five are cuts of the same essay video.

So the honest reading is that the rate is dominated by material that is too short or too
static to classify, not by the classifier being unsure about real cadence. I had assumed
earlier that undetermined was worse on short clips and better on long ones; measured over
the whole set it is actually worse on the long ones, purely because the long ones here are
the slideshows. Both readings were wrong in isolation and the window count is the variable
that matters, not the duration.

One thing left unresolved. `ep01_nomusic.mp4` reports `mixed` with four `field_blended`
windows. Those windows are not the dead band case that was just fixed: they sit at
`dirty=1.0`, `clean=0.0`, comb 17 to 29% of blocks, field matching moves the comb figure by
under 0.3 percentage points, and 30 to 44% of frames solve as a blend. That is the exact
signature the rule describes. The material is parallax-scrolled stills in a video editor,
which really can produce inter-frame blending, so this may well be correct. There is no
ground truth for it either way and it is recorded rather than declared.

## Next steps, in order

1. Post the release link where the audience is. VideoHelp and doom9 download exes from a
   release page, not wheels from PyPI, so the exe link is the one that travels. r/ffmpeg
   and r/DataHoarder are the other two places people argue about whether a rip is
   interlaced. A substantive reply in a live thread beats a fresh self-promo post.
2. Get a real VHS capture or a PAL film transfer through the blend cycle detector. A long
   steady take is the case the feature was built for and the one case still untested.
3. Tighten segment boundaries below one window.
4. Make the short-file case say something useful. A file that only yields one or two
   windows is undetermined most of the time, and the note explaining why is buried under
   the window table. Either the sampler should overlap windows on short input to get more
   samples out of the same frames, or the verdict line itself should say the file is too
   short rather than leaving `undetermined` to carry that meaning.

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
