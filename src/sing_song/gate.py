"""#45: silence the long rests of a sung part's MP3. The voice model leaves a quiet hiss in
rests, and a song with many parts waiting at once adds those hisses up (Eero, "Lempilintu").

    python3 gate.py <part.mp3> <line.json> [<line2.json> ...]   (rewrites the MP3)

A rest counts when every line of the part rests; its first 0.25 s (the word's last consonant)
and last 0.15 s (the next word's first consonants) stay, the rest is faded to silence."""
import json, subprocess, sys
import numpy as np, soundfile as sf
mp3, lines = sys.argv[1], sys.argv[2:]
def rests(path):
    out, t = [], 0.0
    for n in json.load(open(path)):
        if n["pitch"] is None: out.append((t, t + n["sec"]))
        t += n["sec"]
    return out
def merge(spans):
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 1e-6: out[-1] = (out[-1][0], max(out[-1][1], b))
        else: out.append((a, b))
    return out
spans = merge(rests(lines[0]))
for other in lines[1:]:                      # both lines must rest: intersect
    o = merge(rests(other)); spans = [(max(a, c), min(b, d)) for a, b in spans for c, d in o if min(b, d) > max(a, c)]
wav = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", mp3, "-f", "f32le", "-ac", "1", "-ar", "44100", "-"], capture_output=True, check=True).stdout
x = np.frombuffer(wav, np.float32).copy(); sr = 44100; fade = int(0.03 * sr)
for a, b in spans:
    s, e = int((a + 0.25) * sr), int((b - 0.15) * sr)
    if e - s < 2 * fade: continue
    x[s:s + fade] *= np.linspace(1, 0, fade); x[e - fade:e] *= np.linspace(0, 1, fade); x[s + fade:e - fade] = 0
tmp = mp3 + ".tmp.mp3"
subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "f32le", "-ar", "44100", "-ac", "1", "-i", "-", "-b:a", "64k", tmp], input=x.tobytes(), check=True)
import os; os.replace(tmp, mp3)
print(mp3, len(spans), "rests silenced")
