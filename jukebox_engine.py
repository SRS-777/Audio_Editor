"""
Infinite Jukebox engine.

Pipeline
--------
1. analyze()      - track the beats of a recording and describe each beat by its
                    timbre (MFCC), pitch content (chroma), loudness, internal
                    rhythm (onset pattern) and length.  Every beat is compared
                    with every other beat, with the neighbouring beats taken
                    into account, giving a "transition cost" matrix.
2. build_graph()  - keep the cheapest transitions as branches, then restrict
                    them to the strongly connected part of the song so that
                    playback can always get back to earlier material and never
                    has to reach the real ending.
3. RoutePlanner   - decides, beat by beat, whether to continue or to take a
                    branch; the choice is random, weighted by similarity and
                    novelty, so the route keeps changing.
4. BeatStream     - renders the chosen route as a continuous audio stream,
                    crossfading at every jump.

Only numpy and scipy are used, so everything here runs headless.
"""

from collections import Counter, deque
from dataclasses import dataclass
from math import gcd

import numpy as np
from scipy.fft import dct, rfft
from scipy.ndimage import uniform_filter1d
from scipy.signal import resample_poly
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

ANALYSIS_SR = 22050
MAX_DURATION_SEC = 15 * 60

# Onset / timbre spectrogram
ONSET_N_FFT = 1024
ONSET_HOP = 256
N_MELS = 48
N_MFCC = 13
# Chroma spectrogram (needs finer frequency resolution)
CHROMA_N_FFT = 4096
CHROMA_HOP = 1024
RHYTHM_BINS = 8

# Relative importance of each beat description when comparing beats
FEATURE_WEIGHTS = {"timbre": 1.0, "pitch": 1.0, "loudness": 0.4, "rhythm": 0.6, "duration": 0.3}

# Transition context: offset k compares beat (src + k) with beat (dst - 1 + k).
# k = 0 is the beat just played vs. the beat that normally leads into dst,
# k = 1 is the beat we would have heard next vs. the beat we jump to.
CONTEXT_OFFSETS = (-2, -1, 0, 1, 2, 3)
CONTEXT_WEIGHTS = (0.25, 0.5, 1.0, 1.0, 0.5, 0.25)


class AnalysisCancelled(Exception):
    pass


# ----------------------------------------------------------------------
# Data containers
# ----------------------------------------------------------------------
@dataclass
class BeatAnalysis:
    sr: int                      # sample rate of the playback audio
    n_samples: int               # length of the playback audio
    tempo: float                 # beats per minute
    starts: np.ndarray           # (N+1,) sample boundaries; beat k = [starts[k], starts[k+1])
    colors: np.ndarray           # (N, 3) RGB in [0, 1], similar-sounding beats get similar colours
    loudness: np.ndarray         # (N,) mean loudness in dB
    pair_cost: np.ndarray        # (N, N) float32, cost of playing beat b right after beat a-1
    median_cost: float

    @property
    def n_beats(self):
        return len(self.starts) - 1

    @property
    def duration(self):
        return self.n_samples / float(self.sr)

    def beat_time(self, beat):
        return self.starts[beat] / float(self.sr)


@dataclass(frozen=True)
class Branch:
    src: int            # beat after which the jump happens
    dst: int            # beat that is played instead of src + 1
    cost: float
    similarity: float   # 0..1, higher is a closer match


@dataclass
class JukeboxGraph:
    n_beats: int
    branches: list                 # branches[src] -> list[Branch], best first
    loop_start: int | None         # playback can circulate forever inside [loop_start, loop_end]
    loop_end: int | None
    quantile: float                # share of all beat pairs accepted as branches
    requested_quantile: float
    threshold: float
    structure: float               # 0..1, how much better the branches are than a typical pair

    @property
    def has_loop(self):
        return self.loop_start is not None

    @property
    def relaxed(self):
        return self.quantile > self.requested_quantile + 1e-12

    @property
    def n_branches(self):
        return sum(len(b) for b in self.branches)

    def all_branches(self):
        for per_src in self.branches:
            yield from per_src

    def in_loop(self, beat):
        return self.has_loop and self.loop_start <= beat <= self.loop_end

    def loop_coverage(self):
        if not self.has_loop:
            return 0.0
        return (self.loop_end - self.loop_start + 1) / float(self.n_beats)

    def structure_label(self):
        if self.structure >= 0.55:
            return "Strong"
        if self.structure >= 0.35:
            return "Moderate"
        return "Weak"


