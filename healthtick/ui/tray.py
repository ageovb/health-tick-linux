"""System tray icon (Gtk.StatusIcon) with a live countdown ring."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

from ..config import AppPhase, format_duration_seconds  # noqa: E402
from ..i18n import L  # noqa: E402
from .theme import theme  # noqa: E402


class TrayIcon:
    SIZE = 34

    def __init__(self, state, callbacks: dict, icon_path: str | None = None) -> None:
        self.state = state
        self.callbacks = callbacks
        self.icon = Gtk.StatusIcon()
        self.icon.set_title(L.app_name)
        self._signature = None
        self._menu = None
        self.icon.connect("activate", self._on_activate)
        self.icon.connect("popup-menu", self._on_popup)
        self.icon.connect("size-changed", lambda *_: self.update(force=True))
        # Set a pixbuf before making the icon visible; some tray applets ignore
        # status icons that are mapped without an image.
        self.update(force=True)
        self.icon.set_visible(True)

    # ------------------------------------------------------------------- icon

    def update(self, *, force: bool = False) -> None:
        state = self.state
        phase = state.phase
        # Redraw at most once per percent to avoid pointless tray churn.
        progress = self._progress()
        signature = (
            phase.value,
            round(progress * 36),
            state.is_in_quiet_hours,
            state.goal_auto_stopped,
            theme.palette.mode,
        )
        if force or signature != self._signature:
            self._signature = signature
            try:
                self.icon.set_from_pixbuf(self._draw(progress))
            except Exception:
                pass
        tooltip = f"{L.app_name} · {state.phase_label} · {state.formatted_time}"
        if state.is_in_quiet_hours:
            tooltip = f"{L.app_name} · {L.quiet_hours_active} · {state.formatted_quiet_time}"
        elif state.goal_auto_stopped:
            tooltip = f"{L.app_name} · {L.daily_goal_reached}"
        tooltip += f"\n{L.today_done} {state.today_done}/{state.config.daily_goal} · {L.streak} {state.current_streak}"
        self.icon.set_tooltip_text(tooltip)

    def _progress(self) -> float:
        state = self.state
        if state.is_in_quiet_hours:
            return min(1.0, state.quiet_remaining_seconds / 3600) if state.quiet_remaining_seconds else 0.0
        if state.phase in (AppPhase.WORKING, AppPhase.PAUSED) and state.remaining_seconds > 0:
            return state.remaining_seconds / max(1, state.config.work_minutes * 60)
        if state.phase is AppPhase.BREAKING:
            return state.remaining_seconds / max(1, state.current_break_total_seconds)
        return 0.0

    def _color(self) -> tuple[float, float, float, float]:
        p = theme.palette
        state = self.state
        if state.is_in_quiet_hours or state.goal_auto_stopped:
            return self._rgba(p.purple)
        color = {
            AppPhase.WORKING: p.green,
            AppPhase.ALERTING: p.red,
            AppPhase.BREAKING: p.orange,
            AppPhase.WAITING: p.blue,
            AppPhase.PAUSED: p.orange,
        }.get(state.phase, p.green)
        return self._rgba(color)

    @staticmethod
    def _rgba(value: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
        # Palettes contain both hex colours and CSS rgba()/alpha() strings.
        from .widgets import _rgba as parse_color

        return parse_color(value, alpha)

    def _draw(self, progress: float) -> GdkPixbuf.Pixbuf:
        size = self.SIZE
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        cr.set_antialias(cairo.ANTIALIAS_BEST)
        p = theme.palette
        color = self._color()
        cx = cy = size / 2
        radius = size / 2 - 4
        cr.set_line_width(3.0)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        # subtle plate so the icon reads on light and dark panels
        cr.set_source_rgba(*self._rgba(p.bg, 0.55))
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(*self._rgba(p.border, 0.9))
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        if progress > 0.002:
            cr.set_source_rgba(*color)
            cr.arc(cx, cy, radius, -math.pi / 2, -math.pi / 2 + 2 * math.pi * progress)
            cr.stroke()
        # Center glyph
        cr.set_source_rgba(*color)
        phase = self.state.phase
        if self.state.is_in_quiet_hours or self.state.goal_auto_stopped:
            # crescent
            cr.arc(cx, cy, 6.2, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(*self._rgba(p.bg, 0.95))
            cr.arc(cx + 2.8, cy - 2.0, 5.6, 0, 2 * math.pi)
            cr.fill()
        elif phase is AppPhase.PAUSED:
            cr.rectangle(cx - 4.5, cy - 5, 3, 10)
            cr.rectangle(cx + 1.5, cy - 5, 3, 10)
            cr.fill()
        elif phase is AppPhase.WAITING:
            cr.set_line_width(2.4)
            cr.arc(cx, cy, 5.2, 0, 2 * math.pi)
            cr.stroke()
            cr.set_line_width(2.6)
            cr.move_to(cx - 2.6, cy)
            cr.line_to(cx - 0.4, cy + 2.6)
            cr.line_to(cx + 3.4, cy - 2.8)
            cr.stroke()
        elif phase is AppPhase.BREAKING:
            # cup
            cr.set_line_width(2.2)
            cr.arc(cx, cy + 1, 5.6, 0, math.pi)
            cr.stroke()
            cr.arc(cx + 5.4, cy - 0.5, 2.4, -math.pi / 2, math.pi / 2)
            cr.stroke()
            cr.set_line_width(1.8)
            cr.move_to(cx - 7, cy + 7)
            cr.line_to(cx + 7, cy + 7)
            cr.stroke()
        else:
            cr.arc(cx, cy, 5.6, 0, 2 * math.pi)
            cr.fill()
            # clock hand
            cr.set_source_rgba(1, 1, 1, 0.85)
            cr.set_line_width(1.8)
            cr.move_to(cx, cy)
            cr.line_to(cx, cy - 3.4)
            cr.move_to(cx, cy)
            cr.line_to(cx + 2.6, cy + 1.2)
            cr.stroke()
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    # ------------------------------------------------------------------- menu

    def _on_activate(self, _icon) -> None:
        print("TRAY activate", flush=True)
        callback = self.callbacks.get("toggle_panel")
        if callback:
            callback()

    def _on_popup(self, _icon, button, activate_time) -> None:
        print("TRAY popup button", button, flush=True)
        menu = self._build_menu()
        menu.show_all()
        menu.popup_at_pointer(None)

    def _build_menu(self) -> Gtk.Menu:
        state = self.state
        menu = Gtk.Menu()

        status = Gtk.MenuItem(label=f"{state.phase_icon}  {state.phase_label}  {state.formatted_time}")
        status.set_sensitive(False)
        menu.append(status)
        stats_item = Gtk.MenuItem(
            label=f"{L.today_done} {state.today_done}/{state.config.daily_goal}   ·   {L.streak} {state.current_streak}"
        )
        stats_item.set_sensitive(False)
        menu.append(stats_item)
        menu.append(Gtk.SeparatorMenuItem())

        open_item = Gtk.MenuItem(label=L.open_panel)
        open_item.connect("activate", lambda *_: self.callbacks.get("show_panel", lambda: None)())
        menu.append(open_item)

        pause_label = L.resume if state.phase is AppPhase.PAUSED else L.pause
        pause_item = Gtk.MenuItem(label=pause_label)
        pause_item.connect("activate", lambda *_: self.callbacks.get("toggle_pause", lambda: None)())
        menu.append(pause_item)

        break_item = Gtk.MenuItem(label=L.manual_break)
        break_item.set_sensitive(state.phase is AppPhase.WORKING)
        break_item.connect("activate", lambda *_: self.callbacks.get("manual_break", lambda: None)())
        menu.append(break_item)

        delay_item = Gtk.MenuItem(
            label=L.delay_break(format_duration_seconds(state.delay_seconds))
        )
        delay_item.set_sensitive(
            state.phase in (AppPhase.WORKING, AppPhase.ALERTING)
        )
        delay_item.connect("activate", lambda *_: self.callbacks.get("delay_break", lambda: None)())
        menu.append(delay_item)

        can_restart = (
            state.phase in (AppPhase.WORKING, AppPhase.ALERTING)
            and not state.is_in_quiet_hours
        )
        for text, kind in (
            (L.reset_short_break, "short"),
            (L.reset_long_break, "long"),
        ):
            item = Gtk.MenuItem(label=text)
            item.set_sensitive(can_restart)
            item.connect(
                "activate",
                lambda _i, k=kind: self.callbacks.get("restart_cycle", lambda _k: None)(k),
            )
            menu.append(item)

        reset_item = Gtk.MenuItem(label=L.reset)
        reset_item.connect("activate", lambda *_: self.callbacks.get("reset", lambda: None)())
        menu.append(reset_item)

        menu.append(Gtk.SeparatorMenuItem())
        for title, key in (
            (L.achievements, "stats"),
            (L.help, "help"),
            (L.settings, "settings"),
        ):
            item = Gtk.MenuItem(label=title)
            item.connect("activate", lambda _i, k=key: self.callbacks.get(k, lambda: None)())
            menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label=L.quit)
        quit_item.connect("activate", lambda *_: self.callbacks.get("quit", lambda: None)())
        menu.append(quit_item)
        return menu
