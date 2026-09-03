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
- **Natively with CoreML:** four at once took 27.3 s, i.e. 6.8 s each against
  9.4 s alone. A 1.4x gain, at 400% CPU of 8 cores available — so the limit is
  the Neural Engine, not the cores, and going wider will not help much.

For 50 pages that is ~5.7 min on the Mac four at a time, ~8 min one at a time,
~13 min in the cluster, ~15 min here.

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