# ----------------------------------------------------------------------
# Signal helpers
# ----------------------------------------------------------------------
def to_mono(audio):
    audio = np.asarray(audio)
    return audio.mean(axis=1) if audio.ndim == 2 else audio


def resample(y, sr_from, sr_to):
    if sr_from == sr_to:
        return y
    g = gcd(int(sr_from), int(sr_to))
    return resample_poly(y, int(sr_to) // g, int(sr_from) // g)


def _hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)


def _mel_to_hz(m):
    return 700.0 * (10.0 ** (np.asarray(m) / 2595.0) - 1.0)


def _mel_filterbank(sr, n_fft, n_mels, fmin=30.0, fmax=None):
    fmax = fmax or sr / 2.0
    hz = _mel_to_hz(np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    fb = np.zeros((n_mels, len(freqs)), dtype=np.float32)
    for m in range(n_mels):
        up = (freqs - hz[m]) / (hz[m + 1] - hz[m])
        down = (hz[m + 2] - freqs) / (hz[m + 2] - hz[m + 1])
        fb[m] = np.maximum(0.0, np.minimum(up, down))
    fb *= (2.0 / (hz[2:] - hz[:-2]))[:, None]
    return fb


def _chroma_map(sr, n_fft, fmin=55.0, fmax=5000.0):
    """Sparse bin -> pitch-class assignment (C = 0)."""
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    bins = np.where((freqs >= fmin) & (freqs <= fmax))[0]
    midi = 69.0 + 12.0 * np.log2(freqs[bins] / 440.0)
    return bins, np.round(midi).astype(int) % 12


def _stft_reduce(y, n_fft, hop, reducer, check):
    """Magnitude STFT with centred frames, reduced chunk by chunk to save memory."""
    y = np.pad(y.astype(np.float32), (n_fft // 2, n_fft // 2))
    n_frames = 1 + (len(y) - n_fft) // hop
    window = np.hanning(n_fft + 1)[:-1].astype(np.float32)
    frames_view = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop]
    chunk = max(64, (1 << 21) // n_fft)
    out = []
    for s in range(0, n_frames, chunk):
        check()
        mag = np.abs(rfft(frames_view[s:s + chunk] * window, axis=1))
        out.append(reducer(mag))
    return np.concatenate(out, axis=0)


def _segment_means(values, bounds):
    """Mean of values[bounds[k]:bounds[k+1]] for every k (each span is non-empty)."""
    cs = np.concatenate([np.zeros((1,) + values.shape[1:]), np.cumsum(values, axis=0)])
    lengths = (bounds[1:] - bounds[:-1]).reshape((-1,) + (1,) * (values.ndim - 1))
    return (cs[bounds[1:]] - cs[bounds[:-1]]) / lengths


def _frame_bounds(beat_samples, hop, n_frames):
    """Frame index spans for beats given as analysis-rate sample boundaries."""
    b = np.clip(np.round(beat_samples / hop).astype(int), 0, n_frames)
    for k in range(1, len(b)):                 # guarantee at least one frame per beat
        b[k] = max(b[k], b[k - 1] + 1)
    overflow = b[-1] - n_frames
    if overflow > 0:
        b = np.minimum(b, n_frames)
        for k in range(len(b) - 2, -1, -1):
            b[k] = min(b[k], b[k + 1] - 1)
    return np.maximum(b, 0)


# ----------------------------------------------------------------------
# Beat tracking
# ----------------------------------------------------------------------
def onset_envelope(logmel):
    """Spectral flux of a log-mel spectrogram, mean-removed and non-negative."""
    lag = 2
    flux = np.maximum(0.0, logmel[lag:] - logmel[:-lag]).mean(axis=1)
    flux = np.concatenate([np.zeros(lag), flux])
    fps = ANALYSIS_SR / ONSET_HOP
    flux = flux - uniform_filter1d(flux, size=max(3, int(fps)), mode="nearest")
    return np.maximum(flux, 0.0)


def estimate_tempo(onset, fps, prior_bpm=120.0, min_bpm=40.0, max_bpm=240.0):
    """Global tempo from the autocorrelation of the onset envelope, with a log-normal prior."""
    x = onset - onset.mean()
    n = len(x)
    if n < 8 or not np.any(x):
        return prior_bpm
    spec = np.fft.rfft(x, 2 * n)
    acf = np.fft.irfft(np.abs(spec) ** 2)[:n]
    acf = acf / (acf[0] + 1e-12)
    min_lag = max(1, int(fps * 60.0 / max_bpm))
    max_lag = min(n - 2, int(fps * 60.0 / min_bpm))
    if max_lag <= min_lag + 2:
        return prior_bpm
    lags = np.arange(min_lag, max_lag + 1)
    bpm = 60.0 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpm / prior_bpm) / 1.0) ** 2)
    score = np.maximum(acf[lags], 0.0) * prior
    k = int(np.argmax(score))
    lag = float(lags[k])
    if 0 < k < len(score) - 1:                 # parabolic peak refinement
        a, b, c = score[k - 1], score[k], score[k + 1]
        denom = a - 2 * b + c
        if abs(denom) > 1e-12:
            lag += 0.5 * (a - c) / denom
    return 60.0 * fps / lag


