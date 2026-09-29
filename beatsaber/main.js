import * as JoyCon from "joy-con-webhid";

/* =========================================================
   HTML ELEMENTS
   ========================================================= */

const connectButton =
    document.querySelector("#connect-button");

const calibrateButton =
    document.querySelector("#calibrate-button");

const recenterButton =
    document.querySelector("#recenter-button");

const swapButton =
    document.querySelector("#swap-button");

const statusText =
    document.querySelector("#status");

const promptText =
    document.querySelector("#prompt");

const promptMessage =
    document.querySelector("#prompt-message");

const promptFeedback =
    document.querySelector("#prompt-feedback");

const debugText =
    document.querySelector("#debug");

const slashText =
    document.querySelector("#slash");

const scoreText =
    document.querySelector("#score");

const blocksButton =
    document.querySelector("#blocks-button");

const musicInfo =
    document.querySelector("#music-info");

/* =========================================================
   AUDIO AND BEATMAP
   ========================================================= */

let backgroundMusic = null;
let destroySound = null;
let beatmap = null;
let beatmapSource = null;
let beatIndex = 0;
let musicStartTime = 0;
let beatSyncEnabled = false;

// Test mode - allows destroying blocks without Joy-Cons
let testMode = false;
let mouseX = 0;
let mouseY = 0;
let lastMouseX = 0;
let lastMouseY = 0;
let mouseSpeed = 0;

// Load beatmap JSON
/*
    The beatmap, and the track it belongs to.

    A custom pair is tried first. The audio editor this game
    ships inside writes the track you have open, along with its
    detected beats, as custom.* — so whatever is loaded in the
    editor becomes the song here. When there is no custom pair
    the bundled track is used instead, which is also what
    happens for anyone running the game on its own.
*/
const BEATMAP_SOURCES = [
    "custom_beatmap.json",
    "beatmap_dsp.json"
];

async function loadBeatmap() {
    for (const source of BEATMAP_SOURCES) {
        try {
            const response = await fetch(source);

            if (!response.ok) {
                continue;
            }

            const loaded = await response.json();

            if (!Array.isArray(loaded?.beat_timestamps)) {
                continue;
            }

            beatmap = loaded;
            beatmapSource = source;

            console.log(
                `Loaded beatmap: ${beatmap.total_beats} beats ` +
                `at ${beatmap.bpm} BPM (${source})`
            );

            return;
        } catch (error) {
            /* Try the next one. */
        }
    }

    console.warn(
        "No beatmap found. Run extract_beats.py, or blocks " +
        "will spawn on a timer instead of on the beat."
    );
}

// Initialize audio elements
function initAudio() {
    /*
        The track has been both song.wav and song.mp3 at
        different times, and blocks are spawned off the music's
        own clock — so naming the wrong one does not merely
        mute the game, it stops anything from appearing at all.
        Each candidate is tried in turn instead.
    */
    /*
        custom.wav is written by the audio editor from whatever
        track is open there. It is tried first so the editor's
        track wins, and its absence simply falls through to the
        bundled song.
    */
    const MUSIC_SOURCES = [
        'custom.wav', 'song.mp3', 'song.wav', 'song.ogg'
    ];

    let musicSource = 0;

    backgroundMusic = new Audio();
    backgroundMusic.src = MUSIC_SOURCES[musicSource];
    backgroundMusic.loop = true;
    backgroundMusic.volume = 0.6;
    
    // Destroy sound
    destroySound = new Audio();
    destroySound.src = 'destroy.wav'; // or destroy.mp3
    destroySound.volume = 0.8;
    
    backgroundMusic.addEventListener('error', () => {
        /*
            A track the player loaded is not in this list, and
            falling back from it would leave the bundled song
            playing against the loaded song's beatmap — blocks
            on the beat of music nobody is hearing. Better to
            report the failure and change nothing.
        */
        if (backgroundMusic.dataset.custom) {
            console.warn(
                'The loaded track could not be played.'
            );
            return;
        }

        musicSource += 1;

        if (musicSource < MUSIC_SOURCES.length) {
            backgroundMusic.src = MUSIC_SOURCES[musicSource];
            return;
        }

        console.warn(
            'Background music not found. Add one of ' +
            MUSIC_SOURCES.join(', ') + ' to the directory.'
        );
    });
    
    destroySound.addEventListener('error', () => {
        console.warn('Destroy sound not found. Add destroy.wav to the directory.');
    });

    initDestroySound();

    for (const event of ["pointerdown", "keydown"]) {
        window.addEventListener(event, resumeAudio, { passive: true });
    }
}

// Play destroy sound on block hit
/*
    THE HIT SOUND.

    This used to clone the <audio> element and call play() on
    the copy. An HTMLAudioElement is built for streaming a
    track, not for firing a sample: each clone re-prepares its
    source, and play() is asynchronous, so the sound landed
    tens of milliseconds after the cut that caused it. At the
    speed blocks are cut that is long enough to hear as a lag
    between the swing and the sound.

    Web Audio decodes the file once into memory and each hit
    is a new source node started immediately on the audio
    clock. Latency is a buffer rather than a pipeline, and
    overlapping cuts mix instead of fighting over one element.
*/
let audioContext = null;
let destroyBuffer = null;

async function initDestroySound() {
    try {
        audioContext = new (window.AudioContext ||
            window.webkitAudioContext)();

        const response = await fetch("destroy.wav");

        if (!response.ok) {
            throw new Error(`destroy.wav: ${response.status}`);
        }

        destroyBuffer = await audioContext.decodeAudioData(
            await response.arrayBuffer()
        );
    } catch (error) {
        console.warn(
            "Low-latency hit sound unavailable, " +
            "falling back to the audio element:",
            error.message
        );
    }
}

/*
    Browsers suspend an AudioContext until the page has been
    interacted with. Cutting a block is far too late to find
    that out, so it is resumed on the first input of any kind.
*/
function resumeAudio() {
    if (audioContext && audioContext.state === "suspended") {
        audioContext.resume().catch(() => {});
    }
}

function playDestroySound() {
    if (audioContext && destroyBuffer) {
        const source = audioContext.createBufferSource();
        const gain = audioContext.createGain();

        source.buffer = destroyBuffer;
        gain.gain.value = 0.8;

        source.connect(gain).connect(audioContext.destination);
        source.start();

        return;
    }

    if (destroySound) {
        const sound = destroySound.cloneNode();
        sound.volume = 0.8;
        sound.play().catch(() => {});
    }
}

// Create visual slash effect when block is cut
function createSlashEffect(block) {
    const stage = document.querySelector("#stage");
    if (!stage) return;

    // Calculate slash direction based on block direction
    let slashAngle = 0;
    switch (block.direction) {
        case "up":
            slashAngle = 90;  // Vertical up
            break;
        case "down":
            slashAngle = 90;  // Vertical down
            break;
        case "left":
            slashAngle = 0;   // Horizontal left
            break;
        case "right":
            slashAngle = 0;   // Horizontal right
            break;
    }

    // Create slash element
    const slash = document.createElement("div");
    slash.className = "slash-line";
    
    // Set slash color based on block side
    const slashColor = block.side === "left" 
        ? "rgba(255, 80, 100, 0.6)"  // Red
        : "rgba(80, 180, 255, 0.6)";  // Blue
    
    slash.style.setProperty("--slash-color", slashColor);
    slash.style.setProperty("--slash-length", "250px");
    
    // Position slash at block location
    slash.style.position = "absolute";
    slash.style.left = "0";
    slash.style.top = "0";
    slash.style.width = "0";
    slash.style.height = "8px";
    slash.style.transform = 
        `translate3d(${block.x}px, ${block.y}px, ${block.z}px) ` +
        `translate(-50%, -50%) ` +
        `rotate(${slashAngle}deg)`;
    
    stage.appendChild(slash);
    
    // Remove after animation
    setTimeout(() => {
        slash.remove();
    }, 400);
}

// Initialize on load
loadBeatmap();
initAudio();

// Mouse tracking for test mode
const testCursor = document.querySelector("#test-cursor");
let nearestBlockDistance = 0;

window.addEventListener('mousemove', (event) => {
    lastMouseX = mouseX;
    lastMouseY = mouseY;
    mouseX = event.clientX;
    mouseY = event.clientY;
    
    // Update test cursor position
    if (testCursor && testMode) {
        testCursor.style.left = mouseX + 'px';
        testCursor.style.top = mouseY + 'px';
        
        // Calculate mouse speed and update cursor appearance
        const deltaX = mouseX - lastMouseX;
        const deltaY = mouseY - lastMouseY;
        const speed = Math.hypot(deltaX, deltaY) * 60;
        
        if (speed > 300) {
            testCursor.classList.add('fast');
        } else {
            testCursor.classList.remove('fast');
        }
        
        // Scale cursor based on nearest block's depth
        if (nearestBlockDistance < 0) {
            const perspectiveScale = 1 + Math.abs(nearestBlockDistance) / 1000;
            const scaledSize = 230 * perspectiveScale;
            testCursor.style.width = scaledSize + 'px';
            testCursor.style.height = scaledSize + 'px';
        } else {
            testCursor.style.width = '230px';
            testCursor.style.height = '230px';
        }
    }
});

const saberElements = {
    left: document.querySelector("#left-saber"),
    right: document.querySelector("#right-saber")
};

const hiltElements = {
    left: document.querySelector("#left-hilt"),
    right: document.querySelector("#right-hilt")
};

const tipElements = {
    left: document.querySelector("#left-tip"),
    right: document.querySelector("#right-tip")
};

const crossElements = {
    left: document.querySelector("#left-cross"),
    right: document.querySelector("#right-cross")
};


/* =========================================================
   TUNING
   ========================================================= */

const LEFT_PRODUCT_ID = 0x2006;
const RIGHT_PRODUCT_ID = 0x2007;

/*
    One Euro Filter settings - optimized for responsive control.

    MIN_CUTOFF:
        Lower  = steadier at rest, more lag.
        Higher = snappier, more visible jitter at rest.

    BETA:
        How fast the filter opens up as you speed up.
        Raise it if fast swings still feel like they trail
        behind your hand.
*/
/*
    BETA is what makes the saber carry your arm's speed rather
    than a smoothed version of it. The filter widens in
    proportion to how fast you are moving, so a low beta keeps
    heavy smoothing during a fast swing and the blade arrives
    after your arm does — which reads exactly as the saber not
    moving with you. It is raised hard here: at rest MIN_CUTOFF
    still holds the blade steady, but the moment you swing, the
    filter gets out of the way.
*/
const MIN_CUTOFF = 7.0;
const BETA = 0.40;

/*
    How quickly the filter's own speed estimate keeps up. Beta
    is useless if this lags, because the widening arrives after
    the swing it was meant to track.
*/
const DERIVATIVE_CUTOFF = 2.5;

/*
    Latency compensation. Projects the saber slightly ahead
    along its current angular velocity to cancel filter delay.
*/
const PREDICTION_SECONDS = 0.02;
const MAX_PREDICTION_DEGREES = 15;

/*
    How far you turn the controller to reach the edge of the
    play area. Balanced for smooth, controllable movement.
*/
const MAX_YAW_DEGREES = 50;      // Balanced for good control
const MAX_PITCH_DEGREES = 55;    // Balanced for good control

/*
    Where the sabers are held. Both hinge points sit near the
    bottom of the screen, spaced either side of centre.
*/
const ANCHOR_SEPARATION = 420;
const ANCHOR_BOTTOM_MARGIN = 90;

/*
    The blade is a fixed-length object in a perspective scene.
    Increased for better visibility and reach.
*/
const BLADE_LENGTH = 900;

/*
    How the scene is viewed. Smaller perspective means a wider
    lens and stronger foreshortening.
*/
const PERSPECTIVE = 1100;
const HAND_Z = 120;

/*
    THE ARM.

    Real Beat Saber tracks your controller in six degrees of
    freedom: the saber is wherever your hand is and points
    wherever your hand points, so you swing from the shoulder
    and the blade sweeps a wide arc.

    A Joy-Con only reports orientation. The old model dealt
    with that by treating orientation as a pointer — your wrist
    angle was run through a tangent and a soft limit to pick a
    screen position inside a fixed box. That is why you had to
    hold your hand still and aim: the saber did not move
    because your arm moved, it moved because your wrist tilted,
    and past fifty degrees it stopped moving at all.

    Orientation alone is enough to do much better, because a
    swing is a rotation. Your arm and the saber together are
    one rigid lever pivoting at your shoulder. Rotate the lever
    and the far end sweeps through a long, fast arc — which is
    exactly what the Joy-Con measures.

    So the saber is modelled as that lever: a virtual upper arm
    of ARM_LENGTH from a fixed shoulder to your hand, with the
    blade continuing straight on for BLADE_LENGTH. The blade
    direction is taken from the controller one-to-one, with no
    cap and no projection, and the hand position falls out of
    the same rotation instead of being invented separately.

    Checked against the block grid, this puts the blade within
    9px of every lane using arm angles of at most 46 degrees
    across and 32 up — a natural swing, and free in every
    direction rather than pinned inside a box.
*/
const ARM_LENGTH = 300;

/*
    The shoulder sits a little below the old hand line and well
    behind the blade plane, so the lever swings forward into
    the playfield rather than across your face.
*/
/*
    In the real game your hands are below your field of view
    and the blade runs up through it and out of the top of the
    frame. That is most of what makes it first person: you do
    not see a saber on a screen, you see your own saber, too
    close and too big to fit.

    So the pivot goes below the bottom edge and well behind the
    blocks. Behind matters for more than looks — the pivot's
    depth is a hard ceiling on how close the blade can get,
    because the blade can only ever swing forward of it. With
    the pivot at z=260 and blocks living until z=320, every
    block that got closer than the pivot was unreachable by any
    swing. Putting the pivot at 520 puts the whole block path
    in front of it.
*/
const SHOULDER_DROP = 390;
const SHOULDER_Z = 520;

/*
    Test mode stands in for the controller, so it is measured
    in the same arm angles rather than in pixels.
*/
const TEST_MODE_YAW_DEGREES = 55;
const TEST_MODE_PITCH_DEGREES = 40;
const TEST_MODE_TAU = 0.045;

/*
    Sabers are held angled up rather than aimed straight down
    the view axis, where they would project to almost nothing.
*/
/*
    Held steeply, so the blade runs up out of the top of the
    frame rather than stopping in the middle of it. At rest the
    hand sits below the bottom edge and the tip is near the
    top — the saber is too big to fit in the view, which is
    what selling first person depends on.
*/
const REST_TILT_DEGREES = 55;


/*
    Motion trail.

    The blade leaves a fading arc behind it, which is most of
    what makes a swing read as fast rather than merely as the
    saber being somewhere else. Each segment is an older copy of
    the blade, dimmer and thinner the further back it is.

    TRAIL_SEGMENTS costs one element per saber per segment, so
    keep it modest.
*/
const TRAIL_SEGMENTS = 14;
const TRAIL_OPACITY = 0.55;

/*
    The blade reacts to how hard it is being swung: it burns
    brighter and the trail comes out. Holding still leaves a
    clean blade with no smear behind it, which is what makes a
    fast swing actually read as fast.

    Speed is measured against the slash threshold, so a swing
    that scores is a swing that flares.
*/
const GLOW_RESPONSE = 0.85;

/*
    How long the blade takes to extend from the hilt when a
    saber comes up.
*/
const IGNITION_SECONDS = 0.4;

/*
    Slash detection.

    SLASH_SPEED is how fast the blade tip has to travel, in
    pixels per second, before a swing counts.

    SLASH_COOLDOWN_SECONDS keeps one swing from registering
    repeatedly across consecutive frames.
*/
/*
    Tip speed scales with the lever, and the lever is now more
    than twice as long, so the same wrist turn moves the tip
    twice as fast. These track it, or resting drift would read
    as a swing.
*/
const SLASH_SPEED = 3000;
const SLASH_COOLDOWN_SECONDS = 0.18;

/*
    How long a qualifying swing stays live for scoring after it
    is recognised. Long enough to cover the frames a fast blade
    spends crossing a block, short enough that it cannot credit
    a swing you have already finished elsewhere.
*/
const CUT_MEMORY_SECONDS = 0.16;


/*
    Yaw drift compensation - disabled for test mode responsiveness.
*/
const DRIFT_STILL_DPS = 18;
const DRIFT_TAU_SECONDS = 1.2;

/*
    Hold the controller still for this long and its current
    heading is taken as the new neutral, however far it has
    wandered. This is the path that actually rescues a drifted
    session: the gentle pull above is capped at
    DRIFT_SNAP_DEGREES so it cannot fight you when you are
    genuinely holding the saber out to one side, but that cap
    also meant that once drift grew past the cap nothing could
    ever bring it back. Resting is the one unambiguous signal
    that where the saber sits now ought to be where it sits at
    rest.
*/



/*
    How long the running average of heading takes to absorb a
    change. Long against a swing, short against a session.
*/
const AUTO_CENTRE_TAU = 10;

/*
    How far from the calibrated play pose the saber can be
    tilted before heading correction stops. Comfortably wider
    than any swing, far narrower than a saber hanging at your
    side.
*/
const AUTO_CENTRE_MAX_PITCH = 45;

/*
    How hard the estimate of the drift RATE is driven. Set for
    critical damping against the averaging term above, so the
    rate is learned without the neutral oscillating around it.
*/
const DRIFT_RATE_GAIN =
    1 / (AUTO_CENTRE_TAU * AUTO_CENTRE_TAU);

/*
    No real gyroscope drifts this fast. A cap means a bad
    stretch of data cannot teach the correction to run away.
*/
const DRIFT_RATE_LIMIT = 15;


/*
    A residual heading offset this small is treated as drift.
*/
const DRIFT_SNAP_DEGREES = 30;

/*
    Set to -1 if the gyroscope turns out to be handed opposite
    to the accelerometer. If that were wrong, tilt would fight
    gravity during movement and settle back only once you stop.
*/
const GYRO_SIGN = 1;

const AXES_STORAGE_KEY = "joycon-forward-axes";
const HANDS_STORAGE_KEY = "joycon-hand-mapping";

/*
    Right is now defined explicitly, by a cross product against
    your play pose, rather than inherited from a maths
    convention that counts the other way. So no sign correction
    is needed here any more.

    Flip these only if something still feels reversed after
    calibrating.
*/
/*
    Live control corrections.

    These used to be source constants, which meant that if the
    calibrated frame came out mirrored — the single most common
    way a motion controller ends up "moving the wrong way" —
    the only remedy was editing this file and reloading, losing
    the calibration in the process.

    They are adjustable from the keyboard while playing and
    persisted, so a reversed axis is a keypress to correct
    rather than a code change.

        X / Y / R   flip the horizontal, vertical, twist axes
        [ / ]       less / more sensitive
        ; / '       smoother / snappier
        0           back to defaults

    gain multiplies the angle you have to turn through to reach
    the edge of the play area: below 1 the saber needs a bigger
    movement, above 1 a smaller one.
*/
const TUNING_STORAGE_KEY = "joycon-tuning";

const TUNING_DEFAULTS = {
    invertX: 1,
    invertY: 1,
    invertRoll: 1,
    gain: 1,
    responsiveness: 1,

    /*
        One beat in this many carries blocks. 1 is every beat;
        3 is one in three.

        This was 3 because a block took six seconds to arrive
        and a dozen would be in the air at once. Blocks now
        cross in 3.2 seconds, so the same stride left the
        playfield nearly empty — measured over two minutes of
        the bundled track, 43 blocks a minute with a mean of
        2.2 in the air. At 2 it is 68 a minute and 3.5 in the
        air, which is the density the real game calls normal.
        The - and = keys still move it either way.
    */
    beatStride: 2,

    /*
        Rumble shakes the IMU that the controls are read from,
        so it is worth being able to switch off outright: if
        the sabers behave differently with it off, the rumble
        is the cause and not the controls.
    */
    rumble: 1
};

const tuning = { ...TUNING_DEFAULTS };

const SHOW_DEBUG = true;

/*
    The blade's drawn height has to be exactly its length, or
    the hinge and the tip stop lining up. Publishing it to CSS
    keeps the two from drifting apart.
*/
document.documentElement.style.setProperty(
    "--blade-length",
    `${BLADE_LENGTH}px`
);


/* =========================================================
   SMALL MATH HELPERS
   ========================================================= */

const RAD_TO_DEG = 180 / Math.PI;

function clamp(value, minimum, maximum) {
    return Math.max(
        minimum,
        Math.min(maximum, value)
    );
}


