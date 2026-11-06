"""Notification sounds for HealthTick Linux.

macOS ships a set of named system sounds (Glass, Tink, …).  Linux desktops do
not, so equivalent short chimes are synthesized once into the user's cache
directory as mono WAV files and played through whichever command-line player is
available (PulseAudio, canberra, ALSA or ffmpeg).
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Iterable

SAMPLE_RATE = 44100


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    path = Path(base) / "health-tick" / "sounds"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Each name maps to a sequence of (frequency, duration_seconds) notes.  A
# frequency of 0 is a short rest.  The shapes loosely follow the character of
# the macOS originals while staying gentle and short.
_PATTERNS: dict[str, list[tuple[float, float]]] = {
    "Glass": [(1318.5, 0.16), (1760.0, 0.28)],
    "Tink": [(2093.0, 0.09), (2637.0, 0.16)],
    "Blow": [(392.0, 0.18), (329.6, 0.30)],
    "Bottle": [(880.0, 0.10), (1174.7, 0.10), (1568.0, 0.22)],
    "Frog": [(220.0, 0.09), (0, 0.04), (247.0, 0.10), (0, 0.04), (196.0, 0.16)],
    "Funk": [(523.3, 0.10), (659.3, 0.10), (784.0, 0.10), (1046.5, 0.20)],
    "Hero": [(523.3, 0.12), (659.3, 0.12), (784.0, 0.12), (1046.5, 0.28)],
    "Morse": [(880.0, 0.07), (0, 0.05), (880.0, 0.07), (0, 0.05), (880.0, 0.18)],
    "Ping": [(1760.0, 0.22)],
    "Pop": [(740.0, 0.06), (988.0, 0.10)],
    "Purr": [(196.0, 0.40)],
    "Sosumi": [(1046.5, 0.09), (880.0, 0.09), (698.5, 0.20)],
    "Submarine": [(261.6, 0.22), (329.6, 0.30)],
}


def _envelope(i: int, total: int) -> float:
    """Soft attack and exponential-ish decay, so nothing clicks."""
    attack = max(1, int(total * 0.02))
    release = max(1, int(total * 0.35))
    if i < attack:
        return i / attack
    if i > total - release:
        return max(0.0, (total - i) / release)
    return 1.0


def _render(name: str) -> bytes:
    pattern = _PATTERNS.get(name) or _PATTERNS["Glass"]
    frames = bytearray()
    for frequency, duration in pattern:
        count = max(1, int(SAMPLE_RATE * duration))
        if frequency <= 0:
            frames.extend(b"\x00\x00" * count)
            continue
        for i in range(count):
            # Fundamental + a quiet octave gives the chime some body.
            t = i / SAMPLE_RATE
            value = math.sin(2 * math.pi * frequency * t)
            value += 0.25 * math.sin(2 * math.pi * frequency * 2 * t)
            value *= _envelope(i, count) * 0.30
            frames.extend(struct.pack("<h", int(max(-1.0, min(1.0, value)) * 32767)))
    return bytes(frames)


class SoundPlayer:
    def __init__(self) -> None:
        self._paths: dict[str, Path] = {}
        self._player = self._find_player()
        self._cache = cache_dir()

    @staticmethod
    def _find_player() -> list[str] | None:
        for command in (
            ["paplay"],
            ["canberra-gtk-play", "--file"],
            ["aplay", "-q"],
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
        ):
            if shutil.which(command[0]):
                return command
        return None

    @property
    def available(self) -> bool:
        return self._player is not None

    def path_for(self, name: str) -> Path | None:
        if name not in _PATTERNS:
            name = "Glass"
        target = self._cache / f"{name.lower()}.wav"
        if name not in self._paths:
            if not target.exists() or target.stat().st_size < 100:
                self._write_wav(target, _render(name))
            self._paths[name] = target
        return self._paths.get(name)

    @staticmethod
    def _write_wav(target: Path, frames: bytes) -> None:
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=".sound-", suffix=".wav", dir=target.parent)
        os.close(tmp_fd)
        try:
            with wave.open(tmp_name, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(SAMPLE_RATE)
                handle.writeframes(frames)
            os.replace(tmp_name, target)
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    def play(self, name: str = "Glass") -> bool:
        if self._player is None:
            return False
        path = self.path_for(name)
        if path is None:
            return False
        try:
            subprocess.Popen(
                [*self._player, str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError:
            return False

    def pregenerate(self, names: Iterable[str] | None = None) -> None:
        for name in names or _PATTERNS:
            self.path_for(name)


player = SoundPlayer()