def track_beats(onset, fps, bpm, tightness=100.0):
    """Dynamic-programming beat tracker (Ellis, 2007). Returns beat frame indices."""
    period = fps * 60.0 / bpm
    n = len(onset)
    std = onset.std()
    if n < 4 * period or std <= 1e-12:
        return np.arange(0, n, period).round().astype(int)

    half = int(round(period))
    win = np.arange(-half, half + 1)
    local = np.convolve(onset / std, np.exp(-0.5 * (win * 32.0 / period) ** 2), mode="same")

    lo, hi = int(round(-2 * period)), int(round(-period / 2))
    offsets = np.arange(lo, hi + 1)
    txcost = -tightness * np.log(-offsets / period) ** 2

    cum = np.zeros(n)
    backlink = np.full(n, -1)
    started = False
    start_level = 0.01 * local.max()
    for i in range(n):
        if not started and local[i] < start_level:
            cum[i] = local[i]                   # silence before the first beat
            continue
        started = True
        first, last = i + lo, i + hi            # inclusive range of previous-beat candidates
        if last < 0:
            cum[i] = local[i]
            continue
        s = max(0, first)
        cand = cum[s:last + 1] + txcost[s - first:]
        k = int(np.argmax(cand))
        cum[i] = local[i] + cand[k]
        backlink[i] = s + k

    # last beat: final local maximum of the cumulative score that is not weak
    is_max = np.zeros(n, dtype=bool)
    is_max[1:-1] = (cum[1:-1] > cum[:-2]) & (cum[1:-1] >= cum[2:])
    if not np.any(is_max):
        return np.arange(0, n, period).round().astype(int)
    med = np.median(cum[is_max])
    last = int(np.nonzero(is_max & (cum * 2 > med))[0].max())

    beats = [last]
    while backlink[beats[-1]] >= 0:
        beats.append(int(backlink[beats[-1]]))
    beats = np.array(beats[::-1])

    # trim weak beats at both ends
    strength = np.convolve(local[beats], np.hanning(5), mode="same")
    thresh = 0.5 * np.sqrt(np.mean(strength ** 2))
    strong = np.nonzero(strength > thresh)[0]
    if len(strong) >= 2:
        beats = beats[strong[0]:strong[-1] + 1]
    return beats


# ----------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------
def _pairwise_euclidean(x):
    sq = np.sum(x * x, axis=1)
    d2 = sq[:, None] + sq[None, :] - 2.0 * (x @ x.T)
    return np.sqrt(np.maximum(d2, 0.0))


