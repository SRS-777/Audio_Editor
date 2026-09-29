/* =========================================================
   BEAT DETECTION IN THE BROWSER

   extract_beats.py finds the beats in a track before the game
   runs. That is fine for the bundled song and for whatever the
   audio editor hands over, but it means playing your own music
   needs Python, a terminal and a rebuild — so in practice
   nobody plays their own music. This does it in the browser, on
   a file the player drops on the page.

   WHY THIS IS NOT A PORT OF THE PYTHON

   The Python low-passes to 150 Hz, takes the energy envelope of
   what is left, and calls the rises beats. Ported faithfully,
   that found 73 BPM in the bundled 120 BPM track, and 147 very
   irregular beats where the Python found 317 even ones.

   The reason turned out to be a coincidence. Its filter is 101
   taps long, and 101 taps at 44.1 kHz cannot realise a 150 Hz
   cutoff — measured, that filter's -3 dB point is at 296 Hz, so
   it passes the low mids and with them the snare. At 8 kHz,
   where the analysis here has to run, the same 101 taps do
   realise about 118 Hz, the snare goes, and what is left is a
   kick that this track does not state clearly enough to
   autocorrelate. The Python works because its filter is not the
   filter it claims to be, which is not something to build on.

   So the onset detector is a different one:

     1  a short-time Fourier transform, and the frame to frame
        rise in log magnitude summed over every bin — spectral
        flux. Every percussive sound in the mix contributes,
        rather than only what survives one low-pass, and taking
        the rise in log magnitude means a hi-hat over a quiet
        passage counts as much as a kick over a loud one
     2  the flux, less its own local average, which removes the
        slow swell of the track and leaves the events
     3  the tempo, from the autocorrelation of that — but scored
        with a harmonic comb rather than by taking the largest
        peak, because the largest peak is very often at twice or
        two thirds of the beat
     4  the phase, by sliding a pulse train at that tempo over
        the flux and taking the offset that collects most
     5  beats on that grid, dropped where the track is quiet, so
        an intro or a breakdown does not fill with blocks

   Steps 3 and 5 are what the game needs and the Python does not
   provide: blocks are placed by bar position, so the beats have
   to be an even grid rather than whenever a drum happened to be
   loud.

   detectBeats works on plain samples and knows nothing about
   the browser, so it can be run and checked outside one.
   ========================================================= */

/*
    The rate the analysis runs at.

    Percussion that marks the beat reaches well up the
    spectrum — a hi-hat lives above 8 kHz — so unlike a
    kick-only envelope this wants more than the minimum. 22.05
    kHz keeps everything up to 11 kHz, which covers every
    percussive cue that matters, at a quarter of the samples of
    full rate.
*/
const ANALYSIS_RATE = 22050;

/*
    The transform window, in seconds. Long enough to resolve
    pitch, short enough not to smear a drum hit. The hop is a
    quarter of it, which puts a frame every 12 ms or so — finer
    than the timing the game judges to.

    These are given as durations rather than sample counts
    because the sample count that realises them depends on the
    rate. Fixing the counts instead is the mistake the Python
    makes with its filter: 1024 samples is a 46 ms window at
    22 kHz and a 128 ms one at 8 kHz, and at 128 ms a drum hit
    is no longer an event but a smear. Measured, a detector
    fixed at 1024 samples recovered every click track at 22 kHz
    and failed half of them at 8 kHz, with tempo errors up to
    64 BPM.
*/
const FRAME_SECONDS = 0.046;

/*
    The window in samples, rounded to a power of two because the
    transform is radix-2.
*/
function frameSizeFor(sampleRate) {
    const wanted = FRAME_SECONDS * sampleRate;

    const size = 1 << Math.round(Math.log2(wanted));

    /*
        Below 256 the spectrum is too coarse to see a note
        begin; above 4096 the window is long enough to blur one.
    */
    return Math.min(4096, Math.max(256, size));
}

/*
    The flux is compared against its own average over a window
    this long. Long enough to span a bar or two, so it measures
    "louder than the music around it" rather than "louder than
    the chorus".
*/
const FLUX_CONTEXT_SECONDS = 1.5;

/*
    The tempo range searched, in seconds per beat — 60 to 200
    BPM. Wider than the Python's 60 to 180, because the comb
    scoring below is what prevents octave errors, not the range.
*/
const MIN_PERIOD = 0.3;
const MAX_PERIOD = 1.0;