/*
    Keeps an angle continuous across the -180 / +180 boundary.
    Filtering a signal that jumps by 360 produces a violent
    glitch, so this runs before the filter.
*/
function unwrapDegrees(previous, value) {
    if (previous === null) {
        return value;
    }

    let result = value;

    while (result - previous > 180) {
        result -= 360;
    }

    while (result - previous < -180) {
        result += 360;
    }

    return result;
}


/* =========================================================
   VECTOR MATH
   ========================================================= */

function dot(a, b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

function cross(a, b) {
    return {
        x: a.y * b.z - a.z * b.y,
        y: a.z * b.x - a.x * b.z,
        z: a.x * b.y - a.y * b.x
    };
}

function normalize(v) {
    const length = Math.hypot(v.x, v.y, v.z);

    if (!length) {
        return null;
    }

    return {
        x: v.x / length,
        y: v.y / length,
        z: v.z / length
    };
}


/* =========================================================
   QUATERNION MATH
   ========================================================= */

function quaternionMultiply(a, b) {
    return {
        w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
        x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
        y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
        z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w
    };
}

function quaternionConjugate(q) {
    return { w: q.w, x: -q.x, y: -q.y, z: -q.z };
}

function quaternionNormalize(q) {
    const length = Math.hypot(q.w, q.x, q.y, q.z);

    if (!length) {
        return { w: 1, x: 0, y: 0, z: 0 };
    }

    return {
        w: q.w / length,
        x: q.x / length,
        y: q.y / length,
        z: q.z / length
    };
}

/*
    The shortest rotation taking one direction onto another.
    Used to start the orientation estimate off already aligned
    with gravity.
*/
function quaternionAligning(from, to) {
    const axis = cross(from, to);
    const alignment = dot(from, to);

    if (alignment < -0.999999) {
        /*
            Exactly opposed — any perpendicular axis will do.
        */
        const fallback =
            Math.abs(from.x) < 0.9
                ? { x: 1, y: 0, z: 0 }
                : { x: 0, y: 1, z: 0 };

        const perpendicular = normalize(cross(from, fallback));

        return {
            w: 0,
            x: perpendicular.x,
            y: perpendicular.y,
            z: perpendicular.z
        };
    }

    return quaternionNormalize({
        w: 1 + alignment,
        x: axis.x,
        y: axis.y,
        z: axis.z
    });
}

function rotateVector(q, v) {
    const tx = 2 * (q.y * v.z - q.z * v.y);
    const ty = 2 * (q.z * v.x - q.x * v.z);
    const tz = 2 * (q.x * v.y - q.y * v.x);

    return {
        x: v.x + q.w * tx + (q.y * tz - q.z * ty),
        y: v.y + q.w * ty + (q.z * tx - q.x * tz),
        z: v.z + q.w * tz + (q.x * ty - q.y * tx)
    };
}

/*
    Swing-twist decomposition. Extracts only the rotation
    around the given axis, which is the blade's roll. This
    keeps roll correct no matter where the saber points.
*/
function twistAngleDegrees(q, axis) {
    const projection =
        q.x * axis.x +
        q.y * axis.y +
        q.z * axis.z;

    const twist = quaternionNormalize({
        w: q.w,
        x: axis.x * projection,
        y: axis.y * projection,
        z: axis.z * projection
    });

    const angle =
        2 * Math.atan2(projection, twist.w) * RAD_TO_DEG;

    if (angle > 180) {
        return angle - 360;
    }

    if (angle < -180) {
        return angle + 360;
    }

    return angle;
}


/* =========================================================
   ONE EURO FILTER

   Reference: Casiez, Roussel and Vogel, "1 Euro Filter".

   A fixed smoothing factor forces a choice between shaky and
   laggy. This varies smoothing with speed, so it can be both
   steady at rest and responsive during a swing.
   ========================================================= */

function smoothingFactor(deltaTime, cutoff) {
    const tau = 1 / (2 * Math.PI * cutoff);

    return 1 / (1 + tau / deltaTime);
}

class OneEuroFilter {
    constructor(minCutoff, beta, derivativeCutoff) {
        this.minCutoff = minCutoff;
        this.beta = beta;
        this.derivativeCutoff = derivativeCutoff;

        this.previousValue = null;
        this.previousSmoothed = null;

        /*
            Exposed so the render loop can reuse it for latency
            compensation rather than computing its own noisy
            derivative.
        */
        this.derivative = 0;
    }

    reset(value = null) {
        this.previousValue = value;
        this.previousSmoothed = value;
        this.derivative = 0;
    }

    filter(value, deltaTime) {
        if (this.previousSmoothed === null) {
            this.reset(value);
            return value;
        }

        const rawDerivative =
            (value - this.previousValue) / deltaTime;

        const derivativeAlpha =
            smoothingFactor(deltaTime, this.derivativeCutoff);

        this.derivative +=
            derivativeAlpha *
            (rawDerivative - this.derivative);

        /*
            Scaled live by the responsiveness setting. Raising
            both terms together widens the filter at rest and
            when moving alike, so the trade stays between lag
            and jitter rather than tilting the speed response.
        */
        const cutoff =
            this.minCutoff * tuning.responsiveness +
            this.beta * tuning.responsiveness *
                Math.abs(this.derivative);

        const alpha = smoothingFactor(deltaTime, cutoff);

        const smoothed =
            this.previousSmoothed +
            alpha * (value - this.previousSmoothed);

        this.previousValue = value;
        this.previousSmoothed = smoothed;

        return smoothed;
    }
}


/* =========================================================
   SABER STATE

   State is keyed by which physical Joy-Con it came from.
   Which saber a controller drives on screen is a separate
   mapping, further down.

   Input and rendering are kept apart. The HID handler only
   records the newest reading; all smoothing and drawing
   happens once per animation frame, so smoothing stays tied
   to the refresh rate rather than to packet timing.
   ========================================================= */

function createSaberState(device) {
    return {
        device,

        connected: false,

        /*
            Kept so calibration can rumble the controller it is
            currently asking about.
        */
        joyCon: null,

        rawQuaternion: null,
        accelerometer: null,
        gyroscope: { x: 0, y: 0, z: 0 },
        buttons: null,

        /*
            Smoothed gravity direction. Capturing this rather
            than the latest sample keeps the nudge from pressing
            a button out of the calibrated axis.
        */
        accelSmooth: null,

        buttonIsPressed: false,
        buttonWasPressed: false,

        /*
            Real degrees per second, derived from how much the
            orientation changed between packets. Independent of
            however the library chooses to scale its raw gyro
            output.
        */
        angularSpeed: 0,

        previousQuaternion: null,
        previousQuaternionTime: 0,

        /*
            Our own fused orientation, mapping controller
            coordinates to world coordinates with Z pointing up
            along gravity.
        */
        fusionQuaternion: { w: 1, x: 0, y: 0, z: 0 },
        fusionStarted: false,
        fusionSettling: 1.2,
        gyroBias: { x: 0, y: 0, z: 0 },
        lastPacketTime: 0,

        /*
            The controller's long axis, in its own local
            coordinates. Discovered by calibration.
        */
        forwardLocal: null,

        /*
            Where the long axis was aimed at your play pose.

            Elevation is measured against gravity, so it is
            absolute and needs no correction. Only heading has
            to be remembered, because nothing anchors it.
        */
        /*
            Reference directions captured at your play pose:
            where you were aiming, and what right and up mean
            from there. All three are world-space unit vectors.
        */
        neutralForward: null,
        playerRight: null,
        playerUp: null,

        continuousYaw: null,
        continuousPitch: null,
        continuousRoll: null,

        /*
            Slowly-tracked yaw centre, used to cancel gyro
            drift while the controller is stationary.
        */
        driftYaw: 0,

        yawFilter: new OneEuroFilter(
            MIN_CUTOFF,
            BETA,
            DERIVATIVE_CUTOFF
        ),

        pitchFilter: new OneEuroFilter(
            MIN_CUTOFF,
            BETA,
            DERIVATIVE_CUTOFF
        ),

        rollFilter: new OneEuroFilter(
            MIN_CUTOFF,
            BETA,
            DERIVATIVE_CUTOFF
        ),

        /*
            Where the controller is aimed. Drives the blade
            direction rather than being drawn directly.
        */
        /*
            Aim deflection, -1 to 1 on each axis.
        */
        swingX: 0,
        swingY: 0,

        /*
            Unit vector the blade points along, in screen axes.
        */
        directionX: 0,
        directionY: -1,
        directionZ: 0,

        /*
            Blade angle in degrees, clockwise from straight up.
        */
        angle: 0,

        /*
            Wrist rotation about the controller's own length.
            Kept available for cut scoring, but the blade is
            drawn along its swing, not along this.
        */
        twist: 0,

        tipX: 0,
        tipY: 0,
        tipZ: 0,
        headingDegrees: 0,

        /*
            How far the blade has extended from the hilt,
            0 to 1.
        */
        ignition: 0,

        /*
            Where the hand currently is. It slides with the
            swing rather than staying pinned.
        */
        handX: 0,
        handY: 0,

        /*
            Recent blade directions, newest first, for the
            motion trail.
        */
        trail: [],
        tipSpeed: 0,
        tipVelocityX: 0,
        tipVelocityY: 0,
        previousTipX: null,
        previousTipY: null,

        slashCooldown: 0,
        lastSlash: null,
        lastSlashAt: 0,

        swingSpeed: 0
    };
}

const sabers = {
    left: createSaberState("left"),
    right: createSaberState("right")
};

/*
    Which on-screen saber each physical controller drives.

    This is deliberately not assumed. It is measured during
    calibration by asking which controller you are holding,
    and can be flipped by hand at any time.
*/
const handMapping = {
    left: "left",
    right: "right"
};

function screenSideOf(device) {
    return handMapping[device];
}

function deviceForScreenSide(screenSide) {
    return handMapping.left === screenSide ? "left" : "right";
}

function isReady(state) {
    return Boolean(
        state.connected &&
        state.fusionStarted &&
        state.forwardLocal &&
        state.neutralForward
    );
}

/*
    Cached so the render loop never reads layout properties.
*/
const viewport = {
    width: window.innerWidth,
    height: window.innerHeight
};



/* =========================================================
   STORED SETTINGS
   ========================================================= */

function loadStoredSettings() {
    try {
        const axes = JSON.parse(
            window.localStorage.getItem(AXES_STORAGE_KEY)
        );

        for (const device of ["left", "right"]) {
            const axis = axes?.[device];

            if (axis && Number.isFinite(axis.x)) {
                sabers[device].forwardLocal = normalize(axis);
            }
        }
    } catch (error) {
        console.warn("Could not read stored axes:", error);
    }

    try {
        const hands = JSON.parse(
            window.localStorage.getItem(HANDS_STORAGE_KEY)
        );

        if (hands?.left === "right") {
            handMapping.left = "right";
            handMapping.right = "left";
        }
    } catch (error) {
        console.warn("Could not read hand mapping:", error);
    }

    try {
        const stored = JSON.parse(
            window.localStorage.getItem(TUNING_STORAGE_KEY)
        );

        for (const key of Object.keys(TUNING_DEFAULTS)) {
            if (Number.isFinite(stored?.[key])) {
                tuning[key] = stored[key];
            }
        }
    } catch (error) {
        console.warn("Could not read tuning:", error);
    }
}

function storeAxes() {
    try {
        window.localStorage.setItem(
            AXES_STORAGE_KEY,
            JSON.stringify({
                left: sabers.left.forwardLocal,
                right: sabers.right.forwardLocal
            })
        );
    } catch (error) {
        console.warn("Could not store axes:", error);
    }
}

function storeHands() {
    try {
        window.localStorage.setItem(
            HANDS_STORAGE_KEY,
            JSON.stringify(handMapping)
        );
    } catch (error) {
        console.warn("Could not store hand mapping:", error);
    }
}

function storeTuning() {
    try {
        window.localStorage.setItem(
            TUNING_STORAGE_KEY,
            JSON.stringify(tuning)
        );
    } catch (error) {
        console.warn("Could not store tuning:", error);
    }
}

/*
    Applies a change to the control corrections, saves it, and
    says on screen what it now is. Every adjustment goes through
    here so none of them can be made without being shown.
*/
function adjustTuning(change, label) {
    Object.assign(tuning, change);

    tuning.gain = clamp(tuning.gain, 0.3, 3);
    tuning.responsiveness = clamp(tuning.responsiveness, 0.3, 4);

    tuning.beatStride = Math.round(
        clamp(tuning.beatStride, 1, 8)
    );

    storeTuning();

    statusText.textContent =
        `${label} — ` +
        `flip X ${tuning.invertX < 0 ? "on" : "off"}, ` +
        `flip Y ${tuning.invertY < 0 ? "on" : "off"}, ` +
        `sensitivity ${tuning.gain.toFixed(2)}, ` +
        `response ${tuning.responsiveness.toFixed(2)}, ` +
        `1 block per ${tuning.beatStride} beats, ` +
        `rumble ${tuning.rumble ? "on" : "off"}`;

    window.clearTimeout(adjustTuning.timer);
    adjustTuning.timer = window.setTimeout(updateStatus, 2500);
}


/* =========================================================
   SENSOR FUSION

   The library's own fused orientation cannot be used.

   Its per-sample readings are scaled correctly — accelerometer
   at 244e-6 g per count, gyroscope at 0.06103 deg/s per count,
   both standard for this hardware. The problem is what it does
   with them:

     - The field it calls "rps" is 0.06103 / 360, which is
       revolutions per second, not radians per second. It is
       passed straight into a filter expecting radians, so it
       arrives 2*PI too small.

     - calculateActualGyroscope then averages the samples and
       multiplies by a further 0.005 * sampleCount.

   Together the gyroscope reaches the fusion filter around 420
   times smaller than reality, which is indistinguishable from
   no gyroscope at all. What is left is an accelerometer-only
   estimate: it can see gravity, so it knows tilt, but it is
   noisy under any hand movement and has no yaw information
   whatsoever, leaving heading free to wander.

   So we run our own fusion from the raw per-sample values.

   This is a Mahony complementary filter. The gyroscope carries
   short-term motion, which is what makes tracking feel
   attached to your hand, while gravity slowly corrects tilt so
   it cannot accumulate error. The integral term estimates the
   gyroscope's zero offset, which is the main source of heading
   drift.
   ========================================================= */

/*
    Correction strength.

    PROPORTIONAL:
        How hard gravity pulls the estimate straight.
        Higher = tilt settles faster, more swing contamination.

    INTEGRAL:
        Learns the gyroscope's resting offset. This is what
        stops the pointer sliding on its own. Keep it small.
*/
const FUSION_PROPORTIONAL = 1.0;
const FUSION_INTEGRAL = 0.06;

/*
    Gravity only tells the truth when the controller is not
    being accelerated. During a swing the accelerometer reads
    the swing as well, so corrections are suspended until the
    magnitude looks like gravity alone again.
*/
const GRAVITY_TOLERANCE = 0.22;

/*
    The IMU reports three samples per packet. Real spacing is
    derived from arrival times, but a bad first packet or a
    stall should not be integrated as a huge step.
*/
const MAX_SAMPLE_SECONDS = 0.02;

const DEG_TO_RAD = Math.PI / 180;

/*
    Pulls the three gyroscope samples out of a packet in real
    degrees per second, and the three matching accelerometer
    samples in g.
*/
function extractSamples(detail) {
    const gyroscopes = detail.gyroscopes;
    const accelerometers = detail.accelerometers;

    if (!Array.isArray(gyroscopes) || !Array.isArray(accelerometers)) {
        return null;
    }

    const count = Math.min(gyroscopes.length, accelerometers.length);

    const samples = [];

    for (let index = 0; index < count; index += 1) {
        const gyro = gyroscopes[index];
        const accel = accelerometers[index];

        if (!gyro || !accel) {
            continue;
        }

        const gx = gyro[0]?.dps;
        const gy = gyro[1]?.dps;
        const gz = gyro[2]?.dps;

        const ax = accel.x?.acc;
        const ay = accel.y?.acc;
        const az = accel.z?.acc;

        if (
            !Number.isFinite(gx) || !Number.isFinite(gy) ||
            !Number.isFinite(gz) || !Number.isFinite(ax) ||
            !Number.isFinite(ay) || !Number.isFinite(az)
        ) {
            continue;
        }

        samples.push({
            gyro: {
                x: gx * DEG_TO_RAD * GYRO_SIGN,
                y: gy * DEG_TO_RAD * GYRO_SIGN,
                z: gz * DEG_TO_RAD * GYRO_SIGN
            },
            accel: { x: ax, y: ay, z: az }
        });
    }

    return samples.length ? samples : null;
}

/*
    Advances one controller's orientation estimate by a single
    IMU sample.
*/
function fuseSample(state, sample, deltaTime) {
    const q = state.fusionQuaternion;

    let wx = sample.gyro.x;
    let wy = sample.gyro.y;
    let wz = sample.gyro.z;

    const accel = normalize(sample.accel);

    const magnitude = Math.hypot(
        sample.accel.x,
        sample.accel.y,
        sample.accel.z
    );

    const trustGravity =
        accel &&
        Math.abs(magnitude - 1) < GRAVITY_TOLERANCE;

    if (trustGravity) {
        /*
            Where the estimate currently thinks "up" is, seen
            from the controller.
        */
        const estimated = {
            x: 2 * (q.x * q.z - q.w * q.y),
            y: 2 * (q.w * q.x + q.y * q.z),
            z: q.w * q.w - q.x * q.x - q.y * q.y + q.z * q.z
        };

        /*
            The rotation needed to bring the estimate onto what
            gravity actually says.
        */
        const error = cross(accel, estimated);

        state.gyroBias.x += error.x * FUSION_INTEGRAL * deltaTime;
        state.gyroBias.y += error.y * FUSION_INTEGRAL * deltaTime;
        state.gyroBias.z += error.z * FUSION_INTEGRAL * deltaTime;

        wx += FUSION_PROPORTIONAL * error.x + state.gyroBias.x;
        wy += FUSION_PROPORTIONAL * error.y + state.gyroBias.y;
        wz += FUSION_PROPORTIONAL * error.z + state.gyroBias.z;
    } else {
        wx += state.gyroBias.x;
        wy += state.gyroBias.y;
        wz += state.gyroBias.z;
    }

    const half = 0.5 * deltaTime;

    const next = {
        w: q.w + (-q.x * wx - q.y * wy - q.z * wz) * half,
        x: q.x + (q.w * wx + q.y * wz - q.z * wy) * half,
        y: q.y + (q.w * wy - q.x * wz + q.z * wx) * half,
        z: q.z + (q.w * wz + q.x * wy - q.y * wx) * half
    };

    state.fusionQuaternion = quaternionNormalize(next);
}

/*
    Runs fusion over every sample in a packet and returns the
    updated orientation.
*/
function updateFusion(state, detail) {
    const samples = extractSamples(detail);

    if (!samples) {
        return null;
    }

    const now = performance.now();

    let perSample = MAX_SAMPLE_SECONDS;

    if (state.lastPacketTime) {
        perSample = clamp(
            (now - state.lastPacketTime) / 1000 / samples.length,
            0.001,
            MAX_SAMPLE_SECONDS
        );
    }

    state.lastPacketTime = now;

    for (const sample of samples) {
        fuseSample(state, sample, perSample);
    }

    state.fusionSettling = Math.max(
        0,
        state.fusionSettling - samples.length * perSample
    );

    return state.fusionQuaternion;
}

function angularSpeed(state) {
    return state.angularSpeed;
}

/*
    Measures how fast the controller is actually turning, in
    degrees per second, from the angle between consecutive
    orientation readings.

    Doing it this way rather than reading the gyro means the
    number means the same thing on any controller and does not
    depend on the library's scaling choices.
*/
function updateAngularSpeed(state, quaternion) {
    const now = performance.now();

    const previous = state.previousQuaternion;

    if (previous) {
        const elapsed =
            (now - state.previousQuaternionTime) / 1000;

        if (elapsed > 0.0005) {
            const delta = quaternionMultiply(
                quaternionConjugate(previous),
                quaternion
            );

            const angle =
                2 *
                Math.acos(clamp(Math.abs(delta.w), -1, 1)) *
                RAD_TO_DEG;

            const instantaneous = angle / elapsed;

            /*
                Lightly smoothed so a single noisy packet does
                not read as a swing.
            */
            state.angularSpeed +=
                (instantaneous - state.angularSpeed) * 0.35;
        }
    }

    state.previousQuaternion = quaternion;
    state.previousQuaternionTime = now;
}

const IDENTIFY_BUTTONS = [
    "a", "b", "x", "y",
    "up", "down", "left", "right",
    "l", "r", "zl", "zr", "sl", "sr",
    "plus", "minus",
    "leftStick", "rightStick",
    "home", "capture"
];

function anyButtonPressed(state) {
    const buttons = state.buttons;

    if (!buttons) {
        return false;
    }

    /*
        chargingGrip is deliberately excluded — it reports the
        dock state, not a press.
    */
    return IDENTIFY_BUTTONS.some(
        name => buttons[name] === true
    );
}


/* =========================================================
   FRAME CONSTRUCTION
   ========================================================= */

function resetFilters(state) {
    state.continuousYaw = null;
    state.continuousPitch = null;
    state.continuousRoll = null;

    state.driftYaw = 0;
    state.driftRate = 0;

    state.yawFilter.reset();
    state.pitchFilter.reset();
    state.rollFilter.reset();

    state.swingX = 0;
    state.swingY = 0;
    state.angle = 0;

    state.handX = anchorX(screenSideOf(state.device));
    state.handY = anchorY();

    /*
        The trail holds positions from before the reset, which
        would otherwise smear across the screen on the first
        frame after recentring.
    */
    state.trail.length = 0;

    state.previousTipX = null;
    state.previousTipY = null;
    state.tipVelocityX = 0;
    state.tipVelocityY = 0;
    state.slashCooldown = 0;
}

/*
    Where the controller is aimed, in world terms.

    The long axis is rotated into world coordinates and read as
    a direction: how far round it is pointing, and how far up.

    Because the world frame is built on gravity, elevation is
    absolute — it cannot drift, and it needs no reference pose.
    Heading has no such anchor, so it is the only part measured
    against your play pose.
*/
function aimDirection(state) {
    if (!state.forwardLocal || !state.fusionStarted) {
        return null;
    }

    return rotateVector(
        state.fusionQuaternion,
        state.forwardLocal
    );
}

/*
    Builds a set of reference directions centred on your play
    pose: where you were aiming, and what counts as right and up
    from there.

    Heading used to be measured around the world vertical. That
    has a singularity directly overhead, and a saber is held
    steeply enough to sit near it — so heading became jumpy, and
    tipping past vertical flipped it by half a turn outright.
    Since the two hands are never held at quite the same angle,
    it would strike one saber and not the other, which is what
    made one of them run backwards.

    Centring the frame on your play pose puts that singularity a
    right angle away from anywhere you actually aim.

    It also removes the need for a sign correction on the
    horizontal axis. Right is now defined explicitly by the
    cross product rather than inherited from a maths convention
    that happened to count the other way.
*/
function captureNeutral(state) {
    const forward = aimDirection(state);

    if (!forward) {
        return false;
    }

    const worldUp = { x: 0, y: 0, z: 1 };

    let right = normalize(cross(forward, worldUp));

    if (!right) {
        /*
            Aimed straight up or down, where right genuinely has
            no meaning. Any perpendicular will do.
        */
        right = normalize(
            cross(forward, { x: 1, y: 0, z: 0 })
        ) ?? { x: 0, y: 1, z: 0 };
    }

    state.neutralForward = forward;
    state.playerRight = right;
    state.playerUp = cross(right, forward);

    resetFilters(state);

    return true;
}


/* =========================================================
   CALIBRATION

   One controller at a time, one pose at a time, each captured
   by pressing a button on the controller being calibrated.

   The button press does double duty: it says "capture now",
   and it says which controller you are holding, because the
   press arrives from that specific device. So the hand
   assignment falls out of the same step that measures the
   pose, with nothing inferred.

   Deliberately there is no automatic "hold still" detection.
   An automatic gate that fails to fire is indistinguishable
   from one whose threshold is wrong, which leaves you stuck
   with no way to tell what went wrong.

   For each hand:

     Ceiling pose — at rest the accelerometer reads straight
       up. Held pointing at the ceiling, that reading IS the
       controller's long axis, sign included. Measuring each
       Joy-Con separately handles the mirrored left/right
       sensor mounting without hard-coding anything.

     Play pose — gravity now says which way is up as seen from
       the controller, which is enough to build a frame aligned
       to you: forward, right and up.
   ========================================================= */

const CALIBRATION_SEQUENCE = [
    { hand: "right", phase: "ceiling" },
    { hand: "right", phase: "neutral" },
    { hand: "left", phase: "ceiling" },
    { hand: "left", phase: "neutral" }
];

const calibration = {
    active: false,
    index: 0,

    /*
        Which physical controller each hand turned out to be,
        learned as the sequence runs.
    */
    handDevice: { left: null, right: null }
};

function connectedDevices() {
    return ["left", "right"].filter(
        device => sabers[device].connected
    );
}

function currentStep() {
    return calibration.active
        ? CALIBRATION_SEQUENCE[calibration.index]
        : null;
}

function startCalibration() {
    const devices = connectedDevices();

    if (!devices.length) {
        statusText.textContent = "Connect a Joy-Con first";
        return;
    }

    calibration.active = true;
    calibration.index = 0;
    calibration.handDevice = { left: null, right: null };

    /*
        Require a fresh press for the first step rather than
        counting a button that is already held down.
    */
    for (const device of devices) {
        sabers[device].buttonWasPressed = true;
    }

    updatePrompt();
    updateStatus();
}

function cancelCalibration() {
    calibration.active = false;

    updatePrompt();
    updateStatus();
}

function finishCalibration() {
    calibration.active = false;

    storeAxes();
    storeHands();

    updatePrompt();

    statusText.textContent = "Calibrated";

    setTimeout(updateStatus, 1500);
}

/*
    Confirms a capture on the controller itself, so you are not
    reading the screen while holding a pose.
*/
function confirmOnDevice(state) {
    const joyCon = state.joyCon;

    if (!joyCon || typeof joyCon.rumble !== "function") {
        return;
    }

    try {
        joyCon.rumble(160, 320, 0.35);

        setTimeout(() => {
            try {
                joyCon.rumble(160, 320, 0);
            } catch (error) {
                /* The controller may have gone away. */
            }
        }, 120);
    } catch (error) {
        console.warn("Could not rumble:", error);
    }
}

/*
    THE HIT.

    A cut in the real game is felt, not just seen: the
    controller kicks the instant the blade meets the block, and
    how hard depends on how hard you swung. That kick is most
    of what makes a block feel like an object rather than a
    sprite that vanished.

    A Joy-Con rumble is an amplitude and two frequencies, so an
    impact is built as an envelope over them rather than one
    flat buzz:

      The strike is a single hard, low pulse. Low frequencies
      read as mass, so this is what gives the block weight.

      The drag that follows is quieter and higher, and decays.
      This is the blade finishing its travel through the block
      — the friction, and the part that keeps the hit from
      feeling like a click.

    Both scale with swing speed, so a lazy cut is a tap and a
    committed one lands.
*/
const IMPACT_STEPS = [
    { at: 0,  low: 130, high: 260, amplitude: 1.00 },
    { at: 40, low: 200, high: 450, amplitude: 0.45 },
    { at: 85, low: 280, high: 720, amplitude: 0.18 }
];

/*
    A block reached you. The opposite shape to an impact: a
    rougher, higher buzz that says something went wrong rather
    than something landed, so a miss and a cut are told apart
    by feel without looking at the score.
*/
const DAMAGE_STEPS = [
    { at: 0,  low: 320, high: 160, amplitude: 0.60 },
    { at: 60, low: 290, high: 150, amplitude: 0.38 },
    { at: 120, low: 260, high: 140, amplitude: 0.16 }
];

/*
    Below this the rumble is inaudible on the hardware and only
    costs an output report.
*/
const MIN_IMPACT_AMPLITUDE = 0.05;

/*
    RUMBLE IS NOT FREE.

    The motors are bolted to the same board as the gyroscope.
    Running them shakes the IMU, and that shake arrives as real
    angular velocity — so a controller that is buzzing is a
    controller whose orientation is being corrupted while it
    buzzes. Short bursts are fine, because the filter rides
    over them. Continuous rumble is not.

    The first version of this could leave the motors running
    indefinitely. Each new event cleared the pending timers of
    the one before it, and the last step of every envelope is
    the one that sets the amplitude back to zero — so a second
    event arriving mid-envelope cancelled the stop and the
    motors simply stayed on. Misses come in bursts, so in play
    this latched on and stayed on, which is what made the
    controllers feel unstable and drift in odd directions.

    Now the stop is unconditional: it is scheduled on its own
    timer that nothing cancels, and a fresh event reschedules
    it rather than dropping it. Envelopes are also short and
    rate limited, so the motors are off far more than they are
    on.
*/
const RUMBLE_MIN_GAP_MS = 90;
const RUMBLE_MAX_MS = 200;

function stopRumble(state) {
    const joyCon = state.joyCon;

    if (!joyCon || typeof joyCon.rumble !== "function") {
        return;
    }

    try {
        joyCon.rumble(160, 320, 0);
    } catch (error) {
        /* The controller may have gone away. */
    }
}

function playRumble(state, steps, scale) {
    if (!tuning.rumble) {
        return;
    }

    const joyCon = state.joyCon;

    if (!joyCon || typeof joyCon.rumble !== "function") {
        return;
    }

    const now = performance.now();

    /*
        Dropped rather than queued. A rumble that arrives after
        the event it belongs to is worse than no rumble, and
        queueing them is how the motors end up always on.
    */
    if (now - (state.lastRumbleAt ?? -Infinity) < RUMBLE_MIN_GAP_MS) {
        return;
    }

    state.lastRumbleAt = now;

    if (state.rumbleTimers) {
        for (const timer of state.rumbleTimers) {
            clearTimeout(timer);
        }
    }

    const strength = clamp(scale, 0, 1);

    state.rumbleTimers = [];

    for (const step of steps) {
        const amplitude = step.amplitude * strength;

        if (amplitude < MIN_IMPACT_AMPLITUDE) {
            continue;
        }

        state.rumbleTimers.push(
            setTimeout(() => {
                try {
                    joyCon.rumble(step.low, step.high, amplitude);
                } catch (error) {
                    /* The controller may have gone away. */
                }
            }, step.at)
        );
    }

    /*
        The stop lives on its own timer, outside the set that a
        later event clears, so the motors always get switched
        off even if this envelope is superseded.
    */
    clearTimeout(state.rumbleStopTimer);

    state.rumbleStopTimer = setTimeout(
        () => stopRumble(state),
        RUMBLE_MAX_MS
    );
}

function impactOnDevice(state, strength) {
    playRumble(state, IMPACT_STEPS, strength);
}

/*
    Captures whichever pose the current step is asking for.
    Returns an error message, or null when it worked.
*/
function captureStep(step, device) {
    const state = sabers[device];

    if (!state.rawQuaternion) {
        return "no orientation data from that controller yet";
    }

    if (step.phase === "ceiling") {
        /*
            The smoothed accelerometer is used rather than the
            latest sample, so the nudge from pressing the button
            does not end up baked into the axis.
        */
        const axis = normalize(
            state.accelSmooth || state.accelerometer
        );

        if (!axis) {
            return "no gravity reading from that controller";
        }

        state.forwardLocal = axis;

        return null;
    }

    if (!captureNeutral(state)) {
        return "could not build a frame from that pose";
    }

    return null;
}

/*
    Driven by button presses rather than by the frame clock,
    so this runs whenever input arrives.
*/
function advanceCalibration() {
    const step = currentStep();

    if (!step) {
        return;
    }

    const devices = connectedDevices();

    if (!devices.length) {
        cancelCalibration();
        return;
    }

    /*
        Only a fresh press counts. Without an edge check, one
        held button would run through every remaining step.
    */
    const pressed = devices.filter(device => {
        const state = sabers[device];

        return (
            state.buttonIsPressed &&
            !state.buttonWasPressed
        );
    });

    if (pressed.length !== 1) {
        return;
    }

    const device = pressed[0];

    /*
        Once a hand is bound to a controller, the rest of that
        hand's steps must come from the same one.
    */
    const expected = calibration.handDevice[step.hand];

    if (expected && expected !== device) {
        statusText.textContent =
            "That is the other Joy-Con — use the same one " +
            "for both of its steps";

        return;
    }

    const otherHand = step.hand === "left" ? "right" : "left";

    if (calibration.handDevice[otherHand] === device) {
        statusText.textContent =
            `That Joy-Con is already your ${otherHand} hand`;

        return;
    }

    const problem = captureStep(step, device);

    if (problem) {
        statusText.textContent = `Not captured — ${problem}`;
        return;
    }

    calibration.handDevice[step.hand] = device;

    /*
        Keep the mapping a clean pairing — binding one
        controller to a hand implies the other one takes the
        opposite hand.
    */
    handMapping[device] = step.hand;
    handMapping[device === "left" ? "right" : "left"] = otherHand;

    confirmOnDevice(sabers[device]);

    statusText.textContent = "Captured";

    calibration.index += 1;

    /*
        Skip any hand that has no controller left to assign, so
        a single Joy-Con can be set up on its own.
    */
    while (calibration.index < CALIBRATION_SEQUENCE.length) {
        const next = CALIBRATION_SEQUENCE[calibration.index];

        if (calibration.handDevice[next.hand]) {
            break;
        }

        const bound = Object.values(calibration.handDevice)
            .filter(Boolean);

        const unbound = devices.filter(
            candidate => !bound.includes(candidate)
        );

        if (unbound.length) {
            break;
        }

        calibration.index += 1;
    }

    if (calibration.index >= CALIBRATION_SEQUENCE.length) {
        finishCalibration();
        return;
    }

    updatePrompt();
}

function updatePrompt() {
    if (!promptText) {
        return;
    }

    const step = currentStep();

    if (!step) {
        promptText.hidden = true;
        return;
    }

    const hand = step.hand.toUpperCase();

    const instruction =
        step.phase === "ceiling"
            ? `hold it pointing straight UP at the ceiling`
            : `hold it exactly how you want to play, ` +
              `aimed at the screen`;

    promptMessage.textContent =
        `${calibration.index + 1} / ` +
        `${CALIBRATION_SEQUENCE.length} — ` +
        `Take the Joy-Con for your ${hand} hand, ` +
        `${instruction}, then press any button on that ` +
        `same Joy-Con.`;

    promptText.hidden = false;
}

/*
    Live readout while calibrating.

    Without this, a step that will not advance gives you
    nothing to go on. Seeing which controller is sending data,
    and whether its buttons register, makes it obvious where
    the problem is.
*/
function updateCalibrationFeedback() {
    if (!promptFeedback || !calibration.active) {
        return;
    }

    const readings = ["left", "right"].map(device => {
        const state = sabers[device];

        if (!state.connected) {
            return `${device}: off`;
        }

        return (
            `${device}: ` +
            `${state.rawQuaternion ? "motion ok" : "NO MOTION"} ` +
            `${state.buttonIsPressed ? "[button]" : ""}`
        );
    });

    promptFeedback.textContent = readings.join("    ");
}



/* =========================================================
   HID INPUT

   Runs on every packet and does as little as possible.
   ========================================================= */

function handleJoyConInput(joyCon, device, detail) {
    const state = sabers[device];

    /*
        Counted so the diagnostics panel can say whether a
        controller is sending anything, and whether what it
        sends carries motion. "Connected but silent" and
        "sending, but without IMU data" are different faults
        with different fixes, and they look identical from
        outside.
    */
    state.packets = (state.packets ?? 0) + 1;
    state.lastPacketAt = performance.now();

    if (detail.actualAccelerometer) {
        state.imuPackets = (state.imuPackets ?? 0) + 1;
    }

    /*
        Seed the estimate from gravity on the first packet, so
        it starts already upright instead of spending a second
        rotating into place.
    */
    if (!state.fusionStarted && detail.actualAccelerometer) {
        const up = normalize(detail.actualAccelerometer);

        if (up) {
            state.fusionQuaternion = quaternionAligning(
                up,
                { x: 0, y: 0, z: 1 }
            );

            state.fusionStarted = true;
        }
    }

    const quaternion = updateFusion(state, detail);

    if (quaternion) {
        updateAngularSpeed(state, quaternion);

        state.rawQuaternion = quaternion;
    }

    if (detail.actualAccelerometer) {
        state.accelerometer = detail.actualAccelerometer;

        /*
            Roughly a third of a second of history, which is
            enough to ride out the jolt of a button press.
        */
        if (!state.accelSmooth) {
            state.accelSmooth = { ...detail.actualAccelerometer };
        } else {
            const blend = 0.06;

            state.accelSmooth.x +=
                (detail.actualAccelerometer.x -
                    state.accelSmooth.x) * blend;

            state.accelSmooth.y +=
                (detail.actualAccelerometer.y -
                    state.accelSmooth.y) * blend;

            state.accelSmooth.z +=
                (detail.actualAccelerometer.z -
                    state.accelSmooth.z) * blend;
        }
    }

    if (detail.actualGyroscope?.dps) {
        state.gyroscope = detail.actualGyroscope.dps;
    }

    if (detail.buttonStatus) {
        state.buttons = detail.buttonStatus;
    }

    /*
        Calibration is driven from here rather than from the
        frame loop, so a press is acted on the moment it
        arrives. The edge is closed afterwards so one held
        button cannot run through several steps.
    */
    const nowPressed = anyButtonPressed(state);

    state.buttonIsPressed = nowPressed;

    if (calibration.active) {
        advanceCalibration();
    } else if (nowPressed && !state.buttonWasPressed) {
        /*
            Outside calibration a button press recentres, on
            the reasoning that the only reason to press one
            mid-song is that the saber is sitting wrong. Having
            to reach for the keyboard to fix that meant putting
            a controller down, which is its own interruption.
        */
        recenter();
    }

    state.buttonWasPressed = nowPressed;
}


/* =========================================================
   MOVEMENT
   ========================================================= */

/*
    Turns where the controller is aimed into a screen position.

    This is the projection of the controller's length onto the
    screen: the long axis is treated as a pointer, and where its
    line meets the screen plane is where the saber goes. The
    tangent is what makes it a true projection rather than a
    plain angle reading, so sweeping at a constant rate moves
    the saber the way a torch beam sweeps across a wall.
*/
function projectAim(state) {
    const aim = aimDirection(state);

    if (!aim || !state.neutralForward) {
        return null;
    }

    /*
        Where the controller is aimed, read against your play
        pose: how far to the right of it, how far above it, and
        how much still points along it.
    */
    const alongRight = dot(aim, state.playerRight);
    const alongUp = dot(aim, state.playerUp);
    const alongForward = dot(aim, state.neutralForward);

    const heading = Math.atan2(alongRight, alongForward);

    const rise = Math.atan2(
        alongUp,
        Math.hypot(alongRight, alongForward)
    );

    /*
        Returned as true angles in degrees.

        The projection onto the screen is deliberately left
        until the very end of the pipeline. Everything between
        here and there — unwrapping, drift correction, the
        filters — only means anything on a real angle. An
        earlier version applied the tangent here, and then the
        unwrapping treated a projection ratio as though it were
        an angle that wraps at 180 degrees, which injected whole
        turns into the signal and left the drift tracker chasing
        a corrupted value.
    */
    return {
        yaw: heading * RAD_TO_DEG,
        pitch: rise * RAD_TO_DEG,
        roll: twistAngleDegrees(
            state.fusionQuaternion,
            state.forwardLocal
        )
    };
}


function updateSaber(state, deltaTime) {
    if (!isReady(state) || calibration.active) {
        return;
    }

    const angles = projectAim(state);

    if (!angles) {
        return;
    }

    state.continuousYaw =
        unwrapDegrees(state.continuousYaw, angles.yaw);

    state.continuousPitch =
        unwrapDegrees(state.continuousPitch, angles.pitch);

    state.continuousRoll =
        unwrapDegrees(state.continuousRoll, angles.roll);

    /*
        Cancel yaw drift while the controller is essentially
        stationary. Only yaw needs this — gravity keeps pitch
        and roll honest, but nothing anchors heading, so it
        winds slowly on its own and would leave the saber
        sitting askew when you are holding it straight.

        Small residuals are pulled back gently at any time the
        controller is still, on the assumption that they are
        drift. A large one might instead be you holding the
        saber out to one side on purpose, so it is only taken
        as drift once you have been still long enough that
        nothing else explains it.

        The old thresholds could not do this. Correction
        required being under 5 degrees a second — stiller than
        a hand holding a controller ever is — and stopped
        entirely past 8 degrees of error, so a session that
        drifted further than that could never recover, and the
        saber kept a wrong neutral until the page was reloaded.
    */
    const residual = state.continuousYaw - state.driftYaw;

    /*
        YAW CORRECTION, AND WHEN IT IS ALLOWED TO RUN.

        A Joy-Con has no magnetometer, so heading has nothing
        absolute to hold on to. Gyroscope bias winding the
        estimate off course and you turning your body look
        identical to it, and neither can be detected directly,
        so heading has to be inferred from where you aim.

        Over a stretch of play that inference is sound: the
        blocks come in a grid in front of you, so the average
        of where you point is where forward is. The leaky
        integrator below is that average.

        It is only sound WHILE YOU ARE PLAYING. Resting your
        arms at your sides is not a statement about where
        neutral is — it is you not playing — and an earlier
        version of this took it for one, deliberately
        re-centring after a couple of seconds of stillness on
        the reasoning that a resting controller must be at
        neutral. It is not: arms down is a completely
        different orientation, so re-centring onto it moved
        neutral to the parked pose and the calibration was
        gone the moment you lifted the saber again. That path
        is deleted rather than retuned, because no threshold
        makes a wrong premise right.

        What tells the two apart is pitch. Pitch is measured
        against gravity, so unlike heading it cannot drift and
        is always trustworthy. A saber held up to play sits
        near the pose you calibrated; a saber hanging at your
        side is far below it. So the axis that cannot drift
        decides when the axis that does is allowed to be
        corrected, and parking your arms simply stops the
        correction rather than poisoning it.
    */
    const inPlayPose =
        Math.abs(state.continuousPitch) < AUTO_CENTRE_MAX_PITCH;

    if (inPlayPose) {
        /*
            Averaging alone cannot keep up with drift, because
            drift is a ramp and not an offset: the gyroscope's
            zero is wrong by some number of degrees per second,
            so the heading runs away at a steady rate and a
            filter chasing position is permanently behind it.
            Simulated against a 2 deg/s bias, the average
            settled about 21 degrees adrift and stayed there —
            which is roughly the error that keeps showing up
            in play.

            Note that this bias is specifically the one the
            fusion filter cannot remove. It learns the
            gyroscope's offset by comparing against gravity,
            and gravity says nothing about heading, so the yaw
            component of the bias survives into here.

            So the rate is estimated too, and subtracted. The
            correction becomes proportional plus integral
            rather than proportional alone, and a constant
            drift rate is then tracked with no standing error
            instead of a fixed lag. Same simulation: mean
            error zero, worst case under seven degrees.
        */
        state.driftRate = clamp(
            (state.driftRate ?? 0) +
                residual * DRIFT_RATE_GAIN * deltaTime,
            -DRIFT_RATE_LIMIT,
            DRIFT_RATE_LIMIT
        );

        state.driftYaw +=
            residual *
                (1 - Math.exp(-deltaTime / AUTO_CENTRE_TAU)) +
            state.driftRate * deltaTime;

        /*
            A faster pull for small errors while the controller
            is steady, so ordinary drift is taken out promptly
            instead of waiting on the long average.
        */
        if (
            angularSpeed(state) < DRIFT_STILL_DPS &&
            Math.abs(residual) < DRIFT_SNAP_DEGREES
        ) {
            state.driftYaw +=
                residual *
                (1 - Math.exp(-deltaTime / DRIFT_TAU_SECONDS));
        }
    }

    const correctedYaw = state.continuousYaw - state.driftYaw;

    const yaw = state.yawFilter.filter(correctedYaw, deltaTime);

    const pitch = state.pitchFilter.filter(
        state.continuousPitch,
        deltaTime
    );

    const roll = state.rollFilter.filter(
        state.continuousRoll,
        deltaTime
    );

    /*
        Project ahead to cancel the delay the filter adds.
        Clamped so a spike cannot throw the saber across the
        screen.
    */
    const yawLead = clamp(
        state.yawFilter.derivative * PREDICTION_SECONDS,
        -MAX_PREDICTION_DEGREES,
        MAX_PREDICTION_DEGREES
    );

    const pitchLead = clamp(
        state.pitchFilter.derivative * PREDICTION_SECONDS,
        -MAX_PREDICTION_DEGREES,
        MAX_PREDICTION_DEGREES
    );

    /*
        Only now, with real angles all the way through, is the
        projection taken.
    */
    /*
        The angles go through to the blade as they are.

        There is no tangent projection and no soft limit any
        more. Both existed to squeeze an unbounded aim into a
        fixed box on screen, and both are what made the saber
        stop responding once you had turned far enough. The
        lever has no such limit: keep turning and it keeps
        sweeping, the way an arm does.

        gain stays available for anyone who wants less than
        one-to-one, but 1 is now a true one-to-one mapping
        rather than a rescaling of an already distorted one.
    */
    state.aimYaw =
        (yaw + yawLead) * tuning.gain * tuning.invertX;

    state.aimPitch =
        (pitch + pitchLead) * tuning.gain * tuning.invertY;

    state.headingDegrees = state.aimYaw;

    state.twist = roll * tuning.invertRoll;

    /*
        Kept as a normalised readout for the parts that still
        talk in deflection rather than angle. Nothing about the
        blade depends on it now.
    */
    state.swingX = clamp(state.aimYaw / MAX_YAW_DEGREES, -1, 1);
    state.swingY = clamp(state.aimPitch / MAX_PITCH_DEGREES, -1, 1);

    updateBlade(state, deltaTime);

    /*
        Useful for deciding whether a swing was fast enough to
        count as a cut.
    */
    state.swingSpeed = Math.hypot(
        state.yawFilter.derivative,
        state.pitchFilter.derivative
    );
}


/* =========================================================
   BLADE AND SLASHES

   Each saber is held at a fixed point near the bottom of the
   screen and swings about it in three dimensions. The blade is
   a fixed-length object pointing wherever the controller does;
   how long it looks is left entirely to the perspective.

   Directions here are in screen axes: X to the right, Y down,
   Z out of the screen toward you. So aiming away from yourself
   is negative Z.

   A slash is read from how the tip travels across the screen
   plane, ignoring depth — pushing the saber straight away from
   you is a thrust, not a slash. It always resolves to one of
   four directions: whichever axis the tip is moving along more
   strongly wins, so a somewhat diagonal swing still counts as
   the cardinal direction it is nearest rather than being
   rejected.
   ========================================================= */

function anchorX(screenSide) {
    return screenSide === "left"
        ? viewport.width * 0.5 - ANCHOR_SEPARATION / 2
        : viewport.width * 0.5 + ANCHOR_SEPARATION / 2;
}

function anchorY() {
    return viewport.height - ANCHOR_BOTTOM_MARGIN;
}

function classifySlash(velocityX, velocityY) {
    if (Math.abs(velocityX) > Math.abs(velocityY)) {
        return velocityX > 0 ? "right" : "left";
    }

    /*
        Screen Y grows downward.
    */
    return velocityY > 0 ? "down" : "up";
}

function updateBlade(state, deltaTime) {
    const screenSide = screenSideOf(state.device);

    /*
        One rigid lever from the shoulder: the arm out to your
        hand, then the blade carrying on in the same line. The
        controller's own angles drive it directly.
    */
    const heading = state.aimYaw * DEG_TO_RAD;

    /*
        Held angled up by default. Stopped just short of
        straight overhead, where the blade would project to
        nothing and heading would become meaningless — but that
        is a singularity, not a range limit, and it is the only
        one left.
    */
    const rise = clamp(
        state.aimPitch + REST_TILT_DEGREES,
        -88,
        88
    ) * DEG_TO_RAD;

    /*
        Where the lever points, in screen axes.
    */
    const across = Math.cos(rise) * Math.sin(heading);
    const upward = Math.sin(rise);
    const away = Math.cos(rise) * Math.cos(heading);

    state.directionX = across;
    state.directionY = -upward;
    state.directionZ = -away;

    const shoulderX = anchorX(screenSide);
    const shoulderY = anchorY() + SHOULDER_DROP;

    state.handX = shoulderX + state.directionX * ARM_LENGTH;
    state.handY = shoulderY + state.directionY * ARM_LENGTH;
    state.handZ = SHOULDER_Z + state.directionZ * ARM_LENGTH;

    const baseX = state.handX;
    const baseY = state.handY;

    recordTrail(state);

    /*
        Kept for the readout — the angle the blade appears to
        lean at, ignoring depth.
    */
    state.angle = Math.atan2(across, upward) * RAD_TO_DEG;

    const tipX = baseX + state.directionX * BLADE_LENGTH;
    const tipY = baseY + state.directionY * BLADE_LENGTH;

    state.tipZ = state.handZ + state.directionZ * BLADE_LENGTH;

    if (state.previousTipX !== null) {
        const rawX = (tipX - state.previousTipX) / deltaTime;
        const rawY = (tipY - state.previousTipY) / deltaTime;

        /*
            Smoothed a little so a single jittery frame cannot
            register as a slash.
        */
        const blend = 0.4;

        state.tipVelocityX += (rawX - state.tipVelocityX) * blend;
        state.tipVelocityY += (rawY - state.tipVelocityY) * blend;
    }

    state.previousTipX = tipX;
    state.previousTipY = tipY;

    state.tipX = tipX;
    state.tipY = tipY;

    const speed = Math.hypot(
        state.tipVelocityX,
        state.tipVelocityY
    );

    state.tipSpeed = speed;

    /*
        The live swing, recorded every frame it qualifies.

        Scoring used to reuse the slash event below, but that
        is throttled to one per SLASH_COOLDOWN_SECONDS so it
        can drive the visual effects without repeating them.
        Borrowing it for scoring meant that during a long
        sweep the swing only officially existed for one frame
        in every eleven, and a block crossed on any other
        frame went uncut. This has no cooldown, because a
        swing does not stop being a swing between effects.
    */
    if (speed >= SLASH_SPEED) {
        const swing = cutDirectionOf(state);

        if (swing) {
            state.swingDirection = swing;
            state.swingAt = performance.now();
        }
    }

    if (state.slashCooldown > 0) {
        state.slashCooldown -= deltaTime;
    }

    /*
        One slash per swing. The cooldown stops a single fast
        motion registering over and over, and the blade has to
        slow down again before another can start.
    */
    if (speed > SLASH_SPEED && state.slashCooldown <= 0) {
        const direction = classifySlash(
            state.tipVelocityX,
            state.tipVelocityY
        );

        state.slashCooldown = SLASH_COOLDOWN_SECONDS;
        state.lastSlash = direction;
        state.lastSlashAt = performance.now();

        window.dispatchEvent(
            new CustomEvent("saberslash", {
                detail: {
                    side: screenSide,
                    direction,
                    speed,
                    x: tipX,
                    y: tipY
                }
            })
        );
    }
}


/* =========================================================
   RENDER LOOP
   ========================================================= */

let lastFrameTime = performance.now();

/*
    Trail segments are built here rather than written out by
    hand, since there are a lot of them and they are all alike.
*/
const trailElements = { left: [], right: [] };

function buildTrails() {
    const stage = document.querySelector("#stage");

    if (!stage) {
        return;
    }

    for (const screenSide of ["left", "right"]) {
        for (let index = 0; index < TRAIL_SEGMENTS; index += 1) {
            const segment = document.createElement("div");

            segment.className = `saber trail ${screenSide}-ink`;

            /*
                Older segments are dimmer and narrower, which is
                what gives the arc its taper.
            */
            const age = index / TRAIL_SEGMENTS;

            /*
                Kept on the element so the draw loop can scale
                it by how fast the saber is moving without
                recomputing the taper every frame.
            */
            segment.dataset.base =
                (TRAIL_OPACITY * (1 - age) ** 1.7).toFixed(4);

            segment.style.width =
                `${(14 * (1 - age * 0.75)).toFixed(1)}px`;

            stage.appendChild(segment);

            trailElements[screenSide].push(segment);
        }
    }
}

buildTrails();

/*
    Each saber keeps the last few directions it pointed in, so
    the trail can be drawn through them.
*/
function recordTrail(state) {
    /*
        The hand moves too, so each segment has to remember
        where it was held as well as where it pointed.
    */
    state.trail.unshift({
        x: state.directionX,
        y: state.directionY,
        z: state.directionZ,
        twist: state.twist,
        handX: state.handX,
        handY: state.handY,
        handZ: state.handZ
    });

    if (state.trail.length > TRAIL_SEGMENTS) {
        state.trail.length = TRAIL_SEGMENTS;
    }
}

function drawTrail(state, screenSide, intensity) {
    const segments = trailElements[screenSide];

    for (let index = 0; index < segments.length; index += 1) {
        const segment = segments[index];
        const past = state.trail[index];

        /*
            No smear at all when the saber is resting. The trail
            fades in with speed, so it only appears when there is
            actually a swing to describe.
        */
        if (!past || intensity < 0.02) {
            segment.style.visibility = "hidden";
            continue;
        }

        segment.style.visibility = "visible";

        segment.style.opacity = (
            Number(segment.dataset.base) * intensity
        ).toFixed(3);

        segment.style.transform =
            `${bladeTransform(past.handX, past.handY, past.handZ ?? HAND_Z, past)} ` +
            `rotateY(${past.twist.toFixed(2)}deg) ` +
            `scaleY(${state.ignition.toFixed(3)})`;
    }
}

/*
    The transform that stands a blade panel up from the hand and
    points it along a direction.

    The panel stands along its own -Y when untransformed, so
    this is the rotation taking -Y onto that direction. Turning
    -Y onto anything only ever needs an axis lying in the XZ
    plane, which is why the Y term is always zero.
*/
function bladeTransform(baseX, baseY, baseZ, direction) {
    const alignment = clamp(-direction.y, -1, 1);
    const turn = Math.acos(alignment) * RAD_TO_DEG;

    let axisX = -direction.z;
    let axisZ = direction.x;

    if (Math.hypot(axisX, axisZ) < 1e-6) {
        /*
            Already straight up or straight down, so any axis in
            that plane will do.
        */
        axisX = 1;
        axisZ = 0;
    }

    return (
        `translate3d(${baseX.toFixed(2)}px, ` +
        `${baseY.toFixed(2)}px, ${baseZ.toFixed(2)}px) ` +
        `translate(-50%, -100%) ` +
        `rotate3d(${axisX.toFixed(5)}, 0, ${axisZ.toFixed(5)}, ` +
        `${turn.toFixed(2)}deg)`
    );
}

function drawSaber(state, deltaTime) {
    const screenSide = screenSideOf(state.device);

    const element = saberElements[screenSide];

    if (!element) {
        return;
    }

    /*
        The blade extends when the saber comes up and retracts
        when it goes away, rather than snapping into existence.
    */
    const target = isReady(state) ? 1 : 0;
    const step = deltaTime / IGNITION_SECONDS;

    state.ignition = target > state.ignition
        ? Math.min(target, state.ignition + step)
        : Math.max(target, state.ignition - step);

    /*
        The hand is wherever the swing has carried it, not a
        fixed anchor.
    */
    const baseX = state.handX;
    const baseY = state.handY;

    const direction = {
        x: state.directionX,
        y: state.directionY,
        z: state.directionZ
    };

    const orient = bladeTransform(
        baseX,
        baseY,
        state.handZ ?? HAND_Z,
        direction
    );

    /*
        How hard the saber is being swung, measured against the
        threshold a slash has to cross. This drives the glow and
        the trail, so effort shows.
    */
    const intensity = clamp(
        (state.tipSpeed / SLASH_SPEED) * GLOW_RESPONSE,
        0,
        1
    );

    const grow = `scaleY(${state.ignition.toFixed(3)})`;

    /*
        A flat panel disappears when seen edge on, so the blade
        is two panels crossed at a right angle about its own
        length. One of them always faces you.

        The scale at the end is the blade extending from the
        hilt, which grows from the hand because that is where
        the transform origin sits.
    */
    element.style.transform =
        `${orient} rotateY(${state.twist.toFixed(2)}deg) ${grow}`;

    element.style.setProperty("--glow", intensity.toFixed(3));

    const cross = crossElements[screenSide];

    if (cross) {
        cross.style.transform =
            `${orient} rotateY(${(state.twist + 90).toFixed(2)}deg) ` +
            grow;

        cross.style.setProperty("--glow", intensity.toFixed(3));
    }

    /*
        The hilt lies along the blade at the hand and does not
        extend, so it is deliberately left unscaled.
    */
    const hilt = hiltElements[screenSide];

    if (hilt) {
        hilt.style.transform =
            `${orient} rotateY(${state.twist.toFixed(2)}deg)`;
    }

    drawTrail(state, screenSide, intensity);

    /*
        Placed with no rotation of its own, so it stays facing
        you while perspective shrinks it with distance.

        It rides the extending blade, so it sits at whatever
        fraction of full length is currently lit.
    */
    const tip = tipElements[screenSide];

    if (tip) {
        const lit = state.ignition;

        tip.style.opacity = lit.toFixed(3);

        tip.style.transform =
            `translate3d(` +
            `${(baseX + state.directionX * BLADE_LENGTH * lit).toFixed(2)}px, ` +
            `${(baseY + state.directionY * BLADE_LENGTH * lit).toFixed(2)}px, ` +
            `${((state.handZ ?? HAND_Z) + state.directionZ * BLADE_LENGTH * lit).toFixed(2)}px) ` +
            `translate(-50%, -50%)`;

        tip.style.setProperty("--glow", intensity.toFixed(3));
    }

    /*
        Brief flash on the blade when a slash registers.
    */
    const sinceSlash = performance.now() - state.lastSlashAt;

    element.classList.toggle("slashing", sinceSlash < 110);
}

function updateSlashReadout() {
    if (!slashText) {
        return;
    }

    const parts = ["left", "right"].map(screenSide => {
        const state = sabers[deviceForScreenSide(screenSide)];

        const fresh =
            state.lastSlash &&
            performance.now() - state.lastSlashAt < 600;

        return `${screenSide}: ${fresh ? state.lastSlash : "—"}`;
    });

    slashText.textContent = parts.join("    ");
}

function updateDebug() {
    if (!SHOW_DEBUG || !debugText) {
        return;
    }

    const lines = [];

    for (const device of ["left", "right"]) {
        const state = sabers[device];

        const label =
            `${device} joycon → ${screenSideOf(device)} saber`;

        if (!state.connected) {
            lines.push(`${label}: not connected`);
            continue;
        }

        /*
            Live sensor status is shown whether or not the
            controller is calibrated. Knowing that motion and
            buttons are arriving is the first thing worth
            checking when something is not responding.
        */
        const sensors =
            `${state.rawQuaternion ? "motion" : "NO MOTION"} ` +
            `${state.buttons ? "" : "NO BUTTONS "}` +
            `${state.buttonIsPressed ? "[pressed] " : ""}` +
            `turn ${state.angularSpeed.toFixed(0)}°/s`;

        if (!state.forwardLocal) {
            lines.push(
                `${label}: needs calibration — ${sensors}`
            );

            continue;
        }

        if (!isReady(state)) {
            lines.push(
                `${label}: needs recenter — ${sensors}`
            );

            continue;
        }

        const axis = state.forwardLocal;

        lines.push(
            `${label}: ` +
            `blade ${state.angle.toFixed(0)}° ` +
            `heading ${(state.headingDegrees ?? 0).toFixed(1)}° ` +
            `tip ${state.tipSpeed.toFixed(0)}px/s ` +
            `slash ${state.lastSlash ?? "—"} ` +
            `${sensors} ` +
            `swing ${state.swingSpeed.toFixed(0)}°/s ` +
            `drift ${state.driftYaw.toFixed(1)}° ` +
            `(${(state.driftRate ?? 0).toFixed(2)}°/s` +
            `${
                Math.abs(state.continuousPitch ?? 0) <
                AUTO_CENTRE_MAX_PITCH
                    ? ""
                    : ", parked"
            }) ` +
            `axis [${axis.x.toFixed(2)}, ` +
            `${axis.y.toFixed(2)}, ${axis.z.toFixed(2)}]`
        );
    }

    debugText.textContent = lines.join("\n");
}

/*
    Test mode: Direct mouse control for both sabers
*/
function updateTestModeSabers(deltaTime) {
    /*
        Test mode drives the same lever the Joy-Cons drive, so
        what it shows is what the controllers will do. It only
        stands in for the controller's angles: the mouse across
        the window is read as an arm swept across and up.
    */
    const normalizedX = ((mouseX / viewport.width) - 0.5) * 2;
    const normalizedY = ((mouseY / viewport.height) - 0.5) * 2;

    const targetYaw = normalizedX * TEST_MODE_YAW_DEGREES;
    const targetPitch = -normalizedY * TEST_MODE_PITCH_DEGREES;

    /*
        Framerate-independent easing. The old fixed 0.25 per
        frame meant the mouse felt twice as sharp on a 120Hz
        screen as on a 60Hz one.
    */
    const follow = 1 - Math.exp(-deltaTime / TEST_MODE_TAU);

    for (const side of ["left", "right"]) {
        const state = sabers[side];

        state.aimYaw = (state.aimYaw ?? 0) +
            (targetYaw - (state.aimYaw ?? 0)) * follow;

        state.aimPitch = (state.aimPitch ?? 0) +
            (targetPitch - (state.aimPitch ?? 0)) * follow;

        state.ignition = 1;

        updateBlade(state, deltaTime);
    }
}

function renderFrame(now) {
    /*
        Guard against a zero or absurd delta after a tab has
        been in the background, which would make the filter
        produce nonsense.
    */
    const deltaTime = clamp(
        (now - lastFrameTime) / 1000,
        0.001,
        0.05
    );

    lastFrameTime = now;

    /*
        Calibration itself is driven by button presses in the
        input handler. Only its readout belongs here.
    */
    updateCalibrationFeedback();

    // TEST MODE: Direct mouse control for both sabers
    if (testMode) {
        updateTestModeSabers(deltaTime);
    } else {
        updateSaber(sabers.left, deltaTime);
        updateSaber(sabers.right, deltaTime);
    }

    drawSaber(sabers.left, deltaTime);
    drawSaber(sabers.right, deltaTime);

    buildStrikeZone();
    buildPetals();
    updateDancers(deltaTime);
    updateBlocks(deltaTime);
    updateSlashReadout();

    updateDebug();

    requestAnimationFrame(renderFrame);
}

requestAnimationFrame(renderFrame);


/* =========================================================
   STATUS
   ========================================================= */

function updateStatus() {
    if (calibration.active) {
        statusText.textContent = "Calibrating…";
        return;
    }

    const devices = connectedDevices();

    if (!devices.length) {
        statusText.textContent = "No Joy-Cons connected";
        return;
    }

    const uncalibrated = devices.filter(
        device => !sabers[device].forwardLocal
    );

    if (uncalibrated.length) {
        statusText.textContent =
            "Press Calibrate to set up the controllers";

        return;
    }

    const uncentred = devices.filter(
        device => !isReady(sabers[device])
    );

    if (uncentred.length) {
        statusText.textContent =
            "Press Recenter while holding your play pose";

        return;
    }

    statusText.textContent =
        devices.length === 2
            ? "Both Joy-Cons ready"
            : `${devices[0]} Joy-Con ready — connect the other`;
}


/* =========================================================
   CONTROLLER INITIALIZATION
   ========================================================= */

/*
    WHICH SLOT A CONTROLLER TAKES.

    There are two slots, named left and right, and the name is
    an identity rather than a claim about the hardware — which
    hand each controller actually is gets measured during
    calibration, because the left and right Joy-Con have their
    sensors mounted mirrored and assuming it would be wrong
    half the time.

    That makes refusing a controller for having an unexpected
    product id, which is what this used to do, a restriction
    with nothing behind it. The ids 0x2006 and 0x2007 are the
    original Joy-Con pair; a revision, a Joy-Con 2, a third
    party pad or a controller reached through a grip reports
    something else, was turned away with a line in the console,
    and its slot stayed empty — so it streamed input that
    nothing read. The symptom is a controller that connects and
    then does nothing, which is exactly the hardest kind to
    diagnose from the outside.

    So identification is layered, and the last layer always
    succeeds:

      1  the product id, for the controllers we know
      2  the product name, since a Joy-Con of any revision
         says (L) or (R) in it somewhere
      3  whichever slot is free

    Step 3 is what makes an unknown controller work anyway.
    Calibration will ask it which hand it is, the same as for
    one that was recognised.
*/
function preferredSlot(hidDevice) {
    if (hidDevice.productId === LEFT_PRODUCT_ID) {
        return "left";
    }

    if (hidDevice.productId === RIGHT_PRODUCT_ID) {
        return "right";
    }

    const name = hidDevice.productName ?? "";

    if (/\(\s*L\s*\)/i.test(name)) {
        return "left";
    }

    if (/\(\s*R\s*\)/i.test(name)) {
        return "right";
    }

    return null;
}

/*
    Which slot each controller ended up in. Keyed by the JoyCon
    object so the answer is stable: the detection loop asks again
    every second, and a controller that changed slots between
    two packets would be a saber that changed hands mid swing.
*/
const deviceSlots = new Map();

function slotForJoyCon(joyCon) {
    const existing = deviceSlots.get(joyCon);

    if (existing) {
        return existing;
    }

    const taken = new Set(deviceSlots.values());

    const wanted = preferredSlot(joyCon.device);

    /*
        The preferred slot, unless something is already in it —
        two left Joy-Cons, or a pair whose ids both read the
        same, are then still usable as two sabers instead of
        one overwriting the other.
    */
    const slot =
        wanted && !taken.has(wanted)
            ? wanted
            : ["left", "right"].find(
                  candidate => !taken.has(candidate)
              );

    if (!slot) {
        return null;
    }

    deviceSlots.set(joyCon, slot);

    if (slot !== wanted) {
        console.warn(
            `Controller 0x${joyCon.device.productId.toString(16)} ` +
            `"${joyCon.device.productName}" did not identify ` +
            `itself; using the ${slot} slot. Calibration will ` +
            `ask which hand it is.`
        );
    }

    return slot;
}

/*
    Frees the slot a controller held, so unplugging one and
    plugging in another does not run the pair out of slots.
*/
function releaseJoyCon(joyCon) {
    deviceSlots.delete(joyCon);
}

async function initializeJoyCon(joyCon) {
    /*
        The detection loop can fire again while this is still
        awaiting. The flag is set before the first await so a
        second call cannot attach a duplicate listener.
    */
    if (joyCon.setupStarted) {
        return;
    }

    const productId = joyCon.device.productId;
    const device = slotForJoyCon(joyCon);

    if (!device) {
        console.warn(
            "Both saber slots are already in use; ignoring " +
            `0x${productId.toString(16)}.`
        );

        return;
    }

    joyCon.setupStarted = true;

    try {
        /*
            The library opens the device itself when it adopts
            it, and its open() attaches an inputreport listener
            every time it is called without checking whether it
            already has one. Calling it again here therefore
            did not fail — it quietly doubled the listeners, so
            every packet was handled twice and the fusion
            integrated each one twice over.
        */
        if (!joyCon.device.opened) {
            await joyCon.open();
        }

        await joyCon.enableStandardFullMode();
        await joyCon.enableIMUMode();

        /*
            The rumble motors are off until this subcommand is
            sent. Without it every rumble call is accepted and
            silently does nothing, which is why cutting a block
            did not feel like hitting anything.
        */
        try {
            await joyCon.enableVibration();
        } catch (error) {
            console.warn("Could not enable vibration:", error);
        }

        joyCon.addEventListener("hidinput", ({ detail }) => {
            handleJoyConInput(joyCon, device, detail);
        });

        sabers[device].connected = true;
        sabers[device].joyCon = joyCon;

        /*
            A page that reloads mid-rumble leaves the motors
            running until the controller is power cycled.
        */
        window.addEventListener("beforeunload", () => {
            stopRumble(sabers[device]);
        });

        updateStatus();

        console.log(`${device} slot initialized`, {
            productName: joyCon.device.productName,
            productId: `0x${productId.toString(16)}`,
            recognised: preferredSlot(joyCon.device) !== null
        });
    } catch (error) {
        console.error(
            `${device} Joy-Con initialization failed:`,
            error
        );

        joyCon.setupStarted = false;

        sabers[device].connected = false;

        statusText.textContent =
            `${device} connection error: ${error.message}`;
    }
}


/* =========================================================
   BUTTONS AND KEYS
   ========================================================= */

connectButton.addEventListener("click", async () => {
    try {
        statusText.textContent = "Select one Joy-Con…";

        /*
            The browser picker allows one controller at a time.
            Click again for the other one.
        */
        await JoyCon.connectJoyCon();

        statusText.textContent = "Selected — initializing…";
    } catch (error) {
        console.error("Joy-Con selection failed:", error);

        statusText.textContent =
            `Selection error: ${error.message}`;
    }
});

calibrateButton.addEventListener("click", startCalibration);

/*
    Recentering only rebuilds the frame around your current
    pose. The long axis found during calibration is kept, so
    this is the quick one to use between songs.
*/
function recenter() {
    const devices = connectedDevices().filter(
        device => sabers[device].forwardLocal
    );

    if (!devices.length) {
        statusText.textContent = "Calibrate first";
        return;
    }

    const failed = devices.filter(
        device => !captureNeutral(sabers[device])
    );

    statusText.textContent = failed.length
        ? `Could not recenter ${failed.join(" and ")}`
        : "Recentered";

    setTimeout(updateStatus, 1200);
}

recenterButton.addEventListener("click", recenter);

/*
    An escape hatch, in case the shake step picked the wrong
    hand or you switch controllers between hands.
*/
function swapHands() {
    const previous = handMapping.left;

    handMapping.left = handMapping.right;
    handMapping.right = previous;

    storeHands();

    for (const device of ["left", "right"]) {
        resetFilters(sabers[device]);
    }

    statusText.textContent = "Hands swapped";

    setTimeout(updateStatus, 1200);
}

swapButton.addEventListener("click", swapHands);


/* =========================================================
   PLAYING YOUR OWN MUSIC

   Until now the track was whatever had been prepared in
   advance: the bundled song, or what the audio editor wrote
   out. Anything else meant running extract_beats.py by hand
   and rebuilding, which is the kind of errand that means
   nobody ever plays their own music.

   A song dropped on the page is decoded, analysed for its
   beats by the same pipeline the Python uses, and becomes the
   track — no server, no build step, and the file never leaves
   the machine.
   ========================================================= */

const songButton = document.querySelector("#song-button");
const songInput = document.querySelector("#song-input");
const songDrop = document.querySelector("#song-drop");
const songStatus = document.querySelector("#song-status");

/*
    Object URLs for tracks loaded this session. A previous one
    is revoked when it is replaced, because the file it points
    at is held in memory until it is.
*/
let loadedSongUrl = null;

let analysing = false;

function setSongStatus(text, kind) {
    if (!songStatus) {
        return;
    }

    if (!text) {
        songStatus.hidden = true;
        return;
    }

    songStatus.textContent = text;
    songStatus.className = kind ?? "";
    songStatus.hidden = false;
}

/*
    Analysis is a second of arithmetic on the main thread, so
    the status line has to be painted before it starts or it
    will not appear until after the work it is describing.
*/
function nextFrame() {
    return new Promise(resolve =>
        requestAnimationFrame(() => requestAnimationFrame(resolve))
    );
}

async function loadSongFile(file) {
    if (!file || analysing) {
        return;
    }

    /*
        A dropped file is whatever the player dragged in. The
        decoder is the real check — it either decodes or it does
        not — but rejecting the obvious cases first gives a
        useful message instead of a decoder error.
    */
    if (file.type && !file.type.startsWith("audio/")) {
        setSongStatus(
            `${file.name} is not audio (${file.type})`, "bad"
        );
        return;
    }

    analysing = true;

    /*
        Blocks in flight were aimed at beats in the old track,
        and its clock is about to be replaced. Stopping first is
        what keeps them from being judged against the new one.
    */
    const wasRunning = blocksRunning;

    if (blocksRunning) {
        toggleBlocks();
    }

    setSongStatus(`Reading ${file.name}…`);

    await nextFrame();

    try {
        setSongStatus(`Finding the beat in ${file.name}…`);

        await nextFrame();

        const started = performance.now();

        const { analyseSongFile } = await import("./beatdetect.js");

        const result = await analyseSongFile(file);

        const elapsed = Math.round(performance.now() - started);

        if (!result.beatmap.total_beats) {
            setSongStatus(
                `No beat found in ${file.name}. ` +
                "Blocks will spawn on a timer.",
                "bad"
            );
        }

        if (loadedSongUrl) {
            URL.revokeObjectURL(loadedSongUrl);
        }

        loadedSongUrl = result.url;

        beatmap = result.beatmap;
        beatmapSource = file.name;

        /*
            Everything downstream of the beatmap is indexed
            against the old track and has to start again: the
            beat cursor, the blocks planned from it, and the
            stall watchdog, whose clock is about to jump.
        */
        beatIndex = 0;
        patternBeat = 0;
        pendingSpawns.length = 0;
        musicClock = -1;
        musicStalled = 0;

        for (let i = blocks.length - 1; i >= 0; i -= 1) {
            removeBlock(i);
        }

        if (backgroundMusic) {
            backgroundMusic.pause();
            backgroundMusic.dataset.custom = "1";
            backgroundMusic.src = loadedSongUrl;
            backgroundMusic.loop = true;
            backgroundMusic.currentTime = 0;
        }

        if (musicInfo) {
            musicInfo.textContent =
                `♫ ${result.name} — ${result.beatmap.bpm} BPM, ` +
                `${result.beatmap.total_beats} beats`;

            musicInfo.hidden = false;
        }

        if (result.beatmap.total_beats) {
            setSongStatus(
                `${result.name}: ${result.beatmap.bpm} BPM, ` +
                `${result.beatmap.total_beats} beats ` +
                `(${elapsed} ms). Press B to play.`,
                "good"
            );
        }

        console.log(
            `Loaded ${result.name}: ${result.beatmap.total_beats} ` +
            `beats at ${result.beatmap.bpm} BPM, analysed in ` +
            `${elapsed} ms`
        );

        /*
            If they were already playing, carry straight on with
            the new track rather than making them press B again.
        */
        if (wasRunning) {
            toggleBlocks();
        }
    } catch (error) {
        console.error(error);

        setSongStatus(
            `Could not read ${file.name}: ${error.message}`, "bad"
        );
    } finally {
        analysing = false;
    }
}

if (songButton && songInput) {
    songButton.addEventListener("click", () => songInput.click());

    songInput.addEventListener("change", () => {
        loadSongFile(songInput.files?.[0]);

        /*
            Cleared so that choosing the same file again still
            fires a change event.
        */
        songInput.value = "";
    });
}

/*
    Drag and drop, on the window rather than on the overlay:
    the overlay only exists while something is being dragged,
    so it cannot be what notices the drag beginning.

    The counter is because dragenter and dragleave both fire
    when the pointer crosses between child elements. Tracking
    depth rather than a flag is what stops the overlay
    flickering as the file moves across the playfield.
*/
let dragDepth = 0;

function showDrop(show) {
    if (songDrop) {
        songDrop.classList.toggle("over", show);
    }
}

window.addEventListener("dragenter", event => {
    event.preventDefault();

    dragDepth += 1;
    showDrop(true);
});

window.addEventListener("dragover", event => {
    /*
        Without this the browser navigates away to the file,
        which loses the game.
    */
    event.preventDefault();
});

window.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);

    if (!dragDepth) {
        showDrop(false);
    }
});

