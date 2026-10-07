#!/usr/bin/env bash
# Builds "AV Sound Demo.app" from the Swift package (release) and signs it ad hoc.
#
# Usage: sound/demo/macos/build-app.sh [--scratch-path DIR]
#
# The app is written to sound/demo/macos/AVSoundDemo/build/ (ignored by git). It finds the
# repository by walking up from its own location, so no path is stored in the bundle.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
package_dir="$script_dir/AVSoundDemo"
app_name="AV Sound Demo"
executable="AVSoundDemo"
bundle_id="dev.acoustic-vocabularies.sound-demo"
version="0.1.0"

scratch_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scratch-path)
      [[ $# -ge 2 ]] || { echo "build-app.sh: --scratch-path needs a directory" >&2; exit 2; }
      scratch_args=(--scratch-path "$2")
      shift 2
      ;;
    --scratch-path=*)
      scratch_args=(--scratch-path "${1#--scratch-path=}")
      shift
      ;;
    -h | --help)
      sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "build-app.sh: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

swift_build=(swift build --package-path "$package_dir" -c release --product "$executable")
if [[ ${#scratch_args[@]} -gt 0 ]]; then
  swift_build+=("${scratch_args[@]}")
fi

echo "==> ${swift_build[*]}"
"${swift_build[@]}"
bin_dir="$("${swift_build[@]}" --show-bin-path)"

app="$package_dir/build/$app_name.app"
echo "==> Assembling $app"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$bin_dir/$executable" "$app/Contents/MacOS/$executable"

cat >"$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key>
	<string>en</string>
	<key>CFBundleDisplayName</key>
	<string>$app_name</string>
	<key>CFBundleExecutable</key>
	<string>$executable</string>
	<key>CFBundleIdentifier</key>
	<string>$bundle_id</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>CFBundleName</key>
	<string>$app_name</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>$version</string>
	<key>CFBundleVersion</key>
	<string>$version</string>
	<key>LSApplicationCategoryType</key>
	<string>public.app-category.developer-tools</string>
	<key>LSMinimumSystemVersion</key>
	<string>15.0</string>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>NSPrincipalClass</key>
	<string>NSApplication</string>
</dict>
</plist>
PLIST
plutil -lint "$app/Contents/Info.plist" >/dev/null
printf 'APPL????' >"$app/Contents/PkgInfo"

echo "==> Signing (ad hoc)"
codesign --force --sign - "$app"
codesign --verify --strict "$app"

echo "Built: $app"
echo "Open it with: open \"$app\""
echo "Headless check: \"$app/Contents/MacOS/$executable\" --self-check"
