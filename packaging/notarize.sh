#!/usr/bin/env bash
# Submit a Developer ID signed artifact with credentials stored by notarytool in Keychain.
set -euo pipefail

MODE="${1:-}"
ARTIFACT="${2:-}"
PROFILE="${HAOCHEN_NOTARY_PROFILE:-}"

[ "$MODE" = "app" ] || [ "$MODE" = "dmg" ] || {
  echo "usage: HAOCHEN_NOTARY_PROFILE=<profile> $0 app|dmg <artifact>" >&2
  exit 2
}
if [ -z "$ARTIFACT" ] || [ ! -e "$ARTIFACT" ]; then
  echo "artifact not found: $ARTIFACT" >&2
  exit 2
fi
if [ -z "$PROFILE" ]; then
  echo "HAOCHEN_NOTARY_PROFILE is required" >&2
  exit 2
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/haochen-notary.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
SUBMISSION="$ARTIFACT"
if [ "$MODE" = "app" ]; then
  SUBMISSION="$TMP/haochen.zip"
  /usr/bin/ditto -c -k --keepParent "$ARTIFACT" "$SUBMISSION"
fi

RESULT="$TMP/result.json"
xcrun notarytool submit "$SUBMISSION" \
  --keychain-profile "$PROFILE" \
  --wait \
  --output-format json >"$RESULT"
python3 - "$RESULT" <<'PY'
import json
import sys
from pathlib import Path
result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if result.get("status") != "Accepted":
    raise SystemExit(f"notarization was not accepted: {result}")
print(f"notarization accepted: {result.get('id', '<unknown>')}")
PY

xcrun stapler staple "$ARTIFACT"
xcrun stapler validate "$ARTIFACT"
if [ "$MODE" = "app" ]; then
  /usr/sbin/spctl --assess --type execute --verbose=2 "$ARTIFACT"
else
  /usr/sbin/spctl --assess --type open --context context:primary-signature --verbose=2 "$ARTIFACT"
fi