window.addEventListener("drop", event => {
    event.preventDefault();

    dragDepth = 0;
    showDrop(false);

    loadSongFile(event.dataTransfer?.files?.[0]);
});


/* =========================================================
   CONTROLLER DIAGNOSTICS

   Answers, for each slot, the questions that a saber which
   will not move actually turns on — in the order they fail:

     is anything in this slot at all
     is it sending packets
     do those packets carry motion, or only buttons
     has the fusion started, and has the slot been calibrated
     which buttons are being seen right now

   The last line is the one that settles "its input isn't
   taken": press a button and either a name appears or it does
   not, and that distinguishes a controller that is not heard
   from one whose buttons are read under names this game does
   not look at.
   ========================================================= */

const devicesPanel = document.querySelector("#devices");

let devicesVisible = false;

/*
    Packet counts are totals, so a rate needs the previous
    reading and the time since it was taken.
*/
const packetHistory = {
    left: { packets: 0, imu: 0, at: 0 },
    right: { packets: 0, imu: 0, at: 0 }
};

function joyConForSlot(slot) {
    for (const [joyCon, assigned] of deviceSlots) {
        if (assigned === slot) {
            return joyCon;
        }
    }

    return null;
}

function pressedButtonNames(state) {
    if (!state.buttons) {
        return null;
    }

    return IDENTIFY_BUTTONS.filter(
        name => state.buttons[name] === true
    );
}

