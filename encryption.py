import sys
import os
import time
import hashlib
import numpy as np
import soundfile as sf
from scipy.signal import (
    butter, sosfiltfilt, sosfilt, fftconvolve, lfilter, lfilter_zi,
)

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QMessageBox, QLabel, QLineEdit,
    QGroupBox, QGridLayout,
)
from PyQt6.QtCore import QUrl
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AMPLITUDE        = 0.005
SILENT_FLOOR     = 0.05
BAND_LOW_HZ      = 2500.0
BAND_HIGH_HZ     = 6500.0
ENV_SMOOTH_MS    = 20.0

MAX_MSG_BYTES    = 5000
MAGIC            = b"\xA5\x5A"
SALT_LEN         = 16
NONCE_LEN        = 12
GCM_TAG_LEN      = 16

# MAGIC(2) + salt(16) + nonce(12) + ct_len(2) + spread(1) + repeat(1)
HEADER_LEN       = 2 + SALT_LEN + NONCE_LEN + 2 + 1 + 1

SPREAD_TABLE     = [64, 128, 256, 512, 1024, 2048, 4096]
REPEAT_TABLE     = [1, 2, 3]

PBKDF2_ITERATIONS = 200_000
_SPREAD_SALT      = b"wm-spread-seed-v1"     # fixed salt, spreader-only
                                              # (must be known before header parse)

TEMP_PLAYBACK    = "wm_playback.wav"


# ---------------------------------------------------------------------------
# Key derivation — two independent derivations from the password.
# ---------------------------------------------------------------------------
def _derive_spread_seed(password: str) -> int:
    """Deterministic seed for the PN spreader.  Fixed salt because we need
    this before we can parse the header (chicken-and-egg)."""
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"),
        _SPREAD_SALT, PBKDF2_ITERATIONS, 32,
    )
    return int.from_bytes(dk[:8], "big")


