#!/bin/bash
# Install Tranquility Base Director beside Prod (25 Sep).
#
# Usage: scripts/install-director.sh [--launch]
#
# Builds the committed, clean tree with bundle-director.sh, refuses anything
# whose signature would reset macOS permissions, installs to
# /Applications/Tranquility Base Director.app, and seeds the app's own data
# folder on first install. Never stops, replaces or reconfigures Prod or Dev:
# this app is not a lane, so it takes no deployment lock and no preview
# reservation (those guard the lanes that replace each other).
set -euo pipefail
cd "$(dirname "$0")/.."
. "$(dirname "$0")/lib/paths.sh"

APP_NAME="Tranquility Base Director"
BUNDLE_ID="com.robertnowell.voice-dispatch.director"
DEST="/Applications/$APP_NAME.app"
PROD_SUPPORT="$HOME/Library/Application Support/VoiceDispatch"
SUPPORT="$HOME/Library/Application Support/VoiceDispatch-Director"
LAUNCH=0
[ "${1:-}" = "--launch" ] && LAUNCH=1
fail() { echo "✗ $*" >&2; exit 1; }

[ -z "$(git status --porcelain)" ] || { git status --short >&2; fail "the tree is dirty; commit first"; }
scripts/bundle-director.sh debug
SRC="$(tb_bundle_dir debug)/$APP_NAME.app"
[ -d "$SRC" ] || fail "no bundle at $SRC"

read_plist() { /usr/libexec/PlistBuddy -c "Print :$1" "$SRC/Contents/Info.plist" 2>/dev/null; }
[ "$(read_plist CFBundleIdentifier)" = "$BUNDLE_ID" ] || fail "wrong bundle id"
[ "$(read_plist TBAppChannel)" = "director" ] || fail "wrong channel"
[ "$(read_plist TBUpdatesEnabled)" = "false" ] || fail "updates are on"
[ "$(read_plist TBSupportFolder)" = "VoiceDispatch-Director" ] || fail "not its own data folder"
[ "$(read_plist TBHotkeys)" = "false" ] || fail "the hotkey tap is on; Option is Prod's"
[ "$(read_plist TBHotkeysOptional)" = "true" ] || fail "the optional Option hold is not marked optional"
[ "$(read_plist TBClaimsProductSchemes)" = "false" ] || fail "it would take Prod's links"
[ "$(read_plist TBManagesHooks)" = "false" ] || fail "it would rewrite Prod's hooks"
[ "$(read_plist CFBundleURLTypes:0:CFBundleURLSchemes:0)" = "tbdirector" ] || fail "its own scheme is missing"
if /usr/libexec/PlistBuddy -c "Print :CFBundleURLTypes:0:CFBundleURLSchemes:1" "$SRC/Contents/Info.plist" >/dev/null 2>&1; then
  fail "it claims a second URL scheme; only tbdirector is its own"
fi

codesign --verify --deep --strict "$SRC" 2>/dev/null || fail "signature does not verify"
SIGNING=$(codesign -dv --verbose=2 "$SRC" 2>&1 || true)
case "$SIGNING" in
  *$'\nAuthority='*|Authority=*) ;;
  *) fail "ad-hoc signature: every rebuild would reset its permissions. Unlock the signing identity and retry." ;;
esac

# The designated requirement is what macOS records a permission against. The
# same requirement as the installed copy means every grant survives.
if [ -d "$DEST" ]; then
  OLD=$(codesign -dr - "$DEST" 2>&1 | sed -n 's/^designated => //p' || true)
  NEW=$(codesign -dr - "$SRC" 2>&1 | sed -n 's/^designated => //p' || true)
  if [ -n "$OLD" ] && [ "$OLD" != "$NEW" ] && [ "${TB_ALLOW_DIRECTOR_IDENTITY_CHANGE:-0}" != "1" ]; then
    fail "the signing requirement changed; its permissions would reset. Set TB_ALLOW_DIRECTOR_IDENTITY_CHANGE=1 only on purpose."
  fi
  if pgrep -f "$DEST/Contents/MacOS/TranquilityApp" >/dev/null; then
    osascript -e "with timeout of 5 seconds" -e "tell application id \"$BUNDLE_ID\" to quit" -e "end timeout" >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do pgrep -f "$DEST/Contents/MacOS/TranquilityApp" >/dev/null || break; sleep 0.5; done
    pkill -f "$DEST/Contents/MacOS/TranquilityApp" 2>/dev/null || true
  fi