function updateDeviceDiagnostics() {
    if (!devicesPanel || !devicesVisible) {
        return;
    }

    const now = performance.now();
    const lines = [];

    for (const slot of ["left", "right"]) {
        const state = sabers[slot];
        const joyCon = joyConForSlot(slot);

        lines.push(`<b>${slot} slot</b>`);

        if (!joyCon) {
            lines.push(
                '  <span class="dim">empty — press ' +
                "Connect Joy-Con</span>"
            );

            continue;
        }

        const hid = joyCon.device;

        const recognised = preferredSlot(hid);

        lines.push(
            `  ${hid.productName || "(no name reported)"}  ` +
            `0x${hid.productId.toString(16)}` +
            (recognised
                ? ""
                : '  <span class="warn">(unrecognised, ' +
                  "slot assigned)</span>")
        );

        if (!state.connected) {
            lines.push(
                '  <span class="bad">not initialised</span> — ' +
                "see the console for why"
            );

            continue;
        }

        /*
            Rates rather than totals: a controller that sent a
            hundred packets and then stopped looks healthy by
            its total and is the fault being looked for.
        */
        const history = packetHistory[slot];
        const seconds = history.at ? (now - history.at) / 1000 : 0;

        const rate = seconds
            ? Math.round(
                  ((state.packets ?? 0) - history.packets) / seconds
              )
            : 0;

        const imuRate = seconds
            ? Math.round(
                  ((state.imuPackets ?? 0) - history.imu) / seconds
              )
            : 0;

        history.packets = state.packets ?? 0;
        history.imu = state.imuPackets ?? 0;
        history.at = now;

        const silent =
            state.lastPacketAt === undefined ||
            now - state.lastPacketAt > 1000;

        lines.push(
            "  packets " +
            (silent
                ? '<span class="bad">SILENT</span>'
                : `<span class="good">${rate}/s</span>`) +
            "   with motion " +
            (imuRate > 0
                ? `<span class="good">${imuRate}/s</span>`
                : '<span class="bad">0/s — IMU mode did not ' +
                  "take</span>")
        );

        lines.push(
            "  fusion " +
            (state.fusionStarted
                ? '<span class="good">started</span>'
                : '<span class="bad">not started</span>') +
            "   axis " +
            (state.forwardLocal
                ? '<span class="good">set</span>'
                : '<span class="warn">uncalibrated</span>') +
            "   pose " +
            (state.neutralForward
                ? '<span class="good">set</span>'
                : '<span class="warn">press Recenter</span>')
        );

        const names = pressedButtonNames(state);

        lines.push(
            "  buttons " +
            (names === null
                ? '<span class="bad">no button data in ' +
                  "packets</span>"
                : names.length
                    ? `<span class="good">${names.join(" ")}</span>`
                    : '<span class="dim">none pressed</span>')
        );

        lines.push(
            `  drives the <b>${screenSideOf(slot)}</b> saber`
        );
    }

    devicesPanel.innerHTML = lines.join("\n");
}

