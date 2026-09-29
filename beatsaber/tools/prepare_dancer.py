"""
Turn a video or GIF of a character into a background dancer.

    python tools/prepare_dancer.py <file.mp4|file.gif> [...]

Per file it will:

  * pull frames out (ffmpeg for video, Pillow for GIF)
  * find a loop point, so the clip repeats without a visible jump
  * cut the background away
  * undo the background's tint on the soft edge
  * trim the empty margin, scale, and write animated WebP
  * register the result in dancers/manifest.json

HOW THE BACKGROUND IS CUT

Not by deleting every pixel near the background colour. This
character is outlined in black and stands on black, and that
approach would punch holes straight through the line art.

Instead the background is found by asking which dark pixels are
CONNECTED TO THE EDGE of the frame. Black inside the character is
enclosed by the character, so it is never reached and never
touched. Only black with an unbroken path to the border goes.

THE EDGE

Anti aliased pixels around the character are a blend of character
and background, so against black they are darkened versions of the
real colour. Dropped in as they are, they read as a dirty outline.

Against a black background the blend is just colour x coverage, so
coverage can be recovered from brightness and the colour divided
back out. That is what unpremultiplying does here, and it is why
the cutout has a clean edge rather than a grey halo.

ON RESOLUTION

Upscaling cannot invent detail that was never recorded. What it
can do is resample with something better than the browser's
stretch, and render at twice the displayed size so the art stays
sharp on a high density screen. If the source is small it will
still be a small source, well resampled.
"""

import json
import os
import subprocess
import sys
import tempfile
from glob import glob

import numpy as np
from PIL import Image

try:
    import cv2
except ImportError:
    cv2 = None

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
DANCERS = os.path.join(PROJECT, "dancers")

VIDEO_TYPES = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v")

# Frames per second to sample. Enough for a fluid dance, few
# enough that the file stays a background element and not a
# download.
FPS = 14

# Longest loop to keep, in seconds.
MAX_SECONDS = 6.0

# How far a pixel may sit from the sampled background colour and
# still count as background, summed across the three channels.
#
# Wide enough to swallow a gradient and the faint ghost artwork
# some of these clips have behind the character, narrow enough
# that the character's own colours are never in range.
TOLERANCE = 60

# Rendered at twice the 300px the page displays, for high
# density screens.
TARGET_HEIGHT = 290

PAD = 6


def ffmpeg():
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def video_frames(path, folder):
    subprocess.run(
        [
            ffmpeg(), "-y", "-i", path,
            "-vf", f"fps={FPS}",
            "-frames:v", str(int(FPS * MAX_SECONDS * 2)),
            os.path.join(folder, "f%04d.png"),
        ],
        capture_output=True,
    )

    return [
        np.array(Image.open(f).convert("RGB"))
        for f in sorted(glob(os.path.join(folder, "*.png")))
    ]


def gif_frames(path):
    """
    Every frame, as it is meant to be seen.

    Deliberately NOT composited onto a running canvas. GIF
    frames are often partial redraws, which makes stacking them
    look like the right thing to do — but Pillow already
    applies the disposal method when it seeks, so the frame it
    hands back is the finished one.

    Stacking on top of that draws each frame over the one
    before without clearing, and on a clip whose disposal is
    "restore to background" the result is every past pose still
    present underneath: a dancer with four arms and two heads.
    """
    frames = []

    with Image.open(path) as image:
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            frames.append(
                np.array(image.convert("RGBA").convert("RGB"))
            )

    return frames


def find_loop(frames):
    """
    The frame that best matches the first one, so the clip can be
    cut there and repeat without a jump.
    """
    limit = min(len(frames), int(FPS * MAX_SECONDS))

    if limit < FPS * 2:
        return limit

    first = frames[0].astype(np.int16)
    best, score = limit, None

    # Ignore the first second; a near neighbour always matches best
    # and would leave nothing of the dance.
    for index in range(int(FPS * 1.5), limit):
        diff = np.abs(
            frames[index].astype(np.int16) - first
        ).mean()

        if score is None or diff < score:
            score, best = diff, index

    return best


def background_colour(frame):
    """The colour that dominates the frame's border."""
    edge = np.concatenate([
        frame[0, :, :], frame[-1, :, :],
        frame[:, 0, :], frame[:, -1, :],
    ])

    colours, counts = np.unique(edge, axis=0, return_counts=True)

    return colours[counts.argmax()].astype(np.int16)