def _robust_scale(d):
    off = d[~np.eye(len(d), dtype=bool)]
    med = float(np.median(off)) if off.size else 0.0
    return d / med if med > 1e-9 else d


def _beat_colors(timbre_z, chroma, loudness):
    """Map the main directions of variation of each beat's sound to hue / brightness."""
    feats = np.hstack([timbre_z, 2.0 * (chroma - chroma.mean(axis=0))])
    feats = feats - feats.mean(axis=0)
    n = len(feats)
    if n >= 3 and np.any(feats):
        _, _, vt = np.linalg.svd(feats, full_matrices=False)
        pcs = feats @ vt[:2].T
    else:
        pcs = np.zeros((n, 2))

    def unit(v):
        lo, hi = np.percentile(v, 2), np.percentile(v, 98)
        return np.clip((v - lo) / (hi - lo), 0, 1) if hi - lo > 1e-9 else np.full_like(v, 0.5)

    hue = 0.02 + 0.78 * unit(pcs[:, 0])
    sat = 0.55 + 0.35 * unit(pcs[:, 1])
    val = 0.60 + 0.40 * unit(loudness)
    # HSV -> RGB
    i = np.floor(hue * 6).astype(int) % 6
    f = hue * 6 - np.floor(hue * 6)
    p, q, t = val * (1 - sat), val * (1 - f * sat), val * (1 - (1 - f) * sat)
    table = np.stack([np.stack([val, t, p], 1), np.stack([q, val, p], 1), np.stack([p, val, t], 1),
                      np.stack([p, q, val], 1), np.stack([t, p, val], 1), np.stack([val, p, q], 1)])
    return table[i, np.arange(n)]