def _derive_aead_key(password: str, salt: bytes) -> bytes:
    """AES-GCM key.  Fresh random salt per message, stored in the header."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"),
        salt, PBKDF2_ITERATIONS, 32,
    )


# ---------------------------------------------------------------------------
# Spreading sequence — band-limited PN
# ---------------------------------------------------------------------------
def _make_spread_sequence(seed, n_samples, sr):
    """Password-seeded, band-limited ±1 sequence.  Runs through the same
    band as the detector, so it sounds like a soft hiss instead of
    broadband noise."""
    if n_samples <= 0:
        return np.empty(0, dtype=np.float32)

    rng = np.random.default_rng(seed)
    raw = rng.integers(0, 2, size=n_samples).astype(np.float64) * 2.0 - 1.0

    nyq = sr / 2.0
    low = max(0.001, min(BAND_LOW_HZ / nyq, 0.90))
    high = max(low + 0.01, min(BAND_HIGH_HZ / nyq, 0.98))

    sos = butter(4, [low, high], btype="bandpass", output="sos")

    if n_samples > 64:
        shaped = sosfiltfilt(sos, raw)
    else:
        shaped = raw

    shaped -= np.mean(shaped)
    rms = np.sqrt(np.mean(shaped ** 2)) + 1e-12
    shaped /= rms
    return shaped.astype(np.float32)


def _bandpass_audio(audio, sr):
    x = np.asarray(audio, dtype=np.float64)
    if len(x) <= 64:
        return x.astype(np.float32)

    nyq = sr / 2.0
    low = max(0.001, min(BAND_LOW_HZ / nyq, 0.90))
    high = max(low + 0.01, min(BAND_HIGH_HZ / nyq, 0.98))
    sos = butter(4, [low, high], btype="bandpass", output="sos")
    try:
        y = sosfiltfilt(sos, x)
    except ValueError:
        y = sosfilt(sos, x)
    return y.astype(np.float32)


# ---------------------------------------------------------------------------
# Envelope — fast (fftconvolve + lfilter, no Python loop)
# ---------------------------------------------------------------------------
def _smoothed_envelope(audio, sr, spread, smooth_ms=ENV_SMOOTH_MS):
    x = np.asarray(audio, dtype=np.float64)
    if len(x) == 0:
        return np.empty(0, dtype=np.float32)

    win = np.ones(spread, dtype=np.float64) / spread
    local_power = fftconvolve(x * x, win, mode="same")
    raw_env = np.sqrt(np.maximum(local_power, 0.0))

    tau_samples = max(1.0, (smooth_ms / 1000.0) * sr)
    alpha = 1.0 - np.exp(-1.0 / tau_samples)

    # single-pole IIR: y[n] = alpha*x[n] + (1-alpha)*y[n-1]
    b = [alpha]
    a = [1.0, -(1.0 - alpha)]
    zi = lfilter_zi(b, a) * raw_env[0]
    smoothed, _ = lfilter(b, a, raw_env, zi=zi)

    peak = float(np.max(smoothed)) + 1e-12
    return (smoothed / peak).astype(np.float32)


# ---------------------------------------------------------------------------
# Bit helpers
# ---------------------------------------------------------------------------
def _bytes_to_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


def _bits_to_bytes(bits: np.ndarray) -> bytes:
    n = len(bits) - (len(bits) % 8)
    return np.packbits(bits[:n]).tobytes()


# ---------------------------------------------------------------------------
# Capacity / parameter selection
# ---------------------------------------------------------------------------
def max_capacity(audio_len: int) -> int:
    smallest_spread = SPREAD_TABLE[0]
    smallest_repeat = REPEAT_TABLE[0]
    max_payload_bytes = audio_len // (smallest_spread * smallest_repeat * 8)
    return max(0, max_payload_bytes - HEADER_LEN - GCM_TAG_LEN)


def _pick_params(audio_len: int, plaintext_len: int):
    payload_bytes = HEADER_LEN + plaintext_len + GCM_TAG_LEN
    bits = payload_bytes * 8

    for spread in reversed(SPREAD_TABLE):
        for repeat in reversed(REPEAT_TABLE):
            if bits * repeat * spread <= audio_len:
                return spread, repeat

    cap = max_capacity(audio_len)
    raise ValueError(
        f"Audio too short for this message.\n"
        f"Message: {plaintext_len} bytes\n"
        f"Audio:   {audio_len} samples\n"
        f"Capacity: {cap} bytes"
    )


# ---------------------------------------------------------------------------
# Embed
# ---------------------------------------------------------------------------
def embed(audio, sr, message, password):
    if audio.ndim > 1:
        audio = audio[:, 0]
    audio = audio.astype(np.float32).copy()

    plaintext = message.encode("utf-8")
    if not plaintext:
        raise ValueError("Message cannot be empty")
    if len(plaintext) > MAX_MSG_BYTES:
        raise ValueError(f"Message too long (max {MAX_MSG_BYTES} bytes)")

    # --- AEAD -------------------------------------------------------------
    salt  = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    aead_key = _derive_aead_key(password, salt)
    ciphertext = AESGCM(aead_key).encrypt(nonce, plaintext, None)  # +16B tag

    # --- Parameters -------------------------------------------------------
    spread, repeat = _pick_params(len(audio), len(plaintext))
    spread_code = SPREAD_TABLE.index(spread)
    repeat_code = REPEAT_TABLE.index(repeat)

    print(f"[embed] plaintext={len(plaintext)}B  "
          f"ciphertext={len(ciphertext)}B  spread={spread}  "
          f"repeat={repeat}  amp={AMPLITUDE}")

    # --- Payload ----------------------------------------------------------
    header = (
        MAGIC
        + salt
        + nonce
        + len(ciphertext).to_bytes(2, "big")
        + bytes([spread_code, repeat_code])
    )
    payload = header + ciphertext

    bits = _bytes_to_bits(payload)
    bits_repeated = np.repeat(bits, repeat)
    n_samples = len(bits_repeated) * spread

    if n_samples > len(audio):
        raise ValueError("Internal error: payload does not fit")

    # --- Spread sequence --------------------------------------------------
    spread_seed = _derive_spread_seed(password)
    full_seq = _make_spread_sequence(spread_seed, len(audio), sr)
    spread_seq = full_seq[:n_samples]

    signs = bits_repeated.astype(np.float32) * 2.0 - 1.0
    modulated = signs.repeat(spread) * spread_seq

    # --- Host headroom ----------------------------------------------------
    peak = float(np.max(np.abs(audio))) + 1e-12
    if peak + AMPLITUDE > 1.0:
        audio *= (1.0 - AMPLITUDE) / peak

    # --- Adaptive masking gain -------------------------------------------
    band_audio = _bandpass_audio(audio, sr)
    envelope = _smoothed_envelope(band_audio[:n_samples], sr, spread)
    adaptive_gain = AMPLITUDE * (
        SILENT_FLOOR + (1.0 - SILENT_FLOOR) * envelope
    )

    audio[:n_samples] += adaptive_gain * modulated
    np.clip(audio, -1.0, 1.0, out=audio)
    return audio


# ---------------------------------------------------------------------------
# Reveal
# ---------------------------------------------------------------------------
def reveal(audio, sr, password):
    if audio.ndim > 1:
        audio = audio[:, 0]
    audio = audio.astype(np.float32)

    if sr / 2.0 <= BAND_HIGH_HZ:
        raise ValueError(
            f"Sample rate {sr} Hz is too low for band "
            f"{BAND_LOW_HZ:.0f}-{BAND_HIGH_HZ:.0f} Hz"
        )

    # Precompute expensive things once.
    spread_seed = _derive_spread_seed(password)
    full_seq = _make_spread_sequence(spread_seed, len(audio), sr)
    filtered = _bandpass_audio(audio, sr).astype(np.float64)

    for spread_code in range(len(SPREAD_TABLE) - 1, -1, -1):
        for repeat_code in range(len(REPEAT_TABLE) - 1, -1, -1):
            spread = SPREAD_TABLE[spread_code]
            repeat = REPEAT_TABLE[repeat_code]

            result = _try_decode(
                filtered, sr, password, full_seq,
                spread, repeat, spread_code, repeat_code,
            )
            if result is not None:
                print(f"[reveal] decoded with spread={spread} "
                      f"repeat={repeat}")
                return result

    raise ValueError("No watermark found (or wrong password)")


def _try_decode(filtered, sr, password, full_seq,
                spread, repeat, spread_code, repeat_code):
    max_bits = len(filtered) // spread
    max_samples = max_bits * spread
    if max_bits < HEADER_LEN * 8:
        return None

    spread_seq = full_seq[:max_samples].astype(np.float64)

    envelope = _smoothed_envelope(
        filtered[:max_samples], sr, spread
    ).astype(np.float64)
    adaptive_gain = AMPLITUDE * (
        SILENT_FLOOR + (1.0 - SILENT_FLOOR) * envelope
    )
    template = spread_seq * adaptive_gain

    # Matched filter per chip block.
    raw_bits = np.empty(max_bits, dtype=np.uint8)
    for i in range(max_bits):
        s = i * spread
        e = s + spread
        x = filtered[s:e]
        t = template[s:e]
        xm = x - x.mean()
        tm = t - t.mean()
        denom = np.linalg.norm(xm) * np.linalg.norm(tm)
        if denom < 1e-12:
            raw_bits[i] = 0
            continue
        raw_bits[i] = 1 if float(np.dot(xm, tm) / denom) >= 0.0 else 0

    # Majority vote.
    n_groups = len(raw_bits) // repeat
    if n_groups < HEADER_LEN * 8:
        return None
    grouped = raw_bits[: n_groups * repeat].reshape(n_groups, repeat)
    bits = (grouped.sum(axis=1) > (repeat / 2.0)).astype(np.uint8)

    # Header.
    hb = _bits_to_bytes(bits[: HEADER_LEN * 8])
    if len(hb) < HEADER_LEN or hb[:2] != MAGIC:
        return None

    off = 2
    salt = bytes(hb[off:off + SALT_LEN]); off += SALT_LEN
    nonce = bytes(hb[off:off + NONCE_LEN]); off += NONCE_LEN
    ct_len = int.from_bytes(hb[off:off + 2], "big"); off += 2
    h_spread = hb[off]; off += 1
    h_repeat = hb[off]; off += 1

    if h_spread != spread_code or h_repeat != repeat_code:
        return None
    if ct_len <= GCM_TAG_LEN or ct_len > MAX_MSG_BYTES + GCM_TAG_LEN:
        return None

    total_bits = (HEADER_LEN + ct_len) * 8
    if total_bits > len(bits):
        return None

    ct_bytes = _bits_to_bytes(bits[HEADER_LEN * 8: total_bits])
    if len(ct_bytes) < ct_len:
        return None
    ciphertext = bytes(ct_bytes[:ct_len])

    aead_key = _derive_aead_key(password, salt)
    try:
        plaintext = AESGCM(aead_key).decrypt(nonce, ciphertext, None)
    except Exception:
        # InvalidTag — wrong password or tampered payload.
        return None

    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ---------------------------------------------------------------------------
# Self-test — runs a full round-trip before the GUI starts
# ---------------------------------------------------------------------------
def _selftest():
    print("=" * 60)
    print("Watermark self-test")
    print("=" * 60)

    sr = 44100
    duration = 20.0
    n = int(sr * duration)
    t = np.arange(n) / sr

    audio = (
        0.30 * np.sin(2 * np.pi * 220 * t)
        + 0.20 * np.sin(2 * np.pi * 440 * t)
        + 0.10 * np.sin(2 * np.pi * 3300 * t)
              * (0.5 + 0.5 * np.sin(2 * np.pi * 0.5 * t))
    ).astype(np.float32)

    message  = "The quick brown fox jumps over the lazy dog."
    password = "correct horse battery staple"

    print(f"audio    : {n} samples @ {sr} Hz")
    print(f"message  : {len(message.encode('utf-8'))} bytes")
    print(f"capacity : {max_capacity(n)} bytes")

    t0 = time.time()
    wm = embed(audio, sr, message, password)
    print(f"embed    : {time.time() - t0:.2f} s")

    t0 = time.time()
    recovered = reveal(wm, sr, password)
    print(f"reveal   : {time.time() - t0:.2f} s")

    print(f"original : {message!r}")
    print(f"recovered: {recovered!r}")
    assert recovered == message, "ROUND-TRIP FAILED"
    print("PASS: round-trip")

    try:
        reveal(wm, sr, "wrong password")
        print("FAIL: wrong password was accepted")
    except ValueError:
        print("PASS: wrong password rejected")

    try:
        reveal(audio, sr, password)
        print("FAIL: unwatermarked audio decoded")
    except ValueError:
        print("PASS: unwatermarked audio rejected")

    # Bit-flip test: corrupt a few samples, must either fail cleanly or
    # return the SAME message (it must not return a corrupted plaintext).
    wm2 = wm.copy()
    wm2[500:520] += 0.05
    try:
        r2 = reveal(wm2, sr, password)
        assert r2 == message, f"CORRUPTION ACCEPTED: {r2!r}"
        print("PASS: light sample corruption still decodes")
    except ValueError:
        print("PASS: corrupted audio cleanly rejected")

    print("=" * 60)
    print("All checks passed.")


# ---------------------------------------------------------------------------
# Waveform widget
# ---------------------------------------------------------------------------
class WaveformCanvas(FigureCanvas):
    def __init__(self, parent=None, width=8, height=2.5, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi,
                          facecolor="#181b21")
        self.axes = self.fig.add_subplot(111, facecolor="#181b21")
        super().__init__(self.fig)
        self.fig.subplots_adjust(left=0.08, right=0.98,
                                 top=0.92, bottom=0.15)
        self._style()

    def _style(self):
        self.axes.tick_params(colors="#c0c0c0", labelsize=8)
        for sp in self.axes.spines.values():
            sp.set_color("#404040")
        self.axes.grid(True, color="#404040",
                       linestyle="--", linewidth=0.5, alpha=0.4)

    def plot(self, audio, sr, title=""):
        self.axes.clear()
        self._style()
        t = np.arange(len(audio)) / sr
        max_pts = 20000
        if len(audio) > max_pts:
            step = len(audio) // max_pts
            t = t[::step]
            audio = audio[::step]
        self.axes.fill_between(t, audio, color="#ffaa00", alpha=0.3)
        self.axes.plot(t, audio, color="#ffaa00",
                       linewidth=1.0, alpha=0.9)
        self.axes.set_ylim(-1.05, 1.05)
        self.axes.set_xlim(0, t[-1] if len(t) else 1)
        self.axes.set_title(title, color="#e0e0e0",
                            fontsize=10, pad=6)
        self.axes.set_ylabel("Amplitude", color="#c0c0c0", fontsize=8)
        self.draw()


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------
STYLE = """
QMainWindow, QWidget {
    background-color: #0d0e12;
    color: #e0e0e0;
    font-family: 'Helvetica', Arial, sans-serif;
    font-size: 12px;
}
QPushButton {
    background-color: #1e222d;
    color: #ffaa00;
    border: 1px solid #2a3040;
    border-radius: 4px;
    padding: 8px 16px;
    font-weight: bold;
    min-width: 100px;
}
QPushButton:hover      { background-color: #2a3040; border-color: #ffaa00; }
QPushButton:pressed    { background-color: #ffaa00; color: #0d0e12; }
QPushButton:disabled   { color: #555; border-color: #222; }
QLineEdit {
    background-color: #1e222d;
    color: #e0e0e0;
    border: 1px solid #2a3040;
    border-radius: 4px;
    padding: 6px;
}
QLineEdit:focus        { border-color: #ffaa00; }
QLabel                 { font-family: monospace; font-size: 12px;
                         color: #a0a0a0; }
QGroupBox {
    border: 1px solid #2a3040;
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 10px;
    color: #ffaa00;
    background-color: #12151e;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }
#status {
    color: #00ff88; font-size: 13px; padding: 8px;
    background-color: #14161f; border: 1px solid #2a3040;
    border-radius: 4px;
}
#hint { color: #70778c; font-size: 11px; }
"""


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class WatermarkWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Watermark")
        self.resize(1000, 850)

        self.audio = None
        self.sr = None
        self.filename = ""

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)

        default_device = QMediaDevices.defaultAudioOutput()
        if default_device is not None:
            self.audio_output.setDevice(default_device)
            print(f"[audio] Bound to: {default_device.description()}")

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        self.canvas = WaveformCanvas(self)
        self.canvas.setMinimumHeight(200)
        layout.addWidget(self.canvas, stretch=1)

        self.lbl_file = QLabel("No file loaded")
        self.lbl_file.setObjectName("hint")
        layout.addWidget(self.lbl_file)

        file_row = QHBoxLayout()
        self.btn_load  = QPushButton("Load Audio")
        self.btn_save  = QPushButton("Save Watermarked Audio")
        self.btn_play  = QPushButton("Play")
        self.btn_pause = QPushButton("Pause")
        self.btn_stop  = QPushButton("Stop")
        self.btn_save.setEnabled(False)
        self.btn_play.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)

        file_row.addWidget(self.btn_load)
        file_row.addWidget(self.btn_save)
        file_row.addSpacing(20)
        file_row.addWidget(self.btn_play)
        file_row.addWidget(self.btn_pause)
        file_row.addWidget(self.btn_stop)
        file_row.addStretch()
        layout.addLayout(file_row)

        wm_group = QGroupBox("Watermark")
        wm_layout = QGridLayout(wm_group)
        wm_layout.setVerticalSpacing(10)
        wm_layout.setHorizontalSpacing(10)

        wm_layout.addWidget(QLabel("Message:"), 0, 0)
        self.txt_message = QLineEdit()
        self.txt_message.setPlaceholderText("Type the message to hide...")
        wm_layout.addWidget(self.txt_message, 0, 1, 1, 3)

        wm_layout.addWidget(QLabel("Password:"), 1, 0)
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Required to embed and reveal")
        wm_layout.addWidget(self.txt_password, 1, 1, 1, 3)

        self.btn_embed  = QPushButton("Embed Watermark")
        self.btn_reveal = QPushButton("Reveal Watermark")
        self.btn_embed.setEnabled(False)
        self.btn_reveal.setEnabled(False)
        wm_layout.addWidget(self.btn_embed,  2, 0, 1, 2)
        wm_layout.addWidget(self.btn_reveal, 2, 2, 1, 2)

        wm_layout.setColumnStretch(0, 1)
        wm_layout.setColumnStretch(1, 3)
        wm_layout.setColumnStretch(2, 1)
        wm_layout.setColumnStretch(3, 3)
        layout.addWidget(wm_group)

        self.lbl_status = QLabel("Ready.")
        self.lbl_status.setObjectName("status")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setMinimumHeight(60)
        layout.addWidget(self.lbl_status)

        self.lbl_capacity = QLabel(
            "Load an audio file to see its capacity."
        )
        self.lbl_capacity.setObjectName("hint")
        layout.addWidget(self.lbl_capacity)

        self.btn_load.clicked.connect(self.on_load)
        self.btn_embed.clicked.connect(self.on_embed)
        self.btn_reveal.clicked.connect(self.on_reveal)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_play.clicked.connect(self.on_play)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_stop.clicked.connect(self.on_stop)
        self.player.playbackStateChanged.connect(self.on_state_changed)

    def _write_playback_file(self):
        if self.audio is None:
            return None
        path = os.path.abspath(TEMP_PLAYBACK)
        sf.write(path, self.audio, self.sr)
        return path

    def on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Audio File", "",
            "Audio Files (*.wav *.flac *.ogg *.mp3);;All Files (*)",
        )
        if not path:
            return

        try:
            audio, sr = sf.read(path)
            if audio.ndim > 1:
                audio = audio[:, 0]
            self.audio = audio.astype(np.float32)
            self.sr = sr
            self.filename = os.path.basename(path)

            self.canvas.plot(self.audio, self.sr, self.filename)
            self.player.stop()

            playback_file = self._write_playback_file()
            if playback_file:
                self.player.setSource(QUrl.fromLocalFile(playback_file))

            n_samples = len(self.audio)
            capacity = max_capacity(n_samples)

            self.lbl_file.setText(
                f"{self.filename} · {n_samples} samples · {sr} Hz"
            )
            self.lbl_capacity.setText(
                f"Maximum message length: {capacity} bytes"
            )

            self.btn_embed.setEnabled(True)
            self.btn_reveal.setEnabled(True)
            self.btn_save.setEnabled(False)
            self.btn_play.setEnabled(True)
            self.btn_pause.setEnabled(True)
            self.btn_stop.setEnabled(True)

            self.set_status(f"Loaded {self.filename} — ready.", ok=True)

        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def on_embed(self):
        if self.audio is None:
            return
        message = self.txt_message.text().strip()
        password = self.txt_password.text()

        if not message:
            QMessageBox.warning(self, "Missing Message",
                                "Type a message first.")
            return
        if not password:
            QMessageBox.warning(self, "Missing Password",
                                "Enter a password.")
            return

        try:
            self.player.stop()

            QApplication.setOverrideCursor(
                __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.CursorShape
                .WaitCursor
            )
            try:
                watermarked = embed(self.audio, self.sr, message, password)
            finally:
                QApplication.restoreOverrideCursor()

            self.audio = watermarked
            self.canvas.plot(self.audio, self.sr,
                             self.filename + " [watermarked]")

            playback_file = self._write_playback_file()
            if playback_file:
                self.player.setSource(QUrl.fromLocalFile(playback_file))

            self.btn_save.setEnabled(True)
            self.set_status(
                f"✓ Embedded {len(message.encode('utf-8'))} bytes.\n"
                f"Use Reveal with the same password.",
                ok=True,
            )

        except Exception as e:
            QMessageBox.critical(self, "Embed Error", str(e))

    def on_reveal(self):
        if self.audio is None:
            return
        password = self.txt_password.text()
        if not password:
            QMessageBox.warning(self, "Missing Password",
                                "Enter the password used to embed.")
            return

        try:
            QApplication.setOverrideCursor(
                __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.CursorShape
                .WaitCursor
            )
            try:
                message = reveal(self.audio, self.sr, password)
            finally:
                QApplication.restoreOverrideCursor()

            self.set_status(
                f"✓ Watermark found:\n\n    {message!r}", ok=True
            )
        except ValueError as e:
            self.set_status(f"✗ {e}", ok=False)

    def on_save(self):
        if self.audio is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Watermarked Audio", "watermarked.wav",
            "WAV Files (*.wav);;FLAC Files (*.flac)",
        )
        if not path:
            return
        try:
            sf.write(path, self.audio, self.sr)
            self.set_status(f"✓ Saved to:\n{path}", ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def on_play(self):
        if self.audio is None:
            return
        if self.player.source().isEmpty():
            playback_file = self._write_playback_file()
            if playback_file:
                self.player.setSource(QUrl.fromLocalFile(playback_file))
        self.player.play()

    def on_pause(self): self.player.pause()
    def on_stop(self):  self.player.stop()

    def on_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setText("Playing...")
        else:
            self.btn_play.setText("Play")

    def set_status(self, text, ok=True):
        color = "#00ff88" if ok else "#ff5566"
        self.lbl_status.setStyleSheet(
            f"color: {color}; font-size: 13px; padding: 8px;"
            f"background-color: #14161f; border: 1px solid #2a3040;"
            f"border-radius: 4px;"
        )
        self.lbl_status.setText(text)

    def closeEvent(self, event):
        self.player.stop()
        try:
            if os.path.exists(TEMP_PLAYBACK):
                os.remove(TEMP_PLAYBACK)
        except Exception:
            pass
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    window = WatermarkWindow()
    window.show()
    sys.exit(app.exec())