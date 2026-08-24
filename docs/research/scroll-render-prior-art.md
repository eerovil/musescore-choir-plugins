# Prior art for faster scrolling-score video rendering

Research date: 2026-08-24. Sources are limited to official documentation,
specifications, upstream source, and maintainer repositories.

## Bottom line

There is no examined score renderer that is a drop-in offline 4K60 video
renderer. Verovio and OpenSheetMusicDisplay (OSMD) demonstrate timed score
highlighting in an SVG or browser canvas, but not deterministic,
faster-than-real-time 3840x2160/60 export or direct video-encoder integration.

There is, however, established media plumbing that can materially outperform the
current frame path without changing its engraving or timing model. The strongest
route is to keep the existing Cairo-rasterized engraving and coverage mask, upload
them once as tiled GPU textures, do crop/pan, beat-marker blending, and
active-glyph recoloring in one shader, and pass the resulting 3840x2160 GPU surface
directly to NVENC. GStreamer already connects arbitrary OpenGL shaders and GPU
memory to `nvh264enc`; NVIDIA also documents direct OpenGL and CUDA inputs to
NVENC. This removes the Python/NumPy full-frame copy and the raw-video pipe while
preserving the current pixels and one shared picture encode.

For the measured 100.5-second case, 6,030 `rgb24` frames contain
150,045,696,000 bytes (150.0 GB, 139.7 GiB). Those bytes currently cross the
Python-to-ffmpeg pipe even though the strip and coverage are static. NVENC must
still read one output surface per frame, but that surface can stay in GPU memory.
The speedup is therefore plausible, not yet demonstrated on this workload; it
must be measured against the current NVENC p7/CQ16 baseline.

## Baseline and non-negotiable constraints

The current code already makes two important optimizations:

- `geometry.py` rasterizes the Verovio SVG and the note/rest coverage once in
  tiles, then caches both as full-width arrays.
- `video.py` encodes one silent picture and `mux()` stream-copies that picture
  into every voice mix. This is the right architecture; no candidate below may
  render or encode per voice.

The avoidable per-frame work is in `video.py`: copy a 4K crop into an RGB array,
modify a small set of glyph patches and a beat-marker band with NumPy, then write
the entire 24.9 MB frame to ffmpeg stdin. A replacement must retain 3840x2160,
60 distinct frames per second, the current engraved pixels, the MuseScore-MIDI
clock and exact frame timestamps, active note **and rest** highlighting, smooth
horizontal motion, and the existing stream-copy audio mux.

## Closest score-video prior art: `mscz-to-video`