def analyze(audio, sr, progress=None):
    """
    Analyse a recording for the jukebox.

    audio    : (n,) or (n, channels) array - the playback audio
    sr       : its sample rate
    progress : optional callback(fraction, message). It is also called as (None, None)
               between expensive steps; raise AnalysisCancelled inside it to abort.
    """
    def report(frac, msg):
        if progress is not None:
            progress(frac, msg)

    def check():
        report(None, None)                      # lets the caller cancel between chunks

    audio = np.asarray(audio)
    n_samples = audio.shape[0]
    if n_samples < 4 * sr:
        raise ValueError("The track is too short. The jukebox needs at least 4 seconds of audio.")
    if n_samples > MAX_DURATION_SEC * sr:
        raise ValueError("The track is too long. The jukebox supports tracks up to 15 minutes.")

    report(0.02, "Preparing audio")
    y = resample(to_mono(audio).astype(np.float64), sr, ANALYSIS_SR)
    peak = np.max(np.abs(y))
    if peak < 1e-4:
        raise ValueError("The track is silent.")
    y = y / peak

    # --- log-mel spectrogram -> onsets and timbre ---
    report(0.08, "Computing spectrogram")
    fb = _mel_filterbank(ANALYSIS_SR, ONSET_N_FFT, N_MELS)
    mel = _stft_reduce(y, ONSET_N_FFT, ONSET_HOP, lambda m: (m * m) @ fb.T, check)
    power = mel.sum(axis=1)
    logmel = 10.0 * np.log10(np.maximum(mel, 1e-10))
    logmel = np.maximum(logmel, logmel.max() - 80.0)
    fps = ANALYSIS_SR / ONSET_HOP

    # --- beats ---
    report(0.30, "Detecting beats")
    onset = onset_envelope(logmel)
    bpm = estimate_tempo(onset, fps)
    beat_frames = track_beats(onset, fps, bpm)
    if len(beat_frames) >= 3:
        bpm = 60.0 * fps / float(np.median(np.diff(beat_frames)))
    check()

    beat_sec = beat_frames * ONSET_HOP / ANALYSIS_SR
    beat_samples = np.unique(np.round(beat_sec * sr).astype(np.int64))
    beat_samples = beat_samples[(beat_samples >= 0) & (beat_samples < n_samples)]
    min_len = int(0.1 * sr)
    kept = [int(beat_samples[0])] if len(beat_samples) else []
    for s in beat_samples[1:]:
        if s - kept[-1] >= min_len:
            kept.append(int(s))
    if len(kept) < 8:
        raise ValueError("Could not find enough beats in this track.")
    period = int(np.median(np.diff(kept)))
    end = min(n_samples, kept[-1] + period)
    starts = np.array(kept + [end], dtype=np.int64)
    n_beats = len(starts) - 1

    # beat spans expressed in frames of both spectrograms
    starts_a = starts * (ANALYSIS_SR / sr)
    onset_bounds = _frame_bounds(starts_a, ONSET_HOP, len(logmel))

    # --- timbre, loudness, rhythm ---
    report(0.45, "Describing beats")
    mfcc = dct(logmel, type=2, norm="ortho", axis=1)[:, 1:N_MFCC]
    timbre = _segment_means(mfcc, onset_bounds)
    frame_db = 10.0 * np.log10(np.maximum(power, 1e-10))
    loud_mean = _segment_means(frame_db, onset_bounds)
    loud_max = np.array([frame_db[a:b].max() for a, b in zip(onset_bounds[:-1], onset_bounds[1:])])

    onset_norm = onset / (onset.std() + 1e-9)
    rhythm = np.zeros((n_beats, RHYTHM_BINS))
    frame_idx = np.arange(len(onset_norm))
    for k in range(n_beats):
        pos = np.linspace(starts_a[k], starts_a[k + 1], RHYTHM_BINS, endpoint=False) / ONSET_HOP
        rhythm[k] = np.interp(pos, frame_idx, onset_norm)
    durations = np.diff(starts).astype(float)

    # --- pitch content ---
    report(0.55, "Measuring pitch content")
    bins, classes = _chroma_map(ANALYSIS_SR, CHROMA_N_FFT)
    onehot = np.zeros((12, len(bins)), dtype=np.float32)
    onehot[classes, np.arange(len(bins))] = 1.0

    def chroma_reduce(mag):
        c = mag[:, bins] @ onehot.T
        return c / (c.max(axis=1, keepdims=True) + 1e-9)

    chroma_frames = _stft_reduce(y, CHROMA_N_FFT, CHROMA_HOP, chroma_reduce, check)
    chroma_bounds = _frame_bounds(starts_a, CHROMA_HOP, len(chroma_frames))
    chroma = _segment_means(chroma_frames, chroma_bounds)
    chroma = chroma / (np.linalg.norm(chroma, axis=1, keepdims=True) + 1e-9)

    # --- beat-to-beat distances ---
    report(0.70, "Comparing beats")
    timbre_z = (timbre - timbre.mean(axis=0)) / (timbre.std(axis=0) + 1e-6)
    rhythm_z = (rhythm - rhythm.mean(axis=0)) / (rhythm.std(axis=0) + 1e-6)
    groups = {                                   # built one at a time to limit memory
        "timbre": lambda: _robust_scale(_pairwise_euclidean(timbre_z)),
        "pitch": lambda: _robust_scale(np.maximum(0.0, 1.0 - chroma @ chroma.T)),
        "loudness": lambda: _robust_scale(np.abs(loud_mean[:, None] - loud_mean[None, :])
                                          + 0.5 * np.abs(loud_max[:, None] - loud_max[None, :])),
        "rhythm": lambda: _robust_scale(_pairwise_euclidean(rhythm_z)),
        # absolute scale: a 5 % length difference counts as one unit
        "duration": lambda: np.abs(np.log(durations[:, None] / durations[None, :])) / 0.05,
    }
    dist = np.zeros((n_beats, n_beats), dtype=np.float32)
    for name, compute in groups.items():
        dist += np.float32(FEATURE_WEIGHTS[name]) * compute().astype(np.float32)
        check()
    dist /= np.float32(sum(FEATURE_WEIGHTS.values()))

    # --- context-aware transition cost ---
    report(0.85, "Finding similar beats")
    pair_cost = _context_cost(dist)
    valid = _valid_pair_mask(n_beats, min_gap=1)
    median_cost = float(np.median(pair_cost[valid])) if np.any(valid) else 1.0

    report(0.95, "Colouring beats")
    colors = _beat_colors(timbre_z, chroma, loud_mean)

    report(1.0, "Done")
    return BeatAnalysis(sr=int(sr), n_samples=int(n_samples), tempo=float(bpm), starts=starts,
                        colors=colors, loudness=loud_mean, pair_cost=pair_cost,
                        median_cost=max(median_cost, 1e-9))


