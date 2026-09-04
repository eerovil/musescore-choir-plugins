# Where homr should run: measured, 2026-09-03

The scan stage is the slowest thing the app does — a song is 15–20 systems at
10–16 s each, about 200 s on this host. This is a measurement of whether moving
homr to other hardware is worth it. **Conclusion: the only real gain is running
homr natively on the MacBook (about 2.6x), Kubernetes on that same MacBook is
the slowest option of all, and nothing was built.**

Recorded because the question will come back, and because two of the three
answers here are the opposite of what they look like from the outside.

## What was measured

One page, the same page every time: `fixtures/omr-benchmark/B1a-heraa-suomi-p420-287dpi.pdf`
rasterised at 300 dpi with poppler (1.76 MB PNG). homr from `eerovil/homr@main`
(`0.7.0.post83+686d9fd`) on both new installs, run as `homr --gpu no` or
`--gpu auto`. Wall time from `time`, best of three where noted.

Both machines read the page the same way — 6 staves, 3 connected — so this is
one parse on different hardware, not two different parses.

| where | per page | CPU used |
| --- | --- | --- |
| MacBook Air, native, `--gpu auto` (CoreML) | **9.4 s** | 16.4 s |
| MacBook Air, native, `--gpu no` | 9.9 s | 31.2 s |
| MacBook Air, inside the kind cluster | 15–17 s warm, 21 s cold | ~90 s |
| this host (bazzite, 4 cores) | 16–19 s | ~52 s |

## The Apple AI cores are real, and they are not the win

homr already supports them: `homr/onnx_providers.py` picks
`CoreMLExecutionProvider` under `--gpu auto` when onnxruntime offers it, and a
native macOS onnxruntime does (`['CoreMLExecutionProvider',
'AzureExecutionProvider', 'CPUExecutionProvider']`).

But CoreML is worth **5% of wall time**, not a multiple: 9.4 s against 9.9 s for
the same page on the same machine. What it does buy is CPU — 16.4 s against
31.2 s, half — so the page keeps only 1.7 cores busy and the laptop stays cool.
The 2x against this host is the Mac simply being a faster machine, not the
Neural Engine.

### The Neural Engine is the slow unit; the GPU is the fast one

A later round on the same page and the same machine, forcing each compute unit
in turn (`HOMR_COREML_COMPUTE_UNITS`). Single runs, on a quieter machine than
the table above — compare them with each other, not with the 9.4 s:

| setting | per page |
| --- | --- |
| `CPUAndGPU` — GPU only | **7.9 s** |
| default `ALL` — CoreML chooses | 8.4 s, 8.6 s |
| `CPUAndNeuralEngine` — ANE only | 10.7 s |
| `ALL` plus `--coreml-encoder`, cache warm | 10.6 s (26.5 s cold) |

So the accelerator that helps is the **GPU**, and steering work to the Neural
Engine makes the page *slower* — 35% slower than the GPU, and worse than letting
CoreML decide. Every "use the AI cores" instinct in this investigation was
pointed at the wrong silicon.

`--coreml-encoder` is a loss even once its 26.5 s compile is paid and cached:
10.6 s against 8.5 s. It is off by default for a better reason than its own
comment gives.

Two caveats. These are single runs, and repeated identical runs differ by about
0.2 s, so 7.9 against 8.5 is roughly twice the noise — real, but thin. And
nothing here was cold: 8.38 s then 8.61 s back to back.

Where the time goes, off homr's own log for this page: segnet 4.4 s (on CoreML),
the three staves 2.9 s (encoder and decoder on the CPU), and ~2 s of dewarping,
text recognition and file handling that is not inference at all. The decoder
cannot move: its attention cache grows with every token, and CoreML's MLProgram
format rejects that changing shape (see `homr/onnx_providers.py`). Re-exporting
it with a fixed-size cache is the only route to the rest, and that is fork work,
not a flag.

## Kubernetes on the Mac takes the gain away

The cluster reachable at `eeros-macbook-air.taile8d16e.ts.net:6443` is a kind
node in a Linux VM: arm64, 8 CPUs, 24 GB. A pod there reads the page in 15–17 s
— **60% slower than the same laptop running homr natively**, and no better than
the four-core host it was supposed to beat.

The reason is structural rather than tuning: Apple's Neural Engine and GPU are
not exposed to Linux containers at all. There is no driver and no passthrough.
Anything in that VM is plain CPU on the Arm cores, which is exactly what the
timings show. Containerising homr on the Mac is the one arrangement that
guarantees the AI cores cannot be used.

## Concurrency helps a little, and only natively

The Neural Engine is a single shared unit, so pages queue on it.

- **In the pod:** four pages at once took 69.5 s, i.e. 17.4 s each — *slightly
  worse* than one at a time. One page already spreads over 4.3 of the 8 cores,
  so there is nothing to parallelise into.
- **On this host:** four at once took 74.9 s, i.e. 18.7 s each against 17.7 s
  alone. Also slightly worse, and for the same reason — one page already uses
  about 3 of the 4 cores.
