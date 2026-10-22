"""Does a sung part start its notes where the site's piano does? (musescore-choir-plugins#419)

    python3 check_onsets.py <part.json> <score.mid> <track index> <timing.json>

<part.json> is unroll.py's output for one part, <track index> that part's track in score.mid
(0-based, the parts' order). Every sung note except a grace note, and the note a grace note
leads to (it starts late by the time the grace borrows), must start within TOLERANCE seconds
of a note start on that MIDI track. Prints one line per note that does not, with its bar, and
exits 1 if any; run_all/run2 refuse to publish such a part. Es ist ein Ros's breve and long
rests counted as nothing and its lower parts were sung 6-18 s early; a double dot counted as
one put notes off the beat mid-bar."""
import bisect, json, sys

TOLERANCE = 0.05


def _var(b, i):
    v = 0
    while True:
        c = b[i]; i += 1; v = (v << 7) | (c & 0x7F)
        if c < 0x80: return v, i


def midi_onsets(path):
    """Note starts in seconds, per track, on the MIDI's own tempo map (read here, so this
    runs on the system python, which has no MIDI library)."""
    b = open(path, "rb").read()
    if b[:4] != b"MThd": sys.exit(f"{path}: not a MIDI file")
    ntracks, division = int.from_bytes(b[10:12], "big"), int.from_bytes(b[12:14], "big")
    i, tracks, tempos = 14, [], []
    while len(tracks) < ntracks:
        size = int.from_bytes(b[i + 4:i + 8], "big"); end = i + 8 + size; i += 8
        tick, status, starts = 0, 0, []
        while i < end:
            d, i = _var(b, i); tick += d
            if b[i] & 0x80: status = b[i]; i += 1
            if status == 0xFF:
                kind = b[i]; n, i = _var(b, i + 1)
                if kind == 0x51: tempos.append((tick, int.from_bytes(b[i:i + 3], "big")))
                i += n
            elif status in (0xF0, 0xF7):
                n, i = _var(b, i); i += n
            else:
                hi = status & 0xF0
                if hi == 0x90 and b[i + 1] > 0: starts.append(tick)
                i += 1 if hi in (0xC0, 0xD0) else 2
        tracks.append(starts); i = end
    tempos.sort()
    def sec(tick):
        s, last, us = 0.0, 0, 500000
        for t, v in tempos:
            if t >= tick: break
            s += (t - last) * us / division / 1e6; last, us = t, v
        return s + (tick - last) * us / division / 1e6
    return [sorted(set(round(sec(t), 4) for t in tr)) for tr in tracks]


def bar_at(points, sec):
    """1-based bar number at `sec`, from timing.json's [seconds, measure, beat] points."""
    bar = 1
    for s, m, _ in points:
        if s > sec + 1e-6:
            break
        bar = m + 1
    return bar


def off_notes(notes, ref, points):
    """[(bar, sung start, nearest MIDI start)] for each checked note too far from the MIDI."""
    out, t, after_grace = [], 0.0, False
    for n in notes:
        if n.get("pitch") is not None:
            if n.get("grace"):
                after_grace = True
            elif after_grace:
                after_grace = False
            else:
                j = bisect.bisect_left(ref, t)
                near = min((ref[k] for k in (j - 1, j) if 0 <= k < len(ref)), key=lambda x: abs(x - t), default=None)
                if near is None or abs(near - t) > TOLERANCE:
                    out.append((bar_at(points, t), round(t, 3), near))
        t += n["sec"]
    return out


if __name__ == "__main__":
    part, midi, track, timing = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
    tracks = midi_onsets(midi)
    if track >= len(tracks):
        sys.exit(f"no MIDI track {track} (the MIDI has {len(tracks)})")
    bad = off_notes(json.load(open(part)), tracks[track], json.load(open(timing))["points"])
    for bar, t, near in bad:
        print(f"bar {bar}: note at {t} s, nearest MIDI note at {near} s")
    sys.exit(1 if bad else 0)
