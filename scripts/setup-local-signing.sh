#!/bin/bash
# 一次性创建 haochen 本地固定签名身份，修复"重装后录屏授权/Chrome 桥接失效"。
# 只新增一个自签证书到独立钥匙串；不碰登录钥匙串、不读任何密钥。
set -euo pipefail

KC="$HOME/Library/Keychains/haochen-signing.keychain-db"
PW="haochen-local-signing"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> 1/4 生成自签代码签名证书"
cat > "$TMP/o.cnf" <<'EOF'
[req]
distinguished_name=dn
x509_extensions=v3
prompt=no
[dn]
CN=haochen Local Signing
[v3]
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
basicConstraints=critical,CA:false
EOF
openssl req -x509 -newkey rsa:2048 -keyout "$TMP/k.pem" -out "$TMP/c.pem" \
  -days 3650 -nodes -config "$TMP/o.cnf" -extensions v3 >/dev/null 2>&1

echo "==> 2/4 打包 PKCS#12（-legacy 兼容 Apple security import）"
# 新版 openssl 默认算法 Apple 验不了，必须 -legacy；LibreSSL 无此项则回退。
openssl pkcs12 -export -legacy -out "$TMP/id.p12" -inkey "$TMP/k.pem" -in "$TMP/c.pem" \
  -passout "pass:$PW" -name "haochen Local Signing" >/dev/null 2>&1 || \
openssl pkcs12 -export -out "$TMP/id.p12" -inkey "$TMP/k.pem" -in "$TMP/c.pem" \
  -passout "pass:$PW" -name "haochen Local Signing" >/dev/null 2>&1

echo "==> 3/4 建独立钥匙串并导入身份"
security delete-keychain "$KC" 2>/dev/null || true
security create-keychain -p "$PW" "$KC"
security set-keychain-settings "$KC"
security unlock-keychain -p "$PW" "$KC"
# shellcheck disable=SC2046
security list-keychains -d user -s "$KC" $(security list-keychains -d user | sed 's/"//g')
security import "$TMP/id.p12" -k "$KC" -P "$PW" -T /usr/bin/codesign -A
security set-key-partition-list -S apple-tool:,apple: -s -k "$PW" "$KC" >/dev/null 2>&1 || true

echo "==> 4/4 写入 build.sh 需要的钥匙串口令文件"
mkdir -p "$HOME/Library/Application Support/haochen/signing"
printf '%s' "$PW" > "$HOME/Library/Application Support/haochen/signing/signing.keychain-pw"

echo ""
if security find-identity -p codesigning "$KC" | grep -q "haochen Local Signing"; then
  echo "✅ 成功：已创建固定签名身份 'haochen Local Signing'"
  security find-identity -p codesigning "$KC" | grep "haochen Local Signing"
else
  echo "❌ 未找到身份，请把上面输出发给我"
  exit 1
fi