setInterval(updateDeviceDiagnostics, 250);

function toggleDeviceDiagnostics() {
    devicesVisible = !devicesVisible;

    if (devicesPanel) {
        devicesPanel.hidden = !devicesVisible;
    }

    if (devicesVisible) {
        /*
            Rates are measured between samples, so the first one
            after opening the panel has nothing to compare
            against and would read as zero. Seeding it here means
            the first figure shown is already a real rate.
        */
        for (const slot of ["left", "right"]) {
            packetHistory[slot] = {
                packets: sabers[slot].packets ?? 0,
                imu: sabers[slot].imuPackets ?? 0,
                at: performance.now()
            };
        }

        /*
            Every Nintendo device the page has been granted,
            whether or not the library adopted it — so a
            controller that never appears in a slot can still be
            seen to exist.
        */
        if ("hid" in navigator) {
            navigator.hid.getDevices().then(devices => {
                console.log(
                    "HID devices this page may use:",
                    devices.map(d => ({
                        productName: d.productName,
                        productId: `0x${d.productId.toString(16)}`,
                        vendorId: `0x${d.vendorId.toString(16)}`,
                        opened: d.opened
                    }))
                );
            });
        }
    }
}

if (blocksButton) {
    blocksButton.addEventListener("click", toggleBlocks);
}

