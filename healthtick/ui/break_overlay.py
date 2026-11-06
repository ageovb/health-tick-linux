"""Floating, corner and fullscreen break windows."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from ..config import AppPhase, BreakDisplayTarget, BreakPosition  # noqa: E402
from .break_card import BreakCard  # noqa: E402
from .widgets import setup_rgba, spacer, vbox  # noqa: E402


class _BreakWindow(Gtk.Window):
    def __init__(self, state, *, fullscreen: bool = False) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.state = state
        self.fullscreen_mode = fullscreen
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(True)
        # A DIALOG hint keeps floating cards out of the taskbar, but Mutter
        # refuses to make dialogs truly fullscreen. Fullscreen shields use a
        # normal window instead so they cover panels/docks as well.
        if not fullscreen:
            self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_wmclass("health-tick-break", "HealthTick")
        setup_rgba(self)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if fullscreen:
            outer.get_style_context().add_class("fullscreen-bg")
            self.add(outer)
            self.card = BreakCard(state, fullscreen=True)
            self.card.set_halign(Gtk.Align.CENTER)
            self.card.set_valign(Gtk.Align.CENTER)
            outer.pack_start(self.card, True, True, 0)
        else:
            self.set_default_size(312, -1)
            outer.set_margin_start(8)
            outer.set_margin_end(8)
            outer.set_margin_top(8)
            outer.set_margin_bottom(8)
            panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            panel.get_style_context().add_class("popup-window")
            self.card = BreakCard(state)
            self.card.set_margin_start(14)
            self.card.set_margin_end(14)
            self.card.set_margin_top(8)
            self.card.set_margin_bottom(8)
            panel.pack_start(self.card, False, False, 0)
            outer.pack_start(panel, True, True, 0)
            self.add(outer)
        self.connect("delete-event", lambda *_: True)

    def refresh(self, force: bool = False) -> None:
        self.card.refresh(force=force)


class OverlayManager:
    """Owns the break windows and coordinates with the tray panel."""

    def __init__(self, state, menu) -> None:
        self.state = state
        self.menu = menu
        self._windows: list[_BreakWindow] = []
        self._signature: tuple = ()
        self._summary = False

    # ------------------------------------------------------------------ public

    def show_alert(self, icon=None) -> None:
        if self._uses_menu():
            self.hide_windows()
            self.menu.show_panel(icon, auto=True)
            self.menu.refresh(force=True)
        else:
            self.menu.hide_auto()
            self._ensure_windows(fullscreen=self._fullscreen_for_break())

    def show_break(self, icon=None) -> None:
        if self._uses_menu():
            self.hide_windows()
            self.menu.show_panel(icon, auto=True)
            self.menu.refresh(force=True)
        else:
            self.menu.hide_auto()
            self._ensure_windows(fullscreen=self._fullscreen_for_break())

    def show_waiting(self, icon=None) -> None:
        if self._uses_menu():
            self.hide_windows()
            self.menu.show_panel(icon, auto=True)
            self.menu.refresh(force=True)
        else:
            self.menu.hide_auto()
            self._ensure_windows(fullscreen=self._fullscreen_for_break())

    def show_summary(self, icon=None) -> None:
        if self._uses_menu():
            self.hide_windows()
            self.menu.show_panel(icon, auto=True)
            self.menu.refresh(force=True)
        else:
            self.menu.hide_auto()
            self._ensure_windows(
                fullscreen=self.state.config.break_position is BreakPosition.FULLSCREEN
            )

    def refresh(self) -> None:
        for window in self._windows:
            try:
                window.refresh()
            except Exception:
                pass

    def hide(self) -> None:
        self.hide_windows()
        self.menu.hide_auto()

    def hide_windows(self) -> None:
        for window in self._windows:
            window.hide()

    def destroy(self) -> None:
        for window in self._windows:
            try:
                window.destroy()
            except Exception:
                pass
        self._windows.clear()

    # ----------------------------------------------------------------- helpers

    def _uses_menu(self) -> bool:
        return self.state.config.break_position is BreakPosition.MENU_WINDOW

    def _fullscreen_for_break(self) -> bool:
        return self.state.config.break_position is BreakPosition.FULLSCREEN

    def _monitors(self):
        display = Gdk.Display.get_default()
        if display is None:
            return []
        target = self.state.config.break_display_target
        if target is BreakDisplayTarget.ALL:
            result = []
            for index in range(display.get_n_monitors()):
                result.append(display.get_monitor(index))
            return result
        if target is BreakDisplayTarget.SPECIFIC:
            raw = self.state.config.break_display_specific_uuid
            try:
                index = int(raw) if raw else 0
            except (TypeError, ValueError):
                index = 0
            monitor = display.get_monitor(index)
            if monitor is not None:
                return [monitor]
        # Active screen: monitor under the mouse pointer.
        pointer = display.get_default_seat().get_pointer()
        _screen, x, y = pointer.get_position()
        monitor = display.get_monitor_at_point(x, y)
        return [monitor or display.get_primary_monitor()]

    def _monitor_index(self, monitor) -> int:
        display = Gdk.Display.get_default()
        if display is not None:
            for index in range(display.get_n_monitors()):
                if display.get_monitor(index) is monitor:
                    return index
                geo = display.get_monitor(index).get_geometry()
                target = monitor.get_geometry()
                if (geo.x, geo.y, geo.width, geo.height) == (
                    target.x,
                    target.y,
                    target.width,
                    target.height,
                ):
                    return index
        return 0

    def _signature_for(self, fullscreen: bool) -> tuple:
        monitors = []
        for monitor in self._monitors():
            geo = monitor.get_geometry()
            monitors.append((geo.x, geo.y, geo.width, geo.height))
        mode = "summary" if self.state.off_work_summary is not None else self.state.phase.value
        return (
            fullscreen,
            mode,
            self.state.config.break_position,
            self.state.config.break_display_target,
            self.state.config.break_display_specific_uuid,
            tuple(monitors),
        )

    def _ensure_windows(self, *, fullscreen: bool) -> None:
        signature = self._signature_for(fullscreen)
        if signature != self._signature or not self._windows:
            self.destroy()
            self._signature = signature
            for monitor in self._monitors():
                if monitor is None:
                    continue
                window = _BreakWindow(self.state, fullscreen=fullscreen)
                window.refresh(force=True)
                if fullscreen:
                    window.show_all()
                    try:
                        window.fullscreen_on_monitor(
                            window.get_screen(), self._monitor_index(monitor)
                        )
                    except Exception:
                        geo = monitor.get_geometry()
                        window.move(geo.x, geo.y)
                        window.resize(geo.width, geo.height)
                else:
                    window.show_all()
                    self._place(window, monitor)
                window.show()
                print("OVERLAY window", window.get_size(), window.get_position(), file=sys.stderr, flush=True)
                self._windows.append(window)
            self._raise()
        else:
            for window in self._windows:
                window.refresh(force=True)
                window.show()
                window.present()

    def _place(self, window: _BreakWindow, monitor) -> None:
        geo = monitor.get_geometry()
        _minimum, natural = window.get_preferred_size()
        width = natural.width or 312
        height = natural.height or 420
        position = self.state.config.break_position
        if position is BreakPosition.TOP_LEFT:
            x = geo.x + 18
            y = geo.y + 42
        elif position is BreakPosition.CENTER:
            x = geo.x + (geo.width - width) // 2
            y = geo.y + (geo.height - height) // 2
        else:  # top right (and any unexpected value)
            x = geo.x + geo.width - width - 18
            y = geo.y + 42
        x = max(geo.x, min(x, geo.x + geo.width - width))
        y = max(geo.y, min(y, geo.y + geo.height - height))
        window.move(int(x), int(y))
        window.set_keep_above(True)

    def _raise(self) -> None:
        for window in self._windows:
            try:
                window.present()
            except Exception:
                pass
