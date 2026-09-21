# scanverdict

Tells you what scan type and cadence a video file really has, gives you the filter chain
that fixes it, then runs that chain and measures whether it worked.

Container flags lie. A DVD rip whose header says `progressive` is usually 3:2 telecined
film, a capture flagged `tt` is often already deinterlaced, and `idet` on its own gives you
frame counts without telling you what to do about them. scanverdict decodes real pixels,
says which of six things the file is, and proves the recommendation by measuring combing
before and after.

```
verdict: telecine_3_2 (confidence high)
container says: progressive (disagrees)
cadence: 3:2, phase 2, consistent across 4/4 windows
verified: combed blocks 238 -> 4 (-98.3%), 29.97 -> 23.976 fps, frame count 360 -> 288 as expected for 3:2
```

It never touches your file. Nothing is written except what you choose to run yourself.

## Install

```
pipx install scanverdict
```

or a plain `pip install scanverdict` into a virtualenv. Python 3.11 or newer, numpy is the
only dependency.

If you would rather not have Python at all, grab `scanverdict.exe` from the
[releases page](https://github.com/Booyaka101/scanverdict/releases) and run it from a
command prompt. One file, no installer.

**You need ffmpeg and ffprobe on PATH.** All decoding happens through them. Windows builds
are at [gyan.dev](https://www.gyan.dev/ffmpeg/builds/); on macOS `brew install ffmpeg`, on
Debian and Ubuntu `apt install ffmpeg`. If they are missing, scanverdict says so and exits
instead of throwing a traceback.

## First run

Point it at a file:

```
$ scanverdict dvd_rip.mkv
```

Real output, from the telecine fixture the test suite builds:

```
tests/fixtures/telecine.mkv
  640x480 ffv1, 29.97 fps, 20.0 s, yuv420p

verdict: telecine_3_2 (confidence high)
container says: progressive (disagrees)
cadence: 3:2, phase 2, consistent across 4/4 windows
field order: tff
sampled: 4 windows of 120 frames, 4/4 windows agree

evidence
  combing          12.4% of the 1920 blocks in a frame, 40% of frames above threshold
  after matching   0.2% of blocks, 100% of frames clean
  field matches    59% keep the frame as shot (example: cppcccppcccppcccppcccppcccppcc)
  blend solve      fires on 0% of frames
  motion           9.1 gray levels between frames (p90)
  idet             tff

3:2 telecine: field matching rebuilds the film frames and decimate drops the duplicate, 29.97 -> 23.976 fps

ffmpeg -i tests/fixtures/telecine.mkv -vf fieldmatch=order=tff,decimate -c:v libx264 out.mkv

vapoursynth
  import vapoursynth as vs
  core = vs.core

  clip = core.lsmas.LWLibavSource(r"D:\Repos\ideas\scanverdict\tests\fixtures\telecine.mkv")
  clip = core.vivtc.VFM(clip, order=1)  # 1 = top field first
  clip = core.vivtc.VDecimate(clip)  # drops 1 frame in 5 -> 23.976 fps
  clip.set_output()

verified: combed blocks 238 -> 4 (-98.3%), 29.97 -> 23.976 fps, frame count 360 -> 288 as expected for 3:2
note: file holds only 4 non-overlapping windows of 120 frames, not 12
```

The `verified:` line is the part that matters. scanverdict ran its own recommendation over
three sampled spans of the file, recomputed combing on the result, and checked that the
frame count came out where a 3:2 cadence says it should. If the chain had only cut combing
by 40%, or the decimated count had been wrong, the confidence would read `low` and the
reason would be printed underneath.

A true interlaced capture instead, with the evidence block trimmed out here:

```
verdict: interlaced_tff (confidence high)
container says: interlaced_tff (agrees)

ffmpeg -i tests/fixtures/interlaced.mkv -vf bwdif=mode=send_field:parity=tff -c:v libx264 out.mkv

verified: combed blocks 560 -> 6 (-98.9%), 30 -> 60 fps, frame count 360 -> 720 as expected
note: send_field doubles the frame rate to keep the motion you paid for. Use mode=send_frame instead if you need the original rate.
```

## The verdicts

| verdict | what it means | what you get |
| --- | --- | --- |
| `progressive` | whole frames, nothing to undo | no filter, and a note saying so |
| `interlaced_tff` / `interlaced_bff` | real interlace, fields are separate moments | `bwdif=mode=send_field`, double rate |
| `telecine_3_2` | 24p film spread over 29.97 fps video | `fieldmatch,decimate`, back to 23.976 |
| `pulldown_2_2` | every frame repeated once | `fieldmatch,decimate=cycle=2`, half rate |
| `field_blended` | fields averaged together by a bad converter | no ffmpeg chain works, so you get an `srestore` snippet and an honest explanation |
| `mixed` | different parts of the file are different things | a per-window table and the chain each segment needs |
| `undetermined` | not enough motion to measure anything | nothing. It will not guess |

`undetermined` is deliberate. A still frame, a black slate or a frozen capture carries no
evidence either way, and recommending a filter there means a lossy re-encode of content
that may have needed none.

## Mixed files

A file assembled from more than one source gets a table instead of a single chain:

```
verdict: mixed (confidence low)
container says: progressive (disagrees)
sampled: 8 windows of 120 frames, 4/8 windows agree

per window
  #   start     verdict          why
  0        2.0  telecine_3_2     3:2 cadence: one duplicate every 5 frames (100% of slots), 100% of frames clean after field matching
  1        6.6  telecine_3_2     3:2 cadence: one duplicate every 5 frames (100% of slots), 100% of frames clean after field matching
  ...
  4       20.3  interlaced_tff   100% of frames combed, no field match improves them, field order tff throughout
  5       24.9  interlaced_tff   100% of frames combed, no field match improves them, field order tff throughout
  ...

segments need different chains:
  interlaced_tff   bwdif=mode=send_field:parity=tff
  telecine_3_2     fieldmatch=order=tff,decimate
```

Cut on those boundaries and treat each part separately, or do it in VapourSynth and splice.

## Flags

```
scanverdict FILE [FILE ...]

--json                 emit JSON (an object for one file, an array for several)
--csv                  one CSV row per file, header included
--windows N            spans sampled across the file (default 12)
--frames-per-window N  frames decoded per span (default 120)
--no-verify            skip the proving pass
--quiet                one line per file
--version
```

The defaults decode 1440 frames spread across the whole file. Raising `--windows` catches
cadence changes in a long file at the cost of more seeking; lowering it is faster on a slow
disk. A file too short to hold N non-overlapping windows gets fewer, and the output says so.

Several files at once, one line each:

```
$ scanverdict --quiet tests/fixtures/*.mkv
tests/fixtures/interlaced.mkv: interlaced_tff (high)
tests/fixtures/progressive.mkv: progressive (high)
tests/fixtures/telecine.mkv: telecine_3_2 (high) cadence 3:2 phase 2
```

## JSON and CSV

One file gives a JSON object, so this works as-is:

```
$ scanverdict tests/fixtures/telecine.mkv --json | jq -r .verdict
telecine_3_2
```

The object carries the verdict, the confidence, the container claim and whether it agrees,
the full probe result, the sampling layout, the recommendation including both snippets, the
per-window breakdown, and the verification numbers. Several files give an array of those.

`--csv` is the same information flattened for a spreadsheet, one row per file:

```
file,verdict,confidence,field_order,cadence,phase,agreement,container,container_agreement,fps_in,fps_out,filters,comb_before,comb_after,reduction,frames_before,frames_after,verified
tests/fixtures/telecine.mkv,telecine_3_2,high,tff,3:2,2,4/4 windows,progressive,disagrees,29.97,23.976,"fieldmatch=order=tff,decimate",238,4,-0.9831,360,288,yes
```

## How it decides

Every frame is decoded to 8-bit gray through an ffmpeg pipe, letterbox bars are found and
excluded, and four numbers come off each frame.

**Combing.** For every interior row, `d1 = above - here` and `d2 = below - here`. A pixel is
combed when `d1 * d2` exceeds 81, which catches a pixel that sits outside both of its
vertical neighbours rather than on a gradient between them. Pixels are tiled into 8x16
blocks and a block counts as combed when more than 12 of its pixels are, so isolated noise
does not register and a real comb pattern across a moving edge does.

**Field matching.** Five candidates per frame: keep it as shot, or take one field from the
frame and its complement from the frame before or after. The candidate with the lowest comb
score wins and its letter goes into a string. A telecined file produces a period-5 string
like `cppcccppcccppccc`. True interlace produces `ccccccc`, because no neighbour helps.

**Duplicate detection.** Mean absolute difference between consecutive frames. A 3:2 cadence
has exactly one near-zero entry in every five.

**Blend detection.** A least-squares solve for the alpha that best explains frame i as a mix
of its neighbours. A residual under 4.0 with alpha between 0.2 and 0.8 means the frame
really is an average of two others, which is what a bad standards conversion leaves behind.

Each window is classified on its own, then the windows are reconciled. Unanimous windows
give high confidence, a split gives `mixed`, and the container flag is always printed next
to the pixel verdict with `agrees` or `disagrees` spelled out.

## Limitations

**A 24 to 25 fps blend is reported as `progressive`.** When every frame is a blend of two
sources, no frame and no neighbour pair is a clean reference, so the least-squares model has
nothing to fit against. Field blending from a 50i or 60i source is detected correctly. This
is a measured gap, not a guess: the test suite builds a 24 to 25 blended file and asserts
the current behaviour so it stays visible.

**Sharp synthetic content raises the combing floor.** A progressive `testsrc2` frame scores
about 4.6% of its blocks as combed with no interlacing anywhere near it, purely from
single-pixel vertical detail. Real material through a lens and a lossy encoder does not do
this, but crisp animation, scrolling text and pixel art can, and it narrows the margin the
classifier works with.

**Sampling, not a full scan.** With the defaults, 1440 frames are examined. A cadence break
that falls entirely between two sampled windows will be missed. Raise `--windows` on a file
you suspect is spliced.

**Variable frame rate input is flagged, not fixed.** `fieldmatch` and `decimate` both want a
constant rate, so scanverdict tells you to prefix the chain rather than pretending the
problem is not there.

**10-bit and HDR sources work**, because everything is converted to gray8 before measuring.
The extra bit depth carries no cadence information, but detail below 8 bits is not used.

**Decoded windows are held in memory.** Frames are cropped to 512 columns but never
scaled vertically, because field parity has to survive to the metrics. A window costs
`frames_per_window x height x 512` bytes, so 120 frames of 4K is about 130 MB and the verify
pass holds two of those at once. Lower `--frames-per-window` if that matters to you.

**No audio, no GUI, and it will not transcode your file.** The ffmpeg command is printed for
you to run and check.

**Per-scene cadence editing is not this tool's job.** When an NTSC DVD has been edited on
video and the cadence breaks scene by scene, no single chain is right and no automatic
answer is either. That work belongs in
[Wobbly](https://github.com/Jaded-Encoding-Thaumaturgy/Wobbly), which shows you the field
match decisions frame by frame and lets you fix them by hand. scanverdict tells you whether
you are in that situation. Wobbly gets you out of it.

## Development

```
git clone https://github.com/Booyaka101/scanverdict
cd scanverdict
pip install -e ".[dev]"
python -m pytest tests -q
```

The suite builds nine real video fixtures with ffmpeg on first run and caches them under
`tests/fixtures/`, which is gitignored. Nothing is checked in and nothing is mocked. The
first run takes a couple of minutes to generate them, later runs reuse them.

```
python tests/make_fixtures.py            # build them without running tests
python tests/make_fixtures.py --rebuild
```

`ruff check .` for lint. `python -m build` for the wheel and sdist. `pyinstaller
scanverdict.spec` produces the single-file exe, which deliberately does not bundle ffmpeg.

## Shipping it

If you fork this and want people to use it, put the PyInstaller exe on a GitHub release
before doing anything else. This audience downloads exes from VideoHelp and doom9, not
wheels from PyPI, and a release link is the thing that can be pasted into a forum thread
where someone is arguing about whether their DVD rip is interlaced. The PyPI package matters
for the smaller group who will script it.

## License

MIT.