// Test mode button
const testModeButton = document.querySelector("#test-mode-button");
if (testModeButton) {
    testModeButton.addEventListener("click", () => {
        testMode = !testMode;
        document.body.classList.toggle('test-mode', testMode);
        testModeButton.style.background = testMode ? "#4caf50" : "#ff9800";
        testModeButton.textContent = testMode ? "Test Mode ON (T)" : "Test Mode (T)";
        statusText.textContent = testMode 
            ? "TEST MODE: Move mouse over blocks quickly!"
            : "Test mode disabled";
        setTimeout(updateStatus, 2000);
    });
}

/*
    Reaching for the mouse does not work while holding two
    controllers.
*/
window.addEventListener("keydown", event => {
    if (event.code === "Space") {
        event.preventDefault();
        recenter();
        return;
    }

    if (event.code === "KeyC") {
        startCalibration();
        return;
    }

    if (event.code === "KeyS") {
        swapHands();
        return;
    }

    if (event.code === "KeyB") {
        toggleBlocks();
        return;
    }

    /*
        Opens the file picker. The same thing the button does,
        for anyone who would rather not reach for the mouse
        between tracks.
    */
    if (event.code === "KeyL") {
        songInput?.click();
        return;
    }

    if (event.code === "KeyD") {
        toggleDeviceDiagnostics();
        return;
    }

    if (event.code === "KeyT") {
        testMode = !testMode;
        document.body.classList.toggle('test-mode', testMode);
        statusText.textContent = testMode 
            ? "TEST MODE: Move mouse over blocks quickly to destroy them"
            : "Test mode disabled";
        setTimeout(updateStatus, 2000);
        return;
    }

    /*
        Control corrections. Flipping an axis mid-play is the
        fastest way to find out which way round the calibrated
        frame actually came out.
    */
    if (event.code === "KeyX") {
        adjustTuning(
            { invertX: -tuning.invertX },
            "Horizontal flipped"
        );
        return;
    }

    if (event.code === "KeyY") {
        adjustTuning(
            { invertY: -tuning.invertY },
            "Vertical flipped"
        );
        return;
    }

    if (event.code === "KeyR") {
        adjustTuning(
            { invertRoll: -tuning.invertRoll },
            "Twist flipped"
        );
        return;
    }

    if (event.code === "BracketLeft") {
        adjustTuning(
            { gain: tuning.gain / 1.15 },
            "Less sensitive"
        );
        return;
    }

    if (event.code === "BracketRight") {
        adjustTuning(
            { gain: tuning.gain * 1.15 },
            "More sensitive"
        );
        return;
    }

    if (event.code === "Semicolon") {
        adjustTuning(
            { responsiveness: tuning.responsiveness / 1.2 },
            "Smoother"
        );
        return;
    }

    if (event.code === "Quote") {
        adjustTuning(
            { responsiveness: tuning.responsiveness * 1.2 },
            "Snappier"
        );
        return;
    }

    if (event.code === "Minus") {
        adjustTuning(
            { beatStride: tuning.beatStride + 1 },
            "Fewer blocks"
        );
        return;
    }

    if (event.code === "Equal") {
        adjustTuning(
            { beatStride: tuning.beatStride - 1 },
            "More blocks"
        );
        return;
    }

    if (event.code === "KeyV") {
        const next = tuning.rumble ? 0 : 1;

        if (!next) {
            for (const device of ["left", "right"]) {
                stopRumble(sabers[device]);
            }
        }

        adjustTuning(
            { rumble: next },
            next ? "Rumble on" : "Rumble off"
        );

        return;
    }

    if (event.code === "Digit0") {
        adjustTuning({ ...TUNING_DEFAULTS }, "Controls reset");
        return;
    }

    if (event.code === "Escape") {
        cancelCalibration();
    }
});


/* =========================================================
   BEAT BLOCKS

   Blocks fly in from the distance toward you, each one marked
   with the hand it belongs to and the direction it has to be
   cut in. Hitting it with the right saber travelling the right
   way destroys it; letting it reach you is a miss.

   Everything here lives in the same 3D stage as the sabers, so
   a block's position and the blade are directly comparable and
   the hit test is a plain distance in space rather than
   anything to do with what ended up drawn where.
   ========================================================= */

/*
    The playfield is sized to what the blade can actually
    sweep. Your hands do not move, so the tip can only ever
    reach one blade length away from them — halving the blade
    halved that reach, and the grid has to sit inside it or
    blocks would be uncuttable no matter how well you swung.
*/
const BLOCK_SIZE = 85;  // Increased from 58 to 85 for bigger, more visible blocks

/*
    BLOCK SHAPES

    One cube on every beat is the same swing over and over, so
    the blocks come in shapes instead, and the shape is what
    tells you how to cut it:

      cube    the plain block, any of the four arrows
      wide    a bar across, so only a vertical cut passes
              through its short axis
      tall    a bar upright, so only a horizontal cut does
      long    a cube stretched away down the tunnel. It looks
              the same head on and takes longer to pass
              through, so it reads as heavy
      small   half size. Same swing, much less room for error
      dot     no arrow. Cut it any way you like, which after a
              run of arrows is a moment to just swing
      slab    broad and shallow, a big easy target on a strong
              beat

    dirs lists the cuts the shape allows; null means any, and
    is what makes a dot a dot. lead scales the time the block
    spends in the air: below 1 it travels faster and gives you
    less warning, above 1 it lumbers. points is what a clean
    cut is worth, so the awkward shapes pay for themselves.
*/
const BLOCK_TYPES = [
    {
        name: "cube",
        width: 85, height: 85, depth: 85,
        dirs: ["up", "down", "left", "right"],
        lead: 1.0, weight: 30, points: 100
    },
    {
        name: "wide",
        width: 205, height: 76, depth: 85,
        dirs: ["up", "down"],
        lead: 1.0, weight: 13, points: 150
    },
    {
        name: "tall",
        width: 76, height: 185, depth: 85,
        dirs: ["left", "right"],
        lead: 1.0, weight: 11, points: 150
    },
    {
        name: "long",
        width: 85, height: 85, depth: 300,
        dirs: ["up", "down", "left", "right"],
        lead: 1.16, weight: 10, points: 200
    },
    {
        name: "small",
        width: 52, height: 52, depth: 52,
        dirs: ["up", "down", "left", "right"],
        lead: 0.76, weight: 13, points: 180
    },
    {
        name: "dot",
        width: 96, height: 96, depth: 96,
        dirs: null,
        lead: 0.9, weight: 13, points: 120
    },
    {
        name: "slab",
        width: 150, height: 145, depth: 68,
        dirs: ["up", "down", "left", "right"],
        lead: 1.0, weight: 10, points: 130
    }
];

const BLOCK_TYPE_BY_NAME = {};

for (const type of BLOCK_TYPES) {
    BLOCK_TYPE_BY_NAME[type.name] = type;
}

const BLOCK_TYPE_WEIGHT_TOTAL = BLOCK_TYPES.reduce(
    (sum, type) => sum + type.weight, 0
);

function randomBlockType() {
    let roll = Math.random() * BLOCK_TYPE_WEIGHT_TOTAL;

    for (const type of BLOCK_TYPES) {
        roll -= type.weight;

        if (roll <= 0) {
            return type;
        }
    }

    return BLOCK_TYPES[0];
}

/*
    A shape only allows some cuts, so the arrow has to be
    drawn from what the shape permits rather than from all
    four. A dot permits everything and shows nothing, but it
    still carries a direction for the slash effect to point
    along.
*/
function directionForType(type) {
    const choices = type.dirs ?? CUT_DIRECTIONS;

    return choices[
        Math.floor(Math.random() * choices.length)
    ];
}

/*
    Big blocks are easier to hit than small ones, which is the
    point of having both. Measuring the radius from the shape
    keeps that honest instead of giving every block the same
    generous bubble.
*/
function hitRadiusForType(type) {
    return Math.max(type.width, type.height) / 2 + 105;
}

/*
    Where blocks appear and how fast they travel. Depth runs
    negative into the screen, so they start far away and count
    upward toward you.
*/
const BLOCK_SPAWN_Z = -3500;  // Increased from -2400 so blocks spawn further away

/*
    The reference approach speed, in pixels per second. Every
    block starts at BLOCK_SPAWN_Z, so this sets how long the
    nominal block spends in the air — and a type that wants to
    arrive sooner simply travels faster over the same distance
    rather than starting closer, which would read as popping
    into existence mid-flight.

    600 gave a six second flight. That is a long time to watch
    a block approach, and the whole track played as slow
    motion. 1150 brings it to just over three seconds, which
    is the range the real game sits in.
*/
const BLOCK_SPEED = 1150;

/*
    Blocks are cuttable through this depth window, and counted
    as missed once past it.

    The rule that keeps close blocks hittable is structural,
    not a tuned number: BLOCK_MISS_Z must stay well in front of
    SHOULDER_Z. The blade only ever swings forward of its
    pivot, so a block that gets behind the pivot cannot be
    touched however well you swing. Judging every block at 320
    while the pivot sits at 520 means none of them ever reaches
    that dead ground.

    Measured worst-lane clearance across the grid at this
    scale:

        z      -325  -200     0   200   300   400
        gap     107    37    27    20    28    19

    So the whole approach is comfortably reachable, and the
    near field — the part that was unreachable twice over —
    is now among the easiest.
*/
const BLOCK_STRIKE_Z = 0;
const BLOCK_STRIKE_DEPTH = 340;
const BLOCK_MISS_Z = 320;

/*
    The depth a block is actually cut at, which is not the
    centre of the strike window. Measured clearance to the
    blade is best around here, so this is where the block and
    the blade genuinely meet.
*/
const BLOCK_HIT_PLANE_Z = 180;

/*
    Lead time is measured to that plane rather than to the
    window's centre. Measuring it to z=0 released blocks so
    they crossed the beat plane on the beat and then took
    another third of a second to reach the blade, which reads
    as arriving late.
*/
const BLOCK_TRAVEL_SECONDS =
    (BLOCK_HIT_PLANE_Z - BLOCK_SPAWN_Z) / BLOCK_SPEED;

/*
    How close the blade has to pass to count as a hit. A little
    larger than the block itself, as is usual — catching a swing
    that visibly went through is worth more than punishing one
    that was a few pixels wide.
*/
const BLOCK_HIT_RADIUS = 150;  // Increased from 115 to 150 to match bigger blocks

/*
    Each block carries its own radius, derived from its shape;
    this is the nominal one, kept for the telemetry record so
    runs stay comparable.
*/

/*
    The playfield: four columns by three rows, as in the game
    this is modelled on.
*/
const BLOCK_COLUMNS = 4;
const BLOCK_ROWS = 3;
const BLOCK_COLUMN_SPACING = 200;  // Increased to 200 for much more space between red/blue
const BLOCK_ROW_SPACING = 75;      // Increased to 75 for more vertical space
const BLOCK_GRID_HEIGHT = 95;

/*
    Only used when there is no beatmap to spawn against. At the
    old approach speed 1.4 seconds was already sparse; now that
    blocks cross in a third of the time it was dead air.
*/
const BLOCK_INTERVAL_SECONDS = 0.8;

const CUT_DIRECTIONS = ["up", "down", "left", "right"];

/*
    How clearly one axis has to beat the other before a swing
    counts as being in that direction. Higher demands a cleaner
    cut and rejects more diagonals.
*/
/*
    How dominant one axis has to be before a swing counts as
    along it. The real game is deliberately generous here — a
    cut is credited with a large tolerance either side of the
    arrow, because a near miss on direction feels like the game
    being wrong rather than the player. 1.35 demanded a cleaner
    diagonal than Beat Saber itself does.
*/
const CUT_DOMINANCE = 1.05;

/*
    The swing speed that earns a full-strength impact. Above
    SLASH_SPEED a cut counts; at this it lands as hard as the
    motors go.
*/
const IMPACT_FULL_SPEED = 11000;

/*
    Even the gentlest qualifying cut should be felt.
*/
const IMPACT_FLOOR = 0.35;

const ARROW_ROTATION = {
    up: 0,
    right: 90,
    down: 180,
    left: 270
};

const blocks = [];

let blocksRunning = false;
let blockTimer = 0;

/*
    PATTERNS

    Blocks planned but not yet released, each with the music
    time it is due to be cut at. Planning has to run ahead of
    spawning because a block's shape decides how long it spends
    in the air, so a fast little one is released later than a
    lumbering long one aimed at the same beat.

    This is also what lets a pattern place a block between two
    beats: a target time is just a number, so a burst can sit
    on the half beat without a beat having been detected there.
*/
const pendingSpawns = [];

/*
    Counts beats that actually carry blocks, not beats in the
    track. Bar position is read off this, so raising the stride
    thins the pattern out without scrambling where the strong
    beats fall.
*/
let patternBeat = 0;

/*
    The gap between beats, kept so a pattern can place
    something on a fraction of one. Taken from the beatmap's
    BPM where there is one, and otherwise measured off the
    beats either side.
*/
let beatInterval = 0.5;

/*
    The longest any shape asks to be in the air. Planning has
    to look at least this far ahead, or the slowest shapes
    would be planned too late to arrive on their beat.
*/
const BLOCK_MAX_LEAD_SECONDS =
    BLOCK_TRAVEL_SECONDS *
    BLOCK_TYPES.reduce(
        (most, type) => Math.max(most, type.lead), 1
    );

/*
    The opposite cut, for the second block of a pair. A pair
    that asks for the same direction from both hands reads as
    one gesture; mirrored, it reads as two.
*/
const OPPOSITE_DIRECTION = {
    up: "down",
    down: "up",
    left: "right",
    right: "left"
};

/*
    ONE BEAT'S WORTH OF BLOCKS.

    Every third beat carrying one cube was rhythmically flat:
    the track had strong beats and weak ones and the blocks
    ignored the difference. This reads the position in the bar
    and answers it, which is what makes a pattern feel like it
    belongs to the music rather than to a timer:

      beat 1  the downbeat. The heavy shapes go here, and this
              is where a pair lands, because you can hear that
              it is the place for one
      beat 2  a light single, usually small or quick
      beat 3  the half bar. Weaker than the downbeat but still
              an accent, so shapes but rarely a pair
      beat 4  a single, and the one that sometimes turns into a
              run into the next bar

    Nothing here is guaranteed, because a pattern you can
    predict exactly is as dull as no pattern at all.
*/
function planBeat(beatTime) {
    const inBar = patternBeat % 4;
    const plans = [];

    const push = (options, at) => {
        const spec = makeBlockSpec(options);

        pendingSpawns.push({
            spec,
            targetTime: at ?? beatTime
        });

        plans.push(spec);
    };

    /*
        A pair: one for each hand, mirrored, on the same beat.
        Kept to compact shapes — two broad ones at once fills
        the whole playfield and there is nowhere left to swing.
    */
    const pair = () => {
        const direction =
            CUT_DIRECTIONS[
                Math.floor(Math.random() * CUT_DIRECTIONS.length)
            ];

        const row = Math.floor(Math.random() * BLOCK_ROWS);

        push({
            side: "left",
            types: ["cube", "small", "dot"],
            direction,
            row
        });

        push({
            side: "right",
            types: ["cube", "small", "dot"],
            direction: OPPOSITE_DIRECTION[direction],
            row
        });
    };

    if (inBar === 0) {
        if (Math.random() < 0.35) {
            pair();
        } else {
            push({
                types: [
                    "slab", "wide", "tall", "long", "cube"
                ]
            });
        }
    } else if (inBar === 1) {
        push({ types: ["small", "cube", "dot"] });
    } else if (inBar === 2) {
        if (Math.random() < 0.2) {
            pair();
        } else {
            push({
                types: ["cube", "wide", "tall", "dot", "small"]
            });
        }
    } else {
        push({ types: ["cube", "small", "dot", "long"] });

        /*
            A run into the next downbeat: two more on the same
            hand, on the half and three quarter beat, all one
            direction so it plays as a single sweep rather than
            three unrelated cuts. This is the figure that makes
            a section feel fast without the blocks themselves
            being any faster.
        */
        if (Math.random() < 0.28) {
            const side = Math.random() < 0.5 ? "left" : "right";

            const direction =
                Math.random() < 0.5 ? "up" : "down";

            const column = randomColumnFor(side);

            push(
                {
                    side, direction, column,
                    types: ["small", "cube"]
                },
                beatTime + beatInterval * 0.5
            );

            push(
                {
                    side, direction, column,
                    types: ["small", "cube"]
                },
                beatTime + beatInterval * 0.75
            );
        }
    }

    patternBeat += 1;

    return plans;
}

/*
    TIMING

    A cut that lands as the block reaches the plane is on the
    beat; one that lands a third of a second early is not, and
    before this the two scored exactly the same. Judging the
    gap is what makes the rhythm worth playing to rather than
    something happening behind the blocks.

    The windows are in seconds either side of the beat, and are
    generous by the standards of a rhythm game — this is a
    whole arm swing, not a key press.
*/
const TIMING_WINDOWS = [
    { name: "PERFECT", within: 0.055, multiplier: 1.0 },
    { name: "GREAT", within: 0.11, multiplier: 0.8 },
    { name: "GOOD", within: 0.2, multiplier: 0.6 },
    { name: "OK", within: Infinity, multiplier: 0.4 }
];

function judgeTiming(error) {
    for (const window of TIMING_WINDOWS) {
        if (error <= window.within) {
            return window;
        }
    }

    return TIMING_WINDOWS[TIMING_WINDOWS.length - 1];
}

/*
    Shown where you are looking, which is the block you just
    cut, rather than in a corner you would have to look away
    to read.
*/
function showJudgment(block, window, early) {
    const node = document.querySelector("#judgment");

    if (!node) {
        return;
    }

    node.textContent =
        window.name === "PERFECT"
            ? "PERFECT"
            : `${window.name} ${early ? "early" : "late"}`;

    node.className = `judge-${window.name.toLowerCase()}`;

    node.style.left = `${block.x}px`;
    node.style.top = `${block.y}px`;

    node.classList.remove("pop");

    void node.offsetWidth;

    node.classList.add("pop");
}

/*
    Used to tell a playing track from a stuck one.
*/
let musicClock = -1;
let musicStalled = 0;

const MUSIC_STALL_SECONDS = 1.5;

const score = {
    hits: 0,
    misses: 0,
    streak: 0,
    best: 0,
    points: 0,
    perfect: 0,
    timed: 0,
    errorTotal: 0
};

function blockLanePosition(column, row) {
    const centreX = viewport.width / 2;
    const centreY = viewport.height / 2;  // Use center of screen instead of bottom

    const x =
        centreX +
        (column - (BLOCK_COLUMNS - 1) / 2) * BLOCK_COLUMN_SPACING;

    const y =
        centreY +
        (row - (BLOCK_ROWS - 1) / 2) * BLOCK_ROW_SPACING;

    return { x, y };
}

/*
    Builds the cube. Only five faces are made — the back is
    never turned toward you.
*/
function buildBlockElement(side, direction, type) {
    const cube = document.createElement("div");

    cube.className = `block ${side}-block block-${type.name}`;

    const width = type.width;
    const height = type.height;
    const depth = type.depth;

    /*
        The element itself is the block's front face in size,
        and every other face is placed relative to it, so the
        box has to be given its real width and height rather
        than inheriting one square size from the stylesheet.
    */
    cube.style.width = `${width}px`;
    cube.style.height = `${height}px`;

    /*
        Five faces of a box, each with its own size: the front
        is width by height, the sides depth by height, the top
        and bottom width by depth. Only five are built because
        the back is never turned toward you.

        Each face is centred in the element before being
        rotated, so a face smaller than the front — a side face
        on a wide block, say — hinges about the box's middle
        instead of its top left corner.
    */
    const faces = [
        {
            w: width, h: height,
            transform: `translateZ(${depth / 2}px)`,
            front: true
        },
        {
            w: depth, h: height,
            transform: `rotateY(90deg) translateZ(${width / 2}px)`
        },
        {
            w: depth, h: height,
            transform: `rotateY(-90deg) translateZ(${width / 2}px)`
        },
        {
            w: width, h: depth,
            transform: `rotateX(90deg) translateZ(${height / 2}px)`
        },
        {
            w: width, h: depth,
            transform: `rotateX(-90deg) translateZ(${height / 2}px)`
        }
    ];

    for (const spec of faces) {
        const face = document.createElement("div");

        face.className = "block-face";

        face.style.inset = "auto";
        face.style.width = `${spec.w}px`;
        face.style.height = `${spec.h}px`;
        face.style.left = `${(width - spec.w) / 2}px`;
        face.style.top = `${(height - spec.h) / 2}px`;
        face.style.transform = spec.transform;

        /*
            The arrow goes on the face pointing at you, which is
            the only one you read the direction from — and a dot
            block has none at all, which is the whole way you
            recognise one.
        */
        if (spec.front && type.dirs) {
            const arrow = document.createElement("div");

            arrow.className = "block-arrow";

            arrow.style.transform =
                `rotate(${ARROW_ROTATION[direction]}deg)`;

            face.appendChild(arrow);
        }

        if (spec.front && !type.dirs) {
            const pip = document.createElement("div");

            pip.className = "block-pip";

            face.appendChild(pip);
        }

        cube.appendChild(face);
    }

    return cube;
}