def _context_cost(dist):
    """
    pair_cost[a, b] = weighted mean over k of dist[a - 1 + k, b - 1 + k].

    A jump from src = a - 1 to dst = b is cheap when the beats around the jump
    point line up: what was just played resembles what normally precedes dst,
    and dst resembles what would have come next.
    """
    n = len(dist)
    total = np.zeros((n, n), dtype=np.float32)
    weight = np.zeros((n, n), dtype=np.float32)
    for k, w in zip(CONTEXT_OFFSETS, CONTEXT_WEIGHTS):
        o = k - 1                                    # offset relative to (a, b)
        w = np.float32(w)
        if o >= 0:
            if o >= n:
                continue
            total[:n - o, :n - o] += w * dist[o:, o:]
            weight[:n - o, :n - o] += w
        else:
            if -o >= n:
                continue
            total[-o:, -o:] += w * dist[:n + o, :n + o]
            weight[-o:, -o:] += w
    return total / np.maximum(weight, np.float32(1e-6))


def _valid_pair_mask(n, min_gap):
    a = np.arange(n)[:, None]
    b = np.arange(n)[None, :]
    return (a >= 1) & (b >= 1) & (np.abs(a - b) >= min_gap)


# ----------------------------------------------------------------------
# Branch graph
# ----------------------------------------------------------------------
MIN_QUANTILE = 0.004
MAX_QUANTILE = 0.10


def sensitivity_to_quantile(value):
    """Map a 0..100 slider value to the share of beat pairs accepted as branches."""
    return MIN_QUANTILE * (MAX_QUANTILE / MIN_QUANTILE) ** (np.clip(value, 0, 100) / 100.0)


def build_graph(analysis, quantile=0.02, max_branches=4, min_gap=None, min_coverage=0.25):
    """
    Turn the transition costs into a branch graph that can be played forever.

    If the requested threshold yields no usable loop (or one covering less than
    `min_coverage` of the song), the threshold is relaxed step by step.
    """
    n = analysis.n_beats
    if min_gap is None:
        min_gap = int(np.clip(round(n * 0.02), 4, 16))
    cost = analysis.pair_cost
    valid = _valid_pair_mask(n, min_gap)
    if not np.any(valid):
        return JukeboxGraph(n, [[] for _ in range(n)], None, None, quantile, quantile, 0.0, 0.0)
    valid_costs = cost[valid]

    # local minima along each row, so a run of neighbouring matches yields one branch
    masked = np.where(valid, cost, np.inf)
    padded = np.pad(masked, ((0, 0), (2, 2)), constant_values=np.inf)
    is_min = np.ones_like(valid)
    for s in (-2, -1, 1, 2):
        neighbour = padded[:, 2 + s:2 + s + n]
        is_min &= masked <= neighbour if s > 0 else masked < neighbour
    candidates = valid & is_min

    q = float(np.clip(quantile, MIN_QUANTILE, MAX_QUANTILE))
    requested = q
    while True:
        threshold = float(np.quantile(valid_costs, q))
        graph = _graph_for_threshold(analysis, candidates, threshold, max_branches)
        graph.quantile, graph.requested_quantile = q, requested
        good = graph.has_loop and graph.loop_coverage() >= min_coverage
        if good or q >= MAX_QUANTILE:
            return graph
        q = min(MAX_QUANTILE, q * 1.5)


