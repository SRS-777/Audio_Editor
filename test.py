import numpy as np
import soundfile as sf

sr = 44100

# ----------------------------------------------------------------
# MAIN TRACK — a simple 12-second arpeggio in A minor
# ----------------------------------------------------------------
duration_main = 12.0
t_main = np.linspace(0, duration_main, int(sr * duration_main), endpoint=False)

# Slow pulsing A minor chord: A3, C4, E4
main_audio = (
    0.30 * np.sin(2 * np.pi * 220.00 * t_main) +     # A3
    0.20 * np.sin(2 * np.pi * 261.63 * t_main) +     # C4
    0.15 * np.sin(2 * np.pi * 329.63 * t_main)       # E4
)

# Slow tremolo so it's recognisable as the "main" track
main_audio *= (1 + 0.3 * np.sin(2 * np.pi * 0.5 * t_main))

# Fade edges
fade = int(0.1 * sr)
main_audio[:fade] *= np.linspace(0, 1, fade)
main_audio[-fade:] *= np.linspace(1, 0, fade)

# Normalise
main_audio = main_audio / np.max(np.abs(main_audio)) * 0.85
sf.write("main_track.wav", main_audio, sr)
print("Created main_track.wav — 12 seconds, A minor drone")

# ----------------------------------------------------------------
# REMIX CLIP — a fast 3-second melody in C major (completely different)
# ----------------------------------------------------------------
duration_remix = 3.0
t_remix = np.linspace(0, duration_remix, int(sr * duration_remix), endpoint=False)

# Quick melody: C5, E5, G5, C6 — each note 0.75s
melody = np.zeros_like(t_remix)
notes = [523.25, 659.25, 783.99, 1046.50]   # C5, E5, G5, C6
seg = int(sr * 0.75)
for i, f in enumerate(notes):
    start = i * seg
    end = start + seg
    env = np.exp(-np.linspace(0, 5, seg))
    melody[start:end] = 0.6 * np.sin(2 * np.pi * f * t_remix[start:end]) * env

melody = melody / np.max(np.abs(melody)) * 0.85
sf.write("remix_clip.wav", melody, sr)
print("Created remix_clip.wav — 3 seconds, C major melody")