"""Small wrapper around desktop notifications (``notify-send``)."""

from __future__ import annotations

import shutil
import subprocess

from . import APP_NAME

_ICON = "appointment-soon"


class Notifier:
    def __init__(self) -> None:
        self._command = shutil.which("notify-send")

    @property
    def available(self) -> bool:
        return self._command is not None

    def send(
        self,
        title: str,
        body: str,
        *,
        urgency: str = "normal",
        icon: str = _ICON,
        timeout_ms: int = 8000,
    ) -> bool:
        if not self._command:
            return False
        try:
            subprocess.Popen(
                [
                    self._command,
                    "--app-name",
                    APP_NAME,
                    "--icon",
                    icon,
                    "--urgency",
                    urgency,
                    "--expire-time",
                    str(timeout_ms),
                    title,
                    body,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError:
            return False


notifier = Notifier()
