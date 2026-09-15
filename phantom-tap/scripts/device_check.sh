#!/usr/bin/env bash
# device_check.sh - answer one question: can we intercept com.holmesplace traffic
# on this device, and if not, what is the cheapest path that works?
#
# Read-only. Installs nothing, changes no device setting.
set -uo pipefail

PKG="${PKG:-com.holmesplace}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ok()   { printf '  \033[32m[ok]\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33m[warn]\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m[bad]\033[0m  %s\n' "$*"; }
info() { printf '  \033[36m[--]\033[0m   %s\n' "$*"; }
head1(){ printf '\n\033[1m%s\033[0m\n' "$*"; }

ROOTED=0; PINNED=unknown; USER_CA_TRUSTED=unknown; SDK=0

head1 "1. adb"
if ! command -v adb >/dev/null 2>&1; then
  bad "adb not found. Install: sudo apt install android-tools-adb"
  exit 1
fi
ok "adb $(adb version | head -1 | awk '{print $NF}')"

DEVS="$(adb devices | awk 'NR>1 && NF && $2=="device" {print $1}')"
COUNT="$(printf '%s\n' "$DEVS" | grep -c . || true)"
if [ "$COUNT" -eq 0 ]; then
  bad "No authorised device. Enable USB debugging and accept the RSA prompt on the phone."
  adb devices
  exit 1
elif [ "$COUNT" -gt 1 ]; then
  warn "$COUNT devices attached; using the first. Set ANDROID_SERIAL to pick one."
fi
SERIAL="$(printf '%s\n' "$DEVS" | head -1)"
export ANDROID_SERIAL="${ANDROID_SERIAL:-$SERIAL}"
ok "device $ANDROID_SERIAL"

head1 "2. platform"
MODEL="$(adb shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
REL="$(adb shell getprop ro.build.version.release 2>/dev/null | tr -d '\r')"
SDK="$(adb shell getprop ro.build.version.sdk 2>/dev/null | tr -d '\r')"
ABI="$(adb shell getprop ro.product.cpu.abi 2>/dev/null | tr -d '\r')"
info "$MODEL  Android $REL (API $SDK)  $ABI"

head1 "3. root"
# Three independent probes; any one is enough.
if adb shell 'su -c id' 2>/dev/null | grep -q 'uid=0'; then
  ROOTED=1; ok "su works - device is rooted"
elif adb shell 'which su' 2>/dev/null | tr -d '\r' | grep -q '/su'; then
  ROOTED=1; warn "su binary present but did not grant. Approve the Magisk prompt on the phone and re-run."
elif adb shell pm list packages 2>/dev/null | tr -d '\r' | grep -qiE 'magisk|supersu|topjohnwu'; then
  ROOTED=1; warn "Magisk/SuperSU package found but su did not respond."
else
  bad "No root. This is the normal state for a retail phone."
fi
[ "$(adb shell id 2>/dev/null | tr -d '\r' | grep -c 'uid=0')" -gt 0 ] && { ROOTED=1; ok "adb shell already runs as root"; }

head1 "4. the app"
PATHS="$(adb shell pm path "$PKG" 2>/dev/null | tr -d '\r' | sed 's/^package://')"
if [ -z "$PATHS" ]; then
  bad "$PKG is not installed on this device. Install the Holmes Place app and re-run."
  exit 1
fi
VER="$(adb shell dumpsys package "$PKG" 2>/dev/null | tr -d '\r' | grep -m1 versionName= | sed 's/.*versionName=//')"
TSDK="$(adb shell dumpsys package "$PKG" 2>/dev/null | tr -d '\r' | grep -m1 -oE 'targetSdk=[0-9]+' | cut -d= -f2)"
ok "$PKG  version $VER  targetSdk ${TSDK:-?}"

info "pulling apk(s) for static analysis..."
i=0
for p in $PATHS; do
  adb pull "$p" "$WORK/apk_$i.apk" >/dev/null 2>&1 && i=$((i+1))
done
[ "$i" -eq 0 ] && { warn "could not pull the apk; skipping static analysis"; i=0; }

head1 "5. does the app trust user-installed CAs?"
# Android 7+ (API 24) ignores user CAs unless the app opts in via
# network_security_config.xml. Without opt-in, mitmproxy sees nothing.
if [ "${SDK:-0}" -lt 24 ]; then
  USER_CA_TRUSTED=yes; ok "API $SDK < 24 - user CAs are trusted by default"
