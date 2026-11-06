# HealthTick for Linux

A Linux desktop port of [HealthTick](https://github.com/lifedever/health-tick-release) —
a small menu-bar app that reminds you to stand up and rest your eyes.

The upstream project is a macOS SwiftUI app. This repository re-implements the same
features with **Python 3 + GTK 3**, keeps the same SQLite schema and configuration
keys, and can import/export the macOS app's data.

## Features

- **Work / break cycle** — configurable work duration, break length and daily goal.
- **Tray icon** — left-click opens the control panel, right-click opens the menu.
- **Break reminders** — optional confirmation, delay (10% of work time), manual breaks, and resetting the cycle to a short or long break.
- **Forced break overlay** — floating card, corners, center or fullscreen.
- **Idle detection** — the break countdown pauses while you are away from the keyboard.
- **Long breaks** — a longer break every N completed cycles.
- **Pre-break notice** — a notification a few minutes before a break.
- **Quiet hours & work hours** — pause outside working time, with an "overtime" override.
- **Work days & holidays** — weekly schedule plus a synced Chinese holiday calendar.
- **Daily goals, streaks and badges** — progress, streak records and achievement wall.
- **Statistics** — 7/30-day charts, heatmap, completion rate and work-time summary.
- **Off-work summary** — today's break count and work time when the workday ends.
- **Screen-lock reset** — start a fresh session after unlocking the screen.
- **Themes & languages** — system/light/dark appearance, Chinese/English UI.
- **Launch at login** — writes a standard XDG autostart entry.
- **Data portability** — JSON export/import compatible with the macOS app.

## Requirements

- Python 3.10+ with GTK 3 bindings (`python3-gi`).
- A desktop with a system tray. Cinnamon, Xfce, MATE and similar panels work;
  GNOME needs an AppIndicator/legacy-tray extension.
- Optional, detected at runtime:
  - `libXss` — idle detection on X11;
  - `notify-send` (`libnotify-bin`) — desktop notifications;
  - `canberra-gtk-play`, `paplay`, `aplay` or `ffmpeg` — reminder sounds.

Ubuntu/Debian:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 libxss1 libnotify-bin \
    libcanberra-gtk3-module pulseaudio-utils alsa-utils
```

## Install

```bash
./install.sh
health-tick
```

The installer copies the app to `~/.local/share/health-tick/app`, creates
`~/.local/bin/health-tick`, and registers the icon and `.desktop` entry.
Set `PREFIX=/usr/local` to install elsewhere.

```bash
./install.sh --uninstall   # keep the database
./install.sh --purge       # remove the database too
```

Run directly from a source checkout:

```bash
/usr/bin/python3 bin/health-tick          # open the panel on start
/usr/bin/python3 bin/health-tick --hidden # start minimized to the tray
/usr/bin/python3 bin/health-tick --version
```

> Use the system Python that owns `gi`. Virtualenvs usually do not include GTK.

## Data and compatibility

Data is stored in a single SQLite file:

```
${XDG_DATA_HOME:-~/.local/share}/health-tick/data.db
```

Override the location with `HEALTHTICK_DATA_DIR=/path/to/dir`. Backups made during
imports go to `backups/` next to the database.

Settings → Data can export a version-1 JSON file. The same file can be imported by
the macOS app, and macOS exports can be imported here. Legacy AES-GCM encrypted
exports are supported when the optional `cryptography` package is installed.

## Global shortcut

While the app is alerting or on a break, `Ctrl+Enter` confirms/ends it. This is
implemented with an X11 key grab and therefore works on X11 sessions; on Wayland
the shortcut may be ignored.

## Tests

```bash
PYTHONPATH=. /usr/bin/python3 -m unittest discover -s tests -v
```

## Project layout

```
bin/health-tick          launcher
healthtick/app.py        GTK application wiring, tray/overlay/window coordination
healthtick/state.py      timer & phase state machine (port of AppState.swift)
healthtick/config.py     configuration, quiet hours, workday/holiday rules
healthtick/db.py         SQLite persistence, stats queries, import/export
healthtick/idle.py       XScreenSaver / Mutter idle detection
healthtick/sound.py      synthesized notification sounds
healthtick/ui/           all GTK windows and custom-drawn widgets
tests/                   stdlib unittest suite
```

## License

GPL-3.0, following the upstream project. See [LICENSE](LICENSE).
