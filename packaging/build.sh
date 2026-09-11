#!/bin/bash
# haochen reproducible macOS package pipeline.
#
# Release (default): Developer ID + hardened runtime + Apple notarization are mandatory.
# Development: HAOCHEN_BUILD_MODE=development bash packaging/build.sh (ad-hoc, never distribute).
# Output: packaging/dist/haochen.app + packaging/dist/haochen-<version>.dmg

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"          # packaging/
ROOT="$(cd "$DIR/.." && pwd)"                # 仓库根
APP_DIR="$ROOT/app"
VENV="$APP_DIR/.venv"
APP_NAME="haochen"
VERSION_FILE="$ROOT/VERSION"
ENGINE_MANIFEST="$ROOT/engine/package.json"
VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
MARKETING_VERSION="${VERSION%%-*}"
BUNDLE_BUILD="${HAOCHEN_BUILD_NUMBER:-$(printf '%s' "$VERSION" | sed -nE 's/.*\.([0-9]+)$/\1/p')}"
BUNDLE_BUILD="${BUNDLE_BUILD:-0}"
BUILD_MODE="${HAOCHEN_BUILD_MODE:-release}"
SIGNING_IDENTITY="${HAOCHEN_SIGNING_IDENTITY:-}"
NOTARY_PROFILE="${HAOCHEN_NOTARY_PROFILE:-}"
DIST="$DIR/dist"

case "$BUILD_MODE" in
  development) SIGNING_IDENTITY="-" ;;
  release)
    : "${HAOCHEN_SIGNING_IDENTITY:?Release builds require a Developer ID Application identity from Keychain}"
    : "${HAOCHEN_NOTARY_PROFILE:?Release builds require a notarytool Keychain profile}"
    if [[ "$SIGNING_IDENTITY" != "Developer ID Application:"* ]]; then
      echo "ERROR: HAOCHEN_SIGNING_IDENTITY must start with 'Developer ID Application:'" >&2
      exit 1
    fi
    ;;
  legacy)
    # Legacy 分发：稳定自签身份优先（无 Developer ID/公证）。用户已知情选择此模式用于 v0.3.0。
    LEGACY_IDENTITY="haochen Local Signing"
    LEGACY_KEYCHAIN="$HOME/Library/Keychains/haochen-signing.keychain-db"
    if [ -f "$LEGACY_KEYCHAIN" ]; then
        # 钥匙串可能锁定：口令仅从用户本机数据目录的既有文件运行时读取（不入仓）
        LEGACY_PW_FILE="$HOME/Library/Application Support/haochen/signing/signing.keychain-pw"
        if [ -f "$LEGACY_PW_FILE" ]; then
            security unlock-keychain -p "$(cat "$LEGACY_PW_FILE")" "$LEGACY_KEYCHAIN" 2>/dev/null || true
        fi
        ID_HASH="$(security find-identity -p codesigning "$LEGACY_KEYCHAIN" 2>/dev/null \
            | grep "\"$LEGACY_IDENTITY\"" | head -1 | awk '{print $2}' || true)"
    else
        ID_HASH="$(security find-identity -p codesigning 2>/dev/null \
            | grep "\"$LEGACY_IDENTITY\"" | head -1 | awk '{print $2}' || true)"
    fi
    if [ -n "$ID_HASH" ]; then
        SIGNING_IDENTITY="$ID_HASH"
        echo "    legacy 签名身份：$LEGACY_IDENTITY ($ID_HASH)"
    else
        SIGNING_IDENTITY="-"
        echo "    legacy 未找到自签身份，回落 ad-hoc"
    fi
    ;;
  *) echo "ERROR: HAOCHEN_BUILD_MODE must be development, release or legacy" >&2; exit 1 ;;
esac
STAGE="$DIST/stage"

echo "==> [1/6] 验证锁定的构建环境与版本元数据"
UV="${UV:-$HOME/.local/bin/uv}"
export UV_PROJECT_ENVIRONMENT="$VENV"
"$UV" sync --frozen >/dev/null
"$VENV/bin/python" "$ROOT/scripts/version.py" check
PYI="$VENV/bin/pyinstaller"
test -x "$PYI"
test -f "$ENGINE_MANIFEST"

echo "==> [2/6] 冻结读屏执行体 haochen-reader（onefile）"
"$PYI" --noconfirm --clean --onefile \
    --name haochen-reader \
    --paths "$APP_DIR" \
    --distpath "$DIST/reader" --workpath "$DIR/build/reader" --specpath "$DIR/build" \
    "$APP_DIR/reader/haochen_reader.py" >/dev/null
test -x "$DIST/reader/haochen-reader"

echo "==> [3/6] 生成 App 图标（像素小哥，视觉规范色）"
ICON_BASE="$DIR/iconsrc/icon_1024.png"
ICONSET="$DIR/app.iconset"
ICNS="$DIR/haochen.icns"
if [ ! -f "$ICNS" ]; then
    rm -rf "$ICONSET" && mkdir -p "$ICONSET" "$DIR/iconsrc"
    "$VENV/bin/python" - <<PY
