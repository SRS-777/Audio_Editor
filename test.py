import numpy as np
import soundfile as sf

sr = 44100
duration = 5.0

t = np.arange(int(sr * duration)) / sr

music = (
    0.4 * np.sin(2 * np.pi * 220.00 * t) +
    0.3 * np.sin(2 * np.pi * 261.63 * t) +
    0.3 * np.sin(2 * np.pi * 329.63 * t)
)

noise = 0.08 * np.random.randn(len(t))

signal = music + noise

signal[:sr] = noise[:sr]

signal = signal / np.max(np.abs(signal)) * 0.9

sf.write("test_hiss.wav", signal, sr)