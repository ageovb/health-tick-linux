"""Themes and stylesheet for the GTK UI.

Colours follow the macOS source (green work, orange break, red alert, blue
waiting) and adapt to light/dark appearance.  The whole app is styled from one
provider so a runtime appearance change is a single reload.
"""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from ..config import AppAppearance  # noqa: E402


@dataclass(frozen=True)
class Palette:
    mode: str
    bg: str
    bg2: str
    bg3: str
    text: str
    sub: str
    faint: str
    border: str
    green: str
    orange: str
    red: str
    blue: str
    purple: str
    teal: str
    shadow: str


LIGHT = Palette(
    mode="light",
    bg="#ffffff",
    bg2="#f5f5f7",
    bg3="#ececf0",
    text="#1c1c1e",
    sub="#6e6e73",
    faint="#9a9aa0",
    border="rgba(0,0,0,0.09)",
    green="#28a745",
    orange="#f08c00",
    red="#e03131",
    blue="#1c7ed6",
    purple="#9c36b5",
    teal="#0c8599",
    shadow="rgba(0,0,0,0.18)",
)

DARK = Palette(
    mode="dark",
    bg="#1e1e20",
    bg2="#2a2a2d",
    bg3="#353538",
    text="#f2f2f7",
    sub="#a1a1a8",
    faint="#7c7c84",
    border="rgba(255,255,255,0.11)",
    green="#51cf66",
    orange="#ffa94d",
    red="#ff6b6b",
    blue="#4dabf7",
    purple="#da77f2",
    teal="#3bc9db",
    shadow="rgba(0,0,0,0.5)",
)


def detect_system_mode() -> str:
    settings = Gtk.Settings.get_default()
    if settings is not None and settings.get_property("gtk-application-prefer-dark-theme"):
        return "dark"
    try:
        screen = Gdk.Screen.get_default()
        if screen is not None:
            value = screen.get_setting("gtk-theme-name")
            if value and "dark" in str(value).lower():
                return "dark"
    except Exception:
        pass
    return "light"


class Theme:
    def __init__(self) -> None:
        self.appearance = AppAppearance.SYSTEM
        self.palette = LIGHT
        self._provider = None

    def resolve(self) -> Palette:
        if self.appearance is AppAppearance.DARK:
            return DARK
        if self.appearance is AppAppearance.LIGHT:
            return LIGHT
        return DARK if detect_system_mode() == "dark" else LIGHT

    def apply(self, appearance: AppAppearance) -> Palette:
        self.appearance = appearance
        palette = self.resolve()
        self.palette = palette
        screen = Gdk.Screen.get_default()
        if screen is None:
            return palette
        if self._provider is not None:
            Gtk.StyleContext.remove_provider_for_screen(screen, self._provider)
        provider = Gtk.CssProvider()
        css = build_css(palette).encode("utf-8")
        try:
            provider.load_from_data(css)
        except Exception:
            # GTK 3.20+ signature; older builds take a string.
            provider.load_from_data(css.decode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10
        )
        self._provider = provider
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property(
                "gtk-application-prefer-dark-theme", palette.mode == "dark"
            )
        return palette