/*
    Multiples of a candidate period whose autocorrelation is
    added into its score.

    This is the part that fixes the octave errors. A true beat
    period repeats at every multiple of itself, so the
    correlation is high at one, two, three and four periods. A
    spurious period that merely happens to catch a lot of
    onsets — two thirds of the real one, say — matches at its
    own multiples much less consistently. Summing the comb
    rewards the period that explains the whole track.

    The weights fall off because a longer lag is measured over
    less of the track and is noisier for it.
*/
const COMB_HARMONICS = [
    { multiple: 1, weight: 1.0 },
    { multiple: 2, weight: 0.8 },
    { multiple: 3, weight: 0.5 },
    { multiple: 4, weight: 0.35 }
];

/*
    CHOOSING BETWEEN TEMPO CANDIDATES.

    The comb score says which lag best explains the onsets, and
    on its own it is not enough. On one track it preferred
    120 BPM over the 90 the song is actually in — 120 is not
    even a harmonic of 90, and a grid at it scattered 72 ms
    around the drums where a grid at 180 sat within 11.

    So the comb now proposes and something else decides. Each
    candidate gets a grid, and the grid is measured on two
    things the comb cannot see:

      lock       the scatter of the grid against the onset peaks,
                 after any steady drift is taken out. This is
                 the question the comb should have been asked:
                 not "does this period recur" but "do the beats
                 land on the drums"
      explained  the share of onset peaks that some grid beat
                 accounts for. This is what separates a tempo
                 from half of it — both sit on real beats, but
                 the slower one ignores every other one, and a
                 metronome gives the comb no other way to tell

    Measured across nine tracks, the tempo with the tightest
    lock was the right one every time, and where two locked
    equally well the one explaining more peaks was the beat
    rather than half of it.
*/

/*
    A candidate is in contention if its lock is close to the best
    on offer. The floor is there because below about 15 ms the
    differences are the analysis frame rate rather than the
    music, and splitting hairs at that level would pick a tempo
    on noise.
*/
const LOCK_FLOOR_MS = 15;
const LOCK_TOLERANCE = 1.3;

/*
    Candidate periods closer together than this are the same
    candidate, so only the better scoring of the two is kept.
*/
const DISTINCT_RATIO = 1.03;

/*
    How many of the comb's peaks to examine. Past the first
    several the scores are well clear of the real tempo and
    measuring them only costs time.
*/
const MAX_CANDIDATES = 8;

/*
    An onset peak counts as explained by a grid beat within this
    fraction of a beat of it.
*/
const EXPLAINED_FRACTION = 0.2;

/*
    A grid beat is kept when the flux near it reaches this
    fraction of the track's typical beat. Below it the track is
    in an intro, an outro or a breakdown, and blocks there would
    be arriving to silence.
*/
const QUIET_FRACTION = 0.1;

/*
    How far either side of a grid beat to look for the onset
    that supports it, as a fraction of the beat. A drummer is
    not a metronome, and the grid should not insist otherwise.
*/
const SUPPORT_FRACTION = 0.25;

/*
    In-place iterative radix-2 FFT. Only ever used on a power of
    two, which is why it can be this plain.
*/
function fft(real, imag) {
    const n = real.length;

    for (let i = 1, j = 0; i < n; i += 1) {
        let bit = n >> 1;

        for (; j & bit; bit >>= 1) {
            j ^= bit;
        }

        j ^= bit;

        if (i < j) {
            let t = real[i]; real[i] = real[j]; real[j] = t;
            t = imag[i]; imag[i] = imag[j]; imag[j] = t;
        }
    }

    for (let len = 2; len <= n; len <<= 1) {
        const angle = (-2 * Math.PI) / len;

        const wReal = Math.cos(angle);
        const wImag = Math.sin(angle);

        for (let i = 0; i < n; i += len) {
            let curReal = 1;
            let curImag = 0;

            for (let k = 0; k < len / 2; k += 1) {
                const aReal = real[i + k];
                const aImag = imag[i + k];

                const bReal =
                    real[i + k + len / 2] * curReal -
                    imag[i + k + len / 2] * curImag;

                const bImag =
                    real[i + k + len / 2] * curImag +
                    imag[i + k + len / 2] * curReal;

                real[i + k] = aReal + bReal;
                imag[i + k] = aImag + bImag;
                real[i + k + len / 2] = aReal - bReal;
                imag[i + k + len / 2] = aImag - bImag;

                const nextReal = curReal * wReal - curImag * wImag;

                curImag = curReal * wImag + curImag * wReal;
                curReal = nextReal;
            }
        }
    }
}

