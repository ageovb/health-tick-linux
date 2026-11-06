"""GTK application wiring for HealthTick Linux."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, __version__  # noqa: E402
from .config import AppPhase, BreakPosition  # noqa: E402
from .db import Database  # noqa: E402
from .i18n import L  # noqa: E402
from .notify import notifier  # noqa: E402
from .shortcut import ShortcutWatcher  # noqa: E402
from .state import AppState  # noqa: E402
from .ui.break_overlay import OverlayManager  # noqa: E402
from .ui.celebration import CelebrationWindow  # noqa: E402
from .ui.help import HelpWindow  # noqa: E402
from .ui.holiday_calendar import HolidayCalendarWindow  # noqa: E402
from .ui.menu_panel import MenuPanel  # noqa: E402
from .ui.onboarding import OnboardingWindow  # noqa: E402
from .ui.settings import SettingsWindow  # noqa: E402
from .ui.stats import StatsWindow  # noqa: E402
from .ui.theme import theme  # noqa: E402
from .ui.tray import TrayIcon  # noqa: E402
from .ui.widgets import queue_redraw_all  # noqa: E402


def _asset_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / name


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for piece in value.lstrip("vV").replace("-", ".").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


class HealthTickApplication(Gtk.Application):
    def __init__(self, *, hidden: bool = False) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.hidden = hidden
        self._started = False
        self._quitting = False
        self._pending_onboarding = False
        self._language = None
        self._break_position = None
        self._restart_dialogs = 0
        self._update_checking = False
        self._session_bus = None
        self.state: AppState | None = None
        self.menu: MenuPanel | None = None
        self.overlay: OverlayManager | None = None
        self.tray: TrayIcon | None = None
        self.settings_window: SettingsWindow | None = None
        self.stats_window: StatsWindow | None = None
        self.help_window: HelpWindow | None = None
        self.holiday_window: HolidayCalendarWindow | None = None
        self.onboarding_window: OnboardingWindow | None = None
        self.celebration_window: CelebrationWindow | None = None
        self.shortcut: ShortcutWatcher | None = None

    # -------------------------------------------------------------- lifecycle

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        GLib.set_prgname("health-tick")
        GLib.set_application_name(APP_NAME)
        try:
            icon = _asset_path("health-tick.svg")
            if icon.exists():
                Gtk.Window.set_default_icon_from_file(str(icon))
        except Exception:
            pass
        self.hold()

    def do_activate(self) -> None:
        if not self._started:
            self._started = True
            self._build()
            return
        if self.menu is not None:
            self.menu.toggle(self.tray.icon if self.tray else None)

    def do_shutdown(self) -> None:
        self._teardown()
        Gtk.Application.do_shutdown(self)

    def _build(self) -> None:
        self.state = AppState(Database())
        self._language = self.state.config.language
        self._break_position = self.state.config.break_position
        theme.apply(self.state.config.appearance)

        callbacks = {
            "toggle_panel": self._toggle_panel,
            "show_panel": self._show_panel,
            "stats": self._open_stats,
            "help": self._open_help,
            "settings": self._open_settings,
            "holiday_calendar": self._open_holiday,
            "onboarding": self._open_onboarding,
            "check_update": lambda: self._check_updates(manual=True),
            "share": self._share,
            "quit": self._quit,
            "toggle_pause": self._toggle_pause,
            "manual_break": lambda: self.state.manual_break(),
            "delay_break": lambda: self.state.delay_break(),
            "restart_cycle": lambda kind: self.state.restart_cycle(kind),
            "reset": lambda: self.state.reset(),
        }
        self.menu = MenuPanel(self.state, callbacks)
        self.overlay = OverlayManager(self.state, self.menu)
        self.tray = TrayIcon(self.state, callbacks)
        self.celebration_window = CelebrationWindow(self.state)
        self.shortcut = ShortcutWatcher(self.state)

        self.state.add_listener(self._on_state_event)
        self.state.start()
        self._language = self.state.config.language
        self._break_position = self.state.config.break_position
        self._setup_session_signals()
        self.shortcut.update()

        if self.state.config.auto_check_update:
            GLib.timeout_add_seconds(12, self._auto_check_updates)

        if self.state.db.is_onboarding_completed():
            if not self.hidden:
                GLib.timeout_add(350, self._initial_panel)
        else:
            self._pending_onboarding = True
            GLib.timeout_add(300, self._open_onboarding_idle)

    def _initial_panel(self) -> bool:
        if self.menu is not None:
            self.menu.show_panel(self.tray.icon if self.tray else None)
        return False

    def _teardown(self) -> None:
        if self.shortcut is not None:
            self.shortcut.stop()
        if self.state is not None:
            try:
                self.state.shutdown()
            except Exception:
                pass
        if self.tray is not None:
            try:
                self.tray.icon.set_visible(False)
            except Exception:
                pass
        if self.overlay is not None:
            self.overlay.destroy()
        for window in (
            self.settings_window,
            self.stats_window,
            self.help_window,
            self.holiday_window,
            self.onboarding_window,
            self.celebration_window,
            self.menu,
        ):
            if window is not None:
                try:
                    window.destroy()
                except Exception:
                    pass
        if self.state is not None:
            try:
                self.state.db.close()
            except Exception:
                pass
        self._session_bus = None

    # ---------------------------------------------------------------- events

    def _on_state_event(self, event: str, payload: dict) -> None:
        if self.state is None:
            return
        if event == "tick":
            if self.tray is not None:
                self.tray.update()
            if self.menu is not None:
                self.menu.tick()
            if self.overlay is not None:
                self.overlay.refresh()
            return

        if event == "stats":
            if self.tray is not None:
                self.tray.update()
            if self.menu is not None and self.menu.get_visible():
                self.menu.refresh()
            if self.stats_window is not None and self.stats_window.get_visible():
                self.stats_window.refresh()
            return

        if event == "config":
            self._on_config_changed(bool(payload.get("changed")))
            return

        if event == "appearance":
            theme.apply(self.state.config.appearance)
            queue_redraw_all()
            if self.tray is not None:
                self.tray.update(force=True)
            if self.menu is not None:
                self.menu.refresh(force=True)
            if self.overlay is not None:
                self.overlay.refresh()
            if self.stats_window is not None and self.stats_window.get_visible():
                self.stats_window.refresh()
            if self.holiday_window is not None and self.holiday_window.get_visible():
                self.holiday_window.refresh()
            return

        if event == "alert":
            if self.overlay is not None:
                self.overlay.show_alert(self.tray.icon if self.tray else None)
            self.shortcut.update()
            return
        if event == "break":
            if self.overlay is not None:
                self.overlay.show_break(self.tray.icon if self.tray else None)
            self.shortcut.update()
            return
        if event == "waiting":
            if self.overlay is not None:
                self.overlay.show_waiting(self.tray.icon if self.tray else None)
            self.shortcut.update()
            return
        if event == "summary":
            if self.overlay is not None:
                self.overlay.show_summary(self.tray.icon if self.tray else None)
            return
        if event == "hide":
            if self.overlay is not None:
                self.overlay.hide()
            self.shortcut.update()
            return
        if event == "state":
            if self.tray is not None:
                self.tray.update(force=True)
            if self.menu is not None:
                self.menu.refresh()
            self.shortcut.update()
            return
        if event == "restart_prompt":
            self._show_restart_dialog()
            return
        if event == "celebration":
            badge = payload.get("badge")
            if badge is not None and self.celebration_window is not None:
                self.celebration_window.show_badge(badge)
            return
        if event == "onboarding":
            self._pending_onboarding = True
            return
        if event == "holiday_synced":
            if self.holiday_window is not None and self.holiday_window.get_visible():
                self.holiday_window.refresh()
            if self.settings_window is not None and self.settings_window.get_visible():
                self.settings_window.refresh_from_state()
            return
        if event == "data_changed":
            if self.stats_window is not None and self.stats_window.get_visible():
                self.stats_window.refresh()
            if self.menu is not None:
                self.menu.refresh()
            if self.tray is not None:
                self.tray.update(force=True)
            return
        if event == "restart_dismissed":
            return

    def _on_config_changed(self, changed: bool) -> None:
        if self.state is None:
            return
        if self.tray is not None:
            self.tray.update(force=True)
        if self.menu is not None and self.menu.get_visible():
            self.menu.refresh(force=True)
        if self.settings_window is not None and self.settings_window.get_visible():
            self.settings_window.refresh_from_state()
        if self.shortcut is not None:
            self.shortcut.update()

        language = self.state.config.language
        if language != self._language:
            self._language = language
            self._rebuild_for_language()

        position = self.state.config.break_position
        if position != self._break_position:
            self._break_position = position
            self._refresh_break_ui()
        else:
            self._break_position = position

    def _rebuild_for_language(self) -> None:
        if self.menu is None:
            return
        was_visible = self.menu.get_visible()
        old_menu = self.menu
        callbacks = {
            "toggle_panel": self._toggle_panel,
            "show_panel": self._show_panel,
            "stats": self._open_stats,
            "help": self._open_help,
            "settings": self._open_settings,
            "holiday_calendar": self._open_holiday,
            "onboarding": self._open_onboarding,
            "check_update": lambda: self._check_updates(manual=True),
            "share": self._share,
            "quit": self._quit,
            "toggle_pause": self._toggle_pause,
            "manual_break": lambda: self.state.manual_break(),
            "delay_break": lambda: self.state.delay_break(),
            "restart_cycle": lambda kind: self.state.restart_cycle(kind),
            "reset": lambda: self.state.reset(),
        }
        self.menu = MenuPanel(self.state, callbacks)
        if self.overlay is not None:
            self.overlay.menu = self.menu
            self.overlay.destroy()
            self.overlay._signature = ()
        old_menu.destroy()

        for name in ("stats_window", "help_window", "holiday_window", "onboarding_window"):
            window = getattr(self, name)
            if window is not None:
                try:
                    window.destroy()
                except Exception:
                    pass
                setattr(self, name, None)
        if self.settings_window is not None:
            self.settings_window.refresh_from_state()
        self._refresh_break_ui()
        if was_visible and not self._should_use_break_ui():
            self.menu.show_panel(self.tray.icon if self.tray else None)
        if self.tray is not None:
            self.tray.update(force=True)

    def _should_use_break_ui(self) -> bool:
        if self.state is None:
            return False
        return (
            self.state.phase in (AppPhase.ALERTING, AppPhase.BREAKING, AppPhase.WAITING)
            and self.state.config.break_position is BreakPosition.MENU_WINDOW
            and not self.state.is_in_quiet_hours
            and not self.state.goal_auto_stopped
        ) or (
            self.state.off_work_summary is not None
            and self.state.config.break_position is BreakPosition.MENU_WINDOW
        )

    def _refresh_break_ui(self) -> None:
        if self.state is None or self.overlay is None:
            return
        if self.overlay is not None:
            self.overlay.destroy()
            self.overlay._signature = ()
        if self.state.off_work_summary is not None:
            self.overlay.show_summary(self.tray.icon if self.tray else None)
        elif self.state.phase is AppPhase.ALERTING:
            self.overlay.show_alert(self.tray.icon if self.tray else None)
        elif self.state.phase is AppPhase.BREAKING:
            self.overlay.show_break(self.tray.icon if self.tray else None)
        elif self.state.phase is AppPhase.WAITING:
            self.overlay.show_waiting(self.tray.icon if self.tray else None)

    # --------------------------------------------------------------- actions

    def _toggle_panel(self) -> None:
        if self.menu is not None:
            self.menu.toggle(self.tray.icon if self.tray else None)

    def _show_panel(self) -> None:
        if self.menu is not None:
            self.menu.show_panel(self.tray.icon if self.tray else None)

    def _hide_menu(self) -> None:
        if self.menu is not None and self.menu.get_visible():
            self.menu.hide()

    def _open_stats(self) -> None:
        self._hide_menu()
        if self.stats_window is None:
            self.stats_window = StatsWindow(self.state)
        self.stats_window.present_window()

    def _open_help(self) -> None:
        self._hide_menu()
        if self.help_window is None:
            self.help_window = HelpWindow()
        self.help_window.present_window()

    def _open_settings(self) -> None:
        self._hide_menu()
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.state, {
                "holiday_calendar": self._open_holiday,
                "onboarding": self._open_onboarding,
                "check_update": lambda: self._check_updates(manual=True),
            })
        self.settings_window.present_window()

    def _open_holiday(self) -> None:
        if self.holiday_window is None:
            self.holiday_window = HolidayCalendarWindow(self.state)
        self.holiday_window.present_window()

    def _open_onboarding(self) -> None:
        self._hide_menu()
        if self.onboarding_window is not None:
            try:
                self.onboarding_window.destroy()
            except Exception:
                pass
        self.onboarding_window = OnboardingWindow(self.state, on_done=self._on_onboarding_done)
        self.onboarding_window.present_window()

    def _open_onboarding_idle(self) -> bool:
        if self._pending_onboarding:
            self._open_onboarding()
            self._pending_onboarding = False
        return False

    def _on_onboarding_done(self) -> None:
        if self.settings_window is not None and self.settings_window.get_visible():
            self.settings_window.refresh_from_state()
        if self.menu is not None:
            GLib.timeout_add(250, self._initial_panel)
        if self.tray is not None:
            self.tray.update(force=True)

    def _toggle_pause(self) -> None:
        if self.state is None:
            return
        if self.state.goal_auto_stopped:
            self.state.resume_from_goal_stop()
        else:
            self.state.toggle_pause()

    def _share(self) -> None:
        self._hide_menu()
        if self.stats_window is None:
            self.stats_window = StatsWindow(self.state)
        self.stats_window.present_window()
        self.stats_window.share_preview()

    def _quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        try:
            if self.state is not None:
                self.state.persist_now()
        except Exception:
            pass
        self.quit()

    # --------------------------------------------------------- restart prompt

    def _show_restart_dialog(self) -> None:
        if self._restart_dialogs:
            return
        self._restart_dialogs += 1
        dialog = Gtk.MessageDialog(
            transient_for=self.menu if self.menu is not None else None,
            modal=False,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=L.restart_needed_title,
        )
        dialog.format_secondary_text(L.restart_needed_desc)
        dialog.add_button(L.not_now, Gtk.ResponseType.CANCEL)
        dialog.add_button(L.apply_now, Gtk.ResponseType.OK)
        dialog.set_keep_above(True)

        def response(widget, result) -> None:
            self._restart_dialogs = max(0, self._restart_dialogs - 1)
            if result == Gtk.ResponseType.OK:
                if self.state is not None:
                    self.state.apply_config_and_restart()
            elif self.state is not None:
                self.state.dismiss_restart_prompt()
            widget.destroy()

        dialog.connect("response", response)
        dialog.show()

    # ------------------------------------------------------------ session DBus

    def _setup_session_signals(self) -> None:
        try:
            self._session_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            self._session_bus = None
            return
        bus = self._session_bus
        for interface in (
            "org.freedesktop.ScreenSaver",
            "org.gnome.ScreenSaver",
            "org.cinnamon.ScreenSaver",
        ):
            try:
                bus.signal_subscribe(
                    None,
                    interface,
                    "ActiveChanged",
                    None,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._on_screensaver_changed,
                )
            except Exception:
                pass
        try:
            bus.signal_subscribe(
                "org.freedesktop.login1",
                "org.freedesktop.login1.Manager",
                "PrepareForSleep",
                "/org/freedesktop/login1",
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_prepare_for_sleep,
            )
        except Exception:
            pass

    def _on_screensaver_changed(
        self, connection, sender, path, interface, signal, parameters
    ) -> None:
        if self.state is None:
            return
        try:
            (active,) = parameters.unpack()
        except Exception:
            return
        if active:
            self.state.note_screen_lock()
        else:
            self.state.handle_screen_unlock()
            if self.tray is not None:
                self.tray.update(force=True)

    def _on_prepare_for_sleep(
        self, connection, sender, path, interface, signal, parameters
    ) -> None:
        if self.state is None:
            return
        try:
            (starting,) = parameters.unpack()
        except Exception:
            return
        if starting:
            return
        self.state.check_day_change()
        self.state.check_quiet_hours()
        self.state.refresh_stats()
        if self.tray is not None:
            self.tray.update(force=True)

    # ---------------------------------------------------------- update check

    def _auto_check_updates(self) -> bool:
        self._check_updates(manual=False)
        return False

    def _check_updates(self, *, manual: bool) -> None:
        if self._update_checking:
            return
        self._update_checking = True

        def worker() -> None:
            latest = ""
            url = ""
            error = ""
            try:
                request = urllib.request.Request(
                    "https://api.github.com/repos/lifedever/health-tick-release/releases/latest",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": f"HealthTick/{__version__}",
                    },
                )
                with urllib.request.urlopen(request, timeout=12) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                latest = str(payload.get("tag_name") or "").lstrip("vV")
                url = str(payload.get("html_url") or "")
            except Exception as exc:
                error = str(exc)

            def done() -> bool:
                self._update_checking = False
                if error:
                    if manual:
                        self._update_dialog(L.update_check_failed, error, "")
                    return False
                if latest and _version_tuple(latest) > _version_tuple(__version__):
                    if manual:
                        self._update_dialog(
                            L.update_available(latest),
                            L.check_update,
                            url,
                        )
                    else:
                        notifier.send(
                            L.update_available(latest),
                            APP_NAME,
                            icon="software-update-available",
                        )
                elif manual:
                    self._update_dialog(
                        L.update_latest, f"{APP_NAME} {__version__}", url
                    )
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()

    def _update_dialog(self, title: str, body: str, url: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self.menu if self.menu is not None else None,
            modal=False,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.NONE,
            text=title,
        )
        if body:
            dialog.format_secondary_text(body)
        if url:
            dialog.add_button(L.open_releases, Gtk.ResponseType.OK)
        dialog.add_button(L.close, Gtk.ResponseType.CLOSE)
        dialog.set_keep_above(True)

        def response(widget, result) -> None:
            if url and result == Gtk.ResponseType.OK:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            widget.destroy()

        dialog.connect("response", response)
        dialog.show()


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    if "--version" in argv[1:]:
        print(f"{APP_NAME} {__version__}")
        return 0
    hidden = "--hidden" in argv[1:]
    app = HealthTickApplication(hidden=hidden)

    def on_term(*_args) -> bool:
        app._quit()
        return False

    try:
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, on_term)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, on_term)
    except Exception:
        pass
    try:
        return app.run([argv[0]])
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