def build_css(p: Palette) -> str:
    return f"""
/* ------------------------------------------------------------------ base */
window {{
    background-color: transparent;
    color: {p.text};
    font-family: "Noto Sans CJK SC", "Source Han Sans SC", "Ubuntu", "DejaVu Sans", sans-serif;
    font-size: 13px;
}}
.rounded-window {{ background-color: {p.bg}; border-radius: 16px; }}
.popup-window {{ background-color: {p.bg}; border-radius: 14px; }}
.root {{ background-color: {p.bg}; }}
.surface {{ background-color: {p.bg2}; border-radius: 12px; }}
.surface-3 {{ background-color: {p.bg3}; border-radius: 10px; }}
.transparent {{ background-color: transparent; }}
border {{ border-color: {p.border}; }}
separator {{ background-color: {p.border}; min-height: 1px; }}
label {{ color: {p.text}; }}
.muted {{ color: {p.sub}; }}
.faint {{ color: {p.faint}; }}
.tiny {{ font-size: 11px; }}
.small {{ font-size: 12px; }}
.title {{ font-size: 18px; font-weight: 700; }}
.window-title {{ font-size: 21px; font-weight: 700; }}
.subtitle {{ font-size: 12px; color: {p.sub}; }}
.mono {{ font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; }}
.bold {{ font-weight: 700; }}
.green {{ color: {p.green}; }}
.orange {{ color: {p.orange}; }}
.red {{ color: {p.red}; }}
.blue {{ color: {p.blue}; }}
.purple {{ color: {p.purple}; }}

/* --------------------------------------------------------------- buttons */
button {{
    background-image: none;
    background-color: {p.bg2};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 9px;
    padding: 6px 12px;
    box-shadow: none;
    text-shadow: none;
    outline: none;
}}
button:hover {{ background-color: {p.bg3}; }}
button:active {{ background-color: {p.border}; }}
button:disabled {{ color: {p.faint}; }}
button.flat, button.control {{
    background-color: transparent;
    border: none;
    padding: 4px 6px;
}}
button.flat:hover, button.control:hover {{ background-color: {p.bg2}; }}
button.primary {{
    background-image: linear-gradient(to bottom, {p.orange}, {p.orange});
    color: #ffffff;
    border: none;
    padding: 9px 16px;
    font-weight: 700;
}}
button.primary:hover {{ background-image: linear-gradient(to bottom, {p.orange}, {p.orange}); background-color: {p.orange}; }}
button.success {{
    background-image: linear-gradient(to bottom, {p.green}, {p.green});
    color: #ffffff; border: none; padding: 9px 16px; font-weight: 700;
}}
button.danger {{
    background-image: linear-gradient(to bottom, {p.red}, {p.red});
    color: #ffffff; border: none; padding: 8px 14px; font-weight: 600;
}}
button.danger-ghost {{ color: {p.red}; border-color: {p.red}; background-color: transparent; }}
button.pill {{
    border-radius: 999px;
    padding: 4px 12px;
    font-size: 12px;
    background-color: {p.bg2};
}}
button.pill:hover {{ background-color: {p.bg3}; }}
button.pill.primary-pill {{
    background-color: {p.orange};
    color: #ffffff;
    border-color: transparent;
}}
button.tab {{
    background: transparent; border: none; border-radius: 8px;
    padding: 7px 11px; color: {p.sub}; font-weight: 600;
}}
button.tab:checked {{ background-color: {p.bg2}; color: {p.text}; }}
button.icon {{ padding: 5px; border-radius: 8px; background: transparent; border: none; }}

/* ---------------------------------------------------------------- inputs */
entry, spinbutton, combobox button {{
    background-color: {p.bg2};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 5px 8px;
    box-shadow: none;
}}
entry:focus, spinbutton:focus, combobox button:focus {{ border-color: {p.blue}; }}
spinbutton button {{ background: transparent; border: none; }}
combobox arrow {{ color: {p.sub}; }}
checkbutton, radiobutton {{ color: {p.text}; }}
checkbutton check, radiobutton radio {{
    background-color: {p.bg2};
    border: 1px solid {p.border};
}}
checkbutton check:checked, radiobutton radio:checked {{ background-color: {p.blue}; border-color: {p.blue}; }}
switch {{
    background-color: {p.bg3};
    border-radius: 999px;
}}
switch:checked {{ background-color: {p.green}; }}
switch slider {{ background-color: #ffffff; border-radius: 999px; }}
scale trough {{ background-color: {p.bg3}; border-radius: 999px; }}
scale highlight {{ background-color: {p.green}; border-radius: 999px; }}
scale slider {{ background-color: #ffffff; border: 1px solid {p.border}; }}
progressbar trough {{ background-color: {p.bg3}; border-radius: 999px; min-height: 8px; }}
progressbar progress {{ background-color: {p.green}; border-radius: 999px; min-height: 8px; }}

/* -------------------------------------------------------------- notebook */
notebook {{ background: transparent; border: none; }}
notebook > header {{ background: transparent; border: none; padding: 2px; }}
notebook > header > tabs > tab {{
    background: transparent; border: none; padding: 7px 12px; color: {p.sub};
    border-radius: 8px; font-weight: 600;
}}
notebook > header > tabs > tab:checked {{ background-color: {p.bg2}; color: {p.text}; }}
notebook > stack {{ background: transparent; }}

/* ------------------------------------------------------------ list / rows */
row {{ background: transparent; }}
list {{ background: transparent; }}
.frame {{ border: 1px solid {p.border}; border-radius: 12px; }}
.card {{ background-color: {p.bg2}; border-radius: 12px; padding: 12px; }}
.card-white {{ background-color: {p.bg}; border: 1px solid {p.border}; border-radius: 12px; padding: 12px; }}
.menu-control {{
    background: transparent; border: none; border-radius: 8px;
    padding: 5px 3px; color: {p.sub};
}}
.menu-control:hover {{ background-color: {p.bg2}; color: {p.text}; }}
.header-chip {{ border-radius: 8px; padding: 4px 10px; font-size: 11px; font-weight: 600; }}
.chip-green {{ background-color: alpha({p.green}, 0.12); color: {p.green}; }}
.chip-orange {{ background-color: alpha({p.orange}, 0.13); color: {p.orange}; }}
.chip-purple {{ background-color: alpha({p.purple}, 0.12); color: {p.purple}; }}
.chip-blue {{ background-color: alpha({p.blue}, 0.12); color: {p.blue}; }}
.chip-red {{ background-color: alpha({p.red}, 0.12); color: {p.red}; }}
.link {{ color: {p.blue}; }}

/* ------------------------------------------------------------- scrollbar */
scrollbar {{ background: transparent; }}
scrollbar slider {{ background-color: {p.border}; border-radius: 999px; min-width: 6px; min-height: 6px; }}
scrollbar slider:hover {{ background-color: {p.faint}; }}

/* --------------------------------------------------------------- dialogs */
dialog, messagedialog {{ background-color: {p.bg}; }}
tooltip {{
    background-color: {p.bg3};
    color: {p.text};
    border-radius: 6px;
    padding: 4px 8px;
}}
.popover {{ background-color: {p.bg}; border: 1px solid {p.border}; border-radius: 12px; }}
"""


theme = Theme()

# Extra rules that are easier to keep in one place than in f-string templates.
_EXTRA_CSS = """
.fullscreen-bg { background-color: rgba(10,10,12,0.82); }
.fullscreen label { color: #f5f5f7; }
.fullscreen .muted { color: rgba(255,255,255,0.62); }
.fullscreen .faint { color: rgba(255,255,255,0.42); }
.fullscreen .surface { background-color: rgba(255,255,255,0.10); }
.fullscreen button.pill { background-color: rgba(255,255,255,0.14); color: #f5f5f7; border-color: transparent; }
.fullscreen button.pill:hover { background-color: rgba(255,255,255,0.22); }
.celebration {
    background-image: linear-gradient(160deg, #2b1a3a, #14121a 70%);
    border-radius: 22px;
    border: 1px solid rgba(255,255,255,0.10);
}
.celebration label { color: #f5f5f7; }
.celebration .muted { color: rgba(255,255,255,0.62); }
.celebration .faint { color: rgba(255,255,255,0.42); }
"""


_original_build_css = build_css


def build_css(p: Palette) -> str:  # type: ignore[no-redef]
    return _original_build_css(p) + _EXTRA_CSS