/*
    SPECTRAL FLUX.

    How much louder each part of the spectrum got since the last
    frame, summed, keeping only the rises. A note beginning adds
    energy across many bins at once and shows up strongly; a
    note ending removes energy and is discarded, which is right,
    because the beat is where sound starts.

    The difference is taken in log magnitude, so it measures
    proportional change. In linear magnitude a loud passage
    would dominate the whole track and a quiet verse would
    register no beats at all.
*/
function spectralFlux(samples, sampleRate) {
    const FRAME = frameSizeFor(sampleRate);
    const HOP = FRAME / 4;

    const window = new Float64Array(FRAME);

    for (let i = 0; i < FRAME; i += 1) {
        window[i] =
            0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (FRAME - 1));
    }

    const frames = Math.max(
        0, Math.floor((samples.length - FRAME) / HOP) + 1
    );

    const flux = new Float64Array(frames);
    const bins = FRAME / 2;

    const real = new Float64Array(FRAME);
    const imag = new Float64Array(FRAME);

    let previous = new Float64Array(bins);
    let current = new Float64Array(bins);

    for (let frame = 0; frame < frames; frame += 1) {
        const start = frame * HOP;

        for (let i = 0; i < FRAME; i += 1) {
            real[i] = samples[start + i] * window[i];
            imag[i] = 0;
        }

        fft(real, imag);

        let total = 0;

        for (let k = 0; k < bins; k += 1) {
            const magnitude = Math.hypot(real[k], imag[k]);

            /*
                Log of one plus the magnitude, so a silent bin
                is zero rather than minus infinity.
            */
            current[k] = Math.log1p(magnitude);

            const rise = current[k] - previous[k];

            if (rise > 0) {
                total += rise;
            }
        }

        flux[frame] = frame === 0 ? 0 : total;

        const swap = previous;

        previous = current;
        current = swap;
    }

    return {
        flux,
        rate: sampleRate / HOP,

        /*
            WHEN A FRAME HAPPENED.

            A frame's spectrum describes the whole window it
            covers, so it belongs to the middle of that window,
            not to its leading edge. Labelling frames by their
            start — which is what an index divided by the frame
            rate gives — reports every onset half a window early,
            because the first frame that can see a sound begin is
            the one whose window has only just reached it.

            Measured on click tracks at 8 kHz, where the window
            is 64 ms: beats came back 34 to 44 ms early, mean
            41 ms, against a half window of 32 ms. Adding the
            half window back is not a fudge factor, it is where
            the frame actually is.
        */
        offset: FRAME / 2 / sampleRate
    };
}


/*
    The flux still carries the shape of the arrangement: a dense
    chorus reads as high everywhere, a sparse verse as low
    everywhere. Subtracting a local average leaves only what
    stands out from its own surroundings, which is what an onset
    is.
*/
function removeLocalAverage(flux, frameRate) {
    const window = Math.max(
        3, Math.round(FLUX_CONTEXT_SECONDS * frameRate)
    );

    const prefix = new Float64Array(flux.length + 1);

    for (let i = 0; i < flux.length; i += 1) {
        prefix[i + 1] = prefix[i] + flux[i];
    }

    const out = new Float64Array(flux.length);
    const half = window >> 1;

    for (let i = 0; i < flux.length; i += 1) {
        const lo = Math.max(0, i - half);
        const hi = Math.min(flux.length, i + half + 1);

        const average = (prefix[hi] - prefix[lo]) / (hi - lo);

        out[i] = Math.max(0, flux[i] - average);
    }

    return out;
}

