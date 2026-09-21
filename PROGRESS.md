# PROGRESS

Version 1.0.0, built 2026-09-21. The brief is delivered. Nothing is stubbed and nothing is
mocked in the shipped package.

## State

Complete and verified locally on Windows 11 with Python 3.11.9, numpy 2.4.4, ffmpeg 8.1
and ruff 0.16.8.

```
D:\Repos\ideas\scanverdict
  scanverdict/       __main__.py probe.py sampler.py metrics.py classify.py
                     recommend.py verify.py ffmpeg.py __init__.py
  tests/             make_fixtures.py conftest.py test_classify.py test_verify.py
                     test_metrics.py test_probe.py test_cli.py test_recommend.py
  packaging/         exe_entry.py
  .github/workflows/ ci.yml
  pyproject.toml MANIFEST.in scanverdict.spec LICENSE README.md CHANGELOG.md .gitignore
```

## Verified working

Everything below was run, not assumed.

- `python -m pytest tests -q` builds nine real ffmpeg fixtures and passes. Full output is in
  the build summary; the suite takes about two and a half minutes with fixtures cached and
  about five on a cold build.
- `ruff check .` clean with `E,F,W,I,B,UP,C4,RUF` selected.
- Every fixture classifies to the exact expected string: progressive, telecine_3_2,
  interlaced_tff, pulldown_2_2, field_blended, mixed, undetermined, plus the container
  disagreement path.
- Verification measured on real files: telecine `combed blocks 238 -> 4 (-98.3%), 29.97 ->
  23.976 fps, frame count 360 -> 288 as expected for 3:2`; interlace `combed blocks 560 -> 6 (-98.9%), 30 -> 60
  fps, frame count 360 -> 720`.
- `scanverdict tests/fixtures/telecine.mkv --json | jq -r .verdict` prints `telecine_3_2`.
- Wheel and sdist build. The sdist carries `tests/conftest.py` and `tests/make_fixtures.py`,
  and PKG-INFO carries the README body.
- Clean venv at `D:\tmp\cleanenv`: `pip install dist/scanverdict-1.0.0-py3-none-any.whl`
  then `scanverdict --version` prints `scanverdict 1.0.0`, a real analysis runs, and a
  missing file prints `scanverdict: nope.mkv: no such file` with exit 1.
- `pipx install ./dist/scanverdict-1.0.0-py3-none-any.whl` with `PIPX_HOME` and
  `PIPX_BIN_DIR` pointed at `D:	mp` installs and runs. pipx was not already on this box,
  so it went into the throwaway venv rather than the user's Python.
- The sdist unpacked into that venv runs its own test suite from scratch: 102 passed.
- The emitted command run by hand, `ffmpeg -i tests/fixtures/telecine.mkv -vf
  fieldmatch=order=tff,decimate -c:v libx264 out.mkv`, gives 480 frames at 23.976 fps, and
  scanverdict reads that output back as `progressive (high)` with combing at 0.2% of 1920
  blocks, which is the 4 blocks per frame the verify pass predicted.
- `pyinstaller scanverdict.spec` produces a 21.4 MB `dist/scanverdict.exe` that runs
  `--version`, a real analysis and the missing-file path.

## Not done

- Nothing has been published. No PyPI upload, no GitHub release, no account touched. The
  owner ships it.
- The git repo has no remote. `git remote add origin ...` before the first push.
- CI has never run. The workflow is written against ubuntu-latest with apt ffmpeg and is
  unexercised until the first push.
- The exe is Windows-only, built on this machine. A macOS or Linux binary needs a runner on
  that platform.

## Next steps, in order

1. `git remote add origin https://github.com/Booyaka101/scanverdict && git push -u origin main`.
2. Watch the first CI run. The fixture build is the slow part and the likeliest failure is a
   missing ffmpeg filter in the runner's build, which would show as a `FixtureError`.
3. Tag `v1.0.0`, attach `dist/scanverdict.exe` to the release. That link is the thing to
   paste into a VideoHelp or doom9 thread.
4. `twine upload dist/*` for the wheel and sdist.

## Features considered and not built

Judged against the brief's non-goals and against what would need real material to tune.

- **Per-scene cadence output.** Sampled windows cannot see a cadence break that falls
  between them, and doing it properly means decoding every frame. Wobbly already owns this
  job and the README says so.
- **`--full` scan mode.** Decode every frame instead of sampling. Straightforward to add on
  top of `plan()`, and it would turn `mixed` from "windows 0-3 and 4-7 differ" into exact
  frame boundaries. The cost is linear in file length, which is why it is not the default.
- **Cadence phase per scene.** The phase is already reported per window and the note fires
  when it moves. Emitting a cut list needs scene detection, which is a different tool.
- **`--exec` to run the recommended chain on the whole file.** Deliberately excluded by the
  brief. The command is printed for the user to inspect and run.
- **Detecting 24 to 25 frame blending.** A real gap, not a scope decision. Every frame in
  such a file is a mixture, so the least-squares solve has no clean reference. Detecting it
  needs a different model, probably a periodicity test on the residual rather than a
  per-frame fit. The fixture and the test that pins the current wrong-ish answer are already
  in the suite, so whoever picks this up has a target.
- **`field_blended` verification.** There is no ffmpeg chain to run, so there is nothing to
  measure. Proving an `srestore` result would mean requiring VapourSynth at runtime, which
  breaks the one-dependency rule.
- **Bundling ffmpeg in the exe.** Would take the binary from 21 MB to well over 100 MB and
  puts scanverdict in the position of shipping someone else's GPL build. The exe checks for
  ffmpeg and prints install instructions instead.
