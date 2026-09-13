import sys
import os
import numpy as np
import soundfile as sf
import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, 
    QHBoxLayout, QPushButton, QFileDialog, QMessageBox,
    QSlider, QLabel, QComboBox, QDoubleSpinBox, QGroupBox,
    QCheckBox, QGridLayout, QTabWidget, QSizePolicy, QFrame,
    QDialog
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput,QMediaDevices
from scipy.signal import resample
from scipy.signal import fftconvolve

from jukebox_gui import JukeboxWindow

DARK_STYLE = """
QMainWindow { background-color: #0d0e12; }
QWidget { background-color: #0d0e12; color: #e0e0e0; font-family: 'Helvetica', Arial, sans-serif; font-size: 12px; }
QPushButton { 
    background-color: #1e222d; color: #ffaa00; border: 1px solid #2a3040; 
    border-radius: 4px; padding: 5px 12px; font-weight: bold; min-width: 70px; 
}
QPushButton:hover { background-color: #2a3040; border-color: #ffaa00; }
QPushButton:pressed { background-color: #ffaa00; color: #0d0e12; }
QSlider::groove:horizontal { height: 5px; background: #1e222d; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #ffaa00; border-radius: 3px; }
QSlider::handle:horizontal { background: #ffffff; border: 2px solid #ffaa00; width: 14px; margin-top: -4px; margin-bottom: -4px; border-radius: 7px; }
QComboBox { background-color: #1e222d; border: 1px solid #2a3040; border-radius: 4px; padding: 4px 8px; color: #ffaa00; }
QLabel { font-family: monospace; font-size: 12px; color: #a0a0a0; }
QGroupBox { 
    border: 1px solid #2a3040; border-radius: 4px; margin-top: 8px; 
    padding-top: 8px; color: #ffaa00; background-color: #12151e; 
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }
QDoubleSpinBox, QSpinBox { background-color: #1e222d; border: 1px solid #2a3040; border-radius: 3px; padding: 3px; color: #e0e0e0; }
QCheckBox { color: #e0e0e0; }
QTabWidget::pane { border: 1px solid #2a3040; border-radius: 4px; background-color: #0d0e12; }
QTabBar {
    background-color: #0d0e12;
    border: 1px solid #2a3040;
    border-radius: 6px;
    padding: 2px;
    qproperty-drawBase: 0;
}
QTabBar::tab { 
    background-color: #1e222d; 
    color: #a0a0a0; 
    padding: 12px 40px; 
    font-size: 14px; 
    font-weight: bold; 
    border: 1px solid #2a3040; 
    border-radius: 4px; 
    margin: 2px 3px; 
}
QTabBar::tab:selected { 
    background-color: #2a3040; 
    color: #ffaa00; 
    border: 1px solid #3a4050; 
}
QTabBar::tab:hover { 
    background-color: #2a3040; 
    border: 1px solid #3a4050; 
}
#fileInfo { 
    font-family: 'Helvetica', Arial, sans-serif; 
    font-size: 12px; 
    color: #c0c0c0; 
    background-color: #1a1d26; 
    border: 1px solid #2a3040; 
    border-radius: 4px; 
    padding: 4px 10px; 
}
#eqValue { 
    color: #ffaa00; 
    font-size: 12px; 
    font-weight: bold;
    padding: 0 4px;
}
#undoCount {
    color: #70778c;
    font-size: 11px;
    padding: 0 4px;
}
"""

def format_time(seconds):
    if seconds is None or seconds < 0: return "00:00"
    mins = int(seconds // 60); secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"

# ----------------------------------------------------------------------
# Helper: make all controls in a group fill their grid columns
# ----------------------------------------------------------------------
def fill_group(group):
    """Make every widget in a group expand horizontally and center labels."""
    for w in group.findChildren((QPushButton, QDoubleSpinBox, QComboBox, QCheckBox)):
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    for w in group.findChildren(QSlider):
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    for w in group.findChildren(QLabel):
        if w.objectName() != "eqValue":
            w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)

# ----------------------------------------------------------------------
# Waveform Canvas
# ----------------------------------------------------------------------
class AudioCanvas(FigureCanvas):
    def __init__(self, parent=None, width=8, height=3.5, dpi=120):
        self.fig = Figure(figsize=(width, height), dpi=dpi, facecolor='#181b21')
        self.axes = self.fig.add_subplot(111, facecolor='#181b21')
        super().__init__(self.fig)
        self.time_axis = None
        self.signal_data = None
        self.total_duration = 0.0
        self.playhead = None
        self._style_axes()
        self.fig.subplots_adjust(left=0.08, right=0.98, top=0.94, bottom=0.08)
        self.fig.tight_layout()

    def _style_axes(self):
        self.axes.tick_params(colors='#c0c0c0', labelsize=8)
        for spine in self.axes.spines.values():
            spine.set_color('#404040')
        self.axes.grid(True, color='#404040', linestyle='--', linewidth=0.5, alpha=0.4)

    def plot_signal(self, time_axis, viz_signal, filename="", beat_times=None):
        self.axes.clear()
        self._style_axes()
        self.time_axis = time_axis
        self.signal_data = viz_signal
        self.total_duration = time_axis[-1] if len(time_axis) > 0 else 0.0

        max_plot_points = 20000
        if len(viz_signal) > max_plot_points:
            step = len(viz_signal) // max_plot_points
            plot_signal = viz_signal[::step]
            plot_time = time_axis[::step]
        else:
            plot_signal = viz_signal
            plot_time = time_axis

        self.axes.fill_between(plot_time, plot_signal, color='#ffaa00', alpha=0.3)
        self.axes.plot(plot_time, plot_signal, color='#ffaa00', linewidth=1.5, alpha=0.95)

        if beat_times is not None:
            for bt in beat_times:
                if 0 <= bt <= self.total_duration:
                    self.axes.axvline(x=bt, color='#00ff88', linestyle='--', linewidth=1.2, alpha=0.7)

        self.playhead = self.axes.axvline(x=0, color='#ffffff', linestyle='-', linewidth=2.5, visible=False)
        self.axes.set_title(f"Track: {filename}" if filename else "", color='#e0e0e0', fontsize=10, pad=8)
        self.axes.set_ylabel("Amplitude", color='#c0c0c0', fontsize=8)
        self.axes.set_ylim(-1.05, 1.05)
        self.axes.set_xlim(0, self.total_duration)
        self.draw()

    def update_viewport(self, t_current, window_size_sec):
        if self.playhead is None or self.total_duration == 0:
            return
        self.playhead.set_xdata([t_current, t_current])
        self.playhead.set_visible(True)
        if window_size_sec == "Full":
            self.axes.set_xlim(0, self.total_duration)
        else:
            win_val = float(window_size_sec)
            half_win = win_val / 2.0
            left = max(0.0, t_current - half_win)
            right = left + win_val
            if right > self.total_duration:
                right = self.total_duration
                left = max(0.0, right - win_val)
            self.axes.set_xlim(left, right)
        self.draw_idle()

    def hide_playhead(self):
        if self.playhead:
            self.playhead.set_visible(False)
            self.draw_idle()

# ----------------------------------------------------------------------
# Spectrum Canvas
# ----------------------------------------------------------------------
class SpectrumCanvas(FigureCanvas):
    def __init__(self, parent=None, width=8, height=1.2, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi, facecolor='#181b21')
        self.axes = self.fig.add_subplot(111, facecolor='#181b21')
        super().__init__(self.fig)
        self.axes.set_xlabel("Frequency (Hz)", color='#c0c0c0', fontsize=7)
        self.axes.set_ylabel("Magnitude", color='#c0c0c0', fontsize=7)
        self.axes.tick_params(colors='#c0c0c0', labelsize=6)
        for spine in self.axes.spines.values():
            spine.set_color('#404040')
        self.fig.subplots_adjust(left=0.08, right=0.98, top=0.93, bottom=0.15)
        self.fig.tight_layout()

    def plot_spectrum(self, audio, sr, title="Spectrum", center_time=None):
        self.axes.clear()
        if len(audio) < 64:
            return

        n_fft = 16384
        if len(audio) < n_fft:
            n_fft = len(audio)

        if center_time is None:
            start = int(2.0 * sr)
        else:
            start = int(center_time * sr) - n_fft // 2
        start = max(0, min(start, len(audio) - n_fft))
        segment = audio[start:start + n_fft].astype(float)

        window = np.hanning(len(segment))
        segment = segment * window

        fft_data = np.fft.rfft(segment)
        freq = np.fft.rfftfreq(len(segment), d=1/sr)
        mag = np.abs(fft_data)
        mag = mag / (np.sum(window) / 2 + 1e-12)
        mag_db = 20 * np.log10(mag + 1e-12)

        self.axes.plot(freq, mag_db, color='#00e5ff', linewidth=0.8)
        self.axes.set_xlim(0, sr / 2)
        self.axes.set_ylim(-100, 5)
        self.axes.set_title(title, color='#e0e0e0', fontsize=8)
        self.axes.grid(True, color='#404040', linestyle='--', linewidth=0.5, alpha=0.3)
        self.draw()

