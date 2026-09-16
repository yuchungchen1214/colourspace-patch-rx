#!/bin/bash

set -e

APP_NAME="ColourSpace Patch Rx"
VERSION=$(grep -E 'APP_VERSION *= *"' src/main.py | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
SHORT_VERSION="${VERSION%%-*}"
BUILD_VERSION="30001"
DMG_NAME="${APP_NAME// /}-${VERSION}-macos-arm.dmg"
echo "📦 Version: $VERSION"

echo "🔧 Cleaning..."
rm -rf build dist dmg_temp

echo "📦 Building app..."
./.venv/bin/python -m PyInstaller \
--windowed \
--name "$APP_NAME" \
--icon assets/icon.icns \
--add-data "assets:assets" \
--add-data "LICENSE:." \
--add-data "THIRD_PARTY_NOTICES.md:." \
--add-binary "vendor/argyll/macos-arm64/bin/spotread:argyll" \
--add-binary "vendor/argyll/macos-arm64/bin/ccxxmake:argyll" \
--add-data "vendor/argyll/macos-arm64/NOTICE.md:argyll" \
--add-data "vendor/argyll/macos-arm64/License.txt:argyll" \
--add-data "vendor/argyll/macos-arm64/License2.txt:argyll" \
--add-data "vendor/argyll/macos-arm64/BUILDING.md:argyll" \
--add-data "vendor/argyll/macos-arm64/source-patch:argyll/source-patch" \
src/main.py

APP_PATH="dist/$APP_NAME.app"
PLIST="$APP_PATH/Contents/Info.plist"

echo "🧹 Removing quarantine..."
xattr -cr "$APP_PATH"

echo "📝 Setting version..."
/usr/libexec/PlistBuddy -c "Delete :CFBundleShortVersionString" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $SHORT_VERSION" "$PLIST"

/usr/libexec/PlistBuddy -c "Delete :CFBundleVersion" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $BUILD_VERSION" "$PLIST"

echo "🔐 Codesigning..."
codesign --force --deep --sign - "$APP_PATH"

echo "📁 Preparing DMG..."
mkdir dmg_temp
cp -R "$APP_PATH" dmg_temp/

echo "💿 Creating DMG..."
create-dmg \
--volname "$APP_NAME" \
--window-pos 200 120 \
--window-size 800 400 \
--icon-size 100 \
--icon "$APP_NAME.app" 200 150 \
--icon "Applications" 600 150 \
--background assets/background.png \
--hide-extension "$APP_NAME.app" \
--app-drop-link 600 150 \
"$DMG_NAME" \
dmg_temp/

echo "🧹 Cleaning temp..."
rm -rf dmg_temp

echo "✅ Done!"
echo "DMG: $DMG_NAME"
