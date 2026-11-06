"""Statistics and badge wall window."""

from __future__ import annotations

from datetime import date

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from ..config import ALL_BADGES, ALL_TOTAL_BADGES, format_work_time  # noqa: E402
from ..i18n import L  # noqa: E402
from .theme import theme  # noqa: E402
from .widgets import (  # noqa: E402
    AppWindow,
    BadgeTile,
    BarChart,
    Heatmap,
    StatCard,
    hbox,
    label,
    separator,
    spacer,
    vbox,
)


class StatsWindow(AppWindow):
    def __init__(self, state, callbacks: dict | None = None) -> None:
        super().__init__(L.stats_window, 760, 780, subtitle=L.app_slogan)
        self.state = state
        self.callbacks = callbacks or {}
        self._tab_index = 0
        share = Gtk.Button(label=f"⤴ {L.share}")
        share.get_style_context().add_class("flat")
        share.connect("clicked", lambda *_: self.share_preview())
        self.header_extra.pack_start(share, False, False, 0)
        self.rebuild()
        self.connect("delete-event", self._on_delete)

    def _on_delete(self, *_args):
        self.hide()
        return True

    def rebuild(self) -> None:
        notebook = Gtk.Notebook()
        notebook.append_page(self._overview_page(), Gtk.Label(label=L.tab_stats))
        notebook.append_page(self._badges_page(), Gtk.Label(label=L.tab_badges))
        notebook.set_current_page(self._tab_index)
        notebook.connect("switch-page", self._on_tab)
        self.set_body(notebook)
        self.refresh()

    def _on_tab(self, _nb, _page, index: int) -> None:
        self._tab_index = index

    # ---------------------------------------------------------------- overview

    def _overview_page(self) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        inner = vbox(14)
        inner.set_margin_start(4)
        inner.set_margin_end(8)
        inner.set_margin_top(14)
        inner.set_margin_bottom(20)
        scroller.add(inner)

        # Hero progress
        hero = vbox(8)
        hero.get_style_context().add_class("card")
        top = hbox(8)
        self.hero_value = label(f"{self.state.today_done} / {self.state.config.daily_goal}", "title green")
        top.pack_start(self.hero_value, False, False, 0)
        top.pack_start(label(L.today_done, "tiny muted"), False, False, 0)
        self.hero_streak = label("", "small orange bold")
        top.pack_end(self.hero_streak, False, False, 0)
        hero.pack_start(top, False, False, 0)
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(False)
        hero.pack_start(self.progress, False, False, 0)
        self.encourage = label("", "small muted", wrap=True)
        hero.pack_start(self.encourage, False, False, 0)
        inner.pack_start(hero, False, False, 0)

        # Stat card grid
        grid = Gtk.Grid(column_spacing=10, row_spacing=10)
        grid.set_column_homogeneous(True)
        self.card_streak = StatCard(L.current_streak, "0", theme.palette.orange, "🔥")
        self.card_max = StatCard(L.max_streak, "0", theme.palette.purple, "🏔")
        self.card_total = StatCard(L.total, "0", theme.palette.green, "✅")
        self.card_work = StatCard(L.today_work_time, "0", theme.palette.blue, "✍️")
        grid.attach(self.card_streak, 0, 0, 1, 1)
        grid.attach(self.card_max, 1, 0, 1, 1)
        grid.attach(self.card_total, 2, 0, 1, 1)
        grid.attach(self.card_work, 3, 0, 1, 1)
        inner.pack_start(grid, False, False, 0)

        # Week chart
        inner.pack_start(label(L.last_7_days, "small bold"), False, False, 0)
        self.week_chart = BarChart(150)
        week_card = vbox(4)
        week_card.get_style_context().add_class("card")
        week_card.pack_start(self.week_chart, False, False, 0)
        inner.pack_start(week_card, False, False, 0)

        # rates
        rates = hbox(10)
        rates.set_homogeneous(True)
        self.week_rate = StatCard(L.week_goal, "0%", theme.palette.green, "📅")
        self.month_rate = StatCard(L.month_goal, "0%", theme.palette.blue, "🗓")
        rates.pack_start(self.week_rate, True, True, 0)
        rates.pack_start(self.month_rate, True, True, 0)
        inner.pack_start(rates, False, False, 0)

        # Work chart
        inner.pack_start(label(L.today_work_time + " · " + L.last_7_days, "small bold"), False, False, 0)
        self.work_chart = BarChart(140)
        work_card = vbox(4)
        work_card.get_style_context().add_class("card")
        work_card.pack_start(self.work_chart, False, False, 0)
        inner.pack_start(work_card, False, False, 0)

        # Heatmap
        inner.pack_start(label(L.last_30_days, "small bold"), False, False, 0)
        self.heatmap = Heatmap(30)
        heat_card = vbox(4)
        heat_card.get_style_context().add_class("card")
        heat_card.pack_start(self.heatmap, False, False, 0)
        heat_card.pack_start(
            label("■ 0  ■ 50%  ■ 100%", "tiny faint"), False, False, 0
        )
        inner.pack_start(heat_card, False, False, 0)

        self.summary_line = label("", "tiny muted", wrap=True)
        inner.pack_start(self.summary_line, False, False, 0)
        return scroller

    # ------------------------------------------------------------------ badges

    def _badges_page(self) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        inner = vbox(16)
        inner.set_margin_start(4)
        inner.set_margin_end(8)
        inner.set_margin_top(14)
        inner.set_margin_bottom(20)
        scroller.add(inner)

        self.badge_next = label("", "small muted", wrap=True)
        inner.pack_start(self.badge_next, False, False, 0)

        inner.pack_start(label(L.streak_badges, "small bold"), False, False, 0)
        self.streak_grid = Gtk.FlowBox()
        self.streak_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.streak_grid.set_max_children_per_line(6)
        self.streak_grid.set_row_spacing(10)
        self.streak_grid.set_column_spacing(10)
        inner.pack_start(self.streak_grid, False, False, 0)

        inner.pack_start(label(L.total_badges, "small bold"), False, False, 0)
        self.total_grid = Gtk.FlowBox()
        self.total_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.total_grid.set_max_children_per_line(6)
        self.total_grid.set_row_spacing(10)
        self.total_grid.set_column_spacing(10)
        inner.pack_start(self.total_grid, False, False, 0)
        return scroller

    # ----------------------------------------------------------------- refresh

    def refresh(self) -> None:
        s = self.state
        goal = max(1, s.config.daily_goal)
        self.hero_value.set_text(f"{s.today_done} / {goal}")
        self.hero_streak.set_text(f"🔥 {s.current_streak} {L.unit_times}")
        self.progress.set_fraction(min(1.0, s.today_done / goal))
        self.encourage.set_text(s.encourage_text)
        self.card_streak.set_value(str(s.current_streak), theme.palette.orange)
        self.card_max.set_value(str(s.max_streak), theme.palette.purple)
        self.card_total.set_value(str(s.total_count), theme.palette.green)
        self.card_work.set_value(format_work_time(s.today_work_minutes), theme.palette.blue)
        self.week_chart.update(s.week_data, goal=goal, color=theme.palette.green)
        week_done, week_days = s.db.week_completion_rate(goal)
        month_done, month_days = s.db.month_completion_rate(goal)
        self.week_rate.set_value(f"{round(week_done / max(1, week_days) * 100)}%", theme.palette.green)
        self.month_rate.set_value(f"{round(month_done / max(1, month_days) * 100)}%", theme.palette.blue)
        self.work_chart.update(
            s.week_work_data, color=theme.palette.blue, show_values=True
        )
        self.heatmap.update(s.db.last_30_days_counts(), goal)
        best_day, best_count = s.db.best_day_count()
        active_days = s.db.active_days()
        avg = round(s.total_count / active_days, 1) if active_days else 0
        self.summary_line.set_text(
            f"{L.active_days}: {active_days}   ·   {L.avg_daily}: {avg}   ·   {L.best_day}: "
            f"{best_count} ({best_day or '—'})"
        )

        # Badge wall
        for child in list(self.streak_grid.get_children()):
            self.streak_grid.remove(child)
        for child in list(self.total_grid.get_children()):
            self.total_grid.remove(child)
        for badge in ALL_BADGES:
            self.streak_grid.add(
                BadgeTile(
                    badge.icon,
                    badge.name,
                    badge.desc,
                    s.max_streak >= badge.days,
                )
            )
        for badge in ALL_TOTAL_BADGES:
            self.total_grid.add(
                BadgeTile(
                    badge.icon,
                    badge.name,
                    badge.desc,
                    s.total_count >= badge.days,
                )
            )
        if s.next_badge is not None:
            badge = s.next_badge
            self.badge_next.set_markup(
                f"<span size='11000'>{L.badge_next_prefix}</span>"
                f"<b> {badge.icon} {badge.name} </b>"
                f"<span size='11000'>{L.badge_next_mid} </span>"
                f"<b>{max(0, badge.days - s.current_streak)}</b>"
                f"<span size='11000'> {L.badge_next_suffix}</span>"
            )
        else:
            self.badge_next.set_text(L.no_badges_yet if not s.earned_badge else L.earned_badges)
        self.streak_grid.show_all()
        self.total_grid.show_all()

    # ------------------------------------------------------------------- share

    def share_text(self) -> str:
        s = self.state
        lines = [
            f"🌱 {L.app_name} · {date.today():%Y-%m-%d}",
            L.share_goal_reached
            if s.today_done >= s.config.daily_goal
            else L.share_keep_going,
            f"{L.today_done}: {s.today_done}/{s.config.daily_goal}",
            f"{L.share_streak}: {s.current_streak}   {L.share_max_streak}: {s.max_streak}",
            f"{L.share_total_breaks}: {s.total_count}",
            f"{L.share_work_time}: {format_work_time(s.today_work_minutes)}",
        ]
        return "\n".join(lines)

    def share_preview(self) -> None:
        text = self.share_text()
        dialog = Gtk.Dialog(title=L.share, transient_for=self, modal=True)
        dialog.add_button(L.close, Gtk.ResponseType.CLOSE)
        dialog.add_button(L.share_copy, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text(text)
        frame = Gtk.Frame()
        frame.add(view)
        frame.set_size_request(380, 220)
        box.pack_start(frame, True, True, 0)
        box.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text, -1)
            clipboard.store()
        dialog.destroy()

    def present_window(self) -> None:
        self.refresh()
        super().present_window()
