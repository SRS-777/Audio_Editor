"""
Infinite Jukebox window.

Choose a song, let the jukebox analyse it, and it plays forever by jumping
between similar beats. The song is drawn as a circle of beats with arcs joining
beats that sound alike; the playhead and every jump are shown live.

Run standalone:  python jukebox_gui.py [audio file]
"""

import math
import os
import sys
import time
from collections import deque

import numpy as np
import soundfile as sf
from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSizePolicy, QSlider, QToolTip, QVBoxLayout, QWidget
)

import jukebox_engine as je

BG = QColor("#181b21")
ACCENT = QColor("#ffaa00")
TEXT = QColor("#e0e0e0")
MUTED = QColor("#70778c")

AUDIO_FILTER = "Audio Files (*.wav *.flac *.ogg *.mp3);;All Files (*)"


def fmt_time(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    mins, secs = divmod(rest, 60)
    return f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"


# ----------------------------------------------------------------------
# Audio output: pushes a BeatStream into a QAudioSink
# ----------------------------------------------------------------------
class StreamPlayer(QObject):
    BUFFER_SEC = 0.2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sink = None
        self._io = None
        self._stream = None
        self._pending = b""
        self._sr = 0
        self.volume = 1.0
        self.state = "stopped"
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._feed)

    @staticmethod
    def _format(sr, channels):
        fmt = QAudioFormat()
        fmt.setSampleRate(int(sr))
        fmt.setChannelCount(int(channels))
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        return fmt

    @staticmethod
    def playable_rate(sr, channels):
        """The sample rate to play at: `sr` if the device accepts it, else the device's own rate."""
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull() or device.isFormatSupported(StreamPlayer._format(sr, channels)):
            return int(sr)
        return int(device.preferredFormat().sampleRate())

    def start(self, stream):
        self.stop()
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            raise RuntimeError("No audio output device was found.")
        sr, channels = stream.analysis.sr, stream.channels
        self._sink = QAudioSink(device, self._format(sr, channels), self)
        self._sink.setBufferSize(int(sr * channels * 2 * self.BUFFER_SEC))
        self._sink.setVolume(self.volume)
        self._io = self._sink.start()
        if self._io is None or self._sink.error().name != "NoError":
            self.stop()
            raise RuntimeError("The audio device refused to start playback.")
        self._stream, self._sr, self._pending = stream, sr, b""
        self._bytes_per_frame = 2 * channels
        self._feed()
        self._timer.start()
        self.state = "playing"

    def set_volume(self, volume):
        self.volume = float(volume)
        if self._sink is not None:
            self._sink.setVolume(self.volume)

    def pause(self):
        if self.state == "playing":
            self._timer.stop()
            self._sink.suspend()
            self.state = "paused"

    def resume(self):
        if self.state == "paused":
            self._sink.resume()
            self._timer.start()
            self.state = "playing"

    def stop(self):
        self._timer.stop()
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
        self._sink = self._io = self._stream = None
        self._pending = b""
        self.state = "stopped"

    def played_frames(self):
        """Frames that have actually reached the speakers since start()."""
        if self._sink is None:
            return 0
        return int(self._sink.processedUSecs() * self._sr // 1_000_000)

    def _feed(self):
        if self._sink is None:
            return
        free = self._sink.bytesFree()
        if self._pending:
            n = max(0, self._io.write(self._pending))
            self._pending = self._pending[n:]
            free -= n
            if self._pending:
                return
        frames = free // self._bytes_per_frame
        if frames <= 0 or self._stream.exhausted:
            return
        data = je.to_int16_bytes(self._stream.read(frames))
        n = max(0, self._io.write(data))
        self._pending = data[n:]


# ----------------------------------------------------------------------
# Background analysis
# ----------------------------------------------------------------------
class AnalysisWorker(QObject):
    progress = pyqtSignal(float, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    done = pyqtSignal()

    def __init__(self, name, path=None, audio=None, sr=None):
        super().__init__()
        self.name, self.path, self.audio, self.sr = name, path, audio, sr
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _on_progress(self, fraction, message):
        if self._cancelled:
            raise je.AnalysisCancelled()
        if fraction is not None:
            self.progress.emit(fraction, message)

    def run(self):
        try:
            if self.path is not None:
                self.progress.emit(0.0, "Reading file")
                audio, sr = sf.read(self.path, dtype="float32", always_2d=True)
            else:
                audio, sr = np.asarray(self.audio, dtype=np.float32), int(self.sr)
                audio = audio[:, None] if audio.ndim == 1 else audio
            if audio.shape[1] > 2:
                audio = audio[:, :2]
            if audio.shape[1] == 2 and np.allclose(audio[::97, 0], audio[::97, 1]):
                audio = audio[:, :1]                         # dual-mono file
            target = StreamPlayer.playable_rate(sr, audio.shape[1])
            if target != sr:
                self.progress.emit(0.0, "Resampling for the audio device")
                audio = je.resample(audio, sr, target).astype(np.float32)
                sr = target
            analysis = je.analyze(audio, sr, progress=self._on_progress)
            self.succeeded.emit((self.name, audio, analysis))
        except je.AnalysisCancelled:
            pass
        except Exception as exc:                             # shown to the listener
            self.failed.emit(str(exc) or exc.__class__.__name__)
        finally:
            self.done.emit()


# ----------------------------------------------------------------------
# Circular song view
# ----------------------------------------------------------------------
class JukeboxCircle(QWidget):
    beatClicked = pyqtSignal(int)

    FLASH_SEC = 1.6
    TRAIL = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(360, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.analysis = None
        self.graph = None
        self.current = None
        self.trail = deque(maxlen=self.TRAIL)
        self.flashes = deque(maxlen=6)
        self.headline = "Infinite Jukebox"
        self.subtitle = "Choose a song to begin"
        self.detail = ""
        self.progress = None
        self._static = None
        self._static_key = None

    # --- data ---
    def set_song(self, analysis, graph):
        self.analysis, self.graph = analysis, graph
        self.current = None
        self.trail.clear()
        self.flashes.clear()
        self._static = None
        self.update()

    def set_graph(self, graph):
        self.graph = graph
        self._static = None
        self.update()

    def clear_song(self):
        self.set_song(None, None)

    def set_text(self, headline, subtitle="", detail=""):
        self.headline, self.subtitle, self.detail = headline, subtitle, detail
        self.update()

    def set_progress(self, fraction):
        self.progress = fraction
        self.update()

    def set_current(self, beat, branch=None):
        if self.current is not None and self.current != beat:
            self.trail.append(self.current)
        self.current = beat
        if branch is not None:
            self.flashes.append((branch, time.monotonic()))

    def reset_playhead(self):
        self.current = None
        self.trail.clear()
        self.flashes.clear()
        self.update()

    def is_animating(self):
        return any(time.monotonic() - t0 < self.FLASH_SEC for _, t0 in self.flashes)

    # --- geometry ---
    def _geom(self):
        side = min(self.width(), self.height())
        cx, cy = self.width() / 2.0, self.height() / 2.0
        r_outer = side * 0.42
        ring = max(8.0, side * 0.045)
        return cx, cy, r_outer, ring, r_outer - ring - side * 0.012

    def _angle(self, sample):
        return 2.0 * math.pi * sample / float(self.analysis.n_samples)

    @staticmethod
    def _point(cx, cy, r, theta):
        return QPointF(cx + r * math.sin(theta), cy - r * math.cos(theta))

    def _beat_arc(self, painter, cx, cy, radius, beat, gap_deg=0.0):
        s = self.analysis.starts
        a0 = math.degrees(self._angle(s[beat]))
        a1 = math.degrees(self._angle(s[beat + 1]))
        span = a1 - a0
        if span > 3 * gap_deg:
            a0, span = a0 + gap_deg / 2, span - gap_deg
        rect = QRectF(cx - radius, cy - radius, 2 * radius, 2 * radius)
        painter.drawArc(rect, int(round((90.0 - a0) * 16)), -max(1, int(round(span * 16))))

    def _jump_path(self, cx, cy, r, a, b):
        """Curve between the start of beat a and the start of beat b, bowed towards the centre."""
        ta, tb = self._angle(self.analysis.starts[a]), self._angle(self.analysis.starts[b])
        delta = abs(ta - tb)
        mid = (ta + tb) / 2.0
        if delta > math.pi:
            delta = 2 * math.pi - delta
            mid += math.pi
        ctrl_r = r * max(0.0, 1.0 - delta / math.pi) * 0.8
        path = QPainterPath(self._point(cx, cy, r, ta))
        path.quadTo(self._point(cx, cy, ctrl_r, mid), self._point(cx, cy, r, tb))
        return path

    def _beat_color(self, beat, alpha=255):
        r, g, b = self.analysis.colors[beat]
        c = QColor.fromRgbF(float(r), float(g), float(b))
        if self.graph is not None and self.graph.has_loop and not self.graph.in_loop(beat):
            c = QColor.fromRgbF(0.3 + 0.2 * c.redF(), 0.3 + 0.2 * c.greenF(), 0.33 + 0.2 * c.blueF())
        c.setAlpha(alpha)
        return c

    def beat_at(self, pos):
        if self.analysis is None:
            return None
        cx, cy, r_outer, ring, r_inner = self._geom()
        dx, dy = pos.x() - cx, pos.y() - cy
        dist = math.hypot(dx, dy)
        if not (r_inner - 12 <= dist <= r_outer + 12):
            return None
        theta = math.atan2(dx, -dy) % (2 * math.pi)
        sample = theta / (2 * math.pi) * self.analysis.n_samples
        beat = int(np.searchsorted(self.analysis.starts, sample, side="right")) - 1
        return beat if 0 <= beat < self.analysis.n_beats else None

    # --- painting ---
    def resizeEvent(self, event):
        self._static = None
        super().resizeEvent(event)

    def _render_static(self):
        dpr = self.devicePixelRatioF()
        pix = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(BG)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy, r_outer, ring, r_inner = self._geom()
        r_ring = r_outer - ring / 2

        if self.analysis is None:
            p.setPen(QPen(QColor("#2a3040"), ring, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawEllipse(QPointF(cx, cy), r_ring, r_ring)
            p.end()
            return pix

        a = self.analysis
        # arcs between similar beats (best matches drawn last, on top)
        if self.graph is not None:
            pairs = {}
            for br in self.graph.all_branches():
                key = (min(br.src + 1, br.dst), max(br.src + 1, br.dst))
                pairs[key] = max(pairs.get(key, 0.0), br.similarity)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for (b1, b2), sim in sorted(pairs.items(), key=lambda kv: kv[1])[-2500:]:
                c1, c2 = self._beat_color(b1), self._beat_color(b2)
                color = QColor.fromRgbF((c1.redF() + c2.redF()) / 2, (c1.greenF() + c2.greenF()) / 2,
                                        (c1.blueF() + c2.blueF()) / 2, 0.10 + 0.45 * sim ** 2)
                p.setPen(QPen(color, 1.0 + 0.6 * sim))
                p.drawPath(self._jump_path(cx, cy, r_inner, b1, b2))

        # the beats themselves
        gap = 0.25 if a.n_beats < 400 else 0.0
        for k in range(a.n_beats):
            p.setPen(QPen(self._beat_color(k), ring, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            self._beat_arc(p, cx, cy, r_ring, k, gap)

        # time labels at the quarters
        p.setFont(QFont("Helvetica", 8))
        p.setPen(MUTED)
        for q in range(4):
            theta = q * math.pi / 2
            pt = self._point(cx, cy, r_outer + max(14.0, ring * 1.3), theta)
            p.drawText(QRectF(pt.x() - 30, pt.y() - 8, 60, 16), Qt.AlignmentFlag.AlignCenter,
                       fmt_time(q / 4 * a.duration))
        p.end()
        return pix

    def paintEvent(self, event):
        key = (self.width(), self.height(), self.devicePixelRatioF())
        if self._static is None or self._static_key != key:
            self._static, self._static_key = self._render_static(), key
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._static)
        cx, cy, r_outer, ring, r_inner = self._geom()
        r_ring = r_outer - ring / 2

        if self.progress is not None:
            p.setPen(QPen(ACCENT, ring, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            rect = QRectF(cx - r_ring, cy - r_ring, 2 * r_ring, 2 * r_ring)
            p.drawArc(rect, 90 * 16, -int(360 * 16 * max(0.0, min(1.0, self.progress))))

        if self.analysis is not None and self.current is not None:
            now = time.monotonic()
            p.setBrush(Qt.BrushStyle.NoBrush)
            # where playback could jump from here
            if self.graph is not None and 0 <= self.current < self.analysis.n_beats:
                for br in self.graph.branches[self.current]:
                    p.setPen(QPen(QColor(255, 255, 255, 60), 1.2))
                    p.drawPath(self._jump_path(cx, cy, r_inner, br.src + 1, br.dst))
            # jumps just taken
            for br, t0 in self.flashes:
                age = (now - t0) / self.FLASH_SEC
                if age < 1.0:
                    c = QColor(ACCENT)
                    c.setAlphaF(1.0 - age)
                    p.setPen(QPen(c, 3.5 - 2.0 * age))
                    p.drawPath(self._jump_path(cx, cy, r_inner, br.src + 1, br.dst))
            # recently played beats
            for i, beat in enumerate(self.trail):
                c = QColor(ACCENT)
                c.setAlphaF(0.15 + 0.6 * (i + 1) / len(self.trail))
                p.setPen(QPen(c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                self._beat_arc(p, cx, cy, r_outer + 5, beat)
            # the playhead
            if 0 <= self.current < self.analysis.n_beats:
                p.setPen(QPen(QColor("#ffffff"), ring + 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                self._beat_arc(p, cx, cy, r_ring, self.current)
                s = self.analysis.starts
                theta = self._angle((s[self.current] + s[self.current + 1]) / 2)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#ffffff"))
                p.drawEllipse(self._point(cx, cy, r_outer + 12, theta), 4.5, 4.5)

        # centre text
        side = min(self.width(), self.height())
        box_w = r_inner * 1.5
        p.setPen(ACCENT)
        size = max(14, int(side * 0.045))
        font = QFont("Helvetica", size, QFont.Weight.Bold)
        while size > 10 and QFontMetrics(font).horizontalAdvance(self.headline) > box_w:
            size -= 1
            font.setPointSize(size)
        p.setFont(font)
        p.drawText(QRectF(cx - box_w / 2, cy - side * 0.07, box_w, side * 0.07),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, self.headline)
        p.setPen(TEXT)
        p.setFont(QFont("Helvetica", max(9, int(side * 0.02))))
        p.drawText(QRectF(cx - box_w / 2, cy + 4, box_w, side * 0.04),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.subtitle)
        p.setPen(MUTED)
        p.setFont(QFont("Helvetica", max(8, int(side * 0.017))))
        p.drawText(QRectF(cx - box_w / 2, cy + 4 + side * 0.04, box_w, side * 0.08),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                   self.detail)
        p.end()

    # --- interaction ---
    def mouseMoveEvent(self, event):
        beat = self.beat_at(event.position())
        if beat is None:
            self.unsetCursor()
            QToolTip.hideText()
            return
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        n_opts = len(self.graph.branches[beat]) if self.graph is not None else 0
        where = "" if self.graph is None or self.graph.in_loop(beat) or not self.graph.has_loop \
            else "\nOutside the loop (not revisited)"
        QToolTip.showText(event.globalPosition().toPoint(),
                          f"Beat {beat + 1} at {fmt_time(self.analysis.beat_time(beat))}\n"
                          f"{n_opts} jump option{'s' if n_opts != 1 else ''}{where}\nClick to play from here",
                          self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            beat = self.beat_at(event.position())
            if beat is not None:
                self.beatClicked.emit(beat)


# ----------------------------------------------------------------------
# Window
# ----------------------------------------------------------------------
class JukeboxWindow(QWidget):
    playbackStarted = pyqtSignal()

    def __init__(self, editor_track=None, parent=None):
        """editor_track: optional callable returning (audio, sr, name) or None."""
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Infinite Jukebox")
        self.resize(1180, 780)
        self._editor_track = editor_track

        self.song_name = ""
        self.audio = None
        self.analysis = None
        self.graph = None
        self.planner = None
        self.stream = None
        self.player = StreamPlayer(self)
        self._jobs = []
        self._active_worker = None

        self._reset_session()

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        self.circle = JukeboxCircle()
        self.circle.beatClicked.connect(self.play_from_beat)
        root.addWidget(self.circle, stretch=1)

        panel = QVBoxLayout()
        panel.setSpacing(8)
        side = QWidget()
        side.setLayout(panel)
        side.setFixedWidth(320)
        side.setStyleSheet("QLabel, QSlider { background: transparent; }")
        root.addWidget(side)

        # --- song ---
        song_group = QGroupBox("Song")
        song_layout = QVBoxLayout(song_group)
        self.lbl_song = QLabel("No song selected")
        self.lbl_song.setWordWrap(True)
        self.lbl_song.setStyleSheet("color: #e0e0e0;")
        song_layout.addWidget(self.lbl_song)
        row = QHBoxLayout()
        self.btn_choose = QPushButton("Choose Song…")
        self.btn_editor = QPushButton("Use Editor Track")
        self.btn_editor.setVisible(editor_track is not None)
        row.addWidget(self.btn_choose)
        row.addWidget(self.btn_editor)
        song_layout.addLayout(row)
        panel.addWidget(song_group)

        # --- playback ---
        play_group = QGroupBox("Playback")
        play_layout = QVBoxLayout(play_group)
        row = QHBoxLayout()
        self.btn_play = QPushButton("Play")
        self.btn_stop = QPushButton("Stop")
        row.addWidget(self.btn_play)
        row.addWidget(self.btn_stop)
        play_layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Volume"))
        self.slider_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(80)
        row.addWidget(self.slider_volume, stretch=1)
        play_layout.addLayout(row)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        play_layout.addWidget(self.lbl_status)
        panel.addWidget(play_group)

        # --- branching ---
        branch_group = QGroupBox("Jumps")
        grid = QGridLayout(branch_group)
        grid.setVerticalSpacing(6)
        grid.addWidget(QLabel("Similarity"), 0, 0)
        self.lbl_similarity = QLabel()
        self.lbl_similarity.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.lbl_similarity, 0, 1)
        self.slider_similarity = QSlider(Qt.Orientation.Horizontal)
        self.slider_similarity.setRange(0, 100)
        self.slider_similarity.setValue(50)
        self.slider_similarity.setToolTip("Strict keeps only the closest matches; loose allows more jump points.")
        grid.addWidget(self.slider_similarity, 1, 0, 1, 2)
        grid.addWidget(QLabel("Jump frequency"), 2, 0)
        self.lbl_frequency = QLabel()
        self.lbl_frequency.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.lbl_frequency, 2, 1)
        self.slider_frequency = QSlider(Qt.Orientation.Horizontal)
        self.slider_frequency.setRange(0, 100)
        self.slider_frequency.setValue(50)
        self.slider_frequency.setToolTip("How eagerly playback takes a jump when one is available.")
        grid.addWidget(self.slider_frequency, 3, 0, 1, 2)
        self.lbl_graph = QLabel("")
        self.lbl_graph.setWordWrap(True)
        grid.addWidget(self.lbl_graph, 4, 0, 1, 2)
        panel.addWidget(branch_group)

        # --- analysis + session stats ---
        self.stat_labels = {}
        for title, keys in (("Analysis", ("Tempo", "Beats", "Repetition", "Loop")),
                            ("Session", ("Listening", "Jumps", "Heard", "Last jump"))):
            group = QGroupBox(title)
            g = QGridLayout(group)
            g.setVerticalSpacing(4)
            for r, key in enumerate(keys):
                g.addWidget(QLabel(key), r, 0)
                value = QLabel("--")
                value.setStyleSheet("color: #e0e0e0;")
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                g.addWidget(value, r, 1)
                self.stat_labels[key] = value
            g.setColumnStretch(1, 1)
            panel.addWidget(group)

        panel.addStretch()
        hint = QLabel("Click any beat on the circle to play from there.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #70778c; font-size: 11px;")
        panel.addWidget(hint)

        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(250)
        self._rebuild_timer.timeout.connect(self._rebuild_graph)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(33)
        self._tick_timer.timeout.connect(self._tick)

        self.btn_choose.clicked.connect(self.choose_song)
        self.btn_editor.clicked.connect(self.use_editor_track)
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_stop.clicked.connect(self.stop)
        self.slider_similarity.valueChanged.connect(self._on_similarity_changed)
        self.slider_frequency.valueChanged.connect(self._on_frequency_changed)
        self.slider_volume.valueChanged.connect(lambda v: self.player.set_volume(v / 100.0))
        self.player.set_volume(self.slider_volume.value() / 100.0)

        self._on_similarity_changed(self.slider_similarity.value(), rebuild=False)
        self._on_frequency_changed(self.slider_frequency.value())
        self._update_controls()

    # ------------------------------------------------------------------
    # Choosing and analysing a song
    # ------------------------------------------------------------------
    def choose_song(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a Song", "", AUDIO_FILTER)
        if path:
            self.load_file(path)

    def use_editor_track(self):
        track = self._editor_track() if self._editor_track else None
        if track is None:
            QMessageBox.information(self, "No Track", "Load a track in the editor first.")
            return
        self.load_track(*track)

    def load_file(self, path):
        self._start_analysis(AnalysisWorker(os.path.basename(path), path=path))

    def load_track(self, audio, sr, name):
        self._start_analysis(AnalysisWorker(name or "Editor track", audio=np.array(audio, copy=True), sr=sr))

    def _start_analysis(self, worker):
        self.stop()
        if self._active_worker is not None:
            self._active_worker.cancel()
        self.analysis = self.graph = self.planner = self.stream = self.audio = None
        self.song_name = worker.name
        self.setWindowTitle(f"Infinite Jukebox - {worker.name}")
        self.lbl_song.setText(worker.name)
        self.circle.clear_song()
        self.circle.set_text("Analyzing", worker.name, "")
        self.circle.set_progress(0.0)
        self._show_stats_placeholder()

        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_analysis_progress)
        worker.succeeded.connect(self._on_analysis_done)
        worker.failed.connect(self._on_analysis_failed)
        worker.done.connect(thread.quit)
        thread.finished.connect(lambda: self._forget_job(thread, worker))
        self._jobs.append((thread, worker))
        self._active_worker = worker
        self._update_controls()
        thread.start()

    def _forget_job(self, thread, worker):
        self._jobs = [(t, w) for t, w in self._jobs if t is not thread]
        if self._active_worker is worker:
            self._active_worker = None
            self._update_controls()
        thread.deleteLater()

    def _is_current(self):
        return self.sender() is self._active_worker

    def _on_analysis_progress(self, fraction, message):
        if self._is_current():
            self.circle.set_progress(fraction)
            self.circle.set_text("Analyzing", message, self.song_name)

    def _on_analysis_failed(self, message):
        if not self._is_current():
            return
        self.circle.set_progress(None)
        self.circle.set_text("Can't play", message, self.song_name)
        self.lbl_status.setText(message)

    def _on_analysis_done(self, result):
        if not self._is_current():
            return
        name, audio, analysis = result
        self.audio, self.analysis = audio, analysis
        self.circle.set_progress(None)
        self.lbl_song.setText(f"{name}\n{fmt_time(analysis.duration)} · "
                              f"{'stereo' if audio.shape[1] == 2 else 'mono'} · {analysis.sr} Hz")
        self.stat_labels["Tempo"].setText(f"{analysis.tempo:.1f} BPM")
        self.stat_labels["Beats"].setText(str(analysis.n_beats))
        self._rebuild_graph(initial=True)
        self._reset_session()
        self._show_idle_text()
        self._update_controls()

    # ------------------------------------------------------------------
    # Branch graph
    # ------------------------------------------------------------------
    def _on_similarity_changed(self, value, rebuild=True):
        name = "Strict" if value < 34 else "Balanced" if value < 67 else "Loose"
        self.lbl_similarity.setText(f"{name} ({je.sensitivity_to_quantile(value) * 100:.1f}%)")
        if rebuild and self.analysis is not None:
            self._rebuild_timer.start()

    def _on_frequency_changed(self, value):
        self.lbl_frequency.setText("Rare" if value < 34 else "Moderate" if value < 67 else "Often")
        if self.planner is not None:
            self.planner.set_adventure(value / 100.0)

    def _rebuild_graph(self, initial=False):
        if self.analysis is None:
            return
        quantile = je.sensitivity_to_quantile(self.slider_similarity.value())
        self.graph = je.build_graph(self.analysis, quantile=quantile)
        if self.planner is None:
            self.planner = je.RoutePlanner(self.graph, adventure=self.slider_frequency.value() / 100.0)
            self.stream = je.BeatStream(self.audio, self.analysis, self.planner)
        else:
            self.planner.graph = self.graph
        if initial:
            self.circle.set_song(self.analysis, self.graph)
        else:
            self.circle.set_graph(self.graph)

        g, a = self.graph, self.analysis
        self.stat_labels["Repetition"].setText(f"{g.structure_label()} ({g.structure * 100:.0f}%)")
        if g.has_loop:
            self.stat_labels["Loop"].setText(
                f"{fmt_time(a.beat_time(g.loop_start))} - {fmt_time(a.starts[g.loop_end + 1] / a.sr)}")
            text = f"{g.n_branches} jump points · the loop spans {g.loop_coverage():.0%} of the song."
            if g.relaxed:
                text += " Similarity was loosened automatically to find enough jumps."
            if g.structure_label() == "Weak":
                text += " This song repeats little, so some jumps may be audible."
        else:
            self.stat_labels["Loop"].setText("none")
            text = "No loop found. The song will play to its end; try a looser similarity."
        self.lbl_graph.setText(text)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def toggle_play(self):
        if self.player.state == "playing":
            self.pause()
        elif self.player.state == "paused":
            self.player.resume()
            self.playbackStarted.emit()
            self._tick_timer.start()
            self._update_controls()
        else:
            self._start(None)

    def pause(self):
        if self.player.state == "playing":
            self.player.pause()
            self._tick_timer.stop()
            self._update_controls()
            self._update_center_text(paused=True)

    def stop(self):
        if self.player.state != "stopped":
            self.player.stop()
        self._tick_timer.stop()
        self.circle.reset_playhead()
        if self.analysis is not None:
            self._show_idle_text()
        self._update_controls()

    def play_from_beat(self, beat):
        if self.analysis is None:
            return
        if self.player.state == "stopped":
            self._start(beat)
        else:
            self._listened_before += self.player.played_frames()
            self._start(beat, keep_session=True)

    def _start(self, beat, keep_session=False):
        if self.stream is None:
            return
        if not keep_session:
            self._reset_session()
        self.player.stop()
        self.stream.reset(start_beat=beat)
        try:
            self.player.start(self.stream)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Playback Error", str(exc))
            self._update_controls()
            return
        self.playbackStarted.emit()
        self._tick_timer.start()
        self._update_controls()

    def _reset_session(self):
        self._listened_before = 0
        self._session_jumps = 0
        self._heard = set()
        self._last_jump = None
        self._current_beat = None

    def _tick(self):
        if self.stream is None or self.player.state != "playing":
            return
        played = self.player.played_frames()
        for ev in self.stream.consume_events(played):
            if ev.beat < 0:
                continue
            self._current_beat = ev.beat
            self._heard.add(ev.beat)
            if ev.branch is not None:
                self._session_jumps += 1
                self._last_jump = ev.branch
            self.circle.set_current(ev.beat, ev.branch)

        if self.stream.finished and played >= self.stream.generated_frames:
            self.stop()
            self.lbl_status.setText("Reached the end of the song.")
            return
        self._update_center_text()
        self._update_session_stats(played)
        self.circle.update()

    def _update_center_text(self, paused=False):
        a = self.analysis
        if a is None:
            return
        listened = (self._listened_before + self.player.played_frames()) / float(a.sr)
        beat = self._current_beat
        where = f"Beat {beat + 1} of {a.n_beats} · {fmt_time(a.beat_time(beat))}" if beat is not None \
            else "Intro"
        jumps = f"{self._session_jumps} jump{'s' if self._session_jumps != 1 else ''}"
        self.circle.set_text(fmt_time(listened), where, ("Paused · " if paused else "") + jumps)

    def _update_session_stats(self, played):
        a = self.analysis
        listened = (self._listened_before + played) / float(a.sr)
        self.stat_labels["Listening"].setText(f"{fmt_time(listened)} (song is {fmt_time(a.duration)})")
        self.stat_labels["Jumps"].setText(str(self._session_jumps))
        self.stat_labels["Heard"].setText(f"{len(self._heard) / a.n_beats:.0%} of beats")
        br = self._last_jump
        if br is not None:
            self.stat_labels["Last jump"].setText(
                f"{fmt_time(a.starts[br.src + 1] / a.sr)} → {fmt_time(a.beat_time(br.dst))} "
                f"({br.similarity * 100:.0f}%)")

    def _show_idle_text(self):
        g = self.graph
        detail = ("Plays forever by jumping between similar beats" if g is not None and g.has_loop
                  else "No loop found - the song will end normally")
        self.circle.set_text("Ready", "Press Play", detail)
        for key in ("Listening", "Jumps", "Heard", "Last jump"):
            self.stat_labels[key].setText("--")

    def _show_stats_placeholder(self):
        for label in self.stat_labels.values():
            label.setText("--")
        self.lbl_graph.setText("")
        self.lbl_status.setText("")

    def _update_controls(self):
        ready = self.analysis is not None
        state = self.player.state
        self.btn_play.setEnabled(ready)
        self.btn_play.setText("Pause" if state == "playing" else "Resume" if state == "paused" else "Play")
        self.btn_stop.setEnabled(state != "stopped")
        if state == "playing":
            self.lbl_status.setText("Playing - jumps are drawn on the circle as they happen.")
        elif state == "paused":
            self.lbl_status.setText("Paused.")
        elif self._active_worker is not None:
            self.lbl_status.setText("Analyzing the song…")
        elif ready:
            self.lbl_status.setText("Ready.")

    def closeEvent(self, event):
        self.stop()
        for thread, worker in list(self._jobs):
            worker.cancel()
            thread.quit()
            thread.wait(5000)
        super().closeEvent(event)


def main():
    from audio_editor import DARK_STYLE

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    window = JukeboxWindow()
    window.show()
    if len(sys.argv) > 1:
        window.load_file(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
