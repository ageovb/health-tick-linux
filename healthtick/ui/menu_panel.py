"""The tray popup panel (macOS ``MenuView`` equivalent)."""

from __future__ import annotations

import math
from datetime import datetime

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..config import (  # noqa: E402
    AppPhase,
    format_duration_seconds,
    format_work_time,
    format_work_time_short,
)
from ..i18n import L  # noqa: E402
from .break_card import BreakCard  # noqa: E402
from .theme import theme  # noqa: E402
from .widgets import (  # noqa: E402
    PopupWindow,
    TimerCircle,
    WeekPixels,
    fit_ring_pt,
    hbox,
    label,
    make_tool_button,
    phase_color,
    ring_markup,
    separator,
    spacer,
    vbox,
)


class MenuPanel(PopupWindow):
    def __init__(self, state, callbacks: dict) -> None:
        super().__init__(300)
        self.state = state
        self.callbacks = callbacks
        self._pinned = False
        self._auto = False
        self._center_mode = ""
        self._time_label: Gtk.Label | None = None
        self._time_pt = 28.0
        self._phase_label: Gtk.Label | None = None
        self._pause_button: Gtk.Button | None = None
        self._reset_button: Gtk.Button | None = None
        self._anchor = None
        self._last_position: tuple[int, int] | None = None

        self.root.set_margin_start(0)
        self.root.set_margin_end(0)
        content = vbox(10)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(14)
        content.set_margin_bottom(12)
        self.root.pack_start(content, True, True, 0)

        # Header chips -----------------------------------------------------
        self.chips = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.goal_chip = self._chip("✅", L.daily_goal_reached, "chip-green")
        self.quiet_chip = self._chip("🌙", L.quiet_hours_active, "chip-purple")
        self.chips.pack_start(self.goal_chip, False, False, 0)
        self.chips.pack_start(self.quiet_chip, False, False, 0)
        content.pack_start(self.chips, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        # Size each page to its own content; otherwise the taller normal menu
        # leaves a large empty gap under the compact break card.
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(False)
        content.pack_start(self.stack, True, True, 0)

        self.stack.add_named(self._build_normal(), "normal")
        self.break_card = BreakCard(state)
        card_wrap = vbox(0)
        card_wrap.set_margin_top(2)
        card_wrap.set_margin_bottom(2)
        card_wrap.pack_start(self.break_card, False, False, 0)
        self.stack.add_named(card_wrap, "card")
        self.stack.set_visible_child_name("normal")

        content.pack_start(separator(), False, False, 0)
        quit_button = Gtk.Button(label=L.quit)
        quit_button.get_style_context().add_class("flat")
        quit_button.connect("clicked", lambda *_: self.callbacks.get("quit", lambda: None)())
        content.pack_start(quit_button, False, False, 0)

        self.connect("focus-out-event", self._on_focus_out)
        self.connect("key-press-event", self._on_key)
        # The panel swaps between the normal menu and the break card; its size
        # can change after the first layout pass. Re-anchor whenever that
        # happens so it always hugs the tray icon instead of floating.
        self.connect("size-allocate", self._on_size_allocate)
        self.refresh(force=True)

    def _chip(self, emoji: str, text: str, css: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        box.get_style_context().add_class("header-chip")
        box.get_style_context().add_class(css)
        box.set_halign(Gtk.Align.CENTER)
        box.pack_start(label(emoji, "tiny"), False, False, 0)
        box.pack_start(label(text, "tiny bold"), False, False, 0)
        # Children are shown eagerly; the container itself is kept hidden by
        # show_all() and revealed by _refresh_chips().  Without this the chips
        # would appear as empty pills when first made visible.
        for child in box.get_children():
            child.show_all()
        box.set_no_show_all(True)
        box.set_visible(False)
        return box

    # -------------------------------------------------------------- normal page

    def _build_normal(self) -> Gtk.Widget:
        page = vbox(10)
        page.set_margin_top(2)

        self.ring = TimerCircle(124, 4)
        self.ring.set_halign(Gtk.Align.CENTER)
        page.pack_start(self.ring, False, False, 0)

        # Delay the upcoming break from the panel as well, so the option stays
        # reachable even when no confirmation card is shown (eye-care mode).
        self._delay_button = Gtk.Button(label="")
        self._delay_button.get_style_context().add_class("flat")
        self._delay_button.set_no_show_all(True)
        self._delay_button.connect("clicked", lambda *_: self.state.delay_break())
        self._delay_button.set_halign(Gtk.Align.CENTER)
        page.pack_start(self._delay_button, False, False, 0)

        stats = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        stats.set_halign(Gtk.Align.CENTER)
        stats.set_homogeneous(True)
        self.today_value = self._stat_column(stats, "0", L.done, "green")
        self.goal_value = self._stat_column(stats, "8", L.goal, None)
        self.streak_value = self._stat_column(stats, "0", L.streak, "orange")
        page.pack_start(stats, False, False, 0)

        work_row = hbox(4)
        work_row.set_halign(Gtk.Align.CENTER)
        work_row.pack_start(label("✍️", "small"), False, False, 0)
        work_row.pack_start(label(L.today_worked_prefix, "small muted"), False, False, 0)
        self.work_value = label("0m", "small bold blue")
        work_row.pack_start(self.work_value, False, False, 0)
        share = Gtk.Button(label="⤴")
        share.get_style_context().add_class("flat")
        share.set_tooltip_text(L.share)
        share.connect("clicked", lambda *_: self.callbacks.get("share", lambda: None)())
        work_row.pack_start(share, False, False, 0)
        page.pack_start(work_row, False, False, 0)

        self.week_pixels = WeekPixels()
        page.pack_start(self.week_pixels, False, False, 0)

        self.badge_hint = label("", "tiny muted", wrap=True, xalign=0.5)
        self.badge_hint.set_justify(Gtk.Justification.CENTER)
        page.pack_start(self.badge_hint, False, False, 0)

        page.pack_start(separator(), False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        controls.set_homogeneous(True)
        self._pause_button = make_tool_button(L.pause, "⏸", self._toggle_pause)
        self._reset_button = make_tool_button(L.reset, "↺", self._reset)
        controls.pack_start(self._pause_button, True, True, 0)
        controls.pack_start(self._reset_button, True, True, 0)
        controls.pack_start(
            make_tool_button(L.achievements, "🏆", self.callbacks.get("stats", lambda: None)),
            True,
            True,
            0,
        )
        controls.pack_start(
            make_tool_button(L.help, "❓", self.callbacks.get("help", lambda: None)),
            True,
            True,
            0,
        )
        controls.pack_start(
            make_tool_button(L.settings, "⚙️", self.callbacks.get("settings", lambda: None)),
            True,
            True,
            0,
        )
        page.pack_start(controls, False, False, 0)
        return page

    def _stat_column(self, parent: Gtk.Box, value: str, caption: str, css: str | None) -> Gtk.Label:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        value_label = label(value, css or "", xalign=0.5)
        value_label.set_markup(f"<span size='20000' weight='bold'>{value}</span>")
        if css:
            value_label.get_style_context().add_class(css)
        box.pack_start(value_label, False, False, 0)
        box.pack_start(label(caption, "faint tiny", xalign=0.5), False, False, 0)
        parent.pack_start(box, True, True, 0)
        return value_label

    # ------------------------------------------------------------------ state

    def _should_show_card(self) -> bool:
        if self.state.is_in_quiet_hours or self.state.goal_auto_stopped:
            return False
        if self.state.off_work_summary is not None:
            return self.state.config.break_position.value == "menu_window"
        return (
            self.state.phase in (AppPhase.ALERTING, AppPhase.BREAKING, AppPhase.WAITING)
            and self.state.config.break_position.value == "menu_window"
        )

    def refresh(self, *, force: bool = False) -> None:
        show_card = self._should_show_card()
        wanted = "card" if show_card else "normal"
        if self.stack.get_visible_child_name() != wanted:
            self.stack.set_visible_child_name(wanted)
        if show_card:
            self.break_card.refresh(force=force)
            self._refresh_chips()
            return

        self._refresh_chips()
        self._update_center()
        self._update_stats()
        self._update_badge_hint()
        if self._pause_button is not None:
            paused = self.state.phase is AppPhase.PAUSED
            self._pause_button.set_tooltip_text(L.resume if paused else L.pause)
            child = self._pause_button.get_child()
            if child is not None and isinstance(child, Gtk.Box):
                labels = [w for w in child.get_children() if isinstance(w, Gtk.Label)]
                if labels:
                    labels[0].set_text("▶" if paused else "⏸")
                    if len(labels) > 1:
                        labels[1].set_text(L.resume if paused else L.pause)

    def tick(self) -> None:
        """Cheap per-second update (no stat/widget rebuilds)."""
        if not self.get_visible():
            return
        if self.stack.get_visible_child_name() == "card":
            self.break_card.refresh()
        else:
            self._update_center()

    def _refresh_chips(self) -> None:
        self.goal_chip.set_visible(
            self.state.today_done >= self.state.config.daily_goal and not self.state.is_in_quiet_hours
        )
        self.quiet_chip.set_visible(
            self.state.is_in_quiet_hours or self.state.goal_auto_stopped
        )

    def _update_center(self) -> None:
        state = self.state
        off_duty = state.is_in_quiet_hours or state.goal_auto_stopped
        if off_duty:
            mode = "off"
        elif state.phase is AppPhase.WAITING:
            mode = "waiting"
        elif state.phase is AppPhase.WORKING:
            mode = "working"
        else:
            mode = "other"

        if mode != self._center_mode:
            self._center_mode = mode
            if mode == "off":
                button = Gtk.Button()
                button.get_style_context().add_class("flat")
                box = vbox(3)
                box.set_halign(Gtk.Align.CENTER)
                box.pack_start(label("⚡", "title orange", xalign=0.5), False, False, 0)
                box.pack_start(label(L.continue_working, "small orange bold", xalign=0.5), False, False, 0)
                button.add(box)
                button.connect(
                    "clicked",
                    lambda *_: state.resume_from_goal_stop()
                    if state.goal_auto_stopped
                    else state.activate_overtime(),
                )
                self._time_label = None
                self._phase_label = None
                self.ring.set_content(button)
            elif mode == "waiting":
                button = Gtk.Button(label=f"✓ {L.alert_im_back}")
                button.get_style_context().add_class("success")
                button.connect("clicked", lambda *_: state.confirm_return())
                self._time_label = None
                self._phase_label = None
                self.ring.set_content(button)
            else:
                self._time_label = label("", "mono", xalign=0.5)
                # Size against the longest text this phase will show, so the
                # size stays put while the digits tick down.
                probe = "8" * len(state.formatted_time)
                self._time_pt = fit_ring_pt(
                    self._time_label, probe, self.ring.content_radius
                )
                self._time_label.set_markup(
                    ring_markup(state.formatted_time, self._time_pt)
                )
                if mode == "working":
                    button = Gtk.Button(label=f"☕ {L.manual_break}")
                    button.get_style_context().add_class("flat")
                    button.connect("clicked", lambda *_: state.manual_break())
                    self._phase_label = None
                    self.ring.set_content(self._time_label, button, spacing=2)
                else:
                    self._phase_label = label(state.phase_label, "faint tiny", xalign=0.5)
                    self.ring.set_content(self._time_label, self._phase_label, spacing=2)

        if self._time_label is not None:
            self._time_label.set_markup(
                ring_markup(state.formatted_time, self._time_pt)
            )
        if self._phase_label is not None:
            self._phase_label.set_text(state.phase_label)

        delay_visible = state.phase is AppPhase.WORKING and not off_duty
        self._delay_button.set_visible(delay_visible)
        if delay_visible:
            self._delay_button.set_label(
                f"⏳ {L.delay_break(format_duration_seconds(state.delay_seconds))}"
            )

        if off_duty:
            progress = min(1.0, state.quiet_remaining_seconds / 3600) if state.quiet_remaining_seconds else 0.0
        elif state.phase in (AppPhase.WORKING, AppPhase.PAUSED) and state.remaining_seconds > 0:
            progress = state.remaining_seconds / max(1, state.config.work_minutes * 60)
        elif state.phase is AppPhase.BREAKING:
            progress = state.remaining_seconds / max(1, state.current_break_total_seconds)
        else:
            progress = 0.0
        self.ring.ring.set_progress(progress, phase_color(state))

    def _update_stats(self) -> None:
        s = self.state
        self.today_value.set_markup(f"<span size='20000' weight='bold'>{s.today_done}</span>")
        self.goal_value.set_markup(f"<span size='20000' weight='bold'>{s.config.daily_goal}</span>")
        self.streak_value.set_markup(f"<span size='20000' weight='bold'>{s.current_streak}</span>")
        self.work_value.set_text(format_work_time(s.today_work_minutes))
        self.week_pixels.update(s.week_data, s.config.daily_goal)

    def _update_badge_hint(self) -> None:
        s = self.state
        if s.next_badge is not None:
            badge = s.next_badge
            remaining = max(0, badge.days - s.current_streak)
            text = L.fmt(
                "badge_next_prefix",
                icon=badge.icon,
                name=badge.name,
            ) if False else f"🎯 {L.badge_next_prefix}"
            self.badge_hint.set_markup(
                f"<span size='10500'>🎯 {L.badge_next_prefix}</span>"
                f"<span size='12000' weight='bold' color='{theme.palette.green}'>{badge.icon} {badge.name}</span>"
                f"<span size='10500'>{L.badge_next_mid}</span>"
                f"<span size='11000' weight='bold' color='{theme.palette.orange}'>{remaining}</span>"
                f"<span size='10500'>{L.badge_next_suffix}</span>"
            )
            self.badge_hint.set_visible(True)
        elif s.earned_badge is not None:
            badge = s.earned_badge
            self.badge_hint.set_markup(
                f"<span size='11500'>{badge.icon}</span> "
                f"<span size='11000' weight='bold' color='{theme.palette.green}'>{badge.name}</span>"
            )
            self.badge_hint.set_visible(True)
        else:
            self.badge_hint.set_visible(False)

    # ------------------------------------------------------------------ actions

    def _toggle_pause(self) -> None:
        if self.state.goal_auto_stopped:
            self.state.resume_from_goal_stop()
        else:
            self.state.toggle_pause()

    def _reset(self) -> None:
        self.state.reset()

    # ------------------------------------------------------------- positioning

    def show_panel(self, icon=None, *, auto: bool = False) -> None:
        self._auto = auto
        self._pinned = auto
        self._anchor = icon
        self.refresh(force=True)
        self.show_all()
        self._last_position = None
        self._position(icon)
        self.present()
        # Layout settles one main-loop iteration later; nudge into place again
        # with the final size.
        GLib.idle_add(self._reposition_idle)
        # The status icon may not be embedded (and its geometry not yet valid)
        # when an alert auto-opens during startup. Re-check once the tray has
        # settled.
        if icon is not None:
            GLib.timeout_add(700, self._reposition_idle)
            GLib.timeout_add(1800, self._reposition_idle)
        try:
            self.get_window().focus(Gdk.CURRENT_TIME)
        except Exception:
            pass

    def _reposition_idle(self) -> bool:
        if self.get_visible():
            self._position(self._anchor)
        return False

    def _on_size_allocate(self, _widget, _allocation) -> None:
        if self.get_visible():
            self._position(self._anchor)

    def _icon_area(self, geometry, display, pointer_x, pointer_y):
        """Return a trustworthy icon rectangle, or None.

        Gtk.StatusIcon on Cinnamon sometimes reports a stale/off-panel area
        (e.g. a hidden tray container).  An icon that is not close to a screen
        edge cannot be a status-bar icon, so treat it as unusable.
        """
        if geometry is None or len(geometry) < 3:
            return None
        area = geometry[-2] if len(geometry) >= 4 else geometry[1]
        if area is None or getattr(area, "width", 0) <= 1 or getattr(area, "height", 0) <= 1:
            return None
        monitor = display.get_monitor_at_point(
            area.x + area.width // 2, area.y + area.height // 2
        )
        if monitor is None:
            return None
        geo = monitor.get_geometry()
        cx = area.x + area.width / 2
        cy = area.y + area.height / 2
        margin = 96
        inside = (
            geo.x - 24 <= cx <= geo.x + geo.width + 24
            and geo.y - 24 <= cy <= geo.y + geo.height + 24
        )
        near_edge = (
            abs(cy - geo.y) <= margin
            or abs(cy - (geo.y + geo.height)) <= margin
            or abs(cx - geo.x) <= margin
            or abs(cx - (geo.x + geo.width)) <= margin
        )
        if not (inside and near_edge):
            return None
        return area.x, area.y, area.width, area.height

    def _edge_anchor(self, display):
        """Best-effort icon position on the panel edge of the primary screen."""
        monitor = display.get_primary_monitor()
        if monitor is None:
            return 1900, 1070, 24, 24
        geo = monitor.get_geometry()
        wa = monitor.get_workarea()
        top_gap = max(0, wa.y - geo.y)
        bottom_gap = max(0, (geo.y + geo.height) - (wa.y + wa.height))
        left_gap = max(0, wa.x - geo.x)
        right_gap = max(0, (geo.x + geo.width) - (wa.x + wa.width))
        largest = max(top_gap, bottom_gap, left_gap, right_gap)
        if bottom_gap == largest and bottom_gap > 0:
            return wa.x + wa.width - 40, wa.y + wa.height - 4, 24, 24
        if top_gap == largest and top_gap > 0:
            return wa.x + wa.width - 40, wa.y - 20, 24, 24
        if right_gap == largest and right_gap > 0:
            return wa.x + wa.width - 4, wa.y + wa.height - 40, 24, 24
        if left_gap == largest and left_gap > 0:
            return wa.x - 20, wa.y + wa.height - 40, 24, 24
        return geo.x + geo.width - 40, geo.y + geo.height - 40, 24, 24

    def _position(self, icon=None) -> None:
        geometry = None
        if icon is not None:
            try:
                geometry = icon.get_geometry()
            except Exception:
                geometry = None
        display = self.get_display()
        pointer = display.get_default_seat().get_pointer()
        _screen, pointer_x, pointer_y = pointer.get_position()
        area = self._icon_area(geometry, display, pointer_x, pointer_y)

        if area is not None:
            icon_x, icon_y, icon_w, icon_h = area
            anchor_x, anchor_y = icon_x, icon_y
        elif not self._auto:
            # User clicked the icon: the pointer is on it even when Cinnamon's
            # status-icon geometry is stale.
            icon_x, icon_y, icon_w, icon_h = pointer_x - 12, pointer_y + 12, 24, 24
            anchor_x, anchor_y = pointer_x, pointer_y
        else:
            # A stale geometry is worse than a sensible default: anchor to the
            # panel edge reported by the monitor work area.
            icon_x, icon_y, icon_w, icon_h = self._edge_anchor(display)
            anchor_x, anchor_y = icon_x, icon_y

        width, height = self.get_size()
        _minimum, natural = self.get_preferred_size()
        # get_size() can still describe the previously shown content (the
        # normal menu vs. the break card); the preferred size reflects the
        # stack's current child.
        if natural.width > 1:
            width = natural.width
        if natural.height > 1:
            height = natural.height
        monitor = display.get_monitor_at_point(anchor_x, anchor_y)
        if monitor is None:
            monitor = display.get_primary_monitor()
        workarea = monitor.get_workarea() if monitor is not None else Gdk.Rectangle(0, 0, 1920, 1080)

        x = icon_x + icon_w // 2 - width // 2
        x = max(workarea.x + 6, min(x, workarea.x + workarea.width - width - 6))
        icon_center = icon_y + icon_h // 2
        if icon_center > workarea.y + workarea.height // 2:
            y = icon_y - height - 6
        else:
            y = icon_y + icon_h + 6
        y = max(workarea.y + 6, min(y, workarea.y + workarea.height - height - 6))
        target = (int(x), int(y))
        if target == self._last_position:
            return
        self._last_position = target
        self.move(*target)

    def hide_auto(self) -> None:
        if self._auto:
            self._auto = False
            self._pinned = False
            self.hide()

    def toggle(self, icon=None) -> None:
        if self.get_visible() and not self._auto:
            self.hide()
            return
        self.show_panel(icon, auto=False)

    def _on_focus_out(self, *_args) -> bool:
        if self._pinned:
            return False
        self.hide()
        return False

    def _on_key(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._auto = False
            self._pinned = False
            self.hide()
            return True
        return False
