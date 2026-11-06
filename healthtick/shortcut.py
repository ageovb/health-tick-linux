"""Global Ctrl+Enter quick-confirm hotkey (X11 only).

The macOS app uses a system-wide hotkey while a reminder is on screen so the
user can confirm without the reminder stealing keyboard focus.  On Linux we
grab Ctrl+Enter on the root window only during alerting/waiting phases and
release it otherwise, so the shortcut stays available to other apps the rest of
the time.
"""

from __future__ import annotations

import threading
import time

try:
    from gi.repository import GLib
except Exception:  # pragma: no cover
    GLib = None

try:
    from Xlib import X, XK, display as xdisplay
    from Xlib.error import BadAccess, XError
except Exception:  # pragma: no cover - Wayland or python-xlib missing
    X = None
    XK = None
    xdisplay = None
    BadAccess = Exception
    XError = Exception


class ShortcutWatcher:
    def __init__(self, state, action=None) -> None:
        self.state = state
        self.action = action
        self._active = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._available = X is not None and xdisplay is not None

    @property
    def available(self) -> bool:
        return self._available

    def update(self) -> None:
        from .config import AppPhase

        active = (
            self._available
            and self.state.config.shortcut_enabled
            and self.state.phase in (AppPhase.ALERTING, AppPhase.WAITING)
        )
        with self._lock:
            self._active = active
        if active and self._thread is None:
            self._start()

    def _start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _grab(self, root, keycode, display) -> None:
        variants = [
            X.ControlMask,
            X.ControlMask | X.Mod2Mask,  # NumLock
            X.ControlMask | X.LockMask,  # CapsLock
            X.ControlMask | X.Mod2Mask | X.LockMask,
        ]
        for mask in variants:
            try:
                root.grab_key(
                    keycode,
                    mask,
                    True,
                    X.GrabModeAsync,
                    X.GrabModeAsync,
                    X.CurrentTime,
                )
            except (BadAccess, XError):
                pass
        display.sync()

    def _ungrab(self, root, keycode, display) -> None:
        for mask in (
            X.ControlMask,
            X.ControlMask | X.Mod2Mask,
            X.ControlMask | X.LockMask,
            X.ControlMask | X.Mod2Mask | X.LockMask,
        ):
            try:
                root.ungrab_key(keycode, mask, X.CurrentTime)
            except (BadAccess, XError):
                pass
        display.sync()

    def _loop(self) -> None:
        display = None
        grabbed = False
        keycode = 0
        root = None
        try:
            display = xdisplay.Display()
            root = display.screen().root
            keysym = XK.string_to_keysym("Return")
            keycode = display.keysym_to_keycode(keysym)
            if not keycode:
                self._available = False
                return
            while not self._stop.is_set():
                with self._lock:
                    active = self._active
                if active and not grabbed:
                    self._grab(root, keycode, display)
                    grabbed = True
                elif not active and grabbed:
                    self._ungrab(root, keycode, display)
                    grabbed = False
                if grabbed:
                    while display.pending_events():
                        event = display.next_event()
                        if event.type == X.KeyPress:
                            if GLib is not None:
                                GLib.idle_add(self._fire)
                            else:
                                self._fire()
                time.sleep(0.05)
        except Exception:
            self._available = False
        finally:
            if display is not None and root is not None and keycode:
                try:
                    self._ungrab(root, keycode, display)
                except Exception:
                    pass
            if display is not None:
                try:
                    display.close()
                except Exception:
                    pass
            self._thread = None

    def _fire(self) -> bool:
        from .config import AppPhase

        if self.state.phase is AppPhase.ALERTING:
            self.state.confirm_break()
        elif self.state.phase is AppPhase.WAITING:
            self.state.confirm_return()
        if self.action:
            self.action()
        return False

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._active = False