/*
    THE STRIKE ZONE.

    Depth is invisible on a flat screen, so the player has no
    way to tell where along the tunnel a block becomes
    cuttable. These draw it: lane lines running down the
    playfield to show which column is whose, and a frame
    sitting exactly at the plane where blocks are cut.

    Everything is positioned from the same constants the hit
    test uses, so the marks cannot drift away from the rule
    they describe.
*/
let strikeZoneBuilt = false;

function buildStrikeZone() {
    if (strikeZoneBuilt) {
        return;
    }

    const stage = document.querySelector("#stage");

    if (!stage) {
        return;
    }

    strikeZoneBuilt = true;

    const centreX = viewport.width / 2;
    const centreY = viewport.height / 2;

    const halfWidth =
        ((BLOCK_COLUMNS - 1) * BLOCK_COLUMN_SPACING) / 2 +
        BLOCK_SIZE;

    const halfHeight =
        ((BLOCK_ROWS - 1) * BLOCK_ROW_SPACING) / 2 +
        BLOCK_SIZE;

    const zone = document.createElement("div");

    zone.id = "strike-zone";

    /*
        Lane lines: one per column boundary, run from where
        blocks appear to where they are judged, so a column
        reads as a corridor rather than a column of guesses.
    */
    for (let index = 0; index <= BLOCK_COLUMNS; index += 1) {
        const x =
            centreX +
            (index - BLOCK_COLUMNS / 2) * BLOCK_COLUMN_SPACING;

        const line = document.createElement("div");

        line.className = "lane-line";

        if (index === BLOCK_COLUMNS / 2) {
            line.classList.add("lane-centre");
        }

        line.style.transform =
            `translate3d(${x}px, ${centreY}px, ` +
            `${BLOCK_SPAWN_Z}px) ` +
            `translate(-50%, -50%) rotateX(90deg)`;

        line.style.width = "2px";
        line.style.height =
            `${BLOCK_MISS_Z - BLOCK_SPAWN_Z}px`;

        zone.appendChild(line);
    }

    /*
        The frame marks the plane where a block is closest to
        cuttable — the centre of the window, not its edge.
    */
    const frame = document.createElement("div");

    frame.className = "strike-frame";
    frame.style.width = `${halfWidth * 2}px`;
    frame.style.height = `${halfHeight * 2}px`;
    frame.style.transform =
        `translate3d(${centreX}px, ${centreY}px, ` +
        `${BLOCK_STRIKE_Z}px) translate(-50%, -50%)`;

    zone.appendChild(frame);

    stage.appendChild(zone);
}

/*
    Where a hand's blocks live. Each saber keeps to its own
    half, the way the real patterns do, so the two are not
    constantly crossing over.
*/
function randomColumnFor(side) {
    return side === "left"
        ? Math.floor(Math.random() * 2)
        : 2 + Math.floor(Math.random() * 2);
}

/*
    A tall block is taller than the whole three row grid, so it
    only makes sense on the middle row: anywhere else and half
    of it sits outside the band the blade can reach. Broad
    shapes are centred for the same reason.
*/
function rowForType(type) {
    if (
        type.height > BLOCK_ROW_SPACING * 1.6 ||
        type.width > BLOCK_COLUMN_SPACING * 0.9
    ) {
        return 1;
    }

    return Math.floor(Math.random() * BLOCK_ROWS);
}

/*
    Everything a block needs to exist, decided before it is
    released. Planning is separate from spawning because a
    block's shape sets how long it wants to be in the air, and
    that has to be known in order to release it early enough to
    arrive on its beat.
*/
function makeBlockSpec(options = {}) {
    const side =
        options.side ??
        (Math.random() < 0.5 ? "left" : "right");

    let type = options.type;

    if (!type && options.types) {
        type =
            BLOCK_TYPE_BY_NAME[
                options.types[
                    Math.floor(
                        Math.random() * options.types.length
                    )
                ]
            ];
    }

    if (!type) {
        type = randomBlockType();
    }

    const wanted = options.direction;

    const direction =
        wanted && type.dirs && type.dirs.includes(wanted)
            ? wanted
            : directionForType(type);

    const column = options.column ?? randomColumnFor(side);
    const row = options.row ?? rowForType(type);

    return {
        side,
        type,
        direction,
        column,
        row,
        lead: BLOCK_TRAVEL_SECONDS * type.lead
    };
}

function spawnBlock(spec, targetTime) {
    const stage = document.querySelector("#stage");

    if (!stage) {
        return;
    }

    const plan = spec ?? makeBlockSpec();
    const type = plan.type;

    const lane = blockLanePosition(plan.column, plan.row);

    const element = buildBlockElement(
        plan.side, plan.direction, type
    );

    stage.appendChild(element);

    /*
        Every block starts at the same distance, so a shape that
        wants a shorter flight has to cover that distance
        faster. Deriving the speed from the lead rather than the
        other way round is what keeps a block's arrival at the
        cut plane exactly on its beat whatever shape it is.
    */
    const speed =
        (BLOCK_HIT_PLANE_Z - BLOCK_SPAWN_Z) / plan.lead;

    blocks.push({
        side: plan.side,
        direction: plan.direction,
        type: type.name,
        anyDirection: !type.dirs,
        points: type.points,
        hitRadius: hitRadiusForType(type),
        speed,
        targetTime: targetTime ?? null,
        x: lane.x,
        y: lane.y,
        z: BLOCK_SPAWN_Z,
        element,
        dying: 0
    });
}

function removeBlock(index) {
    const block = blocks[index];

    block.element.remove();
    blocks.splice(index, 1);
}

/*
    Shortest distance from a point to the blade, treating the
    blade as the segment between the hand and the tip.
*/
/*
    Depth counts for less than width and height.

    On a flat screen you aim by sight, and sight carries no
    depth. Measured against a pose that visually sweeps clean
    through a block, the true 3D gap ranged from 6 to 188
    pixels depending only on how far away the block was — so
    the same apparent swing scored or did not according to
    something the player has no way to see. That is the "I
    swung right at it and nothing happened" case.

    Shrinking the depth axis to a third makes the test an
    ellipsoid stretched along the view direction: still exact
    about where the blade is on screen, where you can actually
    judge it, and forgiving about how far down the tunnel it
    is, where you cannot. Across the whole approach this keeps
    the weighted gap under the hit radius, so if it looks like
    you cut it, you cut it.
*/
const DEPTH_TOLERANCE = 0.35;

function distanceToBlade(
    block, baseX, baseY, baseZ, tipX, tipY, tipZ
) {
    const ax = tipX - baseX;
    const ay = tipY - baseY;
    const az = (tipZ - baseZ) * DEPTH_TOLERANCE;

    const bx = block.x - baseX;
    const by = block.y - baseY;
    const bz = (block.z - baseZ) * DEPTH_TOLERANCE;

    const lengthSquared = ax * ax + ay * ay + az * az;

    const along = lengthSquared
        ? clamp((bx * ax + by * ay + bz * az) / lengthSquared, 0, 1)
        : 0;

    return Math.hypot(
        bx - ax * along,
        by - ay * along,
        bz - az * along
    );
}

/*
    Which way a saber is travelling right now, or null when the
    swing is too diagonal to call.

    Unlike the reading used for the slash readout, this refuses
    to commit when neither axis clearly dominates. A cut has to
    be deliberately along one of the four directions, so a vague
    diagonal should not be credited as whichever axis happened
    to win by a hair.
*/
function cutDirectionOf(state) {
    const across = Math.abs(state.tipVelocityX);
    const down = Math.abs(state.tipVelocityY);

    if (across > down * CUT_DOMINANCE) {
        return state.tipVelocityX > 0 ? "right" : "left";
    }

    if (down > across * CUT_DOMINANCE) {
        return state.tipVelocityY > 0 ? "down" : "up";
    }

    return null;
}

/*
    Resting the blade on a block does nothing. To destroy it the
    saber has to be inside it, moving hard, and moving the way
    the arrow says — all three at once.

    This runs every frame rather than only on the instant a
    slash is recognised, so a correct swing still counts when
    the block arrives a frame or two into it.
    
    TEST MODE: Press T to enable mouse-based cutting for testing
    without Joy-Cons.
*/
function checkCuts() {
    nearestBlockDistance = 0;
    let closestDist = Infinity;
    
    for (let index = blocks.length - 1; index >= 0; index -= 1) {
        const block = blocks[index];

        if (block.dying) {
            continue;
        }

        if (
            Math.abs(block.z - BLOCK_STRIKE_Z) >
            BLOCK_STRIKE_DEPTH
        ) {
            continue;
        }

        /*
            Test mode used to cut with a flat 2D check of the
            mouse against the block, which meant it scored in a
            different place from where the blade was drawn and
            told you nothing about how the real thing would
            play. It drives the same lever the controllers do,
            so it goes through the same cut as well.
        */
        const state = sabers[deviceForScreenSide(block.side)];

        /*
            Test mode has no controller to be ready, but its
            blade is just as real.
        */
        if (!testMode && !isReady(state)) {
            continue;
        }

        const distance = distanceToBlade(
            block,
            state.handX,
            state.handY,
            state.handZ ?? HAND_Z,
            state.tipX,
            state.tipY,
            state.tipZ
        );

        /*
            A dot block has no arrow, so any committed cut
            counts — but it still has to be a cut, which is why
            this asks for a direction at all rather than simply
            passing. A vague diagonal reads as null and is no
            more a cut on a dot than on an arrow.
        */
        const cutWay = cutDirectionOf(state);

        const swungRight = block.anyDirection
            ? cutWay !== null
            : cutWay === block.direction;

        /*
            Every gate is measured on every frame the block is
            cuttable, whether or not it passes. A miss is then
            explainable afterwards: how near the blade actually
            came, how hard it was being swung at the time, and
            whether it was ever going the right way.
        */
        recordAttempt(
            block,
            distance,
            state.tipSpeed,
            swungRight,
            state
        );

        /*
            Touching is not cutting — but a cut is a swing
            through the block, not a state that has to be true
            on one particular frame.

            Requiring speed, direction and contact to coincide
            exactly meant a fast swing could pass clean through
            a block between two frames and score nothing: on
            the frame it was inside, the tip had already
            slowed or turned. That is what made cuts feel
            unreliable, and the faster the swing the worse it
            got, which is precisely backwards.

            So a qualifying swing is remembered briefly. If the
            blade is inside the block and a swing the right way
            either is happening now or happened a moment ago,
            the block is cut.
        */
        const swingingNow =
            state.tipSpeed >= SLASH_SPEED && swungRight;

        const justSwung =
            (block.anyDirection
                ? state.swingDirection != null
                : state.swingDirection === block.direction) &&
            performance.now() - (state.swingAt ?? -Infinity) <=
                CUT_MEMORY_SECONDS * 1000;

        if (!swingingNow && !justSwung) {
            continue;
        }

        if (distance > (block.hitRadius ?? BLOCK_HIT_RADIUS)) {
            continue;
        }

        block.dying = 1;
        block.element.classList.add("cut");

        score.hits += 1;
        score.streak += 1;
        score.best = Math.max(score.best, score.streak);

        /*
            What the cut was worth: the shape's value, scaled by
            how close to its beat it landed, and again by the
            streak up to a cap. A block cut on the beat is worth
            well over twice one scraped at the edge of the
            window, which is the whole reason to play to the
            music rather than merely to the blocks.
        */
        let window = null;

        if (
            block.targetTime !== null &&
            backgroundMusic &&
            !backgroundMusic.paused
        ) {
            const error =
                backgroundMusic.currentTime - block.targetTime;

            const size = Math.abs(error);

            window = judgeTiming(size);

            score.timed += 1;
            score.errorTotal += size;

            if (window.name === "PERFECT") {
                score.perfect += 1;
            }

            showJudgment(block, window, error < 0);
        }

        const streakBonus = 1 + Math.min(score.streak, 40) / 40;

        score.points += Math.round(
            (block.points ?? 100) *
            (window ? window.multiplier : 0.7) *
            streakBonus
        );

        reportHit(block, state);
        showCallout(score.streak);

        // Create slash effect at block position
        createSlashEffect(block);

        // Play destroy sound on successful cut
        playDestroySound();

        burstSpeedLines(state.tipSpeed);

        /*
            Felt on the hand that landed it, as hard as the
            swing that landed it. SLASH_SPEED is the floor for
            a cut to count at all, so speed is measured from
            there upward rather than from zero — otherwise
            every cut would sit near the top of the range and
            they would all feel the same.
        */
        const over =
            (state.tipSpeed - SLASH_SPEED) /
            (IMPACT_FULL_SPEED - SLASH_SPEED);

        impactOnDevice(
            state,
            IMPACT_FLOOR + (1 - IMPACT_FLOOR) * clamp(over, 0, 1)
        );
    }
}

/* =========================================================
   THE CROWD

   Background dancers, plus the smaller anime furniture:
   petals, and a callout that fires on a streak.

   The dancers are artwork the player supplies. There is no
   drawn fallback: figures authored as vector paths here
   looked exactly as good as that sounds, so the crowd is
   simply absent until dancers/ has something real in it.

   All of it is decoration and none of it is allowed to cost
   anything during play, so the movement is CSS keyframes on
   elements that already exist rather than anything touched
   per frame, and at most two dancers are ever on screen.
   ========================================================= */

/*
    dancers/manifest.json lists the crowd. An entry is either a
    plain file name, or an object that adjusts how that one is
    drawn:

        [
          "plain.webp",
          { "file": "tall.webp", "height": 280 },
          { "file": "dark.webp", "height": 290, "lift": 1.6 }
        ]

    height is how tall it is drawn, in pixels, before the random
    per appearance variation. It belongs to the sprite rather
    than to the crowd because these are not drawn to a common
    scale: a full figure and a round blob mascot both look right
    on screen only at quite different sizes.

    lift brightens the artwork. The playfield is very dark, so a
    character dressed in black reads as a floating head and a
    pair of hands against it however cleanly it was cut out.

    Animated GIF and WebP both work as they are.
*/
let dancerSprites = [];

function normaliseSprite(entry) {
    if (typeof entry === "string") {
        return { file: entry, height: 150, lift: 1 };
    }

    if (!entry || typeof entry.file !== "string") {
        return null;
    }

    return {
        file: entry.file,
        height: Number.isFinite(entry.height)
            ? clamp(entry.height, 40, 460)
            : 150,
        lift: Number.isFinite(entry.lift)
            ? clamp(entry.lift, 0.5, 3)
            : 1
    };
}

async function loadDancerSprites() {
    try {
        const response = await fetch("dancers/manifest.json");

        if (!response.ok) {
            return;
        }

        const entries = await response.json();

        if (Array.isArray(entries)) {
            dancerSprites = entries
                .map(normaliseSprite)
                .filter(Boolean);
        }
    } catch (error) {
        /* No sprite pack; the crowd stays away. */
    }
}

loadDancerSprites();

let dancerTimer = 0;
let dancerIndex = 0;

/*
    Kept well apart, so the crowd is an occasional event rather
    than a constant presence.
*/
/*
    One dancer at a time, and the next only after a gap once the
    last one has gone. Two real characters overlapping read as
    clutter behind the playfield rather than as a crowd, and the
    gap gives the scene somewhere to breathe.
*/
const DANCER_GAP_SECONDS = 6;
const DANCER_STAY_SECONDS = 13;
const MAX_DANCERS = 1;

function beatSeconds() {
    const bpm = beatmap?.bpm ?? 120;

    return 60 / bpm;
}

function spawnDancer() {
    const stage = document.querySelector("#dancers");

    if (!stage || stage.childElementCount >= MAX_DANCERS) {
        return;
    }

    /*
        Taken in turn rather than at random. With one on screen
        at a time and a gap between them, random picking would
        show the same character twice in a row often enough to
        look like the others were missing.
    */
    const sprite = dancerSprites[
        dancerIndex % dancerSprites.length
    ];

    dancerIndex += 1;

    const dancer = document.createElement("div");

    dancer.className = "dancer sprite";
    dancer.innerHTML =
        '<img alt="" src="dancers/' + sprite.file + '">';

    dancer.style.setProperty("--dh", sprite.height + "px");
    dancer.style.setProperty(
        "--lift",
        sprite.lift === 1
            ? "none"
            : "brightness(" + sprite.lift + ")"
    );

    const fromLeft = Math.random() < 0.5;
    const width = viewport.width;

    /*
        Placed out toward the edges. The middle of the screen
        belongs to the blocks.
    */
    const margin = 0.06 + Math.random() * 0.15;

    const x = fromLeft
        ? width * margin
        : width * (1 - margin) - 120;

    dancer.style.setProperty("--x", Math.round(x) + "px");
    dancer.style.setProperty(
        "--enter",
        (fromLeft ? -180 : width + 60) + "px"
    );
    dancer.style.setProperty(
        "--exit",
        (fromLeft ? -180 : width + 60) + "px"
    );
    dancer.style.setProperty(
        "--y",
        (38 + Math.random() * 22) + "%"
    );
    /*
        Under 1 throughout, so the random variation can only
        ever shrink a sprite and never stretch one past its own
        resolution. Kept near the top of the range now that each
        sprite carries a size it was chosen for — the variation
        is there to stop repeats looking identical, not to
        decide how big anything is.
    */
    dancer.style.setProperty(
        "--s",
        (0.86 + Math.random() * 0.14).toFixed(2)
    );
    dancer.style.setProperty(
        "--dur",
        DANCER_STAY_SECONDS + "s"
    );

    /*
        Two beats to a full sway, so the crowd moves with the
        same track the blocks are cut to.
    */
    dancer.style.setProperty(
        "--beat",
        (beatSeconds() * 2).toFixed(3) + "s"
    );

    stage.appendChild(dancer);

    setTimeout(
        () => dancer.remove(),
        DANCER_STAY_SECONDS * 1000
    );
}

function updateDancers(deltaTime) {
    /*
        Nothing is drawn for the crowd any more. Figures appear
        only when real artwork is present in dancers/, so with
        an empty sprite pack this does nothing at all rather
        than falling back to something hand drawn.
    */
    if (!blocksRunning || dancerSprites.length === 0) {
        return;
    }

    const stage = document.querySelector("#dancers");

    /*
        The clock only runs while the stage is empty, so the gap
        is measured from the moment the last dancer left rather
        than from when it arrived. Without that the timer keeps
        ticking underneath whoever is on, and the next one walks
        in the instant they go.
    */
    if (stage && stage.childElementCount > 0) {
        dancerTimer = DANCER_GAP_SECONDS;
        return;
    }

    dancerTimer -= deltaTime;

    if (dancerTimer <= 0) {
        dancerTimer = DANCER_GAP_SECONDS;
        spawnDancer();
    }
}

/*
    Built once. Each petal is a single element running one
    infinite keyframe, so a continuous fall costs nothing once
    it is set up.
*/
const PETAL_COUNT = 26;

const PETAL_COLOURS = [
    "rgba(255, 170, 215, 0.85)",
    "rgba(255, 220, 240, 0.80)",
    "rgba(190, 170, 255, 0.75)",
    "rgba(255, 200, 160, 0.70)"
];