/*
    Autocorrelation through the frequency domain, which is the
    Wiener-Khinchin theorem: the autocorrelation of a signal is
    the inverse transform of its power spectrum. Direct
    correlation over every lag would be quadratic; this is not.

    Zero-padded to twice the length, so the correlation is the
    linear one. Without the padding the transform's periodicity
    wraps the end of the track onto its beginning and every lag
    is contaminated by a comparison between two unrelated
    pieces of music.
*/
function autocorrelate(signal) {
    let size = 1;

    while (size < signal.length * 2) {
        size <<= 1;
    }

    const real = new Float64Array(size);
    const imag = new Float64Array(size);

    real.set(signal);

    fft(real, imag);

    for (let i = 0; i < size; i += 1) {
        real[i] = real[i] * real[i] + imag[i] * imag[i];
        imag[i] = 0;
    }

    /*
        An inverse transform is a forward one on the conjugate,
        scaled. The power spectrum is real, so conjugating it
        does nothing and only the scale is left to undo.
    */
    fft(real, imag);

    for (let i = 0; i < size; i += 1) {
        real[i] /= size;
    }

    return real;
}

/*
    Plausible beat periods, in seconds, best first.

    Scored with the harmonic comb described above rather than
    taken as the largest peak. Normalising each lag by the
    number of frames that could have contributed to it matters
    too: without it every score is biased toward short lags
    simply because there are more of them in the track.

    This proposes; chooseBeat decides.
*/
function tempoCandidates(onsets, frameRate) {
    const correlation = autocorrelate(onsets);

    const minLag = Math.max(1, Math.round(MIN_PERIOD * frameRate));
    const maxLag = Math.round(MAX_PERIOD * frameRate);

    const normalised = new Float64Array(correlation.length);

    for (let lag = 0; lag < correlation.length; lag += 1) {
        const overlap = onsets.length - lag;

        normalised[lag] =
            overlap > frameRate ? correlation[lag] / overlap : 0;
    }

    const scores = new Float64Array(maxLag + 1);

    for (let lag = minLag; lag <= maxLag; lag += 1) {
        let score = 0;

        for (const harmonic of COMB_HARMONICS) {
            const at = lag * harmonic.multiple;

            if (at < normalised.length) {
                score += harmonic.weight * normalised[at];
            }
        }

        scores[lag] = score;
    }

    /*
        Local maxima, best first. A peak rather than every lag,
        because neighbouring lags are the same candidate seen
        twice.
    */
    const peaks = [];

    for (let lag = minLag + 1; lag < maxLag; lag += 1) {
        if (
            scores[lag] >= scores[lag - 1] &&
            scores[lag] > scores[lag + 1]
        ) {
            peaks.push(lag);
        }
    }

    peaks.sort((a, b) => scores[b] - scores[a]);

    const periods = [];

    const add = period => {
        if (period < MIN_PERIOD || period > MAX_PERIOD) {
            return;
        }

        for (const kept of periods) {
            const ratio =
                kept > period ? kept / period : period / kept;

            if (ratio < DISTINCT_RATIO) {
                return;
            }
        }

        periods.push(period);
    };

    for (const lag of peaks) {
        if (periods.length >= MAX_CANDIDATES) {
            break;
        }

        add(lag / frameRate);
    }

    /*
        The beat may be at twice or half the lag the comb likes
        best, and on a track whose offbeats are as loud as its
        beats the comb has no way to prefer one. Both are put up
        for judgement explicitly rather than left to chance.
    */
    if (peaks.length) {
        const top = peaks[0] / frameRate;

        add(top / 2);
        add(top * 2);
    }

    return periods.length ? periods : [minLag / frameRate];
}

/*
    The coarse period is only as precise as one frame, which is
    11.6 ms out of roughly 550 — and that rounding is a drift.
    A grid a fraction of a percent fast leaves the music a
    little later every bar: measured on the bundled track, the
    unrefined period slid 0.27 ms for every second of music,
    which over three and a half minutes is 57 ms, more than the
    window the game pays a perfect cut for.

    So the period is refined against the whole track. The score
    is the onset strength a grid at that period collects at its
    own best phase, summed over every beat — an objective that a
    drifting grid cannot do well on, because by the end it is
    landing between the drums. Searching a fraction of a frame
    either side of the coarse estimate is enough, since the
    comb has already established which beat is the beat.
*/
function refinePeriod(onsets, frameRate, coarse) {
    const span = 0.6 / frameRate;
    const steps = 120;

    let best = coarse;
    let bestScore = -Infinity;

    for (let step = 0; step <= steps; step += 1) {
        const period =
            coarse - span + (2 * span * step) / steps;

        const score = gridScore(onsets, frameRate, period);

        if (score > bestScore) {
            bestScore = score;
            best = period;
        }
    }

    return best;
}

