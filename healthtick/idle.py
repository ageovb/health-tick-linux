"""Cross-desktop idle-time detection.

On X11 we ask the XScreenSaver extension, which is what the desktop itself uses
for its screensaver/lock timer.  Wayland sessions generally do not expose idle
time to X clients, so a few fallbacks are attempted before giving up:

* ``org.gnome.Mutter.IdleMonitor`` D-Bus (GNOME / Cinnamon on Wayland)
* ``org.freedesktop.ScreenSaver`` is only a lock state, not idle time

When nothing is available :meth:`IdleMonitor.idle_seconds` returns a very large
value, which callers interpret as "never idle".
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import time

_NEVER_IDLE = 10 ** 9


class _XScreenSaverInfo(ctypes.Structure):
    _fields_ = [
        ("window", ctypes.c_ulong),
        ("state", ctypes.c_int),
        ("kind", ctypes.c_int),
        ("til_or_since", ctypes.c_ulong),
        ("idle", ctypes.c_ulong),
        ("eventMask", ctypes.c_ulong),
    ]


class IdleMonitor:
    def __init__(self) -> None:
        self._display = None
        self._xss = None
        self._root = None
        self._info = None
        self._available = False
        self._last_error = ""
        self._setup_x11()

    # ------------------------------------------------------------------ setup

    def _setup_x11(self) -> None:
        if not os.environ.get("DISPLAY"):
            self._last_error = "no DISPLAY"
            return
        try:
            x11_name = ctypes.util.find_library("X11") or "libX11.so.6"
            xss_name = ctypes.util.find_library("Xss") or "libXss.so.1"
            x11 = ctypes.CDLL(x11_name)
            xss = ctypes.CDLL(xss_name)

            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XScreenSaverInfo)
            xss.XScreenSaverQueryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(_XScreenSaverInfo),
            ]
            xss.XScreenSaverQueryInfo.restype = ctypes.c_int

            display = x11.XOpenDisplay(None)
            if not display:
                self._last_error = "XOpenDisplay failed"
                return
            self._x11 = x11
            self._xss = xss
            self._display = display
            self._root = x11.XDefaultRootWindow(display)
            self._info = xss.XScreenSaverAllocInfo()
            self._available = bool(self._info)
            if not self._available:
                self._last_error = "XScreenSaverAllocInfo failed"
        except OSError as exc:  # libXss not installed, etc.
            self._last_error = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            self._last_error = str(exc)

    # ------------------------------------------------------------------- query

    def idle_seconds(self) -> float:
        """Seconds since the last user input, or a huge value if unknown."""
        if self._available:
            try:
                if self._xss.XScreenSaverQueryInfo(
                    self._display, self._root, self._info
                ):
                    return self._info.contents.idle / 1000.0
            except Exception:  # pragma: no cover - X server gone
                self._available = False

        dbus_idle = self._mutter_idle_seconds()
        if dbus_idle is not None:
            return dbus_idle
        return float(_NEVER_IDLE)

    def _mutter_idle_seconds(self) -> float | None:
        """Best-effort Wayland fallback via Mutter's idle monitor."""
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                "org.gnome.Mutter.IdleMonitor",
                "/org/gnome/Mutter/IdleMonitor/Core",
                "org.gnome.Mutter.IdleMonitor",
                None,
            )
            value = proxy.call_sync(
                "GetIdletime",
                None,
                Gio.DBusCallFlags.NONE,
                500,
                None,
            )
            (millis,) = value.unpack()
            return float(millis) / 1000.0
        except Exception:
            return None

    @property
    def available(self) -> bool:
        return self._available or self._mutter_idle_seconds() is not None

    @property
    def error(self) -> str:
        return self._last_error

    def close(self) -> None:
        # XCloseDisplay would be nice, but the display is shared with GTK in
        # some sessions; leaking one connection at process exit is harmless.
        self._display = None
        self._available = False


if __name__ == "__main__":  # tiny diagnostic helper
    monitor = IdleMonitor()
    for _ in range(5):
        print(f"idle={monitor.idle_seconds():.1f}s available={monitor.available}")
        time.sleep(1)
