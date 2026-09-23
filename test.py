"""
remix_25s_gen.py

Generates clip_remix_25s.wav: 25-second warm-bell chord pad.
One chord every 2 s (13 events), ring time 2.2 s, gentle arpeggio.
Progression Am - F - C - G, cycling 3x + final Am resolution.

Designed as a superimpose layer for sweet_demo.wav (C major) or
rainfall_demo.wav (A minor) -- it's the same relative progression.
"""

import numpy as np
import soundfile as sf

SR  = 44100
DUR = 25.0
OUT = "clip_remix_25s.wav"
rng = np.random.default_rng(77)

_NOTE = {'C':0,'C#':1,'D':2,'D#':3,'E':4,'F':5,'F#':6,
         'G':7,'G#':8,'A':9,'A#':10,'B':11}

def f(note):
    n = note[:-1]; o = int(note[-1])
    midi = 12 * (o + 1) + _NOTE[n]
    return 440.0 * 2 ** ((midi - 69) / 12)

def warm_bell(freq, dur_s, amp=1.0, tau=0.75):
    """Soft bell: 3 partials, 12 ms attack, exponential decay."""
    n = int(dur_s * SR)
    t = np.arange(n) / SR
    env = np.exp(-t / tau)
    a = max(1, int(0.012 * SR))
    env[:a] *= np.linspace(0, 1, a)
    r = max(1, int(0.15 * SR))
    if r < n:
        env[-r:] *= np.linspace(1, 0, r)

    sig  = 1.00 * np.sin(2 * np.pi * freq       * t)
    sig += 0.30 * np.sin(2 * np.pi * 2 * freq   * t + 0.3)
    sig += 0.10 * np.sin(2 * np.pi * 3 * freq   * t + 0.7)
    return amp * sig * env

# -------------------------------------------------------------- build
total_n = int(DUR * SR)
track   = np.zeros(total_n)

def place(t0, sig):
    i = int(t0 * SR)
    j = min(i + len(sig), total_n)
    if i < total_n and j > i:
        track[i:j] += sig[:j - i]

# Am - F - C - G, voicings in 590-1050 Hz (above the piano's C4-G4)
chords = [
    ['E5', 'A5', 'C6'],   # Am
    ['F5', 'A5', 'C6'],   # F
    ['E5', 'G5', 'C6'],   # C
    ['D5', 'G5', 'B5'],   # G
]

# 13 events: 3 full cycles + final Am
sequence = [0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3, 0]

for k, ci in enumerate(sequence):
    t0 = k * 2.0                        # 0, 2, 4, ..., 24 s

    # Slight amplitude jitter to keep it human
    amp_base = 0.22 * (0.90 + rng.uniform(0, 0.20))

    # 3-note arpeggio: note k at t0 + k*80 ms
    for j, note in enumerate(chords[ci]):
        t_note = t0 + j * 0.08
        place(t_note, warm_bell(f(note), 2.2, amp=amp_base, tau=0.75))

# ------------------------------------------------------------ normalise
peak = np.max(np.abs(track))
if peak > 0:
    track = track / peak * 0.65

sf.write(OUT, track.astype(np.float32), SR)

print(f"Wrote {OUT}")
print(f"  {DUR:.1f} s @ {SR} Hz, mono")
print(f"  13 events at 0, 2, 4, ..., 24 s (one chord every 2 s)")
print(f"  Progression: Am - F - C - G  x3  + Am resolve")
print(f"  Notes 587-1046 Hz (D5-C6), no content below 500 Hz")