function buildPetals() {
    const field = document.querySelector("#petals");

    if (!field || field.childElementCount) {
        return;
    }

    for (let index = 0; index < PETAL_COUNT; index += 1) {
        const petal = document.createElement("div");

        petal.className = "petal";

        petal.style.setProperty(
            "--x",
            (Math.random() * 100) + "%"
        );
        petal.style.setProperty(
            "--pw",
            (7 + Math.random() * 9).toFixed(1) + "px"
        );
        petal.style.setProperty(
            "--pc",
            PETAL_COLOURS[
                Math.floor(Math.random() * PETAL_COLOURS.length)
            ]
        );
        petal.style.setProperty(
            "--sway",
            Math.round(-120 + Math.random() * 240) + "px"
        );
        petal.style.setProperty(
            "--dur",
            (9 + Math.random() * 9).toFixed(1) + "s"
        );
        petal.style.setProperty(
            "--delay",
            (-Math.random() * 16).toFixed(1) + "s"
        );

        field.appendChild(petal);
    }
}

/*
    Streak callouts, tied to milestones rather than to every
    cut so the line still means something when it shows up.
*/
const CALLOUTS = [
    { at: 5,  text: "NICE!" },
    { at: 10, text: "GREAT!!" },
    { at: 20, text: "AMAZING!!" },
    { at: 35, text: "UNREAL!!!" },
    { at: 50, text: "LEGENDARY!!!" }
];

function showCallout(streak) {
    const match = CALLOUTS.find(entry => entry.at === streak);

    if (!match) {
        return;
    }

    const node = document.querySelector("#callout");

    if (!node) {
        return;
    }

    node.textContent = match.text + "  " + streak + "x";

    node.classList.remove("pop");

    void node.offsetWidth;

    node.classList.add("pop");
}

/* =========================================================
   MISS DIAGNOSIS

   A miss on its own says nothing about what to change. These
   record what the block's best moment actually looked like, so
   each one can be attributed to the gate that stopped it:

     reach      the blade never came within the hit radius,
                so no swing timing would have helped
     slow       it was close and aimed right, but never swung
                hard enough to count as a cut
     direction  close and fast, but never travelling the way
                the arrow asked for
     timing     every gate was met at some point, just never
                on the same frame

   Reach failures are a geometry problem and mine to fix.
   Direction and slow failures are the game asking something
   of the player. Telling them apart is the whole point.
   ========================================================= */

const telemetryQueue = [];

let telemetrySending = false;
let telemetryEnabled = true;

function recordAttempt(block, distance, speed, swungRight, state) {
    if (block.bestDistance === undefined) {
        block.bestDistance = Infinity;
        block.bestDistanceZ = block.z;
        block.maxSpeed = 0;
        block.everRightWay = false;
        block.closeFrames = 0;
    }

    if (distance < block.bestDistance) {
        block.bestDistance = distance;
        block.bestDistanceZ = block.z;
    }

    block.maxSpeed = Math.max(block.maxSpeed, speed);

    if (swungRight) {
        block.everRightWay = true;
    }

    if (distance <= BLOCK_HIT_RADIUS) {
        block.closeFrames += 1;
    }

    /*
        Recorded so a drifted session is visible in the data
        rather than only in how wrong it felt.
    */
    block.driftAt = state.driftYaw;
    block.headingAt = state.headingDegrees;
}

function classifyMiss(block) {
    if (block.bestDistance === undefined) {
        return "never-in-window";
    }

    if (block.bestDistance > BLOCK_HIT_RADIUS) {
        return "reach";
    }

    if (block.maxSpeed < SLASH_SPEED) {
        return "slow";
    }

    if (!block.everRightWay) {
        return "direction";
    }

    return "timing";
}

function reportMiss(block) {
    const cause = classifyMiss(block);

    missCauses[cause] = (missCauses[cause] ?? 0) + 1;

    telemetryQueue.push({
        t: Math.round(performance.now()),
        cause,
        side: block.side,
        direction: block.direction,
        lane: { x: Math.round(block.x), y: Math.round(block.y) },
        bestDistance:
            block.bestDistance === undefined
                ? null
                : Math.round(block.bestDistance),
        bestDistanceZ:
            block.bestDistanceZ === undefined
                ? null
                : Math.round(block.bestDistanceZ),
        maxSpeed: Math.round(block.maxSpeed ?? 0),
        closeFrames: block.closeFrames ?? 0,
        slashSpeed: SLASH_SPEED,
        hitRadius: BLOCK_HIT_RADIUS,
        drift:
            block.driftAt === undefined
                ? null
                : Math.round(block.driftAt),
        heading:
            block.headingAt === undefined
                ? null
                : Math.round(block.headingAt)
    });
}

function reportHit(block, state) {
    missCauses.hit = (missCauses.hit ?? 0) + 1;

    telemetryQueue.push({
        t: Math.round(performance.now()),
        cause: "hit",
        side: block.side,
        direction: block.direction,
        lane: { x: Math.round(block.x), y: Math.round(block.y) },
        bestDistance: Math.round(block.bestDistance ?? 0),
        bestDistanceZ: Math.round(block.bestDistanceZ ?? block.z),
        maxSpeed: Math.round(state.tipSpeed),
        closeFrames: block.closeFrames ?? 0,
        slashSpeed: SLASH_SPEED,
        hitRadius: BLOCK_HIT_RADIUS,
        drift:
            block.driftAt === undefined
                ? null
                : Math.round(block.driftAt),
        heading:
            block.headingAt === undefined
                ? null
                : Math.round(block.headingAt)
    });
}

const missCauses = {};

/*
    Shown live, because a raw miss count tells you nothing
    about whether to swing harder, swing straighter, or
    complain to me about the geometry.
*/
const MISS_LABELS = {
    reach: "out of reach",
    slow: "swing too slow",
    direction: "wrong direction",
    timing: "timing",
    "never-in-window": "never cuttable"
};

function updateMissBreakdown() {
    const panel = document.querySelector("#miss-breakdown");

    if (!panel) {
        return;
    }

    const causes = Object.keys(MISS_LABELS)
        .map(key => [key, missCauses[key] ?? 0])
        .filter(([, count]) => count > 0)
        .sort((a, b) => b[1] - a[1]);

    if (causes.length === 0) {
        panel.hidden = true;
        return;
    }

    panel.hidden = false;

    const total = causes.reduce((sum, [, n]) => sum + n, 0);

    panel.innerHTML =
        "<b>why blocks were missed</b>" +
        causes
            .map(([key, count]) => {
                const share = Math.round((count / total) * 100);

                return (
                    `<span>${MISS_LABELS[key]} ` +
                    `<i>${count}</i> ${share}%</span>`
                );
            })
            .join("");
}

/*
    Posted in batches to the dev server, which appends them to
    telemetry.jsonl. Sending is best effort: if the server is
    not there the rows are simply dropped rather than piling up.
*/
async function flushTelemetry() {
    if (
        !telemetryEnabled ||
        telemetrySending ||
        telemetryQueue.length === 0
    ) {
        telemetryQueue.length = 0;
        return;
    }

    telemetrySending = true;

    const batch = telemetryQueue.splice(0, telemetryQueue.length);

    try {
        const response = await fetch("/telemetry", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(batch)
        });

        /*
            The sink only exists under the dev server. Anywhere
            else the first attempt 404s, and retrying every two
            seconds for the whole session would fill the console
            with failures that mean nothing. One refusal is
            enough to stop asking.
        */
        if (!response.ok) {
            telemetryEnabled = false;
        }
    } catch (error) {
        telemetryEnabled = false;
    } finally {
        telemetrySending = false;
    }
}

setInterval(flushTelemetry, 2000);

/*
    A block reached you: marked on the whole screen, and felt
    on the hand that should have cut it.
*/
/*
    Speed lines, fired on a cut and scaled by how hard it
    landed. A committed swing earns them; a cut that barely
    clears the slash threshold does not, because drawn on
    every hit they would be wallpaper rather than an accent,
    which is the usual way this effect is ruined.

    The threshold is set from measured cut speeds rather than
    picked: at a quarter of the way to a full-strength impact
    it never fired at all, because real cuts land lower on
    that scale than the rumble curve assumes.
*/
function burstSpeedLines(speed) {
    const lines = document.querySelector("#speed-lines");

    if (!lines) {
        return;
    }

    const over =
        (speed - SLASH_SPEED) /
        (IMPACT_FULL_SPEED - SLASH_SPEED);

    if (over < 0.12) {
        return;
    }

    lines.style.setProperty(
        "--burst",
        clamp(over, 0, 1).toFixed(2)
    );

    lines.classList.remove("burst");

    void lines.offsetWidth;

    lines.classList.add("burst");
}

function takeDamage(block) {
    const flash = document.querySelector("#damage-flash");

    if (flash) {
        /*
            Restarting a CSS animation needs the class gone for
            a frame, or a second miss during the first flash
            would not re-trigger it.
        */
        flash.classList.remove("hit");

        void flash.offsetWidth;

        flash.classList.add("hit");
    }

    const state = sabers[deviceForScreenSide(block.side)];

    if (state) {
        playRumble(state, DAMAGE_STEPS, 1);
    }
}

function updateBlocks(deltaTime) {
    if (blocksRunning) {
        // Beat-synced spawning
        if (beatSyncEnabled && beatmap && backgroundMusic && !backgroundMusic.paused) {
            const currentTime = backgroundMusic.currentTime;

            /*
                A media element can report that it is playing
                while its clock is not actually advancing —
                audio that failed to decode, a device with no
                output, a stalled buffer. Blocks are released
                against that clock, so when it stops the game
                quietly stops too: no blocks, no error, nothing
                to see. Rather than trust the element, watch
                whether time is really passing, and fall back
                to plain timed spawning when it is not.
            */
            if (currentTime === musicClock) {
                musicStalled += deltaTime;

                if (musicStalled > MUSIC_STALL_SECONDS) {
                    console.warn(
                        "Music clock stalled; spawning blocks " +
                        "on a timer instead."
                    );

                    beatSyncEnabled = false;
                }
            } else {
                musicClock = currentTime;
                musicStalled = 0;
            }
            
            /*
                PLANNING, then RELEASE.

                Planning runs a full lead ahead of the music and
                only decides what a beat is made of. Release
                happens per block, when that block's own flight
                time means it has to leave now to arrive on its
                beat — so shapes with different speeds, and
                notes placed between beats, all still land where
                the music says.
            */
            const beats = beatmap.beat_timestamps;

            while (
                beatIndex < beats.length &&
                beats[beatIndex] <=
                    currentTime + BLOCK_MAX_LEAD_SECONDS
            ) {
                /*
                    Only every Nth beat carries blocks. The
                    beatmap marks every beat in the track, and
                    at 120 BPM that is two a second — striding
                    keeps them on the beat while leaving room to
                    actually swing.
                */
                if (beatIndex % tuning.beatStride === 0) {
                    /*
                        Measured locally rather than from the
                        track's average BPM, so a pattern's
                        half beat stays a half beat through a
                        tempo change.
                    */
                    const nextBeat =
                        beats[beatIndex + tuning.beatStride];

                    if (nextBeat !== undefined) {
                        beatInterval =
                            (nextBeat - beats[beatIndex]) /
                            tuning.beatStride;
                    }

                    planBeat(beats[beatIndex]);
                }

                beatIndex++;
            }

            for (
                let index = pendingSpawns.length - 1;
                index >= 0;
                index -= 1
            ) {
                const pending = pendingSpawns[index];

                if (
                    currentTime >=
                    pending.targetTime - pending.spec.lead
                ) {
                    spawnBlock(
                        pending.spec, pending.targetTime
                    );

                    pendingSpawns.splice(index, 1);
                }
            }
        } else {
            // Original random spawning (fallback when no beatmap or music)
            blockTimer -= deltaTime;

            if (blockTimer <= 0) {
                blockTimer = BLOCK_INTERVAL_SECONDS;
                spawnBlock();
            }
        }
    }

    checkCuts();

    for (let index = blocks.length - 1; index >= 0; index -= 1) {
        const block = blocks[index];

        if (block.dying) {
            /*
                Cut blocks keep their momentum for a moment
                while they fade, rather than blinking out.
            */
            block.dying += deltaTime;
            block.z += block.speed * deltaTime * 0.4;

            if (block.dying > 0.45) {
                removeBlock(index);
                continue;
            }
        } else {
            block.z += block.speed * deltaTime;

            /*
                Marked while the block is inside the window
                the hit test will accept, so "swing now" is
                something you can see rather than judge.
            */
            const cuttable =
                Math.abs(block.z - BLOCK_STRIKE_Z) <=
                    BLOCK_STRIKE_DEPTH &&
                block.z <= BLOCK_MISS_Z;

            if (cuttable !== block.wasCuttable) {
                block.element.classList.toggle("cuttable", cuttable);
                block.wasCuttable = cuttable;
            }

            if (block.z > BLOCK_MISS_Z) {
                // Mark as missed with dark red effect
                if (!block.missed) {
                    block.missed = true;
                    block.element.classList.add("missed");

                    score.misses += 1;
                    score.streak = 0;

                    reportMiss(block);
                    takeDamage(block);
                }

                // Remove after fade animation
                block.missedTime = (block.missedTime || 0) + deltaTime;
                if (block.missedTime > 0.3) {
                    removeBlock(index);
                    continue;
                }
            }
        }

        block.element.style.transform =
            `translate3d(${block.x.toFixed(1)}px, ` +
            `${block.y.toFixed(1)}px, ${block.z.toFixed(1)}px) ` +
            `translate(-50%, -50%)`;
    }

    updateScoreboard();
    updateMissBreakdown();
}

function updateScoreboard() {
    if (!scoreText) {
        return;
    }

    /*
        Accuracy is the average distance from the beat, in
        milliseconds, over the cuts there was a beat to compare
        against. It is the one number that says whether you are
        playing to the music or merely clearing blocks.
    */
    const accuracy = score.timed
        ? `   ${Math.round(
              (score.errorTotal / score.timed) * 1000
          )}ms   perfect ${score.perfect}`
        : "";

    scoreText.textContent =
        `${score.points}   hit ${score.hits}   ` +
        `missed ${score.misses}   streak ${score.streak}   ` +
        `best ${score.best}${accuracy}`;
}

function toggleBlocks() {
    blocksRunning = !blocksRunning;

    blockTimer = 0;

    /*
        Planned blocks belong to the run that planned them. Left
        in place, stopping and starting again released a burst
        of blocks aimed at beats that had already gone past.
    */
    pendingSpawns.length = 0;
    patternBeat = 0;
    
    if (blocksRunning) {
        // Start beat-synced mode if beatmap is available
        if (beatmap && backgroundMusic) {
            beatSyncEnabled = true;
            beatIndex = 0;
            musicClock = -1;
            musicStalled = 0;

            /*
                This track does not reach its first detected
                beat until 36 seconds in, which was 36 seconds
                of standing there before anything to hit
                appeared. Start just far enough ahead of the
                first beat for its block to be in flight, so
                play begins as the music does.
            */
            const firstBeat = beatmap.beat_timestamps?.[0] ?? 0;

            const startAt = Math.max(
                0,
                firstBeat - BLOCK_TRAVEL_SECONDS
            );

            /*
                A media element cannot be seeked before it knows
                how long it is. Assigning currentTime too early
                is not an error — it is quietly dropped, and the
                track then starts from zero. On this beatmap the
                first beat is 36 seconds in, so that meant
                standing in silence for half a minute waiting
                for a block, which is exactly the wait this skip
                exists to remove.
            */
            const seek = () => {
                if (backgroundMusic.currentTime >= startAt - 0.5) {
                    return;
                }

                try {
                    backgroundMusic.currentTime = startAt;
                } catch (error) {
                    /* Not seekable yet; a later attempt gets it. */
                }
            };

            /*
                Tried at every point the element might become
                seekable, because there is no single one that
                can be relied on. A readyState check alone was
                not enough: it passed, the assignment was
                accepted, and the position still came back as
                zero — so the track played from the beginning
                and the first block arrived 31 seconds later,
                measured. Attempting it again after metadata,
                after buffering, and once playback has actually
                begun costs nothing, and each attempt returns
                immediately once the position has taken.
            */
            for (const event of ["loadedmetadata", "canplay"]) {
                backgroundMusic.addEventListener(
                    event,
                    seek,
                    { once: true }
                );
            }

            seek();
            backgroundMusic.play().then(seek).catch(err => {
                console.warn('Could not play background music:', err);
                // Fall back to random spawning
                beatSyncEnabled = false;
            });
            
            statusText.textContent = `Beat-synced! ${beatmap.bpm} BPM`;
            if (musicInfo) {
                musicInfo.textContent = `♫ ${beatmap.bpm} BPM - ${beatmap.total_beats} beats`;
                musicInfo.hidden = false;
            }
        } else {
            beatSyncEnabled = false;
            statusText.textContent = "Blocks incoming (random)";
            if (musicInfo) {
                musicInfo.hidden = true;
            }
        }
    } else {
        // Stop blocks
        beatSyncEnabled = false;
        
        // Stop music
        if (backgroundMusic) {
            backgroundMusic.pause();
            backgroundMusic.currentTime = 0;
        }
        
        if (musicInfo) {
            musicInfo.hidden = true;
        }
        
        // Clear all blocks
        for (let index = blocks.length - 1; index >= 0; index -= 1) {
            removeBlock(index);
        }
        
        statusText.textContent = "Blocks stopped";
    }

    if (blocksButton) {
        blocksButton.textContent = blocksRunning
            ? "Stop blocks (B)"
            : "Start blocks (B)";
    }

    setTimeout(updateStatus, 1200);
}


/* =========================================================
   CONTROLLER DETECTION LOOP

   Also covers controllers waking from sleep or returning
   after a page reload.
   ========================================================= */

setInterval(() => {
    const live = new Set(JoyCon.connectedJoyCons.values());

    /*
        A controller the library has dropped keeps its slot
        otherwise, and after one unplug and replug there would
        be no slot left for the controller in your hand.
    */
    for (const joyCon of [...deviceSlots.keys()]) {
        if (!live.has(joyCon)) {
            const slot = deviceSlots.get(joyCon);

            releaseJoyCon(joyCon);

            if (sabers[slot]?.joyCon === joyCon) {
                sabers[slot].connected = false;
            }
        }
    }

    for (const joyCon of live) {
        initializeJoyCon(joyCon);
    }
}, 1000);


/* =========================================================
   DISCONNECT DETECTION
   ========================================================= */

if ("hid" in navigator) {
    navigator.hid.addEventListener("disconnect", event => {
        /*
            Found by which JoyCon object holds the device that
            went away, not by its product id — an unrecognised
            controller has no id we could look up, and would
            otherwise stay connected for ever in a slot nothing
            could free.
        */
        let device = null;

        for (const [joyCon, slot] of deviceSlots) {
            if (joyCon.device === event.device) {
                device = slot;

                releaseJoyCon(joyCon);

                break;
            }
        }

        if (!device) {
            return;
        }

        const state = sabers[device];

        state.connected = false;
        state.rawQuaternion = null;
        state.accelerometer = null;

        /*
            The orientation estimate is abandoned — it would
            have wound on without us — but the discovered long
            axis is kept, since that is a property of the
            hardware rather than of the session.
        */
        state.neutralForward = null;
        state.playerRight = null;
        state.playerUp = null;
        state.fusionStarted = false;
        state.fusionQuaternion = { w: 1, x: 0, y: 0, z: 0 };
        state.gyroBias = { x: 0, y: 0, z: 0 };
        state.lastPacketTime = 0;

        updateStatus();

        console.log(`${device} Joy-Con disconnected`);
    });
} else {
    statusText.textContent =
        "WebHID is unavailable. Use Chrome or Edge.";
}


/* =========================================================
   WINDOW RESIZE
   ========================================================= */

window.addEventListener("resize", () => {
    viewport.width = window.innerWidth;
    viewport.height = window.innerHeight;
});


/* =========================================================
   GAME INTERFACE

   Ask for the saber by the side of the screen it appears on,
   not by which controller drives it.
   ========================================================= */

export function getSaberState(screenSide) {
    const state = sabers[deviceForScreenSide(screenSide)];

    return {
        /*
            The hinge the saber swings from, and where its tip
            currently is. Hit testing wants the tip, or the line
            between the two.
        */
        baseX: state.handX,
        baseY: state.handY,
        tipX: state.tipX,
        tipY: state.tipY,
        tipSpeed: state.tipSpeed,

        /*
            The most recent slash, always one of "up", "down",
            "left" or "right".
        */
        slash: state.lastSlash,
        slashAt: state.lastSlashAt,
        angle: state.angle,
        swingSpeed: state.swingSpeed,
        buttons: state.buttons,
        ready: isReady(state)
    };
}

window.getSaberState = getSaberState;

loadStoredSettings();
updatePrompt();
updateStatus();
