import json
import numpy as np
from scipy.io import wavfile
from scipy.fft import fft, ifft

def extract_beats_signals(wav_path, output_json="beatmap_dsp.json"):
    # 1. Read discrete-time audio signal x[n] and sample rate fs
    fs, data = wavfile.read(wav_path)
    if data.ndim > 1:
        data = data.mean(axis=1)  # Convert stereo to mono
    x = data / np.max(np.abs(data))  # Normalize amplitude

    # 2. CONVOLUTION: Low-Pass FIR Filter (Sinc filter with Hamming window)
    fc = 150.0  # Cutoff frequency = 150 Hz
    M = 101     # Filter impulse response length (odd)
    n = np.arange(M) - (M - 1) / 2

    # Impulse response h[n] = sinc(2*fc*n/fs) * window[n]
    h_sinc = np.sinc(2 * fc * n / fs)
    w_hamming = 0.54 - 0.46 * np.cos(2 * np.pi * np.arange(M) / (M - 1))
    h_lpf = h_sinc * w_hamming
    h_lpf = h_lpf / np.sum(h_lpf)  # DC gain normalization

    # Apply LTI system via Time-Domain Convolution: y[n] = x[n] * h_lpf[n]
    y_low = np.convolve(x, h_lpf, mode='same')

    # 3. ENVELOPE EXTRACTION: Square-Law + Moving Average Convolution
    p_inst = y_low ** 2  # Instantaneous power

    # Moving average window of 50 ms
    win_len = int(0.050 * fs)
    w_avg = np.ones(win_len) / win_len
    E_envelope = np.convolve(p_inst, w_avg, mode='same')

    # 4. DIFFERENCE SYSTEM: Convolve with h_diff[n] = delta[n] - delta[n-1]
    h_diff = np.array([1, -1])
    d_envelope = np.convolve(E_envelope, h_diff, mode='same')
    d_envelope = np.maximum(0, d_envelope)  # Half-wave rectification

    # 5. WIENER-KHINCHIN THEOREM: Autocorrelation via FFT for Tempo Estimation
    # Subsample to 100 Hz frame rate for efficient FFT
    hop = int(fs / 100)
    d_sub = d_envelope[::hop]
    fs_sub = fs / hop

    # Compute Power Spectral Density (PSD) and Inverse FFT
    N_fft = len(d_sub)
    D_k = fft(d_sub)
    PSD = np.abs(D_k) ** 2
    r_dd = np.real(ifft(PSD))  # Autocorrelation sequence R_dd[m]

    # Search lag range corresponding to 60 BPM (1.0 sec) to 180 BPM (0.33 sec)
    min_lag = int(0.33 * fs_sub)
    max_lag = int(1.00 * fs_sub)
    best_lag = min_lag + np.argmax(r_dd[min_lag:max_lag])
    period_sec = best_lag / fs_sub
    bpm = 60.0 / period_sec

    # 6. BEAT TIMESTAMP PICKING
    # Find local maxima spaced approximately by period_sec
    threshold = np.mean(d_envelope) + 1.5 * np.std(d_envelope)
    beat_timestamps = []
    
    for i in range(1, len(d_envelope) - 1):
        if d_envelope[i] > threshold and d_envelope[i] > d_envelope[i-1] and d_envelope[i] > d_envelope[i+1]:
            t_sec = i / fs
            # Refractory period constraint using detected beat period
            if not beat_timestamps or (t_sec - beat_timestamps[-1]) >= (0.75 * period_sec):
                beat_timestamps.append(round(t_sec, 3))

    print(f"BPM calculated via Wiener-Khinchin FFT: {bpm:.2f}")
    print(f"Extracted {len(beat_timestamps)} beat timestamps.")

    beatmap_data = {
        "bpm": round(bpm, 2),
        "total_beats": len(beat_timestamps),
        "beat_timestamps": beat_timestamps
    }

    with open(output_json, "w") as f:
        json.dump(beatmap_data, f, indent=4)

    return beatmap_data

if __name__ == "__main__":
    extract_beats_signals("song.wav")