/*
    How much onset strength a grid at this period collects,
    at whichever phase suits it best. Divided by the number of
    beats, so a shorter period is not rewarded merely for
    placing more of them.
*/
function gridScore(onsets, frameRate, period) {
    const spacing = period * frameRate;

    let best = 0;

    for (let offset = 0; offset < spacing; offset += 1) {
        let total = 0;
        let beats = 0;

        for (let at = offset; at < onsets.length - 1; at += spacing) {
            const index = Math.floor(at);
            const fraction = at - index;

            total +=
                onsets[index] * (1 - fraction) +
                onsets[index + 1] * fraction;

            beats += 1;
        }

        if (beats) {
            best = Math.max(best, total / beats);
        }
    }

    return best;
}

/*
    Where the grid starts.

    The period says how far apart the beats are and nothing
    about where they fall, so a pulse train at that spacing is
    slid across the whole first beat and the offset that
    collects the most onset strength wins.
*/
function estimatePhase(onsets, frameRate, period) {
    const spacing = period * frameRate;

    /*
        Stepped in eighths of a frame, not whole ones. A frame is
        about 12 ms, so a phase rounded to the nearest frame is
        up to 6 ms out before the music is even considered, and
        that error lands on every beat in the track.
    */
    const step = 0.125;
    const steps = Math.max(1, Math.round(spacing / step));

    let bestOffset = 0;
    let bestTotal = -Infinity;

    for (let index = 0; index <= steps; index += 1) {
        const offset = index * step;

        if (offset >= spacing) {
            break;
        }

        let total = 0;

        for (
            let at = offset;
            at < onsets.length;
            at += spacing
        ) {
            /*
                The grid lands between frames, so both
                neighbours contribute in proportion — otherwise
                the score jumps about with rounding rather than
                with the music.
            */
            const frame = Math.floor(at);
            const fraction = at - frame;

            if (frame + 1 < onsets.length) {
                total +=
                    onsets[frame] * (1 - fraction) +
                    onsets[frame + 1] * fraction;
            }
        }

        if (total > bestTotal) {
            bestTotal = total;
            bestOffset = offset;
        }
    }

    return bestOffset / frameRate;
}

/*
    ONSET PEAKS, in seconds.

    The grid is judged against these rather than against the
    onset signal itself: a peak is a drum, whereas the signal
    between peaks is the tail of one, and a grid should be
    measured on whether it lands on the hits.
*/
function onsetPeaks(onsets, frameRate, frameOffset) {
    let mean = 0;

    for (let i = 0; i < onsets.length; i += 1) {
        mean += onsets[i];
    }

    mean /= onsets.length;

    let variance = 0;

    for (let i = 0; i < onsets.length; i += 1) {
        variance += (onsets[i] - mean) ** 2;
    }

    const threshold = mean + Math.sqrt(variance / onsets.length);

    const peaks = [];

    for (let i = 1; i < onsets.length - 1; i += 1) {
        if (
            onsets[i] > threshold &&
            onsets[i] > onsets[i - 1] &&
            onsets[i] >= onsets[i + 1]
        ) {
            peaks.push(i / frameRate + frameOffset);
        }
    }

    return peaks;
}

/*
    The nearest onset peak to a moment, by binary search — the
    grid is compared against every peak for every candidate, so
    scanning the list each time is the difference between this
    being free and being the slowest part of the analysis.
*/
function nearestPeak(peaks, time) {
    if (!peaks.length) {
        return Infinity;
    }

    let low = 0;
    let high = peaks.length - 1;

    while (low < high) {
        const mid = (low + high) >> 1;

        if (peaks[mid] < time) {
            low = mid + 1;
        } else {
            high = mid;
        }
    }

    let best = Math.abs(peaks[low] - time);

    if (low > 0) {
        best = Math.min(best, Math.abs(peaks[low - 1] - time));
    }

    return best;
}

/*
    Signed distance to the nearest peak, for the drift fit.
*/
function signedToPeak(peaks, time) {
    let low = 0;
    let high = peaks.length - 1;

    while (low < high) {
        const mid = (low + high) >> 1;

        if (peaks[mid] < time) {
            low = mid + 1;
        } else {
            high = mid;
        }
    }

    let best = peaks[low] - time;

    if (low > 0 && Math.abs(peaks[low - 1] - time) < Math.abs(best)) {
        best = peaks[low - 1] - time;
    }

    return best;
}