def _graph_for_threshold(analysis, candidates, threshold, max_branches):
    n = analysis.n_beats
    cost = analysis.pair_cost
    med = analysis.median_cost
    rows, cols = np.nonzero(candidates & (cost <= threshold))
    costs = cost[rows, cols]

    # keep the `max_branches` cheapest candidates of every row
    order = np.lexsort((costs, rows))
    rows, cols, costs = rows[order], cols[order], costs[order]
    rank = np.arange(len(rows)) - np.searchsorted(rows, rows, side="left")
    keep = rank < max_branches
    rows, cols, costs = rows[keep], cols[keep], costs[keep]

    per_src = [[] for _ in range(n)]
    for a, b, c in zip(rows.tolist(), cols.tolist(), costs.tolist()):
        per_src[a - 1].append(Branch(src=a - 1, dst=b, cost=c,
                                     similarity=min(1.0, max(0.0, 1.0 - c / med))))

    # strongly connected region: beats from which playback can always return
    src = np.concatenate([np.arange(n - 1), rows - 1])
    dst = np.concatenate([np.arange(1, n), cols])
    adj = csr_matrix((np.ones(len(src), dtype=np.int8), (src, dst)), shape=(n, n))
    n_comp, labels = connected_components(adj, directed=True, connection="strong")
    sizes = np.bincount(labels, minlength=n_comp)
    biggest = int(np.argmax(sizes))
    loop_start = loop_end = None
    if sizes[biggest] > 1:
        members = np.nonzero(labels == biggest)[0]
        loop_start, loop_end = int(members.min()), int(members.max())
        # (a strongly connected set that includes the sequential edges is contiguous)
        for lst in per_src:
            lst[:] = [br for br in lst
                      if loop_start <= br.src <= loop_end and loop_start <= br.dst <= loop_end]
    else:
        per_src = [[] for _ in range(n)]

    kept = [br.cost for lst in per_src for br in lst]
    structure = float(np.clip(1.0 - np.median(kept) / med, 0.0, 1.0)) if kept else 0.0
    return JukeboxGraph(n_beats=n, branches=per_src, loop_start=loop_start, loop_end=loop_end,
                        quantile=0.0, requested_quantile=0.0, threshold=threshold,
                        structure=structure)


# ----------------------------------------------------------------------
# Route planning
# ----------------------------------------------------------------------
class RoutePlanner:
    """
    Chooses the next beat. Continuing is the default; a branch is taken with a
    probability that grows while no jump has happened, and is forced at the last
    beat of the loop region so the song never reaches its ending.
    """

    def __init__(self, graph, adventure=0.5, seed=None):
        self.graph = graph
        self.rng = np.random.default_rng(seed)
        self.recent = deque(maxlen=48)
        self.edge_uses = Counter()
        self.beats_since_jump = 0
        self.set_adventure(adventure)

    def set_adventure(self, adventure):
        a = float(np.clip(adventure, 0.0, 1.0))
        self.adventure = a
        self.min_chance = 0.02 + 0.20 * a
        self.max_chance = 0.25 + 0.60 * a
        self.chance_step = 0.004 + 0.030 * a
        self.min_dwell = int(round(8 - 6 * a))

    def reset(self):
        self.recent.clear()
        self.beats_since_jump = 0

    def jump_chance(self):
        return min(self.max_chance, self.min_chance + self.chance_step * self.beats_since_jump)

    def next_beat(self, current):
        """Return (next_beat, branch_taken_or_None); next_beat is None at the end of the song."""
        g = self.graph
        self.recent.append(current)
        options = g.branches[current] if 0 <= current < g.n_beats and g.in_loop(current) else []
        forced = bool(options) and current == g.loop_end

        if options:
            weights = self._weights(options)
            wants = False
            if not forced and self.beats_since_jump >= self.min_dwell:
                appeal = float(weights.max())
                wants = self.rng.random() < self.jump_chance() * appeal
            if forced or wants:
                k = int(self.rng.choice(len(options), p=weights / weights.sum()))
                branch = options[k]
                self.edge_uses[(branch.src, branch.dst)] += 1
                self.beats_since_jump = 0
                return branch.dst, branch

        self.beats_since_jump += 1
        if current + 1 >= g.n_beats:
            return None, None
        return current + 1, None

    def _weights(self, options):
        best = max(br.similarity for br in options)
        w = np.array([np.exp(6.0 * (br.similarity - best)) for br in options])
        for i, br in enumerate(options):
            if br.dst in self.recent:
                w[i] *= 0.2                        # do not bounce straight back
            w[i] /= np.sqrt(1.0 + self.edge_uses[(br.src, br.dst)])
        # overall appeal scales with how good the best match is
        return np.maximum(w, 1e-6) * (0.5 + 0.5 * best)


