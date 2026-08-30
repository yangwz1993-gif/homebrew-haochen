#!/bin/bash
# haochen P5 一键打包：读屏执行体 → PyInstaller .app → 资源内嵌 → LSUIElement → 稳定身份签名 → dmg
#
# 用法: bash packaging/build.sh
# 产物: packaging/dist/haochen.app + packaging/dist/haochen-<version>.dmg
#
# 依据：macos-app-dev-reference/MACOS_APP_DEV_REFERENCE.md §6/§7 + packaging/spike 结论。
# 说明：默认用稳定身份 haochen Local Signing（DR 固定，授权跨构建持久）；找不到才回落 ad-hoc。

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"          # packaging/
ROOT="$(cd "$DIR/.." && pwd)"                # 仓库根
APP_DIR="$ROOT/app"
VENV="$APP_DIR/.venv"
APP_NAME="haochen"
# 版本：默认 0.1.10；可用 HAOCHEN_VERSION 覆盖（dmg 名与 Info.plist 版本需一致，勿手改其一）
VERSION="${HAOCHEN_VERSION:-0.1.10}"
DIST="$DIR/dist"
STAGE="$DIST/stage"

echo "==> [1/6] 构建环境（复用 app/.venv，装 pyinstaller）"
UV="${UV:-$HOME/.local/bin/uv}"
"$UV" pip install --python "$VENV/bin/python" pyinstaller >/dev/null
PYI="$VENV/bin/pyinstaller"

echo "==> [2/6] 冻结读屏执行体 haochen-reader（onefile）"
"$PYI" --noconfirm --clean --onefile \
    --name haochen-reader \
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
    --distpath "$DIST" --workpath "$DIR/build/app" --specpath "$DIR/build" \
    "$APP_DIR/run_app.py"

APP="$DIST/$APP_NAME.app"
test -d "$APP"

echo "==> [4/6] 内嵌引擎 + 读屏执行体 → Contents/Resources/"
mkdir -p "$APP/Contents/Resources/engine"
cp "$ROOT/engine/haochen-engine" "$APP/Contents/Resources/engine/haochen-engine"
chmod +x "$APP/Contents/Resources/engine/haochen-engine"
cp "$DIST/reader/haochen-reader" "$APP/Contents/Resources/haochen-reader"
chmod +x "$APP/Contents/Resources/haochen-reader"

echo "==> [5/6] Info.plist（LSUIElement / 版本 / 图标）+ 签名"
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Delete :LSUIElement" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$PLIST" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string haochen" "$PLIST" 2>/dev/null || true

# 签名：默认用「自签受信任」稳定身份（DR 固定→授权跨构建持久）；无稳定身份才回落 ad-hoc。
# 用身份名 + --keychain 签名（-v 会过滤未受信自签证书，故用 find-certificate 探测而非 find-identity -v）；
# set-key-partition-list 已授予 codesign 免密访问私钥；钥匙串可能自动锁定 → 用密码文件显式解锁。
IDENTITY="haochen Local Signing"
KEYCHAIN="$HOME/Library/Keychains/haochen-signing.keychain-db"
KC_PW_FILE="$DIR/signing.keychain-pw"
if [ -f "$KC_PW_FILE" ] && [ -f "$KEYCHAIN" ]; then
    security unlock-keychain -p "$(cat "$KC_PW_FILE")" "$KEYCHAIN" 2>/dev/null || true
fi
ID_HASH="$(security find-certificate -c "$IDENTITY" -Z "$KEYCHAIN" 2>/dev/null \
    | grep -oE 'SHA-1 hash: [0-9A-F]{40}' | head -1 | awk '{print $3}')"
if [ -n "$ID_HASH" ]; then
    echo "    签名：稳定身份 $IDENTITY ($ID_HASH) 【DR 固定，授权跨构建持久】"
    codesign --force --deep --sign "$IDENTITY" --keychain "$KEYCHAIN" "$APP" >/dev/null
else
    echo "    签名：ad-hoc（未在钥匙串找到稳定身份。建议先在 App 内「一键修复签名权限」生成，避免反复授权）"
    codesign --force --deep --sign - "$APP" >/dev/null
fi
# 改完 plist/Resources 必须重签（spike 踩坑 2/3）；--deep 一并签引擎与读屏执行体
codesign --verify --deep --strict "$APP"
echo "    签名验证通过"

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
# 给包名补一个像素小哥图标（Finder 侧栏/桌面用），并验证 .app 与卷的图标都是像素小哥
cp "$ICNS" "$STAGE/$APP_NAME.app/Contents/Resources/haochen.icns" 2>/dev/null || true
test -f "$DMG"

echo ""
echo "==> 构建完成："
echo "    App: $APP"
echo "    DMG: $DMG ($(du -h "$DMG" | cut -f1))"
echo "==> 运行: open \"$APP\""