/*
    HOW WELL A GRID FITS.

    lock is the scatter of the beats about the peaks they match,
    with any steady drift removed first — a grid that is slightly
    the wrong tempo would otherwise be condemned by the drift
    alone, when the refinement step is there to fix exactly that.

    explained is the share of the track's onset peaks that the
    grid accounts for, which is what tells a tempo from half of
    it.
*/
function gradeGrid(peaks, period, phase, duration) {
    const window = period / 3;

    const times = [];
    const errors = [];

    for (let at = phase; at < duration; at += period) {
        const signed = signedToPeak(peaks, at);

        if (Math.abs(signed) < window) {
            times.push(at);
            errors.push(signed);
        }
    }

    const beats = Math.max(1, Math.floor((duration - phase) / period));

    if (times.length < 8) {
        return { lock: Infinity, explained: 0, matched: 0, beats };
    }

    const meanTime =
        times.reduce((a, b) => a + b, 0) / times.length;

    const meanError =
        errors.reduce((a, b) => a + b, 0) / errors.length;

    let covariance = 0;
    let spread = 0;

    for (let i = 0; i < times.length; i += 1) {
        covariance += (times[i] - meanTime) * (errors[i] - meanError);
        spread += (times[i] - meanTime) ** 2;
    }

    const slope = spread ? covariance / spread : 0;

    let residual = 0;

    for (let i = 0; i < times.length; i += 1) {
        residual +=
            (errors[i] -
                (meanError + slope * (times[i] - meanTime))) ** 2;
    }

    const lock = Math.sqrt(residual / times.length) * 1000;

    /*
        Peaks the grid accounts for. Counted over the peaks
        rather than over the beats, so a grid that places twice
        as many beats gains nothing by doing so.
    */
    const reach = EXPLAINED_FRACTION * period;

    let hit = 0;

    for (const peak of peaks) {
        if (peak < phase) {
            continue;
        }

        const offset = (peak - phase) % period;

        if (Math.min(offset, period - offset) <= reach) {
            hit += 1;
        }
    }

    return {
        lock,
        explained: peaks.length ? hit / peaks.length : 0,
        matched: times.length,
        beats
    };
}

/*
    Pick the tempo, and the phase that goes with it.

    Every candidate is refined, graded, and then the choice is
    made on the grades: among those that lock about as tightly
    as the best, the one that explains the most onset peaks. Ties
    there go to the slower tempo, because two grids that fit the
    music equally well are not equally pleasant to play and the
    sparser one leaves room for the pattern to add its own.
*/
function chooseBeat(onsets, frameRate, frameOffset, duration) {
    const peaks = onsetPeaks(onsets, frameRate, frameOffset);
    const candidates = tempoCandidates(onsets, frameRate);

    const graded = [];

    for (const rough of candidates) {
        const period = refinePeriod(onsets, frameRate, rough);

        const phase =
            estimatePhase(onsets, frameRate, period) + frameOffset;

        const grade = gradeGrid(peaks, period, phase, duration);

        /*
            A grid matching fewer than half its beats to a peak
            is not describing this track, whatever its scatter.
        */
        if (grade.matched / grade.beats < 0.4) {
            continue;
        }

        graded.push({ period, phase, ...grade });
    }

    if (!graded.length) {
        const period = refinePeriod(onsets, frameRate, candidates[0]);

        return {
            period,
            phase:
                estimatePhase(onsets, frameRate, period) + frameOffset
        };
    }

    const tightest = Math.min(...graded.map(entry => entry.lock));

    const ceiling = Math.max(
        LOCK_FLOOR_MS, tightest * LOCK_TOLERANCE
    );

    const contenders = graded.filter(entry => entry.lock <= ceiling);

    contenders.sort((a, b) => {
        if (Math.abs(a.explained - b.explained) > 0.02) {
            return b.explained - a.explained;
        }

        return b.period - a.period;
    });

    return contenders[0];
}