- **Natively with CoreML:** four at once took 27.3 s, i.e. 6.8 s each against
  9.4 s alone. A 1.4x gain, at 400% CPU of 8 cores available — so the limit is
  the Neural Engine, not the cores, and going wider will not help much.

So concurrency is only worth anything on the Mac natively, where CoreML leaves
cores idle. Everywhere else a single page already saturates the machine and
running four is a rearrangement of the same total time.

For 50 pages: ~5.7 min on the Mac four at a time, ~8 min one at a time,
~14.5 min in the cluster, ~15.6 min here.

Two machines do add up, since they are independent — this host and the Mac
natively, both going, is ~4.1 min. But the cluster and the native Mac are *the
same laptop*, so that pairing is a choice between them and the pod is the slow
half. And a scan running flat out here is what the heavy-slot queue exists to
prevent: it would starve any render or test suite beside it.

## The cheapest speed-up is not hardware at all

Asked what it would take to read 20 systems in 10 s — about 25x — the budget says
hardware is the last lever, not the first.

**Half the time is startup.** homr on a *blank* image, with no music to read,
takes 5.4 s on this host; 1.6 s of that is importing the library. The scan pays
that once per band, so the fixture's 15 systems spend roughly 80 s of their 201 s
starting up. A resident process with the models already loaded removes it,
without touching a model or a GPU.

**The rest is per-run, not per-note.** A whole page costs 17.7 s here and one
system about 13 s, so reading a page as four bands costs four times the fixed
overhead for the same music. Which raises the question the per-system rule was
built to answer.

### Whole-page reading works again — except on ragged pages

The per-system rule exists because a whole-page parse used to collapse B5's four
staves into one monophonic line of 70 bars. The staff-grouping fix that caused
that has since landed upstream, so the question was re-asked on the benchmark
with homr `0.7.0.post83`. Times include waiting for a heavy slot, so read them as
directional; the per-system route asks for a slot per band and the whole-page
route once.

| page | staves it prints | per-system | whole page |
| --- | --- | --- | --- |
| B1a | 2 | 52.8 s — 2-2-2, 7 bars | 41.7 s — 2 staves, 7 bars ✓ |
| B1b (poor scan) | 2 | 89.1 s — 2-2-2, 7 bars | 34.3 s — 2 staves, 7 bars ✓ |
| B2 | 2 | 74.0 s — 2-2-2-2, 16 bars | 39.7 s — 2 staves, 16 bars ✓ |
| B5 | 4 | 4-4-4 (per CLAUDE.md) | 39.5 s — **4 staves**, 23 bars each ✓ |
| B4 | 2-3-2-3-3 | 2-3-2-3-3, 18 bars (per CLAUDE.md) | 36.6 s — 5 staves, **8 bars** ✗ |

**B5 no longer collapses.** The page that justified the whole design reads back
as the four staves it prints. But **B4 still fails**, and fails in exactly the
documented way: a page whose systems carry different numbers of staves is not a
rectangle, so the parts come back stacked — 5 staves where no system prints more
than 3, and 8 bars where the page has 18. Half the music, silently.

So whole-page reading is roughly **2x** and is correct on every page whose systems
all carry the same staves — which is most of this repertoire — and quietly wrong on
the ones that do not. A rule could tell them apart before reading: `system_finder`
already reports how many staves each band holds, so a page whose bands agree can be
read whole and a page whose bands disagree read band by band. That is a proposal,
not a measurement; nobody has built or tested it.

### What 10 s would actually take

Startup removed and uniform pages read whole gets 20 systems to roughly 40–60 s,
and no further. The last 5x needs the inference itself to be faster, and the Apple
GPU will not do it (5% over CPU, measured above). It needs a CUDA card — this
host's GTX 970 is below onnxruntime's floor — **and** batching, which homr does not
do: it reads one staff at a time, where 20 systems is 40–80 staff images a GPU
would take in one pass. Resident service, modern card, batched staves. That is
fork-level ML work plus hardware, not configuration.

## Why nothing was built

2.6x on a stage that runs once per song, in exchange for a second machine in the
path. Using it would mean a homr service on the Mac under launchd, a remote
engine in `omr.py`, Tailscale between them, and a fallback to local homr
whenever the laptop is asleep — and the Mac would become host state no checkout
can reproduce, which is the complaint `CLAUDE.md` already makes about the `omr`
distrobox container that `scripts/install-homr.sh` replaced.

If it is ever built, the shape is: **native macOS, not a container**, one page
per request, and four in flight at most.

## Reproducing it

On the Mac (no repo checkout needed):

```bash
uv venv --python 3.12 ./homr-venv
VIRTUAL_ENV=./homr-venv uv pip install "homr[cpu] @ git+https://github.com/eerovil/homr.git@main"
./homr-venv/bin/homr --init
./homr-venv/bin/python -c "import onnxruntime as ort; print(ort.get_available_providers())"
cp page.png a.png && time ./homr-venv/bin/homr --gpu auto a.png
```

homr writes its MusicXML and a `_teaser.png` beside the input, so give each run
its own copy of the image or they overwrite each other.
