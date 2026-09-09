#!/usr/bin/env bash
# Read-only release gate. It never reads, prints, or stores credential secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MODE="${1:-help}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

pass() {
  echo "PASS: $*"
}

check_source() {
  local version branch
  version="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
    fail "VERSION 必须是稳定版本，当前为 $version"

  branch="$(git -C "$REPO_ROOT" branch --show-current)"
  [ "$branch" = "main" ] || fail "正式发布必须从 main 构建，当前为 $branch"
  [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || \
    fail "工作区有未提交修改"
  "$REPO_ROOT/app/.venv/bin/python" "$REPO_ROOT/scripts/version.py" check
  grep -Fq "## v$version" "$REPO_ROOT/docs/CHANGELOG.md" || \
    fail "CHANGELOG 缺少 v$version 条目"
  git -C "$REPO_ROOT" rev-parse --verify "refs/tags/v$version" >/dev/null 2>&1 && \
    fail "标签 v$version 已存在；禁止覆盖发布标签"
  pass "源码、分支、版本和变更记录可进入正式构建"
}

check_credentials() {
  local identity profile identities
  identity="${HAOCHEN_SIGNING_IDENTITY:-}"
  profile="${HAOCHEN_NOTARY_PROFILE:-}"
  [ -n "$identity" ] || fail "请设置 HAOCHEN_SIGNING_IDENTITY（只填证书名称）"
  [ -n "$profile" ] || fail "请设置 HAOCHEN_NOTARY_PROFILE（只填 Keychain profile 名）"
  [[ "$identity" = "Developer ID Application:"* ]] || \
    fail "签名身份必须是 Developer ID Application"

  identities="$(security find-identity -v -p codesigning)"
  grep -Fq "\"$identity\"" <<<"$identities" || \
    fail "Keychain 中找不到指定的 Developer ID Application 身份"
  xcrun notarytool history \
    --keychain-profile "$profile" \
    --output-format json >/dev/null
  pass "Developer ID 身份与 Apple 公证 profile 均可用"
}

check_artifacts() {
  local app dmg version expected_dmg bundle_version embedded_version authority team
  app="${2:-}"
  dmg="${3:-}"
  [ -d "$app" ] || fail "App 不存在：$app"
  [ -f "$dmg" ] || fail "DMG 不存在：$dmg"
  version="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"
  expected_dmg="haochen-$version.dmg"
  [ "$(basename "$dmg")" = "$expected_dmg" ] || \
    fail "DMG 文件名应为 $expected_dmg"

  bundle_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
    "$app/Contents/Info.plist")"
  [ "$bundle_version" = "$version" ] || \
    fail "App 版本为 $bundle_version，预期为 $version"
  embedded_version="$(tr -d '[:space:]' < "$app/Contents/Resources/VERSION")"
  [ "$embedded_version" = "$version" ] || \
    fail "App 内嵌 VERSION 为 $embedded_version，预期为 $version"

  codesign --verify --deep --strict "$app"
  codesign --verify --strict "$dmg"
  authority="$(codesign -dv --verbose=4 "$app" 2>&1)"
  grep -Fq "Authority=Developer ID Application:" <<<"$authority" || \
    fail "App 不是 Developer ID Application 签名"
  team="$(sed -n 's/^TeamIdentifier=//p' <<<"$authority")"
  if [ -z "$team" ] || [ "$team" = "not set" ]; then
    fail "App 缺少 TeamIdentifier"
  fi
  xcrun stapler validate "$app"
  xcrun stapler validate "$dmg"
  spctl --assess --type execute --verbose=2 "$app"
  spctl --assess --type open --context context:primary-signature --verbose=2 "$dmg"
  shasum -a 256 "$dmg"
  pass "App 与 DMG 的签名、公证票据和 Gatekeeper 验证全部通过"
}

check_legacy_artifacts() {
  local app dmg version expected_dmg bundle_version embedded_version signature digest cask
  app="${2:-}"
  dmg="${3:-}"
  [ -d "$app" ] || fail "App 不存在：$app"
  [ -f "$dmg" ] || fail "DMG 不存在：$dmg"
  version="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
    fail "legacy 发布也必须使用稳定版本，当前为 $version"
  expected_dmg="haochen-$version.dmg"
  [ "$(basename "$dmg")" = "$expected_dmg" ] || \
    fail "DMG 文件名应为 $expected_dmg"

  bundle_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
    "$app/Contents/Info.plist")"
  [ "$bundle_version" = "$version" ] || \
    fail "App 版本为 $bundle_version，预期为 $version"
  embedded_version="$(tr -d '[:space:]' < "$app/Contents/Resources/VERSION")"
  [ "$embedded_version" = "$version" ] || \
    fail "App 内嵌 VERSION 为 $embedded_version，预期为 $version"

  codesign --verify --deep --strict "$app"
  signature="$(codesign -dv --verbose=4 "$app" 2>&1)"
  grep -Fq "Authority=haochen Local Signing" <<<"$signature" || \
    fail "App 未使用稳定的 haochen Local Signing 身份"
  grep -Fq "Signature=adhoc" <<<"$signature" && \
    fail "ad-hoc 签名不能进入 legacy 发布通道"
  hdiutil verify "$dmg" >/dev/null

  digest="$(shasum -a 256 "$dmg" | awk '{print $1}')"
  cask="$REPO_ROOT/Casks/haochen.rb"
  grep -Fq "version \"$version\"" "$cask" || fail "Cask 版本与 VERSION 不一致"
  grep -Fq "sha256 \"$digest\"" "$cask" || fail "Cask SHA-256 与 DMG 不一致"
  grep -Fq "postflight_steps" "$cask" || fail "legacy Cask 缺少 postflight"
  grep -Fq "com.apple.quarantine" "$cask" || fail "legacy Cask 缺少 quarantine 处理"
  ruby -c "$cask" >/dev/null
  printf '%s  %s\n' "$digest" "$dmg"
  pass "legacy App 自签名、DMG 完整性、版本与 Cask SHA-256 全部一致"
}

case "$MODE" in
  source) check_source ;;
  credentials) check_credentials ;;
  artifacts) check_artifacts "$@" ;;
  legacy-artifacts) check_legacy_artifacts "$@" ;;
  *)
    echo "usage: $0 source|credentials|artifacts|legacy-artifacts <app> <dmg>" >&2
    exit 2
    ;;
esac
