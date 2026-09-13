"""
Tests for the Infinite Jukebox engine.

Run with:  python -m pytest test_jukebox.py -q
"""

import os

import numpy as np
import pytest
import soundfile as sf

import jukebox_engine as je

SR = 44100
HERE = os.path.dirname(os.path.abspath(__file__))

# chord roots (Hz) and qualities per section, one chord per bar
_A3, _C4, _D4, _E4, _F3, _G3 = 220.0, 261.63, 293.66, 329.63, 174.61, 196.0
SECTIONS = {
    "A": {"chords": [(_A3, "m"), (_F3, "M"), (_C4, "M"), (_G3, "M")], "harmonics": 3, "melody": [0, 3, 7, 5]},
    "B": {"chords": [(_D4, "m"), (_G3, "M"), (_E4, "m"), (_A3, "m")], "harmonics": 7, "melody": [7, 5, 3, 0]},
    "C": {"chords": [(_F3, "M"), (_G3, "M"), (_A3, "m"), (_A3, "m")], "harmonics": 1, "melody": [12, 10, 7, 12]},
}


def _chord(root, quality, t, harmonics):
    third = 2 ** ((3 if quality == "m" else 4) / 12)
    out = np.zeros_like(t)
    for ratio in (1.0, third, 1.5):
        for h in range(1, harmonics + 1):
            out += np.sin(2 * np.pi * root * ratio * h * t) / h
    return out