# ----------------------------------------------------------------------
# Pitch Over Time Dialog
# ----------------------------------------------------------------------
class PitchPlotDialog(QDialog):
    def __init__(self, times, pitches, median_pitch, dominant_pitch, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pitch Contour")
        self.resize(900, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        n_voiced = int(np.sum(~np.isnan(pitches)))
        info = QLabel(
            f"Median: {median_pitch:.1f} Hz    "
            f"Dominant: {dominant_pitch:.1f} Hz    "
            f"Voiced frames: {n_voiced} / {len(pitches)}"
        )
        info.setStyleSheet("color: #ffaa00; font-size: 13px; padding: 4px;")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

        fig = Figure(figsize=(9, 3.2), dpi=100, facecolor='#181b21')
        ax = fig.add_subplot(111, facecolor='#181b21')

        ax.plot(times, pitches, color='#00e5ff', linewidth=0.9, alpha=0.9)
        ax.axhline(median_pitch, color='#ffaa00', linestyle='--',
                   linewidth=1.2, label=f"Median: {median_pitch:.1f} Hz")
        ax.axhline(dominant_pitch, color='#00ff88', linestyle=':',
                   linewidth=1.0, label=f"Dominant: {dominant_pitch:.1f} Hz")

        ax.set_xlabel("Time (s)", color='#c0c0c0', fontsize=9)
        ax.set_ylabel("Pitch (Hz)", color='#c0c0c0', fontsize=9)
        ax.set_title("Pitch Over Time", color='#e0e0e0', fontsize=11, pad=10)
        ax.tick_params(colors='#c0c0c0', labelsize=8)
        for spine in ax.spines.values():
            spine.set_color('#404040')
        ax.grid(True, color='#404040', linestyle='--', linewidth=0.5, alpha=0.35)
        ax.legend(loc='upper right', fontsize=9,
                  facecolor='#1e222d', edgecolor='#2a3040',
                  labelcolor='#e0e0e0')
        fig.tight_layout()

        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

# ----------------------------------------------------------------------
# Main Workstation
# ----------------------------------------------------------------------
class AudioWorkstation(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DSP Pro Audio Workstation")
        self.resize(1200, 850)

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)

        # Explicitly bind to whatever macOS says is the current default
        # (Qt's QAudioOutput() alone doesn't always do this correctly)
        default_device = QMediaDevices.defaultAudioOutput()
        if default_device is not None:
            self.audio_output.setDevice(default_device)
            print(f"[audio] Bound to: {default_device.description()}")
        else:
            print("[audio] No default device found — using Qt's choice")
        self.duration = 0.0
        self.user_is_scrubbing = False
        self.current_audio = None
        self.original_audio = None
        self.sr = None
        self.current_filename = ""
        self.original_filename = ""
        self.undo_stack = []
        self.temp_file = "temp_edit.wav"
        self.temp_counter = 0
        self.beat_times = None

        self.speed_factor = 1.0

        self._last_spectrum_ms = -1
        self._spectrum_update_interval_ms = 50

        self.remix_audio = None
        self.remix_filename = ""
        self.previewing_remix = False

        self.jukebox_window = None

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(6)

        # ---- Plot area ----
        plot_widget = QWidget()
        plot_layout = QVBoxLayout(plot_widget)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(2)

        self.canvas = AudioCanvas(self, width=8, height=3.5)
        self.canvas.setMinimumHeight(200)
        plot_layout.addWidget(self.canvas)

        spectrum_controls = QHBoxLayout()
        spectrum_controls.setSpacing(16)

        self.show_spectrum = QCheckBox("Show Spectrum")
        self.show_spectrum.setChecked(False)
        self.show_spectrum.stateChanged.connect(self.toggle_spectrum)
        spectrum_controls.addWidget(self.show_spectrum)

        self.follow_playhead = QCheckBox("Follow Playhead")
        self.follow_playhead.setChecked(False)
        self.follow_playhead.setEnabled(False)
        self.follow_playhead.setToolTip(
            "When checked, the spectrum updates live as playback progresses."
        )
        spectrum_controls.addWidget(self.follow_playhead)

        spectrum_controls.addStretch()
        plot_layout.addLayout(spectrum_controls)

        self.spectrum_canvas = SpectrumCanvas(self, width=8, height=1.2)
        self.spectrum_canvas.setVisible(False)
        self.spectrum_canvas.setMinimumHeight(160)
        self.spectrum_canvas.setMaximumHeight(220)
        self.spectrum_canvas.setStyleSheet(
            "border: 1px solid #2a3040; border-radius: 4px; background-color: #181b21;"
        )
        plot_layout.addWidget(self.spectrum_canvas)

        main_layout.addWidget(plot_widget, stretch=6)

        # ---- Scrubber ----
        progress_layout = QHBoxLayout()
        progress_layout.setSpacing(8)
        self.lbl_current_time = QLabel("00:00")
        self.slider_progress = QSlider(Qt.Orientation.Horizontal)
        self.slider_progress.setRange(0, 1000)
        self.lbl_total_time = QLabel("00:00")
        progress_layout.addWidget(self.lbl_current_time)
        progress_layout.addWidget(self.slider_progress)
        progress_layout.addWidget(self.lbl_total_time)
        main_layout.addLayout(progress_layout)

        # ---- Playback Controls + File Info ----
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(8)

        transport_group = QHBoxLayout()
        transport_group.setSpacing(4)
        self.btn_load = QPushButton("Load")
        self.btn_play = QPushButton("Play")
        self.btn_pause = QPushButton("Pause")
        self.btn_stop = QPushButton("Stop")
        transport_group.addWidget(self.btn_load)
        transport_group.addWidget(self.btn_play)
        transport_group.addWidget(self.btn_pause)
        transport_group.addWidget(self.btn_stop)
        controls_layout.addLayout(transport_group)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        sep1.setStyleSheet("background-color: #2a3040; width: 1px;")
        controls_layout.addWidget(sep1)

        file_ops = QHBoxLayout()
        file_ops.setSpacing(4)
        self.btn_undo = QPushButton("Undo")
        self.lbl_undo_count = QLabel("")
        self.lbl_undo_count.setObjectName("undoCount")
        self.btn_save = QPushButton("Save")
        self.btn_reset = QPushButton("Reset")
        file_ops.addWidget(self.btn_undo)
        file_ops.addWidget(self.lbl_undo_count)
        file_ops.addWidget(self.btn_save)
        file_ops.addWidget(self.btn_reset)
        controls_layout.addLayout(file_ops)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        sep2.setStyleSheet("background-color: #2a3040; width: 1px;")
        controls_layout.addWidget(sep2)

        self.lbl_info = QLabel("No file loaded")
        self.lbl_info.setObjectName("fileInfo")
        controls_layout.addWidget(self.lbl_info, stretch=1)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setFrameShadow(QFrame.Shadow.Sunken)
        sep3.setStyleSheet("background-color: #2a3040; width: 1px;")
        controls_layout.addWidget(sep3)

        view_speed = QHBoxLayout()
        view_speed.setSpacing(6)
        self.lbl_zoom = QLabel("View:")
        self.combo_zoom = QComboBox()
        self.combo_zoom.addItems(["1", "3", "5", "10", "20", "30", "Full"])
        self.combo_zoom.setCurrentText("1")
        self.lbl_speed = QLabel("Speed:")
        self.combo_speed = QComboBox()
        self.combo_speed.addItems(["0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.combo_speed.setCurrentText("1.0x")
        view_speed.addWidget(self.lbl_zoom)
        view_speed.addWidget(self.combo_zoom)
        view_speed.addWidget(self.lbl_speed)
        view_speed.addWidget(self.combo_speed)
        controls_layout.addLayout(view_speed)

        main_layout.addLayout(controls_layout)

        # ---- Tab Widget ----
        self.tab_widget = QTabWidget()
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.setTabPosition(QTabWidget.TabPosition.North)
        self.tab_widget.setMinimumHeight(220)

        # ========== Basic Tab ==========
        basic_tab = QWidget()
        basic_layout = QHBoxLayout(basic_tab)
        basic_layout.setContentsMargins(8, 8, 8, 8)
        basic_layout.setSpacing(12)

        amp_group = QGroupBox("Amplitude")
        amp_layout = QGridLayout(amp_group)
        amp_layout.setVerticalSpacing(8)
        amp_layout.setHorizontalSpacing(10)

        self.btn_reverse = QPushButton("Reverse")
        amp_layout.addWidget(self.btn_reverse, 0, 0, 1, 3)

        amp_layout.addWidget(QLabel("Scale:"), 1, 0)
        self.scale_spin = QDoubleSpinBox(); self.scale_spin.setRange(0.1, 10); self.scale_spin.setValue(1)
        amp_layout.addWidget(self.scale_spin, 1, 1)
        self.btn_scale = QPushButton("Apply Scale")
        amp_layout.addWidget(self.btn_scale, 1, 2)

        amp_layout.addWidget(QLabel("Fade In:"), 2, 0)
        self.fade_in_spin = QDoubleSpinBox(); self.fade_in_spin.setRange(0.1, 60); self.fade_in_spin.setValue(2)
        amp_layout.addWidget(self.fade_in_spin, 2, 1)
        self.btn_fade_in = QPushButton("Apply")
        amp_layout.addWidget(self.btn_fade_in, 2, 2)

        amp_layout.addWidget(QLabel("Fade Out:"), 3, 0)
        self.fade_out_spin = QDoubleSpinBox(); self.fade_out_spin.setRange(0.1, 60); self.fade_out_spin.setValue(2)
        amp_layout.addWidget(self.fade_out_spin, 3, 1)
        self.btn_fade_out = QPushButton("Apply")
        amp_layout.addWidget(self.btn_fade_out, 3, 2)

        amp_layout.setColumnStretch(0, 1)
        amp_layout.setColumnStretch(1, 2)
        amp_layout.setColumnStretch(2, 1)
        fill_group(amp_group)

        time_group = QGroupBox("Time Edit")
        time_layout = QGridLayout(time_group)
        time_layout.setVerticalSpacing(8)
        time_layout.setHorizontalSpacing(10)

        time_layout.addWidget(QLabel("Trim Start:"), 0, 0)
        self.trim_start = QDoubleSpinBox(); self.trim_start.setRange(0, 3600); self.trim_start.setValue(0)
        time_layout.addWidget(self.trim_start, 0, 1)
        time_layout.addWidget(QLabel("End:"), 0, 2)
        self.trim_end = QDoubleSpinBox(); self.trim_end.setRange(0, 3600); self.trim_end.setValue(5)
        time_layout.addWidget(self.trim_end, 0, 3)
        self.btn_trim = QPushButton("Apply Trim")
        time_layout.addWidget(self.btn_trim, 0, 4)

        self.btn_join = QPushButton("Join Audio")
        time_layout.addWidget(self.btn_join, 1, 0, 1, 5)

        time_layout.setColumnStretch(0, 1)
        time_layout.setColumnStretch(1, 2)
        time_layout.setColumnStretch(2, 1)
        time_layout.setColumnStretch(3, 2)
        time_layout.setColumnStretch(4, 1)
        fill_group(time_group)

        basic_layout.addWidget(amp_group, 1)
        basic_layout.addWidget(time_group, 1)
        self.tab_widget.addTab(basic_tab, "Basic")

        # ========== Effects Tab ==========
        effects_tab = QWidget()
        effects_layout = QHBoxLayout(effects_tab)
        effects_layout.setContentsMargins(8, 8, 8, 8)
        effects_layout.setSpacing(12)

        nr_group = QGroupBox("Noise Reduction")
        nr_layout = QGridLayout(nr_group)
        nr_layout.setVerticalSpacing(8)
        nr_layout.setHorizontalSpacing(10)
        nr_layout.addWidget(QLabel("Method:"), 0, 0)
        self.nr_combo = QComboBox()
        self.nr_combo.addItems(["Time Smooth", "Notch 50Hz", "Notch 60Hz"])
        nr_layout.addWidget(self.nr_combo, 0, 1)
        self.btn_nr = QPushButton("Apply NR")
        nr_layout.addWidget(self.btn_nr, 0, 2)
        nr_layout.setColumnStretch(0, 1)
        nr_layout.setColumnStretch(1, 2)
        nr_layout.setColumnStretch(2, 1)
        fill_group(nr_group)

        eq_group = QGroupBox("Equalizer")
        eq_layout = QGridLayout(eq_group)
        eq_layout.setVerticalSpacing(8)
        eq_layout.setHorizontalSpacing(10)

        eq_layout.addWidget(QLabel("Low"), 0, 0)
        self.eq_low = QSlider(Qt.Orientation.Horizontal)
        self.eq_low.setRange(-12, 12); self.eq_low.setValue(0)
        eq_layout.addWidget(self.eq_low, 0, 1)
        self.lbl_eq_low = QLabel("0 dB")
        self.lbl_eq_low.setObjectName("eqValue")
        self.lbl_eq_low.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_eq_low.setMinimumWidth(55)
        eq_layout.addWidget(self.lbl_eq_low, 0, 2)
        self.eq_low.valueChanged.connect(self._update_eq_labels)

        eq_layout.addWidget(QLabel("Mid"), 1, 0)
        self.eq_mid = QSlider(Qt.Orientation.Horizontal)
        self.eq_mid.setRange(-12, 12); self.eq_mid.setValue(0)
        eq_layout.addWidget(self.eq_mid, 1, 1)
        self.lbl_eq_mid = QLabel("0 dB")
        self.lbl_eq_mid.setObjectName("eqValue")
        self.lbl_eq_mid.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_eq_mid.setMinimumWidth(55)
        eq_layout.addWidget(self.lbl_eq_mid, 1, 2)
        self.eq_mid.valueChanged.connect(self._update_eq_labels)

        eq_layout.addWidget(QLabel("High"), 2, 0)
        self.eq_high = QSlider(Qt.Orientation.Horizontal)
        self.eq_high.setRange(-12, 12); self.eq_high.setValue(0)
        eq_layout.addWidget(self.eq_high, 2, 1)
        self.lbl_eq_high = QLabel("0 dB")
        self.lbl_eq_high.setObjectName("eqValue")
        self.lbl_eq_high.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_eq_high.setMinimumWidth(55)
        eq_layout.addWidget(self.lbl_eq_high, 2, 2)
        self.eq_high.valueChanged.connect(self._update_eq_labels)

        self.btn_eq = QPushButton("Apply EQ")
        eq_layout.addWidget(self.btn_eq, 3, 0, 1, 3)

        eq_layout.setColumnStretch(0, 1)
        eq_layout.setColumnStretch(1, 6)
        eq_layout.setColumnStretch(2, 2)
        fill_group(eq_group)

        reverb_group = QGroupBox("Reverb / Echo")
        reverb_layout = QGridLayout(reverb_group)
        reverb_layout.setVerticalSpacing(8)
        reverb_layout.setHorizontalSpacing(10)

        reverb_layout.addWidget(QLabel("Reverb Room (ms):"), 0, 0)
        self.rev_size = QDoubleSpinBox(); self.rev_size.setRange(10, 500); self.rev_size.setValue(100)
        reverb_layout.addWidget(self.rev_size, 0, 1)
        reverb_layout.addWidget(QLabel("Decay:"), 0, 2)
        self.rev_decay = QDoubleSpinBox(); self.rev_decay.setRange(0.1, 0.99); self.rev_decay.setValue(0.5)
        reverb_layout.addWidget(self.rev_decay, 0, 3)
        self.btn_reverb = QPushButton("Apply Reverb")
        reverb_layout.addWidget(self.btn_reverb, 0, 4)

        reverb_layout.addWidget(QLabel("Echo Delay (s):"), 1, 0)
        self.echo_delay = QDoubleSpinBox(); self.echo_delay.setRange(0.01, 2.0); self.echo_delay.setValue(0.2)
        reverb_layout.addWidget(self.echo_delay, 1, 1)
        reverb_layout.addWidget(QLabel("Decay:"), 1, 2)
        self.echo_decay = QDoubleSpinBox(); self.echo_decay.setRange(0.1, 0.9); self.echo_decay.setValue(0.5)
        reverb_layout.addWidget(self.echo_decay, 1, 3)
        self.btn_echo = QPushButton("Apply Echo")
        reverb_layout.addWidget(self.btn_echo, 1, 4)

        reverb_layout.setColumnStretch(0, 2)
        reverb_layout.setColumnStretch(1, 2)
        reverb_layout.setColumnStretch(2, 1)
        reverb_layout.setColumnStretch(3, 2)
        reverb_layout.setColumnStretch(4, 3)
        fill_group(reverb_group)

        effects_layout.addWidget(nr_group, 1)
        effects_layout.addWidget(eq_group, 2)
        effects_layout.addWidget(reverb_group, 2)
        self.tab_widget.addTab(effects_tab, "Effects")

        # ========== Remix Tab ==========
        remix_tab = QWidget()
        remix_layout = QVBoxLayout(remix_tab)
        remix_layout.setContentsMargins(8, 8, 8, 8)
        remix_layout.setSpacing(8)

        # Load + preview row
        load_row = QHBoxLayout()
        self.btn_load_remix = QPushButton("Load Remix Clip")
        load_row.addWidget(self.btn_load_remix)

        self.btn_preview_remix = QPushButton("Preview Remix")
        self.btn_preview_remix.setEnabled(False)
        self.btn_preview_remix.setToolTip("Play the remix clip alone so you can hear it")
        load_row.addWidget(self.btn_preview_remix)

        self.lbl_remix_file = QLabel("No remix loaded")
        load_row.addWidget(self.lbl_remix_file, stretch=1)
        remix_layout.addLayout(load_row)

        # Parameters group
        params_group = QGroupBox("Insert Parameters")
        params_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        params_grid = QGridLayout(params_group)
        params_grid.setVerticalSpacing(8)
        params_grid.setHorizontalSpacing(10)

        # --- Mode row ---
        params_grid.addWidget(QLabel("Mode:"), 0, 0)
        self.remix_mode = QComboBox()
        self.remix_mode.addItems([
            "Insert (splice in)",
            "Replace (overwrite)",
            "Superimpose (mix)",
        ])
        self.remix_mode.setCurrentIndex(0)
        self.remix_mode.setToolTip(
            "Insert: main track grows by the remix length\n"
            "Replace: the remix overwrites a section of the main track (same length)\n"
            "Superimpose: the remix is mixed on top of the main track"
        )
        params_grid.addWidget(self.remix_mode, 0, 1, 1, 5)

        # --- Start / Full Clip / Duration ---
        params_grid.addWidget(QLabel("Start (s):"), 1, 0)
        self.remix_start = QDoubleSpinBox(); self.remix_start.setRange(0, 3600); self.remix_start.setValue(0)
        params_grid.addWidget(self.remix_start, 1, 1)
        self.remix_use_full = QCheckBox("Full Clip"); self.remix_use_full.setChecked(True)
        params_grid.addWidget(self.remix_use_full, 1, 2)
        params_grid.addWidget(QLabel("Duration (s):"), 1, 3)
        self.remix_duration = QDoubleSpinBox(); self.remix_duration.setRange(0.1, 3600); self.remix_duration.setValue(2.0)
        self.remix_duration.setEnabled(False)
        params_grid.addWidget(self.remix_duration, 1, 4)
        self.remix_use_full.toggled.connect(lambda checked: self.remix_duration.setEnabled(not checked))

        # --- Fades and Gain ---
        params_grid.addWidget(QLabel("Fade In (s):"), 2, 0)
        self.remix_fade_in = QDoubleSpinBox(); self.remix_fade_in.setRange(0, 5); self.remix_fade_in.setValue(0.5)
        params_grid.addWidget(self.remix_fade_in, 2, 1)
        params_grid.addWidget(QLabel("Fade Out:"), 2, 2)
        self.remix_fade_out = QDoubleSpinBox(); self.remix_fade_out.setRange(0, 5); self.remix_fade_out.setValue(0.5)
        params_grid.addWidget(self.remix_fade_out, 2, 3)
        params_grid.addWidget(QLabel("Gain:"), 2, 4)
        self.remix_gain = QDoubleSpinBox(); self.remix_gain.setRange(0.0, 5.0); self.remix_gain.setValue(1.0)
        params_grid.addWidget(self.remix_gain, 2, 5)

        # --- Action button ---
        self.btn_insert_remix = QPushButton("Apply Remix")
        params_grid.addWidget(self.btn_insert_remix, 3, 0, 1, 6)

        for c in range(6):
            params_grid.setColumnStretch(c, 1)
        fill_group(params_group)

        remix_layout.addWidget(params_group, 1)
        self.tab_widget.addTab(remix_tab, "Remix")

        # ========== Analysis Tab ==========
        analysis_tab = QWidget()
        analysis_layout = QGridLayout(analysis_tab)
        analysis_layout.setContentsMargins(8, 8, 8, 8)
        analysis_layout.setVerticalSpacing(8)
        analysis_layout.setHorizontalSpacing(12)

        self.btn_pitch = QPushButton("Estimate Pitch")
        analysis_layout.addWidget(self.btn_pitch, 0, 0)
        self.lbl_pitch = QLabel("Pitch: -- Hz")
        analysis_layout.addWidget(self.lbl_pitch, 0, 1)
        self.btn_tempo = QPushButton("Detect Tempo")
        analysis_layout.addWidget(self.btn_tempo, 0, 2)
        self.lbl_tempo = QLabel("Tempo: -- BPM")
        analysis_layout.addWidget(self.lbl_tempo, 0, 3)

        analysis_layout.addWidget(QLabel("Resample to:"), 1, 0)
        self.resample_combo = QComboBox()
        self.resample_combo.addItems(["44100", "22050", "11025", "8000", "Custom"])
        self.resample_combo.setCurrentIndex(0)
        analysis_layout.addWidget(self.resample_combo, 1, 1)

        self.custom_sr_spin = QDoubleSpinBox()
        self.custom_sr_spin.setRange(100, 192000)
        self.custom_sr_spin.setValue(48000)
        self.custom_sr_spin.setDecimals(0)
        self.custom_sr_spin.setSuffix(" Hz")
        self.custom_sr_spin.setVisible(False)
        analysis_layout.addWidget(self.custom_sr_spin, 1, 2)

        self.resample_aliasing = QCheckBox("Aliasing Demo")
        analysis_layout.addWidget(self.resample_aliasing, 1, 3)
        self.btn_resample = QPushButton("Apply Resample")
        analysis_layout.addWidget(self.btn_resample, 1, 4)

        for col in range(5):
            analysis_layout.setColumnStretch(col, 1)

        for w in (self.btn_pitch, self.lbl_pitch, self.btn_tempo, self.lbl_tempo,
                  self.resample_combo, self.custom_sr_spin,
                  self.resample_aliasing, self.btn_resample):
            w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.lbl_pitch.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_tempo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.resample_combo.currentTextChanged.connect(
            lambda text: self.custom_sr_spin.setVisible(text == "Custom")
        )

        self.tab_widget.addTab(analysis_tab, "Analysis")

        # ========== Jukebox Tab ==========
        jukebox_tab = QWidget()
        jukebox_layout = QHBoxLayout(jukebox_tab)
        jukebox_layout.setContentsMargins(8, 8, 8, 8)
        jukebox_layout.setSpacing(12)

        jukebox_group = QGroupBox("Infinite Jukebox")
        jukebox_grid = QGridLayout(jukebox_group)
        jukebox_grid.setVerticalSpacing(8)
        jukebox_grid.setHorizontalSpacing(10)

        lbl_jukebox = QLabel(
            "Detects the beats of a song, finds beats that sound alike, and keeps the song "
            "playing forever by jumping between them. The song is drawn as a circle with "
            "arcs joining similar beats."
        )
        lbl_jukebox.setWordWrap(True)
        jukebox_grid.addWidget(lbl_jukebox, 0, 0, 1, 2)

        self.btn_jukebox_current = QPushButton("Open with Current Track")
        self.btn_jukebox_current.setToolTip("Analyze the track loaded in the editor, including your edits")
        jukebox_grid.addWidget(self.btn_jukebox_current, 1, 0)
        self.btn_jukebox_open = QPushButton("Open Jukebox")
        self.btn_jukebox_open.setToolTip("Open the jukebox and choose any song")
        jukebox_grid.addWidget(self.btn_jukebox_open, 1, 1)

        jukebox_grid.setColumnStretch(0, 1)
        jukebox_grid.setColumnStretch(1, 1)
        fill_group(jukebox_group)

        jukebox_layout.addWidget(jukebox_group, 1)
        self.tab_widget.addTab(jukebox_tab, "Jukebox")

        main_layout.addSpacing(14)
        main_layout.addWidget(self.tab_widget, stretch=4)

        # ---- Connect signals ----
        self.btn_load.clicked.connect(self.load_audio)
        self.btn_play.clicked.connect(self.play_audio)
        self.btn_pause.clicked.connect(self.pause_audio)
        self.btn_stop.clicked.connect(self.stop_audio)
        self.btn_undo.clicked.connect(self.undo_last)
        self.btn_save.clicked.connect(self.save_audio)
        self.btn_reset.clicked.connect(self.reset_audio)

        self.player.positionChanged.connect(self.on_position_changed)
        self.player.playbackStateChanged.connect(self.on_state_changed)
        self.slider_progress.sliderPressed.connect(self.on_slider_pressed)
        self.slider_progress.sliderReleased.connect(self.on_slider_released)
        self.slider_progress.valueChanged.connect(self.on_slider_value_changed)
        self.combo_zoom.currentTextChanged.connect(self.on_zoom_changed)
        self.combo_speed.currentTextChanged.connect(self.on_speed_changed)

        self.btn_reverse.clicked.connect(self.apply_reverse)
        self.btn_scale.clicked.connect(self.apply_scale)
        self.btn_fade_in.clicked.connect(self.apply_fade_in)
        self.btn_fade_out.clicked.connect(self.apply_fade_out)
        self.btn_trim.clicked.connect(self.apply_trim)
        self.btn_join.clicked.connect(self.apply_join)

        self.btn_nr.clicked.connect(self.apply_noise_reduction)
        self.btn_eq.clicked.connect(self.apply_eq)
        self.btn_reverb.clicked.connect(self.apply_reverb)
        self.btn_echo.clicked.connect(self.apply_echo)

        self.btn_load_remix.clicked.connect(self.load_remix_clip)
        self.btn_preview_remix.clicked.connect(self.preview_remix_clip)
        self.btn_insert_remix.clicked.connect(self.apply_remix)

        self.btn_pitch.clicked.connect(self.estimate_pitch)
        self.btn_tempo.clicked.connect(self.detect_tempo)
        self.btn_resample.clicked.connect(self.apply_resample)

        self.btn_jukebox_current.clicked.connect(self.open_jukebox_with_current)
        self.btn_jukebox_open.clicked.connect(self.open_jukebox)

    # ------------------------------------------------------------------
    # EQ label + Undo counter updaters
    # ------------------------------------------------------------------
    def _update_eq_labels(self):
        for slider, label in ((self.eq_low, self.lbl_eq_low),
                              (self.eq_mid, self.lbl_eq_mid),
                              (self.eq_high, self.lbl_eq_high)):
            v = slider.value()
            label.setText(f"{v:+d} dB" if v != 0 else "0 dB")

    def _update_undo_label(self):
        n = len(self.undo_stack)
        if n == 0:
            self.lbl_undo_count.setText("")
        elif n == 1:
            self.lbl_undo_count.setText("(1 step)")
        else:
            self.lbl_undo_count.setText(f"({n} steps)")

    # ------------------------------------------------------------------
    # UI control reset helper
    # ------------------------------------------------------------------
    def _reset_ui_controls(self):
        self.scale_spin.setValue(1.0)
        self.fade_in_spin.setValue(2.0)
        self.fade_out_spin.setValue(2.0)
        self.trim_start.setValue(0.0)
        self.trim_end.setValue(5.0)

        self.nr_combo.setCurrentIndex(0)
        self.eq_low.setValue(0)
        self.eq_mid.setValue(0)
        self.eq_high.setValue(0)
        self._update_eq_labels()
        self.rev_size.setValue(100)
        self.rev_decay.setValue(0.5)
        self.echo_delay.setValue(0.2)
        self.echo_decay.setValue(0.5)

        self.remix_mode.setCurrentIndex(0)
        self.remix_start.setValue(0.0)
        self.remix_use_full.setChecked(True)
        self.remix_duration.setValue(2.0)
        self.remix_duration.setEnabled(False)
        self.remix_fade_in.setValue(0.5)
        self.remix_fade_out.setValue(0.5)
        self.remix_gain.setValue(1.0)

        self.resample_combo.setCurrentIndex(0)
        self.custom_sr_spin.setValue(48000)
        self.custom_sr_spin.setVisible(False)
        self.resample_aliasing.setChecked(False)
        self.lbl_pitch.setText("Pitch: -- Hz")
        self.lbl_tempo.setText("Tempo: -- BPM")

        self.speed_factor = 1.0
        self.combo_zoom.setCurrentText("1")
        self.combo_speed.setCurrentText("1.0x")
        self.show_spectrum.setChecked(False)
        self.follow_playhead.setChecked(False)

    # ------------------------------------------------------------------
    # Core audio management
    # ------------------------------------------------------------------
    def _update_audio_source(self):
        if self.current_audio is None:
            return
        self.player.stop()

        if self.speed_factor != 1.0:
            target_len = max(32, int(round(len(self.current_audio) / self.speed_factor)))
            audio_to_write = resample(self.current_audio, target_len)
            np.clip(audio_to_write, -1.0, 1.0, out=audio_to_write)
        else:
            audio_to_write = self.current_audio

        try:
            if os.path.exists(self.temp_file):
                os.remove(self.temp_file)
        except Exception:
            pass

        self.temp_counter += 1
        self.temp_file = os.path.abspath(f"temp_edit_{self.temp_counter}.wav")

        sf.write(self.temp_file, audio_to_write, self.sr)
        self.player.setSource(QUrl.fromLocalFile(self.temp_file))

        self.duration = len(audio_to_write) / float(self.sr)
        self.lbl_total_time.setText(format_time(self.duration))
        self.slider_progress.setRange(0, 1000)
        self.slider_progress.setValue(0)
        self.lbl_info.setText(
            f"{self.current_filename} | {len(self.current_audio)} samples | {self.sr} Hz"
        )

    def _update_waveform(self, beat_times=None):
        if self.current_audio is None:
            return

        if self.speed_factor != 1.0:
            target_len = max(32, int(round(len(self.current_audio) / self.speed_factor)))
            display_audio = resample(self.current_audio, target_len)
        else:
            display_audio = self.current_audio

        time_axis = np.arange(len(display_audio)) / float(self.sr)
        self.canvas.plot_signal(time_axis, display_audio,
                                filename=self.current_filename,
                                beat_times=beat_times)
        self.canvas.update_viewport(0, self.combo_zoom.currentText())
        if self.show_spectrum.isChecked():
            self.spectrum_canvas.plot_spectrum(self.current_audio, self.sr, "Spectrum")

    def _update_all(self):
        self.stop_audio()
        self._update_audio_source()
        self._update_waveform()
        self.lbl_current_time.setText("00:00")
        self.slider_progress.setValue(0)

    def push_undo(self):
        if self.current_audio is not None:
            self.undo_stack.append((self.current_audio.copy(), self.current_filename))
        self._update_undo_label()

    def undo_last(self):
        if not self.undo_stack:
            QMessageBox.information(self, "Undo", "Nothing to undo.")
            return
        audio, filename = self.undo_stack.pop()
        self.current_audio = audio
        self.current_filename = filename
        self._update_all()
        self._update_undo_label()

    def reset_audio(self):
        if self.original_audio is None:
            QMessageBox.warning(self, "Reset", "No original audio.")
            return
        self.push_undo()
        self.current_audio = self.original_audio.copy()
        self.current_filename = self.original_filename
        self.undo_stack.clear()
        self._update_undo_label()
        self._update_all()
        self._reset_ui_controls()
        QMessageBox.information(self, "Reset", "Audio and controls reset.")

    def load_audio(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Audio File", "", "Audio Files (*.wav *.flac *.ogg *.mp3);;All Files (*)"
        )
        if not file_path:
            return
        try:
            data, sr = sf.read(file_path)
            if data.ndim > 1:
                data = data[:, 0]
            self.current_audio = data.copy()
            self.original_audio = data.copy()
            self.sr = sr
            self.current_filename = os.path.basename(file_path)
            self.original_filename = self.current_filename
            self.undo_stack.clear()
            self._update_undo_label()
            self._update_all()
            self.lbl_pitch.setText("Pitch: -- Hz")
            self.lbl_tempo.setText("Tempo: -- BPM")
            self.beat_times = None
            self.remix_audio = None
            self.remix_filename = ""
            self.lbl_remix_file.setText("No remix loaded")
            self.btn_preview_remix.setEnabled(False)
            self.previewing_remix = False
            self._reset_ui_controls()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not read audio:\n{str(e)}")

    def save_audio(self):
        if self.current_audio is None:
            QMessageBox.warning(self, "No Audio", "Load audio first.")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Audio", "", "WAV Files (*.wav);;All Files (*)"
        )
        if not file_path:
            return
        try:
            sf.write(file_path, self.current_audio, self.sr)
            QMessageBox.information(self, "Saved", f"Saved to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def play_audio(self):
        if self.current_audio is not None:
            if self.jukebox_window is not None:
                self.jukebox_window.pause()
            self.player.play()

    def pause_audio(self):
        self.player.pause()

    def stop_audio(self):
        self.player.stop()
        self.slider_progress.setValue(0)
        self.lbl_current_time.setText("00:00")
        self.canvas.hide_playhead()

    def on_position_changed(self, position_ms):
        if self.user_is_scrubbing or self.duration == 0:
            return
        elapsed_sec = position_ms / 1000.0
        self.canvas.update_viewport(elapsed_sec, self.combo_zoom.currentText())
        self.lbl_current_time.setText(format_time(elapsed_sec))
        progress_val = int((elapsed_sec / self.duration) * 1000)
        self.slider_progress.blockSignals(True)
        self.slider_progress.setValue(progress_val)
        self.slider_progress.blockSignals(False)

        if (self.show_spectrum.isChecked() and self.follow_playhead.isChecked()
                and self.current_audio is not None):
            if abs(position_ms - self._last_spectrum_ms) >= self._spectrum_update_interval_ms:
                self._last_spectrum_ms = position_ms
                self.spectrum_canvas.plot_spectrum(
                    self.current_audio, self.sr, "Spectrum (live)",
                    center_time=elapsed_sec
                )

    def on_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.canvas.hide_playhead()
            if self.previewing_remix:
                self.previewing_remix = False
                self._update_audio_source()
                self._update_waveform()
                if self.remix_filename:
                    self.lbl_remix_file.setText(
                        f"Loaded: {self.remix_filename} "
                        f"({len(self.remix_audio)} samples)"
                    )

    def on_slider_pressed(self):
        self.user_is_scrubbing = True

    def on_slider_value_changed(self, value):
        if self.duration > 0 and self.user_is_scrubbing:
            target_time = (value / 1000.0) * self.duration
            self.lbl_current_time.setText(format_time(target_time))
            self.canvas.update_viewport(target_time, self.combo_zoom.currentText())
            if (self.show_spectrum.isChecked() and self.follow_playhead.isChecked()
                    and self.current_audio is not None):
                self.spectrum_canvas.plot_spectrum(
                    self.current_audio, self.sr, "Spectrum (live)",
                    center_time=target_time
                )

    def on_slider_released(self):
        self.user_is_scrubbing = False
        if self.duration > 0:
            target_ms = int((self.slider_progress.value() / 1000.0) * self.duration * 1000)
            self.player.setPosition(target_ms)

    def on_zoom_changed(self, text):
        elapsed_sec = self.player.position() / 1000.0
        self.canvas.update_viewport(elapsed_sec, text)

    def on_speed_changed(self, text):
        new_speed = float(text.replace('x', ''))
        if new_speed == self.speed_factor:
            return
        rel_pos = 0.0
        if self.duration > 0 and self.player.position() > 0:
            rel_pos = self.player.position() / (self.duration * 1000.0)
        self.speed_factor = new_speed
        self._update_audio_source()
        self._update_waveform()
        if rel_pos > 0 and self.duration > 0:
            self.player.setPosition(int(rel_pos * self.duration * 1000))

    def toggle_spectrum(self, state):
        visible = (state != 0)
        self.spectrum_canvas.setVisible(visible)
        self.follow_playhead.setEnabled(visible)
        if visible and self.current_audio is not None:
            if self.follow_playhead.isChecked():
                t = self.player.position() / 1000.0
                self.spectrum_canvas.plot_spectrum(
                    self.current_audio, self.sr, "Spectrum (live)", center_time=t
                )
            else:
                self.spectrum_canvas.plot_spectrum(self.current_audio, self.sr, "Spectrum")
        self.spectrum_canvas.updateGeometry()
        cw = self.centralWidget()
        if cw is not None and cw.layout() is not None:
            cw.layout().activate()

    # ------------------------------------------------------------------
    # Basic Editing
    # ------------------------------------------------------------------
    def apply_reverse(self):
        if self.current_audio is None: return
        self.push_undo()
        self.current_audio = self.current_audio[::-1]
        self.current_filename += " (reversed)"
        self._update_all()

    def apply_scale(self):
        if self.current_audio is None: return
        self.push_undo()
        factor = self.scale_spin.value()
        self.current_audio = self.current_audio * factor
        np.clip(self.current_audio, -1.0, 1.0, out=self.current_audio)
        self.current_filename += f" (scaled {factor})"
        self._update_all()

    def apply_fade_in(self):
        if self.current_audio is None: return
        self.push_undo()
        fade_sec = self.fade_in_spin.value()
        fade_samples = int(fade_sec * self.sr)
        if fade_samples > len(self.current_audio):
            fade_samples = len(self.current_audio)
        if fade_samples > 0:
            fade_curve = np.linspace(0, 1, fade_samples)
            self.current_audio[:fade_samples] *= fade_curve
        self.current_filename += " (fade in)"
        self._update_all()

    def apply_fade_out(self):
        if self.current_audio is None: return
        self.push_undo()
        fade_sec = self.fade_out_spin.value()
        fade_samples = int(fade_sec * self.sr)
        if fade_samples > len(self.current_audio):
            fade_samples = len(self.current_audio)
        if fade_samples > 0:
            fade_curve = np.linspace(1, 0, fade_samples)
            self.current_audio[-fade_samples:] *= fade_curve
        self.current_filename += " (fade out)"
        self._update_all()

    def apply_trim(self):
        if self.current_audio is None: return
        start_sec = self.trim_start.value()
        end_sec = self.trim_end.value()
        if start_sec >= end_sec:
            QMessageBox.warning(self, "Trim Error", "Start must be less than end.")
            return
        sr = self.sr
        start_sample = int(start_sec * sr)
        end_sample = int(end_sec * sr)
        if start_sample < 0: start_sample = 0
        if end_sample > len(self.current_audio): end_sample = len(self.current_audio)
        if start_sample >= end_sample:
            QMessageBox.warning(self, "Trim Error", "Empty range.")
            return
        self.push_undo()
        self.current_audio = self.current_audio[start_sample:end_sample]
        self.current_filename += " (trimmed)"
        self._update_all()

    def apply_join(self):
        if self.current_audio is None:
            QMessageBox.warning(self, "No Audio", "Load audio first.")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Audio to Join", "", "Audio Files (*.wav *.flac *.ogg *.mp3);;All Files (*)"
        )
        if not file_path: return
        try:
            data, sr2 = sf.read(file_path)
            if data.ndim > 1: data = data[:, 0]
            if sr2 != self.sr:
                QMessageBox.warning(self, "Sample Rate Mismatch", "SR must match. Join cancelled.")
                return
            self.push_undo()
            self.current_audio = np.concatenate([self.current_audio, data])
            self.current_filename += " (joined)"
            self._update_all()
        except Exception as e:
            QMessageBox.critical(self, "Join Error", str(e))

    # ------------------------------------------------------------------
    # Advanced Effects
    # ------------------------------------------------------------------
    def apply_noise_reduction(self):
        if self.current_audio is None:
            return

        method = self.nr_combo.currentText()
        self.push_undo()

        if "Time" in method:
            audio = self.current_audio.astype(float)
            sr = self.sr

            frame_len = 2048
            hop = 512
            window = np.hanning(frame_len)

            n_frames = int(np.ceil(
                (len(audio) - frame_len) / hop
            )) + 1
            n_frames = max(1, n_frames)

            padded_len = (n_frames - 1) * hop + frame_len

            padded = np.pad(
                audio,
                (0, max(0, padded_len - len(audio)))
            )

            stft = np.zeros(
                (n_frames, frame_len // 2 + 1),
                dtype=np.complex128
            )

            for i in range(n_frames):
                start = i * hop
                frame = padded[start:start + frame_len]
                stft[i] = np.fft.rfft(frame * window)

            magnitude = np.abs(stft)

            noise_frames = max(1, min(
                n_frames // 10,
                int(1.0 * sr / hop)
            ))

            noise_profile = np.median(
                magnitude[:noise_frames],
                axis=0
            )

            alpha = 1.5
            beta = 0.05

            cleaned_magnitude = (
                magnitude - alpha * noise_profile[None, :]
            )

            cleaned_magnitude = np.maximum(
                cleaned_magnitude,
                beta * noise_profile[None, :]
            )

            phase = np.exp(1j * np.angle(stft))

            cleaned_stft = (
                cleaned_magnitude * phase
            )

            out = np.zeros(padded_len)
            window_sum = np.zeros(padded_len)

            for i in range(n_frames):
                start = i * hop

                frame = np.fft.irfft(
                    cleaned_stft[i],
                    n=frame_len
                )

                frame *= window

                out[start:start + frame_len] += frame
                window_sum[start:start + frame_len] += window ** 2

            valid = window_sum > 1e-10
            out[valid] /= window_sum[valid]

            out = out[:len(audio)]

            out = np.clip(out, -1.0, 1.0)

            self.current_audio = out
            self.current_filename += " (NR spectral)"

        else:
            freq = 50 if "50" in method else 60

            audio = self.current_audio.astype(float)

            fft = np.fft.rfft(audio)
            freqs = np.fft.rfftfreq(
                len(audio),
                d=1 / self.sr
            )

            notch_width = 2.0

            harmonics = range(
                1,
                int((self.sr / 2) // freq) + 1
            )

            for harmonic in harmonics:
                target = harmonic * freq

                indices = np.abs(
                    freqs - target
                ) <= notch_width

                fft[indices] = 0

            filtered = np.fft.irfft(
                fft,
                n=len(audio)
            )

            filtered = np.clip(
                filtered,
                -1.0,
                1.0
            )

            self.current_audio = filtered
            self.current_filename += " (NR freq)"

        self._update_all()

    def apply_eq(self):
        if self.current_audio is None: return
        low_gain = self.eq_low.value() / 10.0
        mid_gain = self.eq_mid.value() / 10.0
        high_gain = self.eq_high.value() / 10.0
        self.push_undo()
        fft = np.fft.rfft(self.current_audio)
        freqs = np.fft.rfftfreq(len(self.current_audio), d=1/self.sr)
        low_mask = freqs < 200
        mid_mask = (freqs >= 200) & (freqs <= 2000)
        high_mask = freqs > 2000
        gain_linear_low = 10**(low_gain)
        gain_linear_mid = 10**(mid_gain)
        gain_linear_high = 10**(high_gain)
        fft[low_mask] *= gain_linear_low
        fft[mid_mask] *= gain_linear_mid
        fft[high_mask] *= gain_linear_high
        filtered = np.fft.irfft(fft)
        np.clip(filtered, -1.0, 1.0, out=filtered)
        self.current_audio = filtered
        self.current_filename += " (EQ)"
        self._update_all()

    def apply_reverb(self):
        if self.current_audio is None:
            QMessageBox.warning(self, "No Audio", "Load audio first.")
            return
        self.push_undo()

        room_size_ms = self.rev_size.value()
        decay = self.rev_decay.value()
        sr = self.sr

        effective_ms = min(room_size_ms, 400)
        kernel_len = max(100, int(effective_ms * sr / 1000.0))
        kernel = np.zeros(kernel_len)

        np.random.seed(0)
        early_fractions = [0.03, 0.07, 0.13, 0.19, 0.27, 0.37, 0.47, 0.59, 0.73, 0.89]
        for i, frac in enumerate(early_fractions):
            d = int(kernel_len * frac)
            if 0 < d < kernel_len:
                kernel[d] = (decay ** (i + 1)) * np.random.uniform(0.6, 1.0)

        tail_start = int(kernel_len * 0.1)
        if tail_start < kernel_len:
            tail_len = kernel_len - tail_start
            decay_rate = 3.0 * (1 - decay) + 0.5
            env = np.exp(-decay_rate * np.linspace(0, 5, tail_len))
            tail = np.random.randn(tail_len) * env * 0.25
            kernel[tail_start:] += tail

        kernel_energy = np.sqrt(np.sum(kernel ** 2))
        if kernel_energy > 0:
            kernel = kernel / kernel_energy * 0.5

        wet = fftconvolve(self.current_audio, kernel, mode='same')
        mixed = 0.6 * self.current_audio + 0.4 * wet
        np.clip(mixed, -1.0, 1.0, out=mixed)

        self.current_audio = mixed
        self.current_filename += " (reverb)"
        self._update_all()

    def apply_echo(self):
        if self.current_audio is None:
            QMessageBox.warning(self, "No Audio", "Load audio first.")
            return
        self.push_undo()

        delay_sec = self.echo_delay.value()
        decay = self.echo_decay.value()
        sr = self.sr
        delay_samples = int(delay_sec * sr)
        if delay_samples < 1:
            delay_samples = 1

        num_repeats = 12
        kernel_len = delay_samples * num_repeats + 1
        kernel = np.zeros(kernel_len)
        kernel[0] = 1.0
        for i in range(1, num_repeats + 1):
            idx = i * delay_samples
            if idx < kernel_len:
                kernel[idx] = decay ** i

        convolved = fftconvolve(self.current_audio, kernel, mode='same')

        max_val = np.max(np.abs(convolved))
        if max_val > 0:
            convolved = convolved / max_val * 0.95

        self.current_audio = convolved
        self.current_filename += f" (echo {delay_sec}s, decay {decay})"
        self._update_all()

    # ------------------------------------------------------------------
    # Remix
    # ------------------------------------------------------------------
    def load_remix_clip(self):
        if self.current_audio is None:
            QMessageBox.warning(self, "No Main Audio", "Load a main track first.")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Remix Clip", "", "Audio Files (*.wav *.flac *.ogg *.mp3);;All Files (*)"
        )
        if not file_path:
            return
        try:
            data, sr2 = sf.read(file_path)
            if data.ndim > 1:
                data = data[:, 0]
            if sr2 != self.sr:
                num_samples = int(len(data) * self.sr / sr2)
                data = resample(data, num_samples)
            self.remix_audio = data
            self.remix_filename = os.path.basename(file_path)
            self.lbl_remix_file.setText(f"Loaded: {self.remix_filename} ({len(data)} samples)")
            self.btn_preview_remix.setEnabled(True)
            QMessageBox.information(self, "Remix Loaded", f"Loaded {self.remix_filename}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load remix:\n{str(e)}")

    def preview_remix_clip(self):
        if self.remix_audio is None:
            QMessageBox.warning(self, "No Remix", "Load a remix clip first.")
            return

        self.player.stop()

        self.temp_counter += 1
        preview_file = os.path.abspath(f"temp_edit_{self.temp_counter}.wav")
        sf.write(preview_file, self.remix_audio, self.sr)

        self.previewing_remix = True

        self.duration = len(self.remix_audio) / float(self.sr)
        self.lbl_total_time.setText(format_time(self.duration))
        self.slider_progress.setValue(0)

        self.player.setSource(QUrl.fromLocalFile(preview_file))
        self.player.play()

        self.lbl_remix_file.setText(
            f"▶ Previewing: {self.remix_filename} ({self.duration:.2f} s)"
        )

    def apply_remix(self):
        if self.current_audio is None:
            QMessageBox.warning(self, "No Main Audio", "Load a main track first.")
            return
        if self.remix_audio is None:
            QMessageBox.warning(self, "No Remix", "Load a remix clip first.")
            return

        start_time = self.remix_start.value()
        use_full = self.remix_use_full.isChecked()
        duration = self.remix_duration.value() if not use_full else len(self.remix_audio) / self.sr
        fade_in = self.remix_fade_in.value()
        fade_out = self.remix_fade_out.value()
        gain = self.remix_gain.value()
        mode = self.remix_mode.currentText()

        sr = self.sr
        start_sample = int(start_time * sr)
        if start_sample < 0:
            start_sample = 0
        if start_sample > len(self.current_audio):
            start_sample = len(self.current_audio)

        # --- Build the remix segment (trim / gain / fades) ---
        if use_full:
            remix_segment = self.remix_audio.copy()
        else:
            dur_samples = int(duration * sr)
            if dur_samples > len(self.remix_audio):
                dur_samples = len(self.remix_audio)
            remix_segment = self.remix_audio[:dur_samples].copy()

        remix_segment = remix_segment * gain

        fade_in_samples = int(fade_in * sr)
        fade_out_samples = int(fade_out * sr)
        if fade_in_samples > len(remix_segment):
            fade_in_samples = len(remix_segment)
        if fade_out_samples > len(remix_segment):
            fade_out_samples = len(remix_segment)
        if fade_in_samples > 0:
            fade_curve_in = np.linspace(0, 1, fade_in_samples)
            remix_segment[:fade_in_samples] *= fade_curve_in
        if fade_out_samples > 0:
            fade_curve_out = np.linspace(1, 0, fade_out_samples)
            remix_segment[-fade_out_samples:] *= fade_curve_out

        self.push_undo()

        left = self.current_audio[:start_sample]

        # --- Mode 1: Insert (splice in) ---
        if mode.startswith("Insert"):
            right = self.current_audio[start_sample:]
            new_audio = np.concatenate([left, remix_segment, right])
            self.current_filename += f" (remix inserted at {start_time:.1f}s)"

        # --- Mode 2: Replace (overwrite) ---
        elif mode.startswith("Replace"):
            # The remix takes the place of len(remix_segment) samples of the main track
            end_sample = start_sample + len(remix_segment)
            if end_sample > len(self.current_audio):
                # Remix extends past the end — clamp
                end_sample = len(self.current_audio)
                remix_segment = remix_segment[:end_sample - start_sample]
            right = self.current_audio[end_sample:]
            new_audio = np.concatenate([left, remix_segment, right])
            self.current_filename += f" (remix replaced at {start_time:.1f}s)"

        # --- Mode 3: Superimpose (mix on top) ---
        else:
            new_audio = self.current_audio.copy()
            remix_end = start_sample + len(remix_segment)
            if remix_end > len(new_audio):
                # Extend the main track so the tail of the remix fits
                extra = np.zeros(remix_end - len(new_audio))
                new_audio = np.concatenate([new_audio, extra])
            new_audio[start_sample:remix_end] += remix_segment
            np.clip(new_audio, -1.0, 1.0, out=new_audio)
            self.current_filename += f" (remix superimposed at {start_time:.1f}s)"

        self.current_audio = new_audio
        self._update_all()
        QMessageBox.information(self, "Remix Applied", f"{mode} at {start_time:.1f}s")

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def estimate_pitch(self):
        if self.current_audio is None:
            QMessageBox.warning(self, "No Audio", "Load audio first.")
            return

        audio = self.current_audio.astype(float)
        sr = self.sr
        frame_len = 2048
        hop = 512
        min_freq = 50.0
        max_freq = 400.0
        min_lag = int(sr / max_freq)
        max_lag = int(sr / min_freq)

        if len(audio) < frame_len:
            QMessageBox.warning(
                self,
                "Too Short",
                "Audio is too short for pitch analysis."
            )
            return

        n_frames = 1 + (len(audio) - frame_len) // hop

        times = np.zeros(n_frames)
        pitches = np.full(n_frames, np.nan)

        for i in range(n_frames):

            start = i * hop
            frame = audio[start:start + frame_len]

            times[i] = start / sr

            # Remove DC component
            frame = frame - np.mean(frame)

            rms = np.sqrt(np.mean(frame ** 2))

            if rms < 1e-3:
                continue

            window = np.hanning(frame_len)
            frame = frame * window

            corr = np.correlate(frame, frame, mode="full")

            # Keep non-negative lags
            corr = corr[frame_len - 1:]

            if corr[0] <= 0:
                continue

            # Normalize autocorrelation
            corr = corr / corr[0]

            # Make sure lag range is valid
            max_lag_use = min(max_lag, len(corr) - 1)

            if min_lag >= max_lag_use:
                continue

            candidates = []

            for lag in range(min_lag + 1, max_lag_use):

                if (corr[lag] > corr[lag - 1] and
                        corr[lag] >= corr[lag + 1]):

                    candidates.append(lag)

            if not candidates:
                continue


            peak_lag = max(
                candidates,
                key=lambda lag: corr[lag]
            )

            peak_value = corr[peak_lag]

            if peak_value < 0.30:
                continue
            if 1 <= peak_lag < len(corr) - 1:

                a = corr[peak_lag - 1]
                b = corr[peak_lag]
                c = corr[peak_lag + 1]

                denominator = a - 2 * b + c

                if abs(denominator) > 1e-12:
                    delta = 0.5 * (a - c) / denominator
                else:
                    delta = 0.0

                refined_lag = peak_lag + delta

            else:
                refined_lag = float(peak_lag)

 
            half_lag = refined_lag / 2.0

            if half_lag >= min_lag:

                h = int(round(half_lag))

                if h > min_lag and h < len(corr) - 1:

                    half_value = corr[h]

                    if half_value >= 0.85 * peak_value:
                        refined_lag = half_lag

            pitch = sr / refined_lag

            # Final frequency sanity check
            if min_freq <= pitch <= max_freq:
                pitches[i] = pitch


        valid_mask = ~np.isnan(pitches)

        if np.sum(valid_mask) >= 5:

            valid_indices = np.where(valid_mask)[0]

            # Fill gaps temporarily for filtering
            filled = pitches.copy()

            for idx in np.where(~valid_mask)[0]:

                nearest = valid_indices[
                    np.argmin(np.abs(valid_indices - idx))
                ]

                filled[idx] = pitches[nearest]

            # Simple median filter implemented manually
            filtered = filled.copy()

            for i in range(2, len(filled) - 2):

                values = filled[i - 2:i + 3]

                filtered[i] = np.median(values)

            # Restore unvoiced frames
            pitches = np.where(
                valid_mask,
                filtered,
                np.nan
            )


        valid = ~np.isnan(pitches)

        if not np.any(valid):

            self.lbl_pitch.setText(
                "Pitch: no clear pitch detected"
            )

            QMessageBox.information(
                self,
                "Pitch",
                "No clearly pitched content was detected.\n"
                "This can happen with silence, noise, percussion, "
                "or polyphonic audio."
            )

            return


        valid_pitches = pitches[valid]

        median_pitch = float(
            np.median(valid_pitches)
        )

        bins = np.round(valid_pitches / 5.0) * 5.0

        values, counts = np.unique(
            bins,
            return_counts=True
        )

        dominant_pitch = float(
            values[np.argmax(counts)]
        )


        self.lbl_pitch.setText(
            f"Pitch: median={median_pitch:.1f} Hz, "
            f"dominant={dominant_pitch:.1f} Hz"
        )


        dlg = PitchPlotDialog(
            times,
            pitches,
            median_pitch,
            dominant_pitch,
            self
        )
        dlg.exec()

    def detect_tempo(self):
        if self.current_audio is None:
            QMessageBox.warning(self, "No Audio", "Load audio first.")
            return
        audio = self.current_audio
        sr = self.sr
        hop = int(0.01 * sr)
        if hop < 1: hop = 1
        energy = []
        for i in range(0, len(audio)-hop, hop):
            block = audio[i:i+hop]
            rms = np.sqrt(np.mean(block**2))
            energy.append(rms)
        energy = np.array(energy)
        energy = energy / (np.max(energy) + 1e-12)
        threshold = 0.3
        peaks = []
        for i in range(1, len(energy)-1):
            if energy[i] > threshold and energy[i] > energy[i-1] and energy[i] > energy[i+1]:
                peaks.append(i)
        if len(peaks) < 2:
            self.lbl_tempo.setText("Tempo: Could not detect")
            return
        intervals = np.diff(peaks)
        median_interval = np.median(intervals)
        if median_interval == 0:
            self.lbl_tempo.setText("Tempo: --")
            return
        interval_sec = median_interval * 0.01
        bpm = 60 / interval_sec
        self.lbl_tempo.setText(f"Tempo: {bpm:.1f} BPM")
        beat_times = [p * 0.01 for p in peaks]
        self.beat_times = beat_times
        time_axis = np.arange(len(audio)) / sr
        self.canvas.plot_signal(time_axis, audio, filename=self.current_filename, beat_times=beat_times)
        self.canvas.update_viewport(0, self.combo_zoom.currentText())
        QMessageBox.information(self, "Tempo", f"Detected {len(peaks)} beats, BPM = {bpm:.1f}")

    def apply_resample(self):
        if self.current_audio is None:
            QMessageBox.warning(self, "No Audio", "Load audio first.")
            return

        text = self.resample_combo.currentText()
        if text == "Custom":
            new_sr = int(self.custom_sr_spin.value())
        else:
            new_sr = int(text)

        if new_sr < 100 or new_sr > 192000:
            QMessageBox.warning(self, "Resample", "Choose a rate between 100 and 192000 Hz.")
            return
        if new_sr == self.sr:
            QMessageBox.information(self, "Resample", "Same sample rate.")
            return

        self.push_undo()

        if self.resample_aliasing.isChecked():
            factor = max(1, self.sr // new_sr)
            resampled = self.current_audio[::factor]
            self.sr = new_sr
            self.current_audio = resampled
            self.current_filename += f" (resampled {new_sr}Hz, aliasing)"
        else:
            num_samples = int(len(self.current_audio) * new_sr / self.sr)
            resampled = resample(self.current_audio, num_samples)
            self.sr = new_sr
            self.current_audio = resampled
            self.current_filename += f" (resampled {new_sr}Hz)"

        self._update_all()
        if self.show_spectrum.isChecked():
            self.spectrum_canvas.plot_spectrum(self.current_audio, self.sr, "Spectrum (after resample)")

    # ------------------------------------------------------------------
    # Infinite Jukebox
    # ------------------------------------------------------------------
    def _editor_track(self):
        if self.current_audio is None:
            return None
        return self.current_audio, self.sr, self.current_filename

    def open_jukebox(self):
        if self.jukebox_window is None:
            self.jukebox_window = JukeboxWindow(editor_track=self._editor_track)
            self.jukebox_window.playbackStarted.connect(self.pause_audio)
        self.jukebox_window.show()
        self.jukebox_window.raise_()
        self.jukebox_window.activateWindow()
        return self.jukebox_window

    def open_jukebox_with_current(self):
        if self.current_audio is None:
            QMessageBox.warning(self, "No Audio", "Load audio first.")
            return
        self.open_jukebox().load_track(*self._editor_track())

    def closeEvent(self, event):
        if self.jukebox_window is not None:
            self.jukebox_window.close()
        super().closeEvent(event)

# ----------------------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    window = AudioWorkstation()
    window.show()
    sys.exit(app.exec())