def background_mask(frame, key):
    """
    Pixels close to the background colour AND joined to the
    border by an unbroken run of them.

    Both halves matter. In one of these clips the character's
    black tights are within two units of the backdrop, so colour
    alone would erase her legs; they survive because they are
    enclosed by her outline and the fill can never reach them.
    """
    distance = np.abs(
        frame.astype(np.int16) - key
    ).sum(axis=2)

    # A vivid backdrop sits far from everything, so the blend
    # into it covers much more ground and needs more room.
    spread = int(key.max()) - int(key.min())
    limit = TOLERANCE * (2.4 if spread >= 60 else 1.0)

    near = (distance <= limit).astype(np.uint8)

    if cv2 is not None:
        _, labels = cv2.connectedComponents(near, connectivity=4)
    else:
        from scipy import ndimage

        labels, _ = ndimage.label(near)

    edge = np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]
    ])

    outside = [int(v) for v in np.unique(edge) if v != 0]

    if not outside:
        return np.zeros(frame.shape[:2], dtype=bool), distance

    return np.isin(labels, outside), distance


def cut_out(frame, key):
    """RGBA with the background gone and the soft edge cleaned."""
    background, distance = background_mask(frame, key)

    rgb = frame.astype(np.float32)

    alpha = np.where(background, 0.0, 1.0)

    # Coverage across the anti aliased edge. A pixel halfway
    # between the character and the backdrop sits halfway along
    # the distance scale, so the distance is the coverage.
    spread = int(key.max()) - int(key.min())
    scale = TOLERANCE * (2.4 if spread >= 60 else 1.0) * 2.2

    coverage = np.clip(distance / float(scale), 0, 1)

    # Only the band touching the background is softened. The
    # interior stays solid however close its colour happens to
    # be to the backdrop, which is what keeps those tights.
    if cv2 is not None:
        grown = cv2.dilate(
            background.astype(np.uint8),
            np.ones((3, 3), np.uint8),
            iterations=2,
        ).astype(bool)
    else:
        grown = background

    band = grown & ~background
    alpha[band] = np.minimum(alpha[band], coverage[band])

    # Undo the backdrop mixed into the edge. An edge pixel is
    # character x coverage plus backdrop x the rest, so the
    # backdrop's share is subtracted and what remains divided
    # back up. Against a black backdrop this reduces to a plain
    # divide; against this plum one it is the difference between
    # a clean edge and a dirty purple halo.
    a = np.maximum(alpha, 0.12)[:, :, None]
    rgb = np.clip(
        (rgb - key.astype(np.float32) * (1.0 - a)) / a, 0, 255
    )

    rgb = despill(rgb, key)

    out = np.dstack([rgb, alpha * 255.0]).astype(np.uint8)

    return keep_largest(out)


def despill(rgb, key):
    """
    Pull the backdrop's own colour out of the subject.

    A saturated backdrop does not only sit behind the subject,
    it bounces off it. On a green screen that leaves a green
    rim on every edge and a green cast through fine detail like
    hair, and keying alone cannot remove it because those
    pixels genuinely are part of the subject — they are just
    the wrong colour.

    The fix is to cap the backdrop's dominant channel at what
    the other two suggest it should be. A green fringe becomes
    grey, hair stays hair, and anything legitimately green in
    the subject is barely touched because its other channels
    are high enough to permit it.

    Only runs for a strongly coloured backdrop. Black, white
    and the muted ones do not bounce a hue worth removing.
    """
    spread = int(key.max()) - int(key.min())

    if spread < 60:
        return rgb

    channel = int(np.argmax(key))
    others = [i for i in range(3) if i != channel]

    limit = (rgb[:, :, others[0]] + rgb[:, :, others[1]]) / 2.0

    rgb[:, :, channel] = np.minimum(rgb[:, :, channel], limit)

    return rgb


def keep_largest(frame):
    """
    Discard everything except the biggest solid object.

    Some clips are composites: the character in front, and a
    faint outline drawing of her behind as decoration. That
    ghost is too close in colour to her own hair to separate by
    colour — raising the key until it disappears takes the edge
    of her hair with it.

    It is separable by structure instead. The character is one
    large connected shape; the ghost is loose line work that is
    not joined to her. So the largest object is kept and
    everything else dropped, which removes the backdrop artwork
    without touching a pixel of the subject.
    """
    if cv2 is None:
        return frame

    solid = (frame[:, :, 3] > 90).astype(np.uint8)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        solid, connectivity=8
    )

    if count <= 2:
        return frame

    # Row 0 is the background label, so the subject is the
    # largest of the rest.
    areas = stats[1:, cv2.CC_STAT_AREA]
    subject = 1 + int(np.argmax(areas))

    # Grown a little so the subject's own soft edge, which falls
    # below the solidity threshold, is not clipped off with the
    # debris.
    mask = cv2.dilate(
        (labels == subject).astype(np.uint8),
        np.ones((3, 3), np.uint8),
        iterations=3,
    ).astype(bool)

    frame[~mask, 3] = 0

    return frame


