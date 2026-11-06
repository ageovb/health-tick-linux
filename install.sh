#!/usr/bin/env bash
# Install HealthTick for the current user (no root required).
#
#   ./install.sh            install into ~/.local
#   PREFIX=/usr/local ./install.sh
#   ./install.sh --uninstall
#   ./install.sh --purge    uninstall and delete the local database
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
APP_DIR="$PREFIX/share/health-tick/app"
BIN_DIR="$PREFIX/bin"
ICON_DIR="$PREFIX/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="$PREFIX/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/health-tick.desktop"
LAUNCHER="$BIN_DIR/health-tick"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/health-tick"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/health-tick.desktop"

uninstall() {
    local purge="${1:-}"
    rm -rf "$APP_DIR"
    rm -f "$LAUNCHER" "$DESKTOP_FILE" "$ICON_DIR/health-tick.svg" "$AUTOSTART"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true
    fi
    if [ "$purge" = "purge" ]; then
        rm -rf "$DATA_DIR"
        echo "Removed application and data directory: $DATA_DIR"
    else
        echo "Removed application. Your data is kept in: $DATA_DIR"
    fi
}

case "${1:-}" in
    --uninstall) uninstall ;;
    --purge) uninstall purge ;;
    --help|-h)
        sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    "")
        ;;
    *)
        echo "Unknown option: $1" >&2
        exit 2
        ;;
esac

if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "--purge" ]; then
    exit 0
fi

# Use the same interpreter the launcher's shebang points at; a version-manager
# python3 earlier on PATH may not have the system GI bindings.
PYTHON="${PYTHON:-/usr/bin/python3}"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3 || true)"
fi
[ -n "$PYTHON" ] || {
    echo "python3 is required." >&2
    exit 1
}

"$PYTHON" - <<'PY'
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk  # noqa: F401
except Exception as exc:  # pragma: no cover - installer diagnostics
    raise SystemExit(
        "GTK 3 Python bindings are required (python3-gi / python3-gobject). "
        f"Import failed: {exc}"
    )
PY

echo "Installing HealthTick into $APP_DIR"
install -d "$APP_DIR/bin" "$BIN_DIR" "$ICON_DIR" "$DESKTOP_DIR"
rm -rf "$APP_DIR/healthtick"
cp -a "$SRC/healthtick" "$APP_DIR/healthtick"
find "$APP_DIR/healthtick" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
install -m 755 "$SRC/bin/health-tick" "$APP_DIR/bin/health-tick"
install -m 644 "$SRC/healthtick/assets/health-tick.svg" "$ICON_DIR/health-tick.svg"
install -m 644 "$SRC/health-tick.desktop" "$DESKTOP_FILE"

# Small wrapper so the launcher keeps working if the app directory moves.
cat > "$LAUNCHER" <<EOF
#!/bin/sh
exec "$APP_DIR/bin/health-tick" "\$@"
EOF
chmod 755 "$LAUNCHER"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true
fi

echo
echo "Installed. Start it with: health-tick"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "Note: $BIN_DIR is not on your PATH; add it or run $LAUNCHER directly." ;;
esac
echo "Data is stored in ${XDG_DATA_HOME:-$HOME/.local/share}/health-tick/data.db"