elif [ "$i" -gt 0 ]; then
  NSC=0
  for f in "$WORK"/apk_*.apk; do
    if unzip -l "$f" 2>/dev/null | grep -q 'res/xml/network_security_config'; then NSC=1; fi
  done
  if [ "$NSC" -eq 0 ]; then
    USER_CA_TRUSTED=no
    bad "no network_security_config.xml -> app uses the platform default -> user CAs are IGNORED"
  else
    # binary xml, but the string pool keeps the literals readable
    if unzip -p "$WORK"/apk_*.apk res/xml/network_security_config.xml 2>/dev/null \
        | strings | grep -q 'user'; then
      USER_CA_TRUSTED=yes; ok "network_security_config declares a 'user' trust anchor"
    else
      USER_CA_TRUSTED=no; bad "network_security_config present but does not trust user CAs"
    fi
  fi
else
  warn "unknown - apk not available"
fi

head1 "6. certificate pinning"
if [ "$i" -gt 0 ]; then
  HITS=""
  for f in "$WORK"/apk_*.apk; do
    unzip -o "$f" -d "$WORK/x" >/dev/null 2>&1 || true
  done
  scan() { grep -rlsa "$1" "$WORK/x" 2>/dev/null | head -1; }
  [ -n "$(scan 'CertificatePinner')" ]        && HITS="$HITS okhttp-CertificatePinner"
  [ -n "$(scan 'com/datatheorem/android')" ]  && HITS="$HITS TrustKit"
  [ -n "$(scan 'sha256/')" ]                  && HITS="$HITS pin-literals(sha256/)"
  [ -n "$(scan 'checkServerTrusted')" ]       && HITS="$HITS custom-TrustManager"
  FLUTTER=""; [ -n "$(ls "$WORK"/x/lib/*/libflutter.so 2>/dev/null)" ] && FLUTTER=yes
  RN="";      [ -n "$(ls "$WORK"/x/assets/index.android.bundle 2>/dev/null)" ] && RN=yes

  if [ -n "$FLUTTER" ]; then
    PINNED=flutter
    bad "Flutter app (libflutter.so). Flutter bypasses the Android proxy AND the system
        trust store - it uses its own BoringSSL. Neither a proxy setting nor a system CA
        will work; this needs Frida + a libflutter hook, or reFlutter."
  elif [ -n "$HITS" ]; then
    PINNED=yes
    bad "pinning indicators:$HITS"
  else
    PINNED=no
    ok "no pinning indicators found (okhttp/TrustKit/sha256 literals absent)"
  fi
  [ -n "$RN" ] && info "React Native bundle detected (assets/index.android.bundle)"
else
  warn "unknown - apk not available"
fi

head1 "VERDICT"
if [ -n "${FLUTTER:-}" ]; then
  echo "  Flutter runtime. Capture needs Frida with a libflutter.so hook (or reFlutter),"
  echo "  which needs root. Cheapest path: a rooted spare device or a Waydroid/redroid"
  echo "  container on the Mini-PC."
elif [ "$ROOTED" -eq 1 ]; then
  echo "  ROOT AVAILABLE -> full HTTP capture is possible."
  echo "  Next: scripts/device_setup.sh installs the mitmproxy CA into the system store"
  [ "$PINNED" = yes ] && echo "        and loads frida unpin.js (pinning was detected)."
  echo "  Then: pt capture"
elif [ "$USER_CA_TRUSTED" = yes ] && [ "$PINNED" = no ]; then
  echo "  NO ROOT, but the app trusts user CAs and does not pin -> capture works unrooted."
  echo "  Next: install the mitmproxy CA as a user certificate, then: pt capture"
else
  echo "  NO ROOT and the app will not trust a user CA${_x:-}."
  echo "  This device cannot be intercepted as-is. Options, cheapest first:"
  echo "    1. Waydroid or redroid on the Mini-PC (rooted by construction) - recommended"
  echo "    2. A spare device with Magisk"
  echo "    3. Skip HTTP entirely and run the UI-automation path (slower, loses tight races)"
fi
echo
echo "  summary: rooted=$ROOTED  user_ca_trusted=$USER_CA_TRUSTED  pinned=$PINNED  api=$SDK"
