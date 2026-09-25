#!/bin/bash
# Tranquility Base Director: the right-hands app, built from the same
# TranquilityApp target as Prod and Dev, installed BESIDE Prod (25 Sep).
#
# It is not a lane. Nothing here replaces Prod or takes its place:
#   - its own name, bundle id and green icon;
#   - its own data folder (VoiceDispatch-Director), so its own one-instance
#     lock, queue, log and roster;
#   - no hotkey tap: Option and every chord are Prod's, and without a tap the
#     app needs neither Accessibility nor Input Monitoring;
#   - its own link scheme (tbdirector://), and it never takes Prod's two;
#   - it never repairs the hooks, which feed Prod's folder;
#   - its own preferences suite;
#   - updates off.
# Signed by the same stable local identity as Dev (bundle.sh finds it), so
# the permissions macOS records for it survive every rebuild.
set -euo pipefail
cd "$(dirname "$0")/.."

export VD_APP_NAME="Tranquility Base Director"
export VD_BUNDLE_ID="com.robertnowell.voice-dispatch.director"
export VD_APP_CHANNEL="director"
export VD_UPDATES_ENABLED="false"
export VD_URL_SCHEMES="tbdirector"
export VD_OWN_SCHEME="tbdirector"
export VD_SUPPORT_FOLDER="VoiceDispatch-Director"
export VD_DEFAULTS_SUITE="com.robertnowell.voice-dispatch.director"
export VD_HOTKEYS="false"
export VD_CLAIMS_PRODUCT_SCHEMES="false"
export VD_MANAGES_HOOKS="false"
export TB_FEED_URL="https://updates.tranquilitybase.to/director-appcast.xml"

exec scripts/bundle.sh "${1:-debug}"