# ----------------------------------------------------------------------
# Audio rendering
# ----------------------------------------------------------------------
PREROLL = -1          # pseudo beat: audio before the first beat
OUTRO = -2            # pseudo beat: audio after the last beat


@dataclass
class BeatEvent:
    frame: int                  # stream position (in frames) where this segment starts
    beat: int                   # beat index, or PREROLL / OUTRO
    branch: Branch | None = None  # set when this beat was reached by a jump


class BeatStream:
    """Renders the planner's route as a continuous stream of frames."""

    def __init__(self, audio, analysis, planner, crossfade_ms=30.0):
        a = np.asarray(audio, dtype=np.float32)
        self.audio = a[:, None] if a.ndim == 1 else a
        self.analysis = analysis
        self.planner = planner
        self.channels = self.audio.shape[1]
        self.xfade = max(1, int(analysis.sr * crossfade_ms / 1000.0))
        ramp = np.linspace(0.0, np.pi / 2, self.xfade, dtype=np.float32)
        self._fade_in = np.sin(ramp)[:, None]
        self._fade_out = np.cos(ramp)[:, None]
        self.events = deque()
        self.reset()

    def reset(self, start_beat=None):
        self._chunks = deque()
        self._buffered = 0
        self._generated = 0
        self.events.clear()
        self.finished = False
        self._next = PREROLL if start_beat is None else int(start_beat)
        self._next_branch = None               # branch by which self._next is reached
        self.planner.reset()

    def _slice(self, start, length):
        out = np.zeros((length, self.channels), dtype=np.float32)
        s = max(0, start)
        e = min(len(self.audio), start + length)
        if e > s:
            out[s - start:e - start] = self.audio[s:e]
        return out

    def _render_segment(self):
        starts = self.analysis.starts
        beat, arrived_by = self._next, self._next_branch
        if beat == PREROLL:
            seg = self.audio[:starts[0]]
            self._next, self._next_branch = 0, None
        elif beat == OUTRO:
            seg = self.audio[starts[-1]:]
            self.finished = True
        else:
            seg = self.audio[starts[beat]:starts[beat + 1]]
            if arrived_by is not None:
                # crossfade from the natural continuation into the jump target
                length = min(self.xfade, len(seg))
                natural = self._slice(int(starts[arrived_by.src + 1]), length)
                seg = seg.copy()
                seg[:length] = seg[:length] * self._fade_in[:length] + natural * self._fade_out[:length]
            nxt, branch = self.planner.next_beat(beat)
            self._next = OUTRO if nxt is None else nxt
            self._next_branch = branch

        if len(seg):
            self.events.append(BeatEvent(frame=self._generated, beat=beat, branch=arrived_by))
            self._chunks.append(seg)
            self._buffered += len(seg)
            self._generated += len(seg)

    def read(self, frames):
        """Return exactly `frames` frames (zero padded once the stream has finished)."""
        while self._buffered < frames and not self.finished:
            self._render_segment()
        out = np.zeros((frames, self.channels), dtype=np.float32)
        pos = 0
        while pos < frames and self._chunks:
            chunk = self._chunks[0]
            take = min(frames - pos, len(chunk))
            out[pos:pos + take] = chunk[:take]
            pos += take
            if take == len(chunk):
                self._chunks.popleft()
            else:
                self._chunks[0] = chunk[take:]
        self._buffered -= pos
        return out

    @property
    def generated_frames(self):
        return self._generated

    @property
    def exhausted(self):
        """True once the song has ended and every rendered frame has been read."""
        return self.finished and self._buffered == 0

    def consume_events(self, played_frame):
        """Pop and return every event whose segment has started by `played_frame`."""
        out = []
        while self.events and self.events[0].frame <= played_frame:
            out.append(self.events.popleft())
        return out


def to_int16_bytes(frames):
    return (np.clip(frames, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