[`CarlGao4/mscz-to-video`](https://github.com/CarlGao4/mscz-to-video) is the
closest examined project: an AGPL MuseScore-to-video renderer with configurable
4K/60 output, smooth cursor movement, parallel frame jobs, repeated-frame caching,
and PyTorch CPU/GPU composition. Its maintainer reports about 52 fps at 4K on a
single RTX 4060 Mobile GPU. This is project-reported performance, not a benchmark
reproduced here.

Its [upstream source](https://github.com/CarlGao4/mscz-to-video/blob/main/convert_core.py)
shows the useful architecture and its limit. It asks MuseScore `--score-media` for
page PNGs plus measure/note positions, optionally keeps the page images in GPU
memory, generates frames in parallel, and caches identical frames. But even the
GPU path ends with `.cpu().numpy().tobytes()` and sends `rgb24` to an ffmpeg pipe.
The project also documents that smooth cursor movement makes almost every frame
unique, largely defeating its repeated-frame cache.

This validates GPU-side crop/highlight composition and parallel frame production
as real score-video practice. It does not remove the host transfer that dominates
this renderer, and it is not a drop-in replacement: it pans MuseScore pages,
colors rectangular note/bar regions rather than exact glyph coverage, and uses a
different timing/layout path. Its AGPL code should not be copied unless license
compatibility is decided explicitly; the design can still inform an independent
prototype.

## 1. GPU texture composition into NVENC — recommended

### Demonstrated upstream behavior

- GStreamer's [`glshader`](https://gstreamer.freedesktop.org/documentation/opengl/glshader.html)
  performs arbitrary GLSL operations, and its OpenGL design represents a video
  plane as a GPU texture in
  [`GstGLMemory`](https://gstreamer.freedesktop.org/documentation/additional/design/opengl.html#gstglmemory).
- GStreamer's official
  [`nvh264enc`](https://gstreamer.freedesktop.org/documentation/nvcodec/nvh264enc.html)
  accepts `GLMemory` and `CUDAMemory` directly in RGBA/RGBx/BGRA/BGRx or NV12,
  with advertised dimensions up to 4096x4096. Thus 3840x2160 fits without a
  system-memory conversion. The sibling
  [`cudacompositor`](https://gstreamer.freedesktop.org/documentation/nvcodec/cudacompositor.html)
  also demonstrates composition whose input and output stay in `CUDAMemory`.
- NVIDIA's
  [NVENC programming guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvenc-video-encoder-api-prog-guide/index.html#nvenc-block-linear-cuda-array-input)
  documents registering a CUDA array, mapping it, and passing the mapped pointer
  directly to `NvEncEncodePicture()`. NVIDIA's maintained SDK samples include
  [`AppEncGL`](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/read-me/index.html#encoder-applications),
  which encodes OpenGL textures, and the API also accepts CUDA device pointers and
  CUDA arrays. This is an established encoder input path, not a hypothetical API.

### Transfer inference for this renderer

Upload the current RGB strip and 8-bit coverage mask once, split into texture-size
tiles. For each frame, a shader can:

1. sample the strip at the existing integer `left` offset;
2. draw white outside the strip;
3. apply the current full-height beat-marker blend;
4. test the small frame-local list of active note/rest rectangles and recolor
   covered pixels with the same coverage and RGB constants; and
5. write one 3840x2160 GPU output surface with PTS `frame_no / 60`.

This preserves crisp text because it reuses the current target-resolution Cairo
raster and performs no scale. The score/MIDI logic, scroll anchors, active-event
calculation, and one-picture/many-audio mux remain unchanged. Only composition and
transport move off Python. A direct NVENC SDK implementation has the highest
control; a small GStreamer OpenGL filter is the more established integration and
already handles timestamps, H.264 packaging, and GL-to-NVENC negotiation.

This is a transfer inference: neither GStreamer nor NVIDIA supplies the
score-specific shader or proves a speedup for this strip size. Long scores also
cannot be assumed to fit one GPU texture, so tiled textures are required.

### Verified on the target host

The target GTX 970 host already has GStreamer 1.28.3's `glshader` and `nvh264enc`.
The encoder advertises direct `GLMemory` and `CUDAMemory` input at up to 4096x4096,
including RGBA/RGBx and NV12. A one-frame 3840x2160 `glupload` -> `nvh264enc`
negotiation test succeeded with the existing GPU and driver.

A synthetic ceiling test then generated 600 3840x2160 RGBA frames directly in
OpenGL and encoded them with NVENC p7/VBR/CQ16 to a null sink in **7.30 seconds**,
or about **82 fps**. The measured production-shaped Python/raw-pipe loop was about
34 fps (29.1 ms/frame). This is not a score-render benchmark—the test shader did
not sample score tiles or highlight glyphs—but it proves that this host can keep
4K frames in GL memory through NVENC at more than 60 fps and shows enough headroom
to justify the score-specific prototype.

### Smallest falsifying prototype

Render a representative dense 10-second/600-frame excerpt with the existing
strip, coverage, anchors, and events. Upload strip/coverage tiles once; implement
the current crop, one active note, one active rest, and the beat marker in a
minimal GL shader; feed `GLMemory` to `nvh264enc` with the current H.264 settings.
For ten selected frames, read back the pre-encode surface and compare it to the
NumPy frame (exactly, or document the one unavoidable color-conversion boundary).
Use ffprobe to require 3840x2160, 60 fps, 600 frames, and monotonic 1/60-second
timestamps. Reject the route if it cannot sustain more than 60 fps offline, if
negotiation inserts a per-frame GPU download/upload, or if the glyph-edge and
marker pixels cannot match.

## 2. Let ffmpeg own the repeated strip and crop — useful experiment, incomplete solution

### Demonstrated upstream behavior

- FFmpeg's [`loop`](https://ffmpeg.org/ffmpeg-filters.html#loop) filter repeats
  cached video frames. Its [`crop`](https://ffmpeg.org/ffmpeg-filters.html#crop)
  filter evaluates `x` and `y` for every frame; the
  [upstream crop source](https://github.com/FFmpeg/FFmpeg/blob/master/libavfilter/vf_crop.c)
  implements software cropping by adjusting frame dimensions and plane pointers,
  rather than copying the cropped pixels.
- [`sendcmd`](https://ffmpeg.org/ffmpeg-filters.html#sendcmd_002c-asendcmd)
  sends commands at exact time intervals, and `crop` exposes runtime `x`/`y`
  commands. A generated piecewise expression or command file can therefore use
  the existing MIDI-derived anchors. [`zoompan`](https://ffmpeg.org/ffmpeg-filters.html#zoompan)
  also evaluates pan expressions per output frame, but adds scaling machinery
  that is unnecessary here. [`scroll`](https://ffmpeg.org/ffmpeg-filters.html#scroll)
  is unsuitable because it only scrolls at constant speed.
- FFmpeg's [`maskedmerge`](https://ffmpeg.org/ffmpeg-filters.html#maskedmerge)
  blends two streams with per-pixel mask weights. `hwupload_cuda` uploads a
  system-memory frame, and
  [`overlay_cuda`](https://ffmpeg.org/ffmpeg-filters.html#overlay_005fcuda)
  overlays CUDA frames. The installed ffmpeg advertises `crop`, `loop`,
  `maskedmerge`, `sendcmd`, `zoompan`, `scroll`, `hwupload_cuda`, and
  `overlay_cuda`.

### Transfer inference and limit

For the unhighlighted picture, ffmpeg can loop a static strip, crop it according
to frame time, pad the ends, upload only the 4K crop, and encode. That eliminates
the Python full-frame copy and pipe while retaining one encode and exact frame
timestamps. It may be the smallest useful speed experiment.

Stock filters do **not** complete the requirement cleanly. The coverage image
marks every playable glyph, while only a changing subset may be colored. A graph
can gate individual rectangles with timed overlays/draw operations and then use
`maskedmerge`, but thousands of note intervals can turn into thousands of filter
operations evaluated per frame. `overlay_cuda` only overlays already-created
frames; it does not generate the score-specific active-glyph mask. Uploading a
new 4K mask each frame merely changes the raw-pipe problem from RGB to grayscale.
A custom ffmpeg GPU filter would solve this, but is effectively route 1 with a
different host framework.

There is another unproven edge: the one-frame input is an unusually wide score
strip. Decoder and filter limits must be tested rather than inferred from crop's
API. Tiling the strip inside a stock filtergraph may reintroduce full-strip work.

### Smallest falsifying prototype

First render the full 100.5-second pan with no highlights from a looped static
strip and generated crop commands, using the current NVENC options. Measure wall
time, CPU time, and whether only the 4K crop reaches `hwupload_cuda`. Reject it if
the ultra-wide frame is unsupported or if it does not beat the current path
materially. Only then test a dense 30-second passage using actual note/rest
intervals and coverage. Reject the stock-filter route if graph construction or
filtering grows with total note count enough to miss 60 fps, or if exact
antialiased glyph recoloring cannot be reproduced. Do not ship the pan-only win;
highlighting is required.

## 3. Browser WebGL/Canvas plus WebCodecs — valid prototype, poor production bet

### Demonstrated upstream behavior

- Verovio's official
  [MIDI playback tutorial](https://book.verovio.org/interactive-notation/playing-midi.html)
  calls `getElementsAtTime()`, adds a CSS class to active SVG note groups, and
  changes their fill color. This demonstrates retained SVG highlighting, not
  video export.
- OSMD exposes a
  [single-horizontal-staff option and cursor colors](https://github.com/opensheetmusicdisplay/opensheetmusicdisplay/blob/develop/src/OpenSheetMusicDisplay/OSMDOptions.ts),
  and supports SVG and Canvas backends. Its own
  [renderer source](https://github.com/opensheetmusicdisplay/opensheetmusicdisplay/blob/develop/src/OpenSheetMusicDisplay/OpenSheetMusicDisplay.ts)
  warns that browser canvas width is limited to 32,767 pixels, which is already
  too short for many continuous scores.
- The W3C
  [WebCodecs specification](https://www.w3.org/TR/webcodecs/#videoencoder-interface)
  accepts timestamped `VideoFrame`s and is intended for non-real-time editing as
  well as live media. However,
  [`hardwareAcceleration`](https://www.w3.org/TR/webcodecs/#hardware-acceleration)
  is only a hint that the user agent may ignore. The WebCodecs maintainers also
  record that direct WebGPU-buffer-to-encoder interop was
  [closed as not planned](https://github.com/w3c/webcodecs/issues/83); the stated
  alternative is OffscreenCanvas, not a guaranteed zero-copy GPU surface API.

### Transfer inference and verdict

A WebGL shader can implement the same tiled-texture compositor as route 1, and
WebCodecs can encode frames with deterministic timestamps without recording a GUI
in real time. That makes it technically eligible. It is still a weaker production
route: hardware H.264 selection and zero-copy canvas-to-encoder transfer are not
guaranteed, muxing needs another library, browser/headless behavior adds a moving
dependency, and Verovio/OSMD do not remove the per-frame video problem. Replacing
the current Verovio engraving with OSMD would also create unnecessary score-layout
and glyph-fidelity risk.

### Smallest falsifying prototype

In headless Chromium, render 600 4K frames as fast as possible from the existing
raster tiles in OffscreenCanvas/WebGL, create timestamped `VideoFrame`s, and feed
`VideoEncoder` with H.264 and `prefer-hardware`. Require 600 encoded frames, exact
timestamps, actual hardware use, and throughput above 60 fps. Failure on any one
rejects the route. Even success would be machine/browser-specific evidence, so it
should not outrank the GStreamer/NVENC path.

## Retained vector renderers and caching

These are useful technologies, but they attack the wrong stage here.

- Skia can create a GPU-backed
  [`SkSurface`](https://skia.org/docs/user/api/skcanvas_creation/#gpu) and replay a
  retained [`SkPicture`](https://api.skia.org/classSkPicture.html#details) onto a
  canvas. Replaying vector commands at 4K for every frame is more work than
  sampling the already-cached raster tiles. Skia could host the GPU compositor,
  but it does not by itself provide the direct NVENC handoff that GStreamer does.
- [`resvg`](https://github.com/linebender/resvg) separates SVG parsing from
  rendering and offers reproducible static raster output. `librsvg` likewise lets
  one loaded
  [`RsvgHandle`](https://gnome.pages.gitlab.gnome.org/librsvg/Rsvg-2.0/overview.html)
  render repeatedly to Cairo. Both are CPU/static renderers in these documented
  paths. Replacing CairoSVG may reduce one-time strip creation or memory, but the
  strip is already rendered once and reused; it cannot remove the 6,030 4K frame
  copies.
- Incremental engraving, OSMD's lazy drawing, or re-rendering only the visible
  vector window can reduce startup or peak strip memory. It would rasterize text
  and music glyphs repeatedly and risks frame-to-frame antialiasing differences.
  The useful cache is the one already present: static target-resolution raster
  tiles and coverage tiles, retained on the GPU. Keep only nearby tiles resident
  if GPU memory requires it; that is a memory refinement of route 1, not a
  separate renderer.

No prototype is recommended for these until profiling shows one-time
rasterization is a meaningful fraction of total wall time. If it is, the smallest
test is a pixel-diff and wall-time comparison of CairoSVG, resvg, and librsvg for
the same full strip and coverage render; it must not be confused with a per-frame
speedup.

## Ranked conclusions

1. **GStreamer GL/CUDA shader to NVENC: highest confidence and ceiling.** It is
   the only examined established stack that removes both Python composition and
   host raw-frame transport while preserving the current engraved raster,
   highlighting, MIDI timing, 4K60 output, and one shared encode. The installed
   stack passed a 4K GPU-memory-to-NVENC negotiation and an 82 fps synthetic
   ceiling test. Prototype the real tiled score shader first.
2. **FFmpeg-owned loop/crop: smallest experiment, conditional value.** It can
   prove how much the Python copy/pipe costs with little architectural work. Stock
   filters are not yet a credible complete highlighter; stop if the dense-note
   prototype scales badly, and do not accept a pan-only implementation.
3. **WebGL + WebCodecs: technically possible, lower confidence.** It avoids GUI
   recording and can use explicit timestamps, but hardware/zero-copy behavior is
   not guaranteed. Prototype only if GStreamer integration is unavailable.
4. **Verovio/OSMD animation: reject as the rendering pipeline.** Keep Verovio's
   engraving and IDs, but their interactive cursors do not solve offline 4K60
   composition or encoding. Do not replace the MuseScore-MIDI clock.
5. **Skia/resvg/librsvg or incremental vector rendering: reject as the primary
   speed fix.** They may improve one-time SVG rasterization, not the measured
   150 GB per-frame path. Do not trade the current crisp, stable raster cache for
   per-frame vector replay without contrary profiling.
6. **`mscz-to-video`: borrow the experiment design, not the pipeline.** Its GPU
   composition and parallel jobs are relevant prior art, but its smooth-scroll
   path still downloads RGB frames to an ffmpeg pipe, and its page/rectangle
   visuals do not meet this renderer's exact glyph-highlighting contract.

All promising routes retain the current final step: encode one silent picture
once, then attach each voice mix with video stream copy. Any prototype that
records a browser/window in real time, lowers resolution or frame rate, changes
the engraving, derives timing from wall-clock playback, or re-encodes per voice
fails the specification regardless of wall time.
