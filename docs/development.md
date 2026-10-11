# Development

## Prerequisites (Fedora)

```bash
sudo dnf install python3 python3-gobject gtk4 libadwaita git java-latest-openjdk-devel   # GUI + Android
# Android SDK: cmdline-tools, platform 37, build-tools 36 (sdkmanager). Set ANDROID_HOME.
```
The receiver is developed in a venv that can see the system `gi` bindings:

```bash
cd fedora
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m localbridge            # launch the desktop app
```

Android: `cd android && echo "sdk.dir=$ANDROID_HOME" > local.properties && ./gradlew assembleDebug`
(APK: `android/app/build/outputs/apk/debug/app-debug.apk`; install with `adb install -r`).

Everyday commands: see docs/testing.md. Nothing is installed globally; no sudo is needed to build or test.

## Progress checklist

Legend: [x] done with test evidence · [~] implemented, not verified on target hardware · [ ] not done

### Phase 0 — environment & architecture
- [x] Environment inspected (Fedora 44, Py 3.14, GTK 4.22/Adw 1.9, JDK 25, SDK 35–37, no device)
- [x] architecture.md, threat-model.md, protocol.md, openapi.yaml
- [x] Dependency versions chosen and building (AGP 9.4.1, Kotlin 2.4.21, Compose BOM 2026.09.00)

### Phase 1 — working secure transfer
- [x] Receiver: TLS identity, QR pairing, session auth, approval, streaming, SHA-256 verify
- [x] Android client core: pinned TLS, pairing, streaming upload (interop-tested against the receiver)
- [~] Android pairing UI + QR scanner (compiled; never run on a device)

### Phase 2 — reliability
- [x] Multiple files, cancellation, honest progress, limits, disk-full, interrupted uploads
- [ ] Resumable uploads (intentionally deferred)
- [ ] Multi-GB tests on real storage; throughput on real Wi-Fi

### Phase 3 — Android UX
- [~] Home, Pair, Send (in-app picker + review), Transfers, History, Settings screens; share-sheet intake; foreground service
- [ ] Instrumentation / ViewModel tests; accessibility pass with TalkBack; tablet layout

### Phase 4 — Fedora UX
- [x] GTK4/Libadwaita window: start/stop, QR (expiring), approvals, progress, destination, devices
- [x] Scripted end-to-end smoke of the real window
- [ ] Desktop notifications, keyboard shortcut audit, Orca accessibility check

### Phase 5 — hardening
- [x] Security-relevant unit/integration tests (see threat-model.md)
- [ ] **Physical hotspot test; hotspot client-isolation findings**
- [ ] Fuzzing of the upload path; external review

### Phase 6 — packaging
- [x] Android release APK builds (unsigned)
- [~] RPM spec + desktop file written (`packaging/`), **not built or install-tested**
- [ ] Signed APK, F-Droid metadata, release checks (docs/release.md)

## Next exact step

Install the debug APK on a physical phone (`adb install`), run `python -m localbridge` on the laptop,
and walk through: hotspot → pair → send a video and a folder of mixed files. Record results and any
client-isolation behaviour in docs/testing.md, then fix whatever the first real run breaks.