fi

echo "→ installing $DEST"
rm -rf "$DEST"
cp -R "$SRC" "$DEST"
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
codesign --verify --deep --strict "$DEST" 2>/dev/null || fail "installed bundle does not verify"

# Its own folder, seeded once. The roster and voices are copied so the four
# right-hands are there on first launch; provider keys are copied, but NOT the
# hub token or device key: this app must not publish to the hub as this Mac.
mkdir -p "$SUPPORT"; chmod 700 "$SUPPORT"
for f in right-hands.json voices.json enrolled.json agent-command.json codex-thread-names.json; do
  if [ ! -f "$SUPPORT/$f" ] && [ -f "$PROD_SUPPORT/$f" ]; then cp -p "$PROD_SUPPORT/$f" "$SUPPORT/$f"; fi
done
if [ ! -f "$SUPPORT/secrets.json" ] && [ -f "$PROD_SUPPORT/secrets.json" ]; then
  python3 - "$PROD_SUPPORT/secrets.json" "$SUPPORT/secrets.json" <<'PY'
import json, sys
keys = json.load(open(sys.argv[1]))
kept = {k: v for k, v in keys.items() if k not in ("hub-token", "device-key")}
json.dump(kept, open(sys.argv[2], "w"), indent=2, sort_keys=True)
PY
fi
# Files only: a folder at 600 cannot be entered.
find "$SUPPORT" -maxdepth 1 -type f -exec chmod 600 {} + 2>/dev/null || true

# The right-hands' brains (25 Sep): Yobi1 and Sys-3PO answer through small ask
# scripts, refreshed on every install because they are the app's, not the
# user's. A hand in this app's roster that has no `ask` of its own, or one
# pointing at these scripts, is given ours; any other `ask` is kept. Prod's
# roster is never touched. Run through python3: the app keeps every file in
# its folder at 600 (PrivateStorage), so the scripts are never executable there.
mkdir -p "$SUPPORT/brains"
cp -p scripts/brains/yobi1-ask scripts/brains/sys3po-ask scripts/brains/hand-status "$SUPPORT/brains/"
chmod 700 "$SUPPORT/brains" "$SUPPORT/brains/"*
if [ -f "$SUPPORT/right-hands.json" ]; then
  python3 - "$SUPPORT/right-hands.json" "$SUPPORT/brains" <<'PY2'
import json, sys
path, brains = sys.argv[1], sys.argv[2]
data = json.load(open(path))
py = "/usr/bin/python3"
status = [py, f"{brains}/hand-status", "--session", "{session}", "--name"]
wanted = {  # (hand, key) -> argv; the card's status (tb-indicators) and the brain
    ("Yobi1", "ask"): [py, f"{brains}/yobi1-ask", "{text}"],
    ("Sys-3PO", "ask"): [py, f"{brains}/sys3po-ask", "{text}"],
    ("Yobi1", "projects"): status + ["Yobi1"],
    ("Sys-3PO", "projects"): status + ["Sys-3PO", "--health"],
    ("Director", "ready"): ["director", "--json", "ready"],
}
given = []
for hand in data.get("hands", []):
    for (name, key), argv in wanted.items():
        if hand.get("name") != name:
            continue
        ours = any(brains in str(a) for a in hand.get(key) or [])
        if (not hand.get(key) or ours) and hand.get(key) != argv:
            hand[key] = argv
            given.append(f"{name} {key}")
if given:
    json.dump(data, open(path, "w"), indent=2)
    print("  roster: set " + ", ".join(given))
PY2
fi

echo "✓ installed $DEST beside Prod"
echo "  data folder: $SUPPORT"
echo "  signature: $(printf '%s\n' "$SIGNING" | sed -n 's/^Authority=//p' | head -1)"
if [ "$LAUNCH" -eq 1 ]; then
  open -a "$DEST"
  echo "  launched"
fi
