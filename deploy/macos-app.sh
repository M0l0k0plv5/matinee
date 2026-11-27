#!/bin/sh
# Makes Matinee an app on macOS: ~/Applications/Matinee.app, with Matinee's icon, found by Spotlight and kept in the Dock.
# It runs "kino --window" from this checkout: the backend, and Matinee in a Chromium window as big as the screen;
# Cmd+Q closes the window and ends the backend with it. Its log: ~/Library/Logs/Matinee.log.
# Needs Python 3, mpv and Chromium, e.g.:
#   brew install python mpv && brew install --cask ungoogled-chromium
# A second Matinee beside the first (another checkout, to try it out): KINO_NAME="Matinee" ./deploy/macos-app.sh makes
# ~/Applications/Matinee.app, with a port, settings, Chromium profile and log of its own (KINO_PORT, 8766 if not set).
set -eu
DIR="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${KINO_NAME:-Matinee}"
APP="${KINO_APP:-$HOME/Applications/$NAME.app}"
SLUG="$(printf %s "$NAME" | tr 'A-Z' 'a-z' | tr -cs 'a-z0-9' '-' | sed 's/^-*//; s/-*$//')"
# the first Matinee keeps the usual places; a second one gets its own, or both would share port, settings and profile
OWN=""
if [ "$SLUG" != kino ]; then
  OWN="export KINO_PORT=${KINO_PORT:-8766}
export XDG_CONFIG_HOME=\"\$HOME/.config/$SLUG\"
export XDG_CACHE_HOME=\"\$HOME/.cache/$SLUG\"
export XDG_STATE_HOME=\"\$HOME/.local/state/$SLUG\""
fi
CHROMIUM="${KINO_CHROMIUM:-/Applications/Chromium.app/Contents/MacOS/Chromium}"

[ "$(uname)" = Darwin ] || { echo "Only for macOS." >&2; exit 1; }
[ -x "$CHROMIUM" ] || { echo "Chromium not found at $CHROMIUM (brew install --cask ungoogled-chromium)." >&2; exit 1; }
for tool in python3 mpv; do
  command -v "$tool" >/dev/null || echo "Note: $tool is missing (brew install $tool)." >&2
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# The icon: deploy/kino.svg drawn by Chromium, in every size macOS asks for
cat > "$TMP/icon.html" <<HTML
<html><body style="margin:0;background:transparent">
<img src="file://$DIR/deploy/kino.svg" style="width:1024px;height:1024px;display:block"></body></html>
HTML
"$CHROMIUM" --headless --disable-gpu --user-data-dir="$TMP/profile" --no-first-run --use-mock-keychain \
  --default-background-color=00000000 --hide-scrollbars --window-size=1024,1024 --allow-file-access-from-files \
  --screenshot="$TMP/icon.png" "file://$TMP/icon.html" >/dev/null 2>&1
mkdir "$TMP/Matinee.iconset"
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" "$TMP/icon.png" --out "$TMP/Matinee.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z "$((s * 2))" "$((s * 2))" "$TMP/icon.png" --out "$TMP/Matinee.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$TMP/Matinee.iconset" -o "$APP/Contents/Resources/Matinee.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>local.$SLUG.launcher</string>
  <key>CFBundleExecutable</key><string>Matinee</string>
  <key>CFBundleIconFile</key><string>Matinee</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST

# Started from Finder or the Dock, an app gets no shell's PATH: Homebrew's tools added
cat > "$APP/Contents/MacOS/Matinee" <<LAUNCHER
#!/bin/sh
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
export KINO_CHROMIUM="$CHROMIUM"
$OWN
LOG="\$HOME/Library/Logs/$NAME.log"
mkdir -p "\$(dirname "\$LOG")"
# the log kept small: past 5 MB it starts anew at the next start, the one before kept with .1 added
if [ -f "\$LOG" ] && [ "\$(stat -f %z "\$LOG")" -gt 5000000 ]; then mv -f "\$LOG" "\$LOG.1"; fi
"$DIR/kino" --window >>"\$LOG" 2>&1 || osascript -e 'display alert "$NAME" message "$NAME stopped with an error. See ~/Library/Logs/$NAME.log." as critical' >/dev/null 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/Matinee"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" || true
echo "Installed $APP"