def union_box(frames):
    box = None

    for frame in frames:
        rows = np.where(frame[:, :, 3].any(axis=1))[0]
        cols = np.where(frame[:, :, 3].any(axis=0))[0]

        if not len(rows) or not len(cols):
            continue

        found = (cols[0], rows[0], cols[-1] + 1, rows[-1] + 1)

        box = found if box is None else (
            min(box[0], found[0]), min(box[1], found[1]),
            max(box[2], found[2]), max(box[3], found[3]),
        )

    return box


def prepare(path):
    name = os.path.splitext(os.path.basename(path))[0]
    name = "".join(
        c if c.isalnum() else "-" for c in name.lower()
    ).strip("-")[:28] or "dancer"

    print(f"\n{os.path.basename(path)}", flush=True)

    if path.lower().endswith(VIDEO_TYPES):
        with tempfile.TemporaryDirectory() as folder:
            frames = video_frames(path, folder)
    else:
        frames = gif_frames(path)

    if not frames:
        print("  no frames could be read")
        return None

    print(f"  {len(frames)} frames at {frames[0].shape[1]}"
          f"x{frames[0].shape[0]}")

    end = find_loop(frames)
    frames = frames[:end]
    print(f"  looping at frame {end} ({end / FPS:.1f}s)", flush=True)

    key = background_colour(frames[0])
    print(f"  backdrop sampled as rgb"
          f"{tuple(int(v) for v in key)}")

    cut = [cut_out(f, key) for f in frames]

    box = union_box(cut)

    if box:
        x0 = max(0, box[0] - PAD)
        y0 = max(0, box[1] - PAD)
        x1 = min(cut[0].shape[1], box[2] + PAD)
        y1 = min(cut[0].shape[0], box[3] + PAD)

        cut = [f[y0:y1, x0:x1] for f in cut]
        print(f"  trimmed to {x1 - x0}x{y1 - y0}")

    # Did the key actually work? A clip where almost nothing was
    # removed is one whose background did not separate — a busy
    # or gradient backdrop, or artwork layered behind the
    # subject. Shipping that puts a rectangular slab of
    # background into the scene, so it is refused here instead.
    clear = float(np.mean([
        (f[:, :, 3] == 0).mean() for f in cut[::5]
    ]))

    filled = (x1 - x0) * (y1 - y0) if box else 0
    whole = cut[0].shape[0] * cut[0].shape[1]

    if clear < 0.25:
        print(f"  SKIPPED: only {clear * 100:.0f}% of the frame "
              f"came away as background, so the subject did not "
              f"separate cleanly.", flush=True)
        return None

    images = [Image.fromarray(f, "RGBA") for f in cut]

    width, height = images[0].size

    # Only ever scale down. Enlarging past the source adds bytes
    # and no detail, and the page can stretch it for free.
    scale = min(1.0, TARGET_HEIGHT / height)
    size = (
        max(1, round(width * scale)),
        max(1, round(height * scale)),
    )

    if scale < 1:
        images = [i.resize(size, Image.LANCZOS) for i in images]
        print(f"  scaled x{scale:.2f} to {size[0]}x{size[1]}")
    else:
        print(f"  kept at {size[0]}x{size[1]} (source is smaller "
              f"than the target; not upscaled)")

    os.makedirs(DANCERS, exist_ok=True)
    out = os.path.join(DANCERS, f"{name}.webp")

    images[0].save(
        out, "WEBP",
        save_all=True,
        append_images=images[1:],
        duration=int(1000 / FPS),
        loop=0,
        quality=72,
        method=4,
    )

    print(f"  wrote dancers/{name}.webp "
          f"({os.path.getsize(out) / 1024:.0f} KB)")

    return f"{name}.webp"


def register(names):
    path = os.path.join(DANCERS, "manifest.json")

    try:
        with open(path) as handle:
            existing = json.load(handle)
    except Exception:
        existing = []

    merged = list(dict.fromkeys(list(existing) + names))

    with open(path, "w") as handle:
        json.dump(merged, handle, indent=2)

    print(f"\nmanifest.json lists: {merged}", flush=True)


def main():
    sources = sys.argv[1:]

    if not sources:
        print(__doc__)
        return 1

    written = [
        result
        for path in sources
        if os.path.exists(path)
        for result in [prepare(path)]
        if result
    ]

    if written:
        register(written)
        print("\nReload the page.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