def make_song(structure="ABABCA", bars_per_section=4, bpm=110.0, sr=SR, seed=0, jitter_ms=4.0):
    """Synthesised 4/4 song. Returns (audio, true_beat_times, labels) with labels = (section, bar, beat)."""
    rng = np.random.default_rng(seed)
    beat_len = 60.0 / bpm
    n_bars = len(structure) * bars_per_section
    lead_in = 0.5
    total = lead_in + n_bars * 4 * beat_len + 1.0
    audio = np.zeros(int(total * sr))
    beat_times, labels = [], []

    def add(sig, at):
        i = int(at * sr)
        j = min(len(audio), i + len(sig))
        if j > i:
            audio[i:j] += sig[: j - i]

    for bar in range(n_bars):
        section = structure[bar // bars_per_section]
        bar_in_section = bar % bars_per_section
        spec = SECTIONS[section]
        root, quality = spec["chords"][bar_in_section]
        bar_start = lead_in + bar * 4 * beat_len

        # sustained chord + bass for the whole bar
        t = np.arange(int(4 * beat_len * sr)) / sr
        env = np.minimum(1.0, t / 0.02) * np.exp(-t * 0.6)
        add(0.10 * env * _chord(root, quality, t, spec["harmonics"]), bar_start)
        add(0.20 * env * np.sin(2 * np.pi * root / 2 * t), bar_start)

        for beat in range(4):
            bt = bar_start + beat * beat_len
            beat_times.append(bt)
            labels.append((section, bar_in_section, beat))
            at = bt + rng.normal(0, jitter_ms / 1000.0)
            gain = 1.0 + rng.normal(0, 0.05)
            tt = np.arange(int(0.25 * sr)) / sr
            if beat in (0, 2):              # kick
                sweep = 2 * np.pi * np.cumsum(50 + 90 * np.exp(-tt * 30)) / sr
                add(0.9 * gain * np.sin(sweep) * np.exp(-tt * 18), at)
            else:                           # snare
                noise = rng.standard_normal(len(tt))
                add(0.35 * gain * noise * np.exp(-tt * 25), at)
            for half in (0.0, 0.5):         # hi-hat on eighths
                h = rng.standard_normal(int(0.04 * sr))
                h = np.diff(h, prepend=0.0) * np.exp(-np.arange(len(h)) / sr * 90)
                add(0.08 * gain * h, at + half * beat_len)
            # melody note on each beat
            semis = spec["melody"][(bar_in_section + beat) % 4] + 12
            nt = np.arange(int(beat_len * 0.9 * sr)) / sr
            add(0.12 * np.sin(2 * np.pi * root * 2 ** (semis / 12) * nt) * np.exp(-nt * 6), at)

    audio += 0.003 * rng.standard_normal(len(audio))
    audio /= np.max(np.abs(audio)) * 1.1
    return audio.astype(np.float32), np.array(beat_times), labels


def make_clicks(bpm, seconds, sr=SR, offset=0.3):
    audio = np.zeros(int(seconds * sr), dtype=np.float32)
    period = 60.0 / bpm
    times = np.arange(offset, seconds - 0.2, period)
    click = (np.sin(2 * np.pi * 1000 * np.arange(int(0.02 * sr)) / sr)
             * np.exp(-np.arange(int(0.02 * sr)) / sr * 200)).astype(np.float32)
    for k, t in enumerate(times):
        i = int(t * sr)
        audio[i:i + len(click)] += click * (1.0 if k % 4 == 0 else 0.6)
    return audio, times


def make_through_composed(seconds=90.0, bpm=110.0, sr=SR, seed=1):
    """Music that never repeats: every beat has a new pitch, timbre and envelope."""
    rng = np.random.default_rng(seed)
    beat_len = 60.0 / bpm
    audio = np.zeros(int(seconds * sr))
    t_all = np.arange(len(audio)) / sr
    pos = 0.3
    while pos < seconds - 2:
        n = int(beat_len * sr)
        tt = np.arange(n) / sr
        f = 110 * 2 ** (rng.random() * 3)
        sig = sum(np.sin(2 * np.pi * f * k * tt) / k for k in range(1, rng.integers(2, 9)))
        sig = sig * np.exp(-tt * rng.uniform(1, 8))
        if rng.random() < 0.7:
            sig += rng.standard_normal(n) * np.exp(-tt * rng.uniform(10, 40)) * rng.uniform(0.1, 0.6)
        i = int(pos * sr)
        audio[i:i + n] += 0.3 * sig
        pos += beat_len
    audio += 0.1 * np.sin(2 * np.pi * (200 + 300 * t_all / seconds) * t_all)
    return (audio / np.max(np.abs(audio)) * 0.9).astype(np.float32)


@pytest.fixture(scope="module")
def song():
    audio, beat_times, labels = make_song()
    analysis = je.analyze(audio, SR)
    return audio, beat_times, labels, analysis


def _match_beats(detected, truth, tol):
    idx = np.clip(np.searchsorted(truth, detected), 1, len(truth) - 1)
    nearest = np.where(np.abs(truth[idx - 1] - detected) < np.abs(truth[idx] - detected), idx - 1, idx)
    err = detected - truth[nearest]
    return nearest, err, np.abs(err) <= tol


# ----------------------------------------------------------------------
# Beat detection
# ----------------------------------------------------------------------
@pytest.mark.parametrize("bpm", [85.0, 120.0, 150.0])
def test_click_track_tempo_and_beat_positions(bpm):
    audio, truth = make_clicks(bpm, 30.0)
    analysis = je.analyze(audio, SR)
    assert analysis.tempo == pytest.approx(bpm, rel=0.02)
    detected = analysis.starts[:-1] / SR
    _, err, ok = _match_beats(detected, truth, tol=0.03)
    assert ok.mean() > 0.95
    assert len(detected) >= 0.9 * len(truth)


def test_fixture_file_is_detected_at_120_bpm():
    path = os.path.join(HERE, "test_120bpm.wav")
    if not os.path.exists(path):
        pytest.skip("test_120bpm.wav not present")
    audio, sr = sf.read(path)
    analysis = je.analyze(audio, sr)
    assert analysis.tempo == pytest.approx(120.0, rel=0.03)


def test_song_beats_line_up_with_the_music(song):
    _, truth, _, analysis = song
    detected = analysis.starts[:-1] / SR
    _, err, ok = _match_beats(detected, truth, tol=0.035)
    assert analysis.tempo == pytest.approx(110.0, rel=0.02)
    assert ok.mean() > 0.95
    assert len(detected) >= 0.95 * len(truth)


def test_analysis_rejects_unusable_audio():
    with pytest.raises(ValueError):
        je.analyze(np.zeros(SR * 10, dtype=np.float32), SR)        # silence
    with pytest.raises(ValueError):
        je.analyze(np.random.default_rng(0).standard_normal(SR), SR)  # too short


def test_stereo_input_is_supported():
    audio, truth = make_clicks(120.0, 12.0)
    stereo = np.stack([audio, 0.5 * audio], axis=1)
    analysis = je.analyze(stereo, SR)
    assert analysis.tempo == pytest.approx(120.0, rel=0.02)


# ----------------------------------------------------------------------
# Similar beats / branches
# ----------------------------------------------------------------------
def test_branches_connect_equivalent_beats_of_repeated_sections(song):
    _, truth, labels, analysis = song
    graph = je.build_graph(analysis, quantile=0.02)
    detected = analysis.starts[:-1] / SR
    nearest, _, ok = _match_beats(detected, truth, tol=0.05)
    assert graph.n_branches >= 20

    correct = total = 0
    for br in graph.all_branches():
        # jumping src -> dst replaces beat src+1 with dst, so those two should be equivalent
        a, b = br.src + 1, br.dst
        if not (ok[a] and ok[b]):
            continue
        total += 1
        correct += labels[nearest[a]] == labels[nearest[b]]
    assert total >= 20
    assert correct / total >= 0.8, f"only {correct}/{total} branches join equivalent beats"


def test_structure_is_rated_higher_for_repetitive_music(song):
    strong = je.build_graph(song[3], quantile=0.02)
    weak = je.build_graph(je.analyze(make_through_composed(), SR), quantile=0.02)
    assert strong.structure > weak.structure + 0.3
    assert strong.structure_label() == "Strong"
    assert weak.structure_label() != "Strong"


def test_graph_always_has_an_exit_inside_the_loop(song):
    analysis = song[3]
    for q in (je.MIN_QUANTILE, 0.01, 0.05):
        graph = je.build_graph(analysis, quantile=q)
        assert graph.has_loop
        # every branch stays inside the loop region
        for br in graph.all_branches():
            assert graph.in_loop(br.src) and graph.in_loop(br.dst)
            assert abs(br.src + 1 - br.dst) >= 4
        # the last loop beat must be able to jump back
        assert graph.branches[graph.loop_end]
        assert all(br.dst <= graph.loop_end for br in graph.branches[graph.loop_end])


def test_threshold_is_relaxed_when_no_loop_exists(song):
    analysis = song[3]
    graph = je.build_graph(analysis, quantile=je.MIN_QUANTILE, min_coverage=0.9)
    assert graph.quantile >= graph.requested_quantile


# ----------------------------------------------------------------------
# Playback route
# ----------------------------------------------------------------------
def _simulate(graph, beats, seed, adventure=0.5):
    planner = je.RoutePlanner(graph, adventure=adventure, seed=seed)
    route, jumps, current = [0], 0, 0
    for _ in range(beats):
        nxt, branch = planner.next_beat(current)
        assert nxt is not None, "playback reached the end of the song"
        jumps += branch is not None
        route.append(nxt)
        current = nxt
    return np.array(route), jumps


def test_playback_continues_indefinitely(song):
    analysis = song[3]
    graph = je.build_graph(analysis)
    route, jumps = _simulate(graph, 20000, seed=1)
    assert route.max() <= graph.loop_end
    assert jumps > 100
    visited = np.unique(route[route >= graph.loop_start])
    assert len(visited) / (graph.loop_end - graph.loop_start + 1) > 0.9


def test_route_varies_and_is_not_a_fixed_loop(song):
    analysis = song[3]
    graph = je.build_graph(analysis)
    route_a, _ = _simulate(graph, 4000, seed=1)
    route_b, _ = _simulate(graph, 4000, seed=2)
    assert not np.array_equal(route_a, route_b)

    tail = route_a[-2000:]
    for period in range(1, 1000):
        assert not np.array_equal(tail[period:], tail[:-period]), f"route repeats every {period} beats"

    planner = je.RoutePlanner(graph, seed=7)
    destinations = set()
    current = 0
    for _ in range(4000):
        nxt, branch = planner.next_beat(current)
        if branch is not None:
            destinations.add((branch.src, branch.dst))
        current = nxt
    assert len(destinations) >= 10


def test_adventure_controls_jump_rate(song):
    graph = je.build_graph(song[3])
    _, calm = _simulate(graph, 6000, seed=4, adventure=0.0)
    _, wild = _simulate(graph, 6000, seed=4, adventure=1.0)
    assert wild > calm * 1.5


# ----------------------------------------------------------------------
# Audio rendering
# ----------------------------------------------------------------------
def test_stream_renders_route_with_crossfaded_jumps(song):
    audio, _, _, analysis = song
    graph = je.build_graph(analysis)
    planner = je.RoutePlanner(graph, adventure=1.0, seed=11)
    stream = je.BeatStream(audio, analysis, planner, crossfade_ms=30.0)

    seconds = 120
    out = np.concatenate([stream.read(4410) for _ in range(seconds * 10)])
    assert out.shape == (seconds * SR, 1)
    assert not stream.finished

    events = list(stream.events)
    starts = analysis.starts
    jumps = [e for e in events if e.branch is not None]
    assert len(jumps) > 10
    for prev, ev in zip(events, events[1:]):
        # each segment is exactly one beat long (or the pre-roll)
        length = ev.frame - prev.frame
        expected = starts[0] if prev.beat == je.PREROLL else starts[prev.beat + 1] - starts[prev.beat]
        assert length == expected
        if ev.branch is None and prev.beat >= 0:
            assert ev.beat == prev.beat + 1
        if ev.branch is not None:
            assert ev.branch.src == prev.beat and ev.branch.dst == ev.beat

    # away from the crossfade, the rendered beat is the original audio
    for ev in jumps[:5]:
        s, e = starts[ev.beat], starts[ev.beat + 1]
        xf = stream.xfade
        if ev.frame + (e - s) <= len(out):
            np.testing.assert_allclose(out[ev.frame + xf:ev.frame + (e - s), 0], audio[s + xf:e], atol=1e-6)


def test_crossfade_removes_clicks_at_jump_points():
    sr = 8000
    t = np.arange(sr * 8) / sr
    audio = np.sin(2 * np.pi * 110.3 * t).astype(np.float32)   # jump spans a non-integer number of cycles
    starts = np.arange(0, len(audio) + 1, 2000)
    starts[-1] = len(audio)
    n = len(starts) - 1
    analysis = je.BeatAnalysis(sr=sr, n_samples=len(audio), tempo=240.0, starts=starts,
                               colors=np.zeros((n, 3)), loudness=np.zeros(n),
                               pair_cost=np.ones((n, n), np.float32), median_cost=1.0)
    branch = je.Branch(src=10, dst=3, cost=0.1, similarity=0.9)
    branches = [[] for _ in range(n)]
    branches[10] = [branch]
    graph = je.JukeboxGraph(n, branches, 3, 10, 0.02, 0.02, 0.1, 0.9)

    def render(ms):
        stream = je.BeatStream(audio, analysis, je.RoutePlanner(graph, seed=0), crossfade_ms=ms)
        stream.reset(start_beat=9)
        return stream.read(2000 * 3)[:, 0]

    # the sine phase at beat 3 differs from beat 11, so a hard cut is a discontinuity
    hard, soft = render(0.001), render(20.0)
    step = lambda x: np.max(np.abs(np.diff(x[3990:4010])))
    assert step(hard) > 3 * step(soft)
    assert step(soft) <= np.max(np.abs(np.diff(audio))) * 1.5


def test_stream_without_loop_plays_to_the_end():
    audio, _ = make_clicks(120.0, 6.0)
    analysis = je.analyze(audio, SR)
    n = analysis.n_beats
    graph = je.JukeboxGraph(n, [[] for _ in range(n)], None, None, 0.02, 0.02, 0.0, 0.0)
    stream = je.BeatStream(audio, analysis, je.RoutePlanner(graph), crossfade_ms=20)
    out = np.concatenate([stream.read(SR) for _ in range(8)])
    assert stream.finished
    np.testing.assert_allclose(out[:len(audio), 0], audio, atol=1e-6)
    assert np.all(out[len(audio):] == 0)


def test_int16_conversion_interleaves_and_clips():
    frames = np.array([[0.5, -0.5], [2.0, -2.0]], dtype=np.float32)
    data = np.frombuffer(je.to_int16_bytes(frames), dtype="<i2")
    assert list(data) == [16383, -16383, 32767, -32767]