/*
    The strongest onset within reach of a moment in the track.
*/
function supportAt(onsets, frameRate, frameOffset, time, reach) {
    const centre = Math.round((time - frameOffset) * frameRate);

    let strongest = 0;

    for (
        let i = Math.max(0, centre - reach);
        i <= Math.min(onsets.length - 1, centre + reach);
        i += 1
    ) {
        if (onsets[i] > strongest) {
            strongest = onsets[i];
        }
    }

    return strongest;
}

function median(values) {
    if (!values.length) {
        return 0;
    }

    const sorted = [...values].sort((a, b) => a - b);

    return sorted[sorted.length >> 1];
}

/*
    Beats in a mono signal.

    Returns the beatmap the game reads: a tempo, and the times
    to put blocks at.
*/
export function detectBeats(samples, sampleRate, options = {}) {
    if (!samples.length) {
        throw new Error("The track is empty.");
    }

    if (samples.length < frameSizeFor(sampleRate) * 8) {
        throw new Error("The track is too short to find a beat in.");
    }

    const {
        flux, rate: frameRate, offset: frameOffset
    } = spectralFlux(samples, sampleRate);

    const onsets = removeLocalAverage(flux, frameRate);

    let loudest = 0;

    for (let i = 0; i < onsets.length; i += 1) {
        if (onsets[i] > loudest) {
            loudest = onsets[i];
        }
    }

    if (loudest === 0) {
        throw new Error("The track has no detectable rhythm.");
    }

    const duration = samples.length / sampleRate;

    let beat;
    let phase;

    if (options.period) {
        beat = options.period;

        phase =
            estimatePhase(onsets, frameRate, beat) + frameOffset;
    } else {
        const chosen = chooseBeat(
            onsets, frameRate, frameOffset, duration
        );

        beat = chosen.period;
        phase = chosen.phase;
    }

    /*
        How much onset strength sits near each grid beat. The
        window is a quarter beat either side, so a hit slightly
        ahead of or behind the grid still counts as supporting
        it, but one a whole beat away does not.
    */
    const reach = Math.max(
        1, Math.round(SUPPORT_FRACTION * beat * frameRate)
    );

    const grid = [];
    const support = [];

    for (let at = phase; at < duration; at += beat) {
        grid.push(at);

        support.push(
            supportAt(onsets, frameRate, frameOffset, at, reach)
        );
    }

    /*
        The floor is taken from the median of the supports rather
        than from the loudest, so one crashing cymbal cannot
        decide that the rest of the track is silent.
    */
    const floor =
        (options.quietFraction ?? QUIET_FRACTION) * median(support);

    const times = [];

    for (let i = 0; i < grid.length; i += 1) {
        if (support[i] > floor) {
            times.push(Math.round(grid[i] * 1000) / 1000);
        }
    }

    return {
        bpm: Math.round((60 / beat) * 100) / 100,
        total_beats: times.length,
        beat_timestamps: times
    };
}

/*
    Mono, because the beat is in both channels and averaging
    them halves the work. Done by hand rather than by asking the
    decoder for one channel, which not every browser honours.
*/
function toMono(buffer) {
    const channels = buffer.numberOfChannels;
    const first = buffer.getChannelData(0);

    if (channels === 1) {
        return first;
    }

    const mono = new Float32Array(first.length);

    for (let channel = 0; channel < channels; channel += 1) {
        const data = buffer.getChannelData(channel);

        for (let i = 0; i < mono.length; i += 1) {
            mono[i] += data[i] / channels;
        }
    }

    return mono;
}

/*
    Decode a file the player chose and find its beats.

    Returns the beatmap, a URL the audio element can play, and
    how long the track is. The decoded buffer is deliberately
    not reused for playback: it was decoded at the analysis rate
    and mixed to mono, so it would play back in mono and dull.
*/
export async function analyseSongFile(file, options = {}) {
    const bytes = await file.arrayBuffer();

    /*
        decodeAudioData resamples to the context's rate, so the
        context is what asks for the analysis rate. It also
        consumes the buffer it is given, hence the copy — the
        same bytes are wanted again for playback.
    */
    const context = new OfflineAudioContext(1, 1, ANALYSIS_RATE);

    const decoded = await context.decodeAudioData(bytes.slice(0));

    const beatmap = detectBeats(
        toMono(decoded), decoded.sampleRate, options
    );

    return {
        beatmap,
        url: URL.createObjectURL(new Blob([bytes], { type: file.type })),
        duration: decoded.duration,
        name: file.name
    };
}
