"""Reusable GTK/Cairo widgets used across the HealthTick windows."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Callable, Iterable, Sequence

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gtk, Pango, PangoCairo  # noqa: E402

from .theme import theme  # noqa: E402


# ---------------------------------------------------------------------------
# Small helpers


def label(text: str = "", css: str = "", *, wrap: bool = False, xalign: float = 0.0) -> Gtk.Label:
    widget = Gtk.Label(label=text)
    widget.set_xalign(xalign)
    if wrap:
        widget.set_line_wrap(True)
        widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if css:
        for name in css.split():
            widget.get_style_context().add_class(name)
    return widget


def hbox(spacing: int = 8, *children, margin: int = 0) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
    if margin:
        box.set_margin_start(margin)
        box.set_margin_end(margin)
        box.set_margin_top(margin)
        box.set_margin_bottom(margin)
    for child in children:
        if child is not None:
            box.pack_start(child, False, False, 0)
    return box


def vbox(spacing: int = 8, *children, margin: int = 0) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    if margin:
        box.set_margin_start(margin)
        box.set_margin_end(margin)
        box.set_margin_top(margin)
        box.set_margin_bottom(margin)
    for child in children:
        if child is not None:
            box.pack_start(child, False, False, 0)
    return box


def separator(*, vertical: bool = False) -> Gtk.Separator:
    return Gtk.Separator(
        orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL
    )


def spacer(width: int = 0, height: int = 0) -> Gtk.Widget:
    widget = Gtk.Box()
    widget.set_size_request(width, height)
    return widget


def _rgba(value: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    value = value.strip()
    if value.startswith("rgba"):
        body = value[value.index("(") + 1 : value.rindex(")")]
        parts = [part.strip() for part in body.split(",")]
        return (
            float(parts[0]) / 255,
            float(parts[1]) / 255,
            float(parts[2]) / 255,
            float(parts[3]) if len(parts) > 3 else alpha,
        )
    if value.startswith("alpha("):
        body = value[value.index("(") + 1 : value.rindex(")")]
        color, _alpha = body.split(",")
        rgb = _rgba(color, alpha)
        return (rgb[0], rgb[1], rgb[2], alpha)
    if value.startswith("#"):
        value = value[1:]
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        return (
            int(value[0:2], 16) / 255,
            int(value[2:4], 16) / 255,
            int(value[4:6], 16) / 255,
            alpha,
        )
    return (0.5, 0.5, 0.5, alpha)


def phase_color(state) -> str:
    from ..config import AppPhase

    p = theme.palette
    if state.is_in_quiet_hours or state.goal_auto_stopped:
        return p.orange
    return {
        AppPhase.WORKING: p.green,
        AppPhase.ALERTING: p.red,
        AppPhase.BREAKING: p.orange,
        AppPhase.WAITING: p.blue,
        AppPhase.PAUSED: p.orange,
    }.get(state.phase, p.green)


def draw_text(
    cr,
    x: float,
    y: float,
    text: str,
    *,
    size: float = 12,
    color: str = "#000000",
    weight: str = "normal",
    align: str = "left",
    max_width: float | None = None,
) -> None:
    layout = PangoCairo.create_layout(cr)
    description = Pango.FontDescription(f"Sans {size}")
    if weight == "bold":
        description.set_weight(Pango.Weight.BOLD)
    layout.set_font_description(description)
    layout.set_text(str(text), -1)
    width, height = layout.get_pixel_size()
    if align == "center":
        x -= width / 2
    elif align == "right":
        x -= width
    cr.set_source_rgba(*_rgba(color))
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


def rounded_rect(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    radius = min(radius, width / 2, height / 2)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


# ---------------------------------------------------------------------------
# Timer ring


class TimerRing(Gtk.DrawingArea):
    def __init__(self, size: int = 120, line_width: float = 4.0) -> None:
        super().__init__()
        self.size = size
        self.line_width = line_width
        self.progress = 0.0
        self.color = theme.palette.green
        self.set_size_request(size, size)
        self.connect("draw", self._draw)

    def set_progress(self, progress: float, color: str | None = None) -> None:
        progress = max(0.0, min(1.0, progress))
        color = color or self.color
        if abs(progress - self.progress) < 0.0005 and color == self.color:
            return
        self.progress = progress
        self.color = color
        self.queue_draw()

    def _draw(self, widget, cr) -> bool:
        p = theme.palette
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        radius = min(width, height) / 2 - self.line_width - 2
        cx, cy = width / 2, height / 2
        cr.set_line_width(self.line_width)
        cr.set_line_cap(1)  # round
        cr.set_source_rgba(*_rgba(p.border, 1.0))
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        if self.progress > 0.0001:
            cr.set_source_rgba(*_rgba(self.color))
            cr.arc(cx, cy, radius, -math.pi / 2, -math.pi / 2 + 2 * math.pi * self.progress)
            cr.stroke()
        return False


class TimerCircle(Gtk.Overlay):
    """Ring plus centered content (time, label or a button)."""

    def __init__(self, size: int = 120, line_width: float = 4.0) -> None:
        super().__init__()
        self.size = size
        self.ring = TimerRing(size, line_width)
        self.add(self.ring)
        self.center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.center.set_halign(Gtk.Align.CENTER)
        self.center.set_valign(Gtk.Align.CENTER)
        self.add_overlay(self.center)
        self.set_overlay_pass_through(self.center, True)

    def set_content(self, *widgets: Gtk.Widget, spacing: int = 3) -> None:
        for child in list(self.center.get_children()):
            self.center.remove(child)
        self.center.set_spacing(spacing)
        for widget in widgets:
            if widget is not None:
                self.center.pack_start(widget, False, False, 0)
        self.center.show_all()
        self.set_overlay_pass_through(self.center, False)

    @property
    def content_radius(self) -> float:
        """Radius available to centred content, leaving a margin inside the stroke."""
        return max(8.0, self.size / 2 - self.ring.line_width - 6)


def ring_markup(text: str, pt: float, weight: str = "300") -> str:
    return f"<span size='{int(pt * 1000)}' weight='{weight}'>{_escape(text)}</span>"


def fit_ring_pt(
    label: Gtk.Label,
    text: str,
    radius: float,
    *,
    max_pt: float = 28.0,
    weight: str = "300",
) -> float:
    """Largest point size at which ``text`` fits a circle of ``radius``.

    Text is centred, so its box must satisfy ``hypot(w/2, h/2) <= radius``.
    Pass the widest text the ring will show, then reuse the size for updates.
    """
    pt = max_pt
    while pt > 8:
        label.set_markup(ring_markup(text, pt, weight))
        width, height = label.get_layout().get_pixel_size()
        if math.hypot(width / 2, height / 2) <= radius:
            break
        pt -= 1
    return pt


# ---------------------------------------------------------------------------
# Menu week pixels


class WeekPixels(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.set_halign(Gtk.Align.CENTER)

    def update(self, data: Sequence[tuple[str, int]], goal: int) -> None:
        for child in list(self.get_children()):
            self.remove(child)
        p = theme.palette
        for day, count in data:
            ratio = count / max(1, goal)
            if count == 0:
                color = _rgba(p.border)
                text_color = p.sub
            elif ratio >= 1:
                color = _rgba(p.green)
                text_color = "#ffffff"
            elif ratio >= 0.5:
                color = _rgba(p.green, 0.62)
                text_color = "#ffffff"
            else:
                color = _rgba(p.green, 0.32)
                text_color = "#ffffff" if ratio > 0.2 else p.text
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            square = Gtk.Label(label=str(count))
            square.set_size_request(23, 23)
            square.get_style_context().add_class("tiny")
            square.get_style_context().add_class("bold")
            square.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(*_rgba(text_color)))
            square.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(*color))
            try:
                day_date = datetime.strptime(day, "%Y-%m-%d")
                day_text = str(day_date.day)
            except ValueError:
                day_text = ""
            day_label = label(day_text, "faint tiny", xalign=0.5)
            cell.pack_start(square, False, False, 0)
            cell.pack_start(day_label, False, False, 0)
            self.pack_start(cell, False, False, 0)
        self.show_all()


# ---------------------------------------------------------------------------
# Charts


class BarChart(Gtk.DrawingArea):
    def __init__(self, height: int = 130, color: str | None = None) -> None:
        super().__init__()
        self.data: list[tuple[str, int]] = []
        self.goal = 0
        self.color = color
        self.show_values = True
        self.set_size_request(-1, height)
        self.connect("draw", self._draw)

    def update(
        self,
        data: Iterable[tuple[str, int]],
        *,
        goal: int = 0,
        color: str | None = None,
        show_values: bool = True,
    ) -> None:
        self.data = list(data)
        self.goal = goal
        if color:
            self.color = color
        self.show_values = show_values
        self.queue_draw()

    def _draw(self, widget, cr) -> bool:
        p = theme.palette
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        if not self.data:
            draw_text(cr, width / 2, height / 2 - 6, "—", size=14, color=p.faint, align="center")
            return False
        color = self.color or p.green
        bottom = height - 22
        top = 16 if self.show_values else 6
        left = 8
        right = width - 8
        count = max(1, len(self.data))
        slot = (right - left) / count
        bar_width = min(30, slot * 0.58)
        max_value = max([value for _day, value in self.data] + [self.goal if self.goal else 0, 1])
        # faint baseline
        cr.set_source_rgba(*_rgba(p.border))
        cr.rectangle(left, bottom, right - left, 1)
        cr.fill()
        if self.goal:
            goal_y = bottom - (self.goal / max_value) * (bottom - top)
            cr.set_source_rgba(*_rgba(p.orange, 0.45))
            cr.set_line_width(1)
            cr.set_dash([3, 3])
            cr.move_to(left, goal_y)
            cr.line_to(right, goal_y)
            cr.stroke()
            cr.set_dash([])
        for index, (day, value) in enumerate(self.data):
            x = left + slot * index + (slot - bar_width) / 2
            bar_height = 0 if value <= 0 else max(2, (value / max_value) * (bottom - top))
            y = bottom - bar_height
            if value > 0:
                cr.set_source_rgba(*_rgba(color, 0.9))
                rounded_rect(cr, x, y, bar_width, bar_height, min(5, bar_width / 2))
                cr.fill()
            if self.show_values and value:
                draw_text(cr, x + bar_width / 2, max(0, y - 15), str(value), size=9, color=p.sub, align="center")
            try:
                day_date = datetime.strptime(day, "%Y-%m-%d")
                day_text = f"{day_date.month}/{day_date.day}"
            except ValueError:
                day_text = day
            draw_text(cr, x + bar_width / 2, bottom + 5, day_text, size=8, color=p.faint, align="center")
        return False


class Heatmap(Gtk.DrawingArea):
    def __init__(self, days: int = 30) -> None:
        super().__init__()
        self.days = days
        self.data: list[tuple[str, int]] = []
        self.goal = 8
        self.columns = 10
        self.cell = 22
        self.gap = 4
        rows = math.ceil(days / self.columns)
        self.set_size_request(self.columns * (self.cell + self.gap) + 4, rows * (self.cell + self.gap) + 24)
        self.connect("draw", self._draw)

    def update(self, data: Iterable[tuple[str, int]], goal: int) -> None:
        self.data = list(data)
        self.goal = max(1, goal)
        self.queue_draw()

    def _draw(self, widget, cr) -> bool:
        p = theme.palette
        width = widget.get_allocated_width()
        columns = max(1, (width - 4) // (self.cell + self.gap))
        for index, (day, count) in enumerate(self.data):
            row, col = divmod(index, columns)
            x = 2 + col * (self.cell + self.gap)
            y = 2 + row * (self.cell + self.gap)
            ratio = count / self.goal
            if count == 0:
                color = _rgba(p.border, 0.9)
            elif ratio >= 1:
                color = _rgba(p.green)
            elif ratio >= 0.5:
                color = _rgba(p.green, 0.65)
            elif ratio >= 0.25:
                color = _rgba(p.green, 0.4)
            else:
                color = _rgba(p.green, 0.22)
            cr.set_source_rgba(*color)
            rounded_rect(cr, x, y, self.cell, self.cell, 4)
            cr.fill()
            if count > 0:
                draw_text(
                    cr,
                    x + self.cell / 2,
                    y + 5,
                    str(count),
                    size=8,
                    color="#ffffff" if ratio >= 0.5 else p.text,
                    align="center",
                )
        rows = math.ceil(len(self.data) / columns) if self.data else 1
        draw_text(
            cr,
            2,
            6 + rows * (self.cell + self.gap),
            "30 days",
            size=8,
            color=p.faint,
        )
        return False


# ---------------------------------------------------------------------------
# Stat cards / badges


class StatCard(Gtk.Box):
    def __init__(self, title: str, value: str = "0", color: str | None = None, icon: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.get_style_context().add_class("card")

        header = hbox(4)
        if icon:
            header.pack_start(label(icon, "small"), False, False, 0)
        header.pack_start(label(title, "muted tiny"), False, False, 0)
        self.pack_start(header, False, False, 0)

        self.value_label = label(value, "bold")
        self.value_label.set_xalign(0)
        self.value_label.set_markup(
            f"<span size='22000' weight='bold'>{_escape(value)}</span>"
        )
        if color:
            self.value_label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(*_rgba(color)))
        self.pack_start(self.value_label, False, False, 0)

    def set_value(self, value: str, color: str | None = None) -> None:
        self.value_label.set_markup(
            f"<span size='22000' weight='bold'>{_escape(value)}</span>"
        )
        if color:
            self.value_label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(*_rgba(color)))


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class BadgeTile(Gtk.Box):
    def __init__(self, icon: str, name: str, desc: str, earned: bool, size: int = 54) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_size_request(86, -1)
        circle = Gtk.Label(label=icon)
        circle.set_size_request(size, size)
        circle.set_justify(Gtk.Justification.CENTER)
        circle.set_xalign(0.5)
        circle.set_yalign(0.5)
        circle.get_style_context().add_class("surface")
        if earned:
            circle.get_style_context().add_class("badge-earned")
        else:
            circle.set_opacity(0.38)
        self.pack_start(circle, False, False, 0)
        title = label(name, "tiny bold" if earned else "tiny faint", wrap=True, xalign=0.5)
        title.set_justify(Gtk.Justification.CENTER)
        title.set_opacity(1.0 if earned else 0.55)
        self.pack_start(title, False, False, 0)
        if desc:
            subtitle = label(desc, "faint tiny", wrap=True, xalign=0.5)
            subtitle.set_justify(Gtk.Justification.CENTER)
            subtitle.set_opacity(0.8 if earned else 0.4)
            self.pack_start(subtitle, False, False, 0)


# ---------------------------------------------------------------------------
# Window shells


def setup_rgba(window: Gtk.Window) -> None:
    window.set_app_paintable(True)
    screen = window.get_screen()
    visual = screen.get_rgba_visual() if screen is not None else None
    if visual is not None:
        window.set_visual(visual)


_setup_rgba = setup_rgba


class AppWindow(Gtk.Window):
    """Undecorated window with a custom draggable title bar."""

    def __init__(self, title: str, width: int = 720, height: int = 640, *, subtitle: str = "") -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._title_text = title
        self._subtitle_text = subtitle
        self.set_decorated(False)
        self.set_default_size(width, height)
        self.set_resizable(True)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_skip_taskbar_hint(False)
        self.set_wmclass("health-tick", "HealthTick")
        _setup_rgba(self)
        self.get_style_context().add_class("rounded-window")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.get_style_context().add_class("rounded-window")
        outer.set_border_width(0)
        self.add(outer)

        self.header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.header.set_margin_start(18)
        self.header.set_margin_end(14)
        self.header.set_margin_top(12)
        self.header.set_margin_bottom(10)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.title_label = label(title, "window-title")
        title_box.pack_start(self.title_label, False, False, 0)
        self.subtitle_label = label(subtitle, "subtitle")
        self.subtitle_label.set_no_show_all(not bool(subtitle))
        title_box.pack_start(self.subtitle_label, False, False, 0)
        self.header.pack_start(title_box, True, True, 0)

        self.header_extra = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.header.pack_end(self.header_extra, False, False, 0)

        close = Gtk.Button(label="✕")
        close.get_style_context().add_class("flat")
        close.set_tooltip_text("Close")
        close.connect("clicked", lambda *_: self.hide())
        self.header.pack_end(close, False, False, 0)

        # The whole header is the drag handle; it sits behind the buttons.
        self.header.connect("button-press-event", self._begin_drag)

        outer.pack_start(self.header, False, False, 0)
        outer.pack_start(separator(), False, False, 0)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.body.set_margin_start(18)
        self.body.set_margin_end(18)
        self.body.set_margin_top(12)
        self.body.set_margin_bottom(18)
        outer.pack_start(self.body, True, True, 0)
        self.connect("delete-event", self._on_delete)

    def _begin_drag(self, widget, event) -> bool:
        if event.button == 1:
            self.begin_move_drag(
                event.button, int(event.x_root), int(event.y_root), event.time
            )
            return True
        return False

    def _on_delete(self, *_args):
        self.hide()
        return True

    def set_body(self, widget: Gtk.Widget) -> None:
        for child in list(self.body.get_children()):
            self.body.remove(child)
        self.body.pack_start(widget, True, True, 0)
        widget.show_all()

    def set_title_text(self, title: str) -> None:
        self._title_text = title
        self.title_label.set_text(title)
        self.set_title(title)

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle_text = subtitle
        self.subtitle_label.set_text(subtitle)
        self.subtitle_label.set_no_show_all(not bool(subtitle))
        self.subtitle_label.set_visible(bool(subtitle))

    def present_window(self) -> None:
        self.show_all()
        self.present()
        # Cinnamon's focus-stealing prevention can map the window below the
        # currently focused app when it was opened from a tray popup.  Raise
        # and focus it explicitly so it is actually visible.
        try:
            gdk_window = self.get_window()
            if gdk_window is not None:
                gdk_window.raise_()
                gdk_window.focus(Gdk.CURRENT_TIME)
        except Exception:
            pass
        self.queue_draw()


class PopupWindow(Gtk.Window):
    """Borderless always-on-top popup, used for the menu bar panel."""

    def __init__(self, width: int = 300) -> None:
        # A managed toplevel (rather than an override-redirect POPUP) is used
        # so the window can take keyboard focus and emit focus-out-event when
        # the user clicks elsewhere.  It still looks and behaves like a panel.
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        _setup_rgba(self)
        self.set_default_size(width, -1)
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.root.get_style_context().add_class("rounded-window")
        self.root.set_size_request(width, -1)
        self.add(self.root)


def make_tool_button(text: str, icon: str, callback: Callable[[], None], title_only: bool = False) -> Gtk.Button:
    button = Gtk.Button()
    button.get_style_context().add_class("menu-control")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.set_halign(Gtk.Align.CENTER)
    if icon:
        box.pack_start(label(icon, "small", xalign=0.5), False, False, 0)
    if text:
        box.pack_start(label(text, "tiny", xalign=0.5), False, False, 0)
    button.add(box)
    button.set_tooltip_text(text or icon)
    button.connect("clicked", lambda *_: callback())
    return button


def add_pointer_cursor(widget: Gtk.Widget) -> None:
    try:
        window = widget.get_window()
        if window is not None:
            window.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), "pointer"))
    except Exception:
        pass


def queue_redraw_all() -> None:
    """Ask every mapped window to redraw after a theme change."""
    for window in Gtk.Window.list_toplevels():
        try:
            window.queue_draw()
            stack = [window]
            while stack:
                widget = stack.pop()
                widget.queue_draw()
                if isinstance(widget, Gtk.Container):
                    stack.extend(widget.get_children())
        except Exception:
            pass
