#!/usr/bin/env bash
# Kotlin client <-> Python receiver interop over real TLS (loopback only; NOT a hotspot test).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'kill "${RECV_PID:-0}" 2>/dev/null || true; rm -rf "$WORK"' EXIT

head -c $((24 * 1024 * 1024)) /dev/urandom > "$WORK/test.bin"
cd "$ROOT/fedora"
.venv/bin/python tools/headless_receiver.py 127.0.0.1 "$WORK/inbox" "$WORK/uri" &
RECV_PID=$!
for _ in $(seq 50); do [ -s "$WORK/uri" ] && break; sleep 0.1; done
[ -s "$WORK/uri" ] || { echo "receiver failed to start"; exit 1; }

cd "$ROOT/android"
LB_URI_FILE="$WORK/uri" LB_TEST_FILE="$WORK/test.bin" \
  ./gradlew testDebugUnitTest --tests '*InteropTest*' -q

cmp "$WORK/test.bin" "$WORK/inbox/interop-ü.bin" && echo "OK: received file is byte-identical"
[ "$(cat "$WORK/inbox/unknown-size.bin")" = "chunked-unknown-length" ] && echo "OK: unknown-size (chunked) upload received"
