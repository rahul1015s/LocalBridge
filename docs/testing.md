# Testing

Last verified: 2026-10-11, Fedora 44 (kernel 7.2.8), Python 3.14.7, JDK 25, Gradle 9.8.1, AGP 9.4.1.

## Commands

```bash
# Fedora receiver (77 tests, real TLS sockets on 127.0.0.1)
cd fedora && .venv/bin/python -m pytest -q
cd fedora && .venv/bin/ruff check . && .venv/bin/ruff format --check .

# Android JVM unit tests (28 tests; 2 are skipped unless interop env is set)
cd android && ./gradlew testDebugUnitTest

# Kotlin client <-> Python receiver over real TLS (loopback)
./scripts/interop_test.sh
```

## Results

| Suite | Result | Environment |
|---|---|---|
| `fedora/tests` | **77 passed** | mock-free: real uvicorn + TLS over loopback |
| Android unit | **28 run, 26 passed, 2 skipped (interop), 0 failed** | JVM, fake receiver |
| `scripts/interop_test.sh` | **passed**: pinned handshake, 24 MB file byte-identical, chunked unknown-size upload, forged fingerprint fails closed | loopback, JVM client |
| Release build `assembleRelease` | builds (unsigned, R8 on, ~2.5 MB) | — |
| GTK window smoke (scripted) | passed: start → pair dialog → approve dialog → transfer row → stop | real Wayland session, scripted clicks, **loopback network** |

Mutation spot-checks: removing the approval gate or the pairing single-use flag each makes the
relevant tests fail.

### Measurements (loopback, same process for client+server — indicative only)

* 1 GiB upload over TLS: ~400 MB/s, peak RSS 88 MB (bounded streaming confirmed).
* These say nothing about Wi-Fi/hotspot throughput, which will be far lower and has not been measured.

## What is covered

Python: pairing credentials (generation, expiry, single use, replay, revocation, wrong secret/sid
indistinguishable, bounded pending), rate limiting, state machine & invalid transitions, approval
gating, cross-session isolation, no remote approve, filename sanitising (traversal, Unicode, bidi,
long names), no-overwrite duplicates, symlink refusal, partial-file perms, disk-full (ENOSPC) and
insufficient space, size limits (declared and at stream time), malformed/oversized JSON, invalid
IDs, hash mismatch, interrupted upload, mid-transfer cancel, receiver shutdown (socket closed,
partials wiped), restart yields new identity.

Android: certificate pinning (match, mismatch, expired, empty chain), QR parsing/validation (public
hosts rejected), streaming hash with bounded buffer, unknown-size (chunked), file changing mid-send,
engine: approval wait, reject, expiry, cancel, network loss, partial failure, unreadable files,
unknown size has no fake progress, duplicate prevention, speed meter.

## NOT yet tested (do not infer from the above)

* **Physical Android device + Fedora laptop**, including **mobile-hotspot** behaviour and client
  isolation. No device was attached and no hotspot test was run. Hotspot support is **unproven**.
* The Android UI has **never been run** on a device or emulator (the emulator crashed with SIGSEGV
  on this machine in both GPU modes). Compose screens compile but are untested visually; the QR
  scanner (CameraX) and system file picker are untested.
* Foreground service / notifications behaviour on real Android versions; Android instrumentation
  tests (none written yet).
* ViewModel tests; large multi-GB transfers on real storage; network interruption on real Wi-Fi.
* RPM build/install (`rpmbuild` is not installed here).
* Android 8–13 behaviour (minSdk 26); targetSdk 36.

## Test matrix (to fill in on real hardware)

| Android | Fedora | Network | Status |
|---|---|---|---|
| 14 (emulator image present) | 44 | loopback | partial (core only) |
| 14/15 physical | 44 | phone hotspot | not run |
| 14/15 physical | 44 | shared Wi-Fi | not run |
