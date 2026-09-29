# Audio Editor

A desktop audio workstation built with Python and PyQt6. Load a track, edit it,
look at it, hide a message in it, or play a rhythm game with it.

Everything runs locally. Nothing is uploaded anywhere.

## Features

The main window is a set of tabs.

**Basic** — Play, pause and stop with a seek bar and a speed control. Scale the
volume, fade in, fade out, reverse, trim to a time range, or join another file
onto the end. Undo steps back through your edits and Reset returns to the file
you loaded.

**Effects** — Noise reduction by spectral gating, a three band equalizer
(low, mid, high, ±24 dB), reverb, and echo.

**Remix** — Load a second clip and splice it in, overwrite a section with it, or
mix it on top. You set the position, the length and the fades, and you can
preview before applying.

**Analysis** — Estimate the pitch, detect the tempo in BPM, and resample to a
different rate. The waveform view can show a live spectrum and mark detected
beats.

**Jukebox** — An infinite jukebox. It finds beats that sound alike and jumps
between them, so a song plays forever and never repeats the same way twice. The
song is drawn as a circle of beats with arcs for the jumps, and you watch the
playhead move.

**Beat Saber** — A browser rhythm game that uses your loaded track. Beats are
detected in Python, written into a beatmap, and the game opens in your browser.
You slice the blocks with Nintendo Joy-Cons, or with the mouse in test mode.

**Watermark** — Hide a text message inside the audio, encrypted with AES-GCM
under a password. The message is spread across a mid frequency band so it
survives normal listening. You need the same password to read it back.

## Install

You need Python 3.9 or newer.

```bash
pip install numpy scipy soundfile matplotlib PyQt6 cryptography
```

## Run

```bash
python audio_editor.py
```

The jukebox also runs on its own:

```bash
python jukebox_gui.py path/to/song.wav
```

## Beat Saber setup

The game is a web app, so it has to be built once before the Beat Saber tab
works.

```bash
cd beatsaber
npm install
npx vite build
```

Use Chrome or Edge. The Joy-Cons are read over WebHID, which Firefox and Safari
do not support. Press Connect Joy-Con once per controller. If the usual port is
busy the game opens on another one, and the browser treats that as a new site,
so you have to connect the controllers again.

## Tests

```bash
python -m pytest test_jukebox.py -q
```

## Files

| File | What it does |
| --- | --- |
| `audio_editor.py` | The main window and all the editing tabs |
| `encryption.py` | Watermark embed and reveal |
| `jukebox_engine.py` | Beat analysis and route planning, no GUI |
| `jukebox_gui.py` | The jukebox window |
| `beatsaber_launcher.py` | Serves the game and opens the browser |
| `beatsaber/` | The game itself |

Supported formats are whatever `soundfile` reads: WAV, FLAC, OGG and MP3.
