#!/usr/bin/env bash
# User-level install of the LocalBridge desktop app. No sudo, no system changes.
# Uses the project's venv (editable install), so `git pull` updates the app.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/fedora/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv --system-site-packages "$VENV"
fi
"$VENV/bin/pip" install -q -e "$ROOT/fedora"

BIN="$HOME/.local/bin"; APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$BIN" "$APPS" "$ICONS"
cp "$ROOT/packaging/io.github.localbridge.LocalBridge.svg" "$ICONS/"
gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
cat > "$BIN/localbridge" <<LAUNCH
#!/usr/bin/env bash
exec "$VENV/bin/python" -m localbridge "\$@"
LAUNCH
chmod +x "$BIN/localbridge"
sed "s|^Exec=.*|Exec=$BIN/localbridge|" "$ROOT/packaging/io.github.localbridge.LocalBridge.desktop" \
  > "$APPS/io.github.localbridge.LocalBridge.desktop"
update-desktop-database "$APPS" 2>/dev/null || true
echo "Installed. Launch from the app menu (LocalBridge) or run: $BIN/localbridge"
