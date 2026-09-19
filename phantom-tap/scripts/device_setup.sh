#!/usr/bin/env bash
# device_setup.sh - route an Android device's traffic through mitmproxy so the
# app's own conversation with its backend can be recorded, once.
#
# This only makes sense for capturing YOUR OWN account's traffic on a device you
# own. Run device_check.sh first; it decides whether this can work at all.
#
# What this does:  starts mitmproxy, points the device's Wi-Fi proxy at this
#                  machine, and installs the mitmproxy CA as a *user* cert.
# What it does NOT do:  defeat certificate pinning. If device_check.sh reports
#                  pinning, see the note at the bottom.
set -uo pipefail

PORT="${PORT:-8080}"
CA="$HOME/.mitmproxy/mitmproxy-ca-cert.cer"

die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
info() { printf '\033[36m%s\033[0m\n' "$*"; }

command -v adb >/dev/null      || die "adb not found (sudo apt install android-tools-adb)"
command -v mitmdump >/dev/null || die "mitmdump not found (pip install mitmproxy)"

adb get-state >/dev/null 2>&1  || die "no device over adb. Enable USB debugging and accept the prompt."

# The address the phone will use to reach this machine. Adjust if your LAN differs.
HOST_IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')"
[ -n "$HOST_IP" ] || die "could not determine this machine's LAN IP; set HOST_IP by hand"
info "this machine is $HOST_IP; the phone will proxy through $HOST_IP:$PORT"

# Generate the CA if mitmproxy has never run.
if [ ! -f "$CA" ]; then
    info "generating the mitmproxy CA..."
    timeout 3 mitmdump >/dev/null 2>&1 || true
fi
[ -f "$CA" ] || die "mitmproxy CA still missing at $CA"

ROOTED=0
adb shell 'su -c id' 2>/dev/null | grep -q 'uid=0' && ROOTED=1

info "installing the mitmproxy CA as a user certificate (Settings you may need to confirm on the phone)..."
adb push "$CA" /sdcard/Download/mitmproxy-ca.cer >/dev/null && \
    info "  pushed to Download/. On the phone: Settings > Security > Install a certificate > CA."

info "setting the device Wi-Fi proxy..."
adb shell settings put global http_proxy "$HOST_IP:$PORT" && info "  proxy set to $HOST_IP:$PORT"

cat <<EOF

Now, in another terminal:

  pt capture          # prints the exact mitmdump command
  # then drive the app: log in, open the schedule, tap a not-yet-open class,
  # register for one and pick a seat.

When you are done, undo the proxy so the phone browses normally again:

  adb shell settings put global http_proxy :0

EOF

if [ "$ROOTED" -eq 0 ]; then
cat <<'EOF'
NOTE: on Android 7+ an app only trusts a *user* CA if it opts in. If the capture
stays empty, the app is either ignoring user CAs or pinning certificates. That is
exactly what device_check.sh reports. In that case use a rooted device or an
emulator, and for a pinned app reach for a standard research tool such as
`objection` (`objection -g com.holmesplace explore` then `android sslpinning
disable`) on that device you own. This project does not ship a bypass.
EOF
fi
