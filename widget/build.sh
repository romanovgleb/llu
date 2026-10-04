#!/usr/bin/env bash
# Generate the Xcode project, build, and install LLUWidget.app to /Applications.
# Requires full Xcode (26.4 works on macOS 26.5) and xcodegen (auto-installed).
set -euo pipefail
cd "$(dirname "$0")"

command -v xcodegen >/dev/null 2>&1 || brew install xcodegen
xcodegen generate

xcodebuild -project LLUWidget.xcodeproj \
  -scheme LLUWidgetApp \
  -configuration Release \
  -derivedDataPath build \
  CODE_SIGN_IDENTITY="-" CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM="" \
  build

rm -rf "/Applications/LLUWidget.app"
cp -R "build/Build/Products/Release/LLUWidget.app" /Applications/
open /Applications/LLUWidget.app
echo "Installed to /Applications/LLUWidget.app."
echo "Open Notification Center → Edit Widgets → add 'LLM Usage'."
