"""
Serves the Beat Saber game and opens it in a browser.

The game is a web page, so it needs a server rather than a file
path. Two things make that less trivial than it sounds:

WebHID, which is how the Joy-Cons are read, is only available in
a secure context. http://localhost counts as one; opening the
page from disk does not, so file:// would load the game with no
way to connect a controller.

Range requests are required for seeking. The game skips the
silent intro of a track by jumping the audio to just before the
first beat, and a browser can only seek media that the server
will serve in parts. Python's own SimpleHTTPRequestHandler
answers a Range request with the whole file and a 200, so the
media never becomes seekable — the skip is silently ignored and
the track plays from the beginning. That was measured: the first
block arrived 31 seconds in. The handler below implements 206
Partial Content, which fixes it.
"""

import http.server
import os
import re
import socket
import socketserver
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_DIR = os.path.join(HERE, "beatsaber", "dist")

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class _Handler(http.server.SimpleHTTPRequestHandler):
    """A static handler that can serve part of a file."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=GAME_DIR, **kwargs)

    def log_message(self, *args):
        """Quiet: this runs behind a GUI, not in a terminal."""

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        header = self.headers.get("Range")

        if not header:
            return super().send_head()

        match = _RANGE.fullmatch(header.strip())

        if not match:
            return super().send_head()

        path = self.translate_path(self.path)

        if os.path.isdir(path):
            return super().send_head()

        try:
            handle = open(path, "rb")
        except OSError:
            self.send_error(404)
            return None

        size = os.fstat(handle.fileno()).st_size
        first, last = match.group(1), match.group(2)

        if first:
            start = int(first)
            end = int(last) if last else size - 1
        else:
            # "bytes=-500" means the last 500 bytes.
            if not last:
                handle.close()
                self.send_error(400)
                return None

            start = max(0, size - int(last))
            end = size - 1

        if start >= size:
            handle.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None

        end = min(end, size - 1)
        handle.seek(start)

        self.send_response(206)
        self.send_header(
            "Content-Type", self.guess_type(path)
        )
        self.send_header(
            "Content-Range", f"bytes {start}-{end}/{size}"
        )
        self.send_header(
            "Content-Length", str(end - start + 1)
        )
        self.end_headers()

        return _Slice(handle, end - start + 1)


class _Slice:
    """A read-only window onto part of a file, for copyfile()."""

    def __init__(self, handle, length):
        self._handle = handle
        self._left = length

    def read(self, amount=-1):
        if self._left <= 0:
            return b""

        if amount < 0 or amount > self._left:
            amount = self._left

        chunk = self._handle.read(amount)
        self._left -= len(chunk)

        return chunk

    def close(self):
        self._handle.close()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


_server = None
_port = None

"""
The port matters, and it has to be the same one every time.

WebHID grants access per origin, and an origin includes the
port. The game asked for a free port on each launch and got a
different one — 58175, then 58176, then 58177 — so every launch
was a new origin to the browser, with no controllers granted to
it. The Joy-Cons had to be picked again from the browser's
chooser on every single launch, one click per controller, and
picking only one left the other simply absent. From inside the
game that is indistinguishable from a controller that is not
detected: Windows has it, the game does not, and nothing says
why.

A fixed port keeps the origin stable, so the grant persists
across launches the way it does on the dev server, which is why
the dev server never had this problem.

8770 has no assigned use and nothing on this machine was
listening on it. If it is taken the game still runs, on an
arbitrary port, and the caller is told — because in that case
the controllers do have to be granted again.
"""
PREFERRED_PORT = 8770

port_is_stable = True


def _listen():
    """
    Bind the preferred port, falling back to any free one.

    Binding is what reserves a port, so this binds directly
    rather than probing for a free port and binding it after —
    between those two steps anything else on the machine could
    take it.
    """
    global port_is_stable

    try:
        server = _Server(("127.0.0.1", PREFERRED_PORT), _Handler)
        port_is_stable = True
    except OSError:
        server = _Server(("127.0.0.1", 0), _Handler)
        port_is_stable = False

    return server, server.server_address[1]


def is_available():
    """Whether the game has been built and can be served."""
    return os.path.isfile(os.path.join(GAME_DIR, "index.html"))


def start():
    """
    Start the server if it is not already running, and return the
    address the game is reachable at.

    Kept alive for the life of the application rather than
    restarted per launch, so reopening the game is instant and
    the browser tab can simply be refreshed.
    """
    global _server, _port

    if not is_available():
        raise FileNotFoundError(
            "The game has not been built. Run 'npm install' and "
            "'npx vite build' in the beatsaber folder."
        )

    if _server is None:
        _server, _port = _listen()

        thread = threading.Thread(
            target=_server.serve_forever,
            name="beatsaber-server",
            daemon=True,
        )
        thread.start()

    return f"http://localhost:{_port}/"


CUSTOM_AUDIO = "custom.wav"
CUSTOM_BEATMAP = "custom_beatmap.json"


def write_custom_track(audio, sr, beat_times, name=None):
    """
    Write the editor's track and its beats for the game to use.

    The game looks for these before its own bundled song, so
    once they exist the track you have open in the editor is
    what you play against. Removing them puts the bundled song
    back.
    """
    import json

    import numpy as np
    import soundfile as sf

    data = np.asarray(audio, dtype=np.float32)

    peak = float(np.max(np.abs(data))) if data.size else 0.0

    if peak > 1.0:
        data = data / peak

    sf.write(
        os.path.join(GAME_DIR, CUSTOM_AUDIO),
        data,
        int(sr),
        subtype="PCM_16",
    )

    beats = [round(float(t), 4) for t in beat_times]
    beats.sort()

    span = (beats[-1] - beats[0]) if len(beats) > 1 else 0.0
    bpm = round(60.0 * (len(beats) - 1) / span, 2) if span else 0.0

    payload = {
        "bpm": bpm,
        "total_beats": len(beats),
        "beat_timestamps": beats,
    }

    if name:
        payload["source"] = str(name)

    with open(
        os.path.join(GAME_DIR, CUSTOM_BEATMAP), "w"
    ) as handle:
        json.dump(payload, handle)

    return len(beats), bpm


def clear_custom_track():
    """Drop the editor's track, so the bundled song plays."""
    removed = False

    for name in (CUSTOM_AUDIO, CUSTOM_BEATMAP):
        path = os.path.join(GAME_DIR, name)

        if os.path.exists(path):
            try:
                os.remove(path)
                removed = True
            except OSError:
                pass

    return removed


def has_custom_track():
    return os.path.exists(
        os.path.join(GAME_DIR, CUSTOM_AUDIO)
    )


def open_game():
    """Start serving and open the game in the default browser."""
    url = start()
    webbrowser.open(url)

    return url


def stop():
    global _server, _port

    if _server is not None:
        _server.shutdown()
        _server.server_close()
        _server = None
        _port = None
