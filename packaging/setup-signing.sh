#!/bin/bash
# haochen 自签受信任代码签名证书（一次性设置，仅最后一步输一次管理员密码）
#
# 产物：
#   专用钥匙串       ~/Library/Keychains/haochen-signing.keychain-db
#   签名身份         haochen Local Signing（受信任 for codeSign，DR 稳定）
#   钥匙串密码       随机生成，存 packaging/signing.keychain-pw（chmod 600，build.sh 读取）
#
# 作用：让 .app 有**稳定**的 code 签名 identity → macOS TCC「辅助功能」授权按 DR 认，
#       授权跨构建持久，不再「每次重建都要重授权」。
#
# 用法：bash packaging/setup-signing.sh
#       （最后 `sudo security add-trusted-cert` 会提示输入**管理员密码**——这是唯一需要你的步骤）
# 之后：bash packaging/build.sh  会自动用该身份签名（见 build.sh §5）。

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
IDENTITY="haochen Local Signing"
KEYCHAIN="$HOME/Library/Keychains/haochen-signing.keychain-db"
KC_PW_FILE="$DIR/signing.keychain-pw"
CERT="$DIR/signing-cert.pem"
KEY="$DIR/signing-key.pem"
P12="$DIR/signing-id.p12"
KC_PW="$(openssl rand -hex 20)"

echo "==> 生成自签证书（codeSigning EKU，3 年）"
openssl req -new -newkey rsa:2048 -x509 -nodes -days 1095 \
  -subj "/CN=$IDENTITY" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature" \
  -addext "extendedKeyUsage=codeSigning" \
  -keyout "$KEY" -out "$CERT" 2>/dev/null

echo "==> 导出 p12（legacy 兼容 macOS security）"
openssl pkcs12 -export -legacy -out "$P12" \
  -inkey "$KEY" -in "$CERT" -passout pass:"$KC_PW" 2>/dev/null

echo "==> 创建/解锁专用钥匙串"
security create-keychain -p "$KC_PW" "$KEYCHAIN" 2>/dev/null || true
security set-keychain-settings "$KEYCHAIN"   # 不自动锁定（防锁死导致 codesign 挂起）
security unlock-keychain -p "$KC_PW" "$KEYCHAIN"

echo "==> 导入身份"
security import "$P12" -k "$KEYCHAIN" -P "$KC_PW" \
  -T /usr/bin/codesign -T /usr/bin/security 2>&1 | tail -1

echo "==> 允许 codesign 免弹窗访问私钥"
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KC_PW" "$KEYCHAIN"
echo "$KC_PW" > "$KC_PW_FILE" && chmod 600 "$KC_PW_FILE"

echo "==> 把证书设为「受信任 for 代码签名」（唯一需要管理员密码的步骤）"
echo "    即将弹出管理员密码输入（security 代理）……"
security add-trusted-cert -d -r trustRoot -p codeSign -k "$KEYCHAIN" "$CERT"

echo "==> 验证签名身份"
security unlock-keychain -p "$KC_PW" "$KEYCHAIN"
security find-identity -p codesigning -v "$KEYCHAIN"

echo ""
echo "==> 完成。签名身份：$IDENTITY"
echo "    身份名（用身份名称或指纹）："
security find-identity -p codesigning -v "$KEYCHAIN" | grep -o '"'"$IDENTITY"'"' | head -1
echo "    钥匙串密码已存：$KC_PW_FILE"
echo "    下一步：bash packaging/build.sh  即用稳定身份签名；授权跨构建持久。"