from PIL import Image, ImageDraw
BOSS=1024
CREAM=(253,246,227,255); BORDER=(43,43,34,255)
img=Image.new("RGBA",(BOSS,BOSS),(0,0,0,0)); d=ImageDraw.Draw(img)
m=64; d.rounded_rectangle((m,m,BOSS-m,BOSS-m), radius=220, fill=CREAM, outline=BORDER, width=14)
pet=Image.open("$APP_DIR/assets/pet/idle.png").convert("RGBA")
sz=640; pet=pet.resize((sz,sz), Image.Resampling.NEAREST)
img.alpha_composite(pet, ((BOSS-sz)//2, (BOSS-sz)//2-28))
img.save("$ICON_BASE")
for s in (16,32,128,256,512,1024):
    img.resize((s,s), Image.Resampling.LANCZOS).save(f"$ICONSET/icon_{s}x{s}.png")
for s in ((16,32),(32,64),(128,256),(256,512),(512,1024)):
    img.resize(s, Image.Resampling.LANCZOS).save(f"$ICONSET/icon_{s[0]}x{s[1]}@2x.png")
PY
    iconutil -c icns "$ICONSET" -o "$ICNS"
fi

echo "==> [4/6] PyInstaller onedir 直出 haochen.app"
"$PYI" --noconfirm --clean --onedir --windowed \
    --name "$APP_NAME" \
    --paths "$APP_DIR" \
    --icon "$ICNS" \
    --osx-bundle-identifier "com.haochen.app" \
    --add-data "$APP_DIR/assets:assets" \
    --add-data "$ROOT/config:config" \
    --add-data "$APP_DIR/ext:ext" \
    --add-data "$VERSION_FILE:." \
    --distpath "$DIST" --workpath "$DIR/build/app" --specpath "$DIR/build" \
    "$APP_DIR/run_app.py"

APP="$DIST/$APP_NAME.app"
test -d "$APP"

echo "==> [4/6] 内嵌引擎 + 读屏执行体 → Contents/Resources/"
mkdir -p "$APP/Contents/Resources/engine"
cp "$ROOT/engine/haochen-engine" "$APP/Contents/Resources/engine/haochen-engine"
chmod +x "$APP/Contents/Resources/engine/haochen-engine"
cp "$ENGINE_MANIFEST" "$APP/Contents/Resources/engine/package.json"
cp "$DIST/reader/haochen-reader" "$APP/Contents/Resources/haochen-reader"
chmod +x "$APP/Contents/Resources/haochen-reader"

echo "==> [5/6] Info.plist（LSUIElement / 版本 / 图标）+ 签名"
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Delete :LSUIElement" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $MARKETING_VERSION" "$PLIST" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $MARKETING_VERSION" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $BUNDLE_BUILD" "$PLIST" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUNDLE_BUILD" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string haochen" "$PLIST" 2>/dev/null || true

SIGN_ARGS=(--force --sign "$SIGNING_IDENTITY")
if [ -n "${HAOCHEN_KEYCHAIN:-}" ]; then
    SIGN_ARGS+=(--keychain "$HAOCHEN_KEYCHAIN")
fi
case "$BUILD_MODE" in
  release) SIGN_ARGS+=(--options runtime --timestamp) ;;
  development) SIGN_ARGS+=(--timestamp=none) ;;
  legacy)
    # Legacy 模式：无 hardened runtime/timestamp 要求；自签身份在用户搜索列表中。
    if [ -z "${HAOCHEN_KEYCHAIN:-}" ] && [ -f "$LEGACY_KEYCHAIN" ]; then
        SIGN_ARGS+=(--keychain "$LEGACY_KEYCHAIN")
    fi
    ;;
esac

sign_macho() {
    local target="$1"
    /usr/bin/codesign "${SIGN_ARGS[@]}" "$target" >/dev/null
}

# Sign nested Mach-O files from the inside out. The Bun engine needs narrowly
# scoped JIT entitlements; the PyInstaller app itself does not receive them.
while IFS= read -r -d '' binary; do
    if /usr/bin/file "$binary" | grep -q 'Mach-O'; then
        sign_macho "$binary"
    fi
done < <(find "$APP/Contents" -type f ! -path '*/Resources/engine/haochen-engine' -print0)
/usr/bin/codesign "${SIGN_ARGS[@]}" --entitlements "$DIR/engine.entitlements" \
    "$APP/Contents/Resources/engine/haochen-engine" >/dev/null
/usr/bin/codesign "${SIGN_ARGS[@]}" --entitlements "$DIR/app.entitlements" "$APP" >/dev/null
/usr/bin/codesign --verify --deep --strict "$APP"

if [ "$BUILD_MODE" = "release" ]; then
    HAOCHEN_NOTARY_PROFILE="$NOTARY_PROFILE" "$DIR/notarize.sh" app "$APP"
fi

echo "==> [6/6] dmg（含 Applications 拖装链接 + 像素小哥卷图标）"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/$APP_NAME.app"
ln -s /Applications "$STAGE/Applications"
# 卷图标：让 Finder 里 dmg 挂上后显示像素小哥（默认 dmg 图标不是像素小哥）
cp "$ICNS" "$STAGE/.VolumeIcon.icns"
/usr/bin/SetFile -a C "$STAGE" 2>/dev/null || true
DMG="$DIST/$APP_NAME-$VERSION.dmg"
rm -f "$DMG"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
test -f "$DMG"
if [ "$BUILD_MODE" = "release" ]; then
    /usr/bin/codesign "${SIGN_ARGS[@]}" "$DMG" >/dev/null
    HAOCHEN_NOTARY_PROFILE="$NOTARY_PROFILE" "$DIR/notarize.sh" dmg "$DMG"
fi

echo ""
echo "==> 构建完成："
echo "    App: $APP"
echo "    DMG: $DMG ($(du -h "$DMG" | cut -f1))"
echo "==> 运行: open \"$APP\""
