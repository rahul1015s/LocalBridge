# LocalBridge

Send videos, music, photos and documents between your **Android phone** and your **Fedora Linux**
computer, directly over your own Wi-Fi or phone hotspot. No cloud, no account, no analytics.

> **Status: pre-beta.** The Fedora receiver and the Android transfer core are implemented and tested
> (loopback, including real Kotlin↔Python TLS interop). The Android UI compiles but **has not yet been
> run on a phone**, and **mobile-hotspot behaviour is untested**. See [docs/testing.md](docs/testing.md).
> The project has not been independently security-audited.

## How it works

1. On Fedora press **Start receiving** → a QR code appears (valid 2 minutes, single use).
2. On the phone tap **Connect to computer** and scan it. The phone pins the computer's TLS certificate
   fingerprint from the QR; anything else is refused.
3. The computer asks **"Pair with this phone?"** → Pair.
4. Pick files on the phone (or use *Share → LocalBridge*). The computer lists them and asks
   **Accept / Reject**. Nothing is written before you accept.
5. Files stream over TLS in bounded chunks, are SHA-256 verified, and saved without ever overwriting
   an existing file.

## Features (implemented)

* Any file type, any number of files, large files (streamed; 1 GiB used ~88 MB RAM in testing)
* Certificate-pinned HTTPS; single-use short-lived QR pairing with explicit desktop confirmation
* Explicit per-transfer approval; cancel from either side; interrupted files never look complete
* Honest progress (no percentage when size is unknown), speed and ETA only when measurable
* Safe storage: sanitised names, no overwrite (`name (1).ext`), 0600 partial files, free-space checks
* Native GTK4/Libadwaita desktop app; Jetpack Compose/Material 3 Android app (teal, light/dark)
* Receiver binds one chosen interface on an ephemeral port and stops listening when you press Stop

Not yet: resumable transfers, saved/trusted devices (each session is re-paired), IPv6, discovery
without QR, desktop notifications.

## Supported platforms

Fedora 44 (developed and tested here). Android 8.0+ (minSdk 26, target 36; only built, not run).

## Install & run (development)

```bash
# Fedora
sudo dnf install python3 python3-gobject gtk4 libadwaita
cd fedora && python3 -m venv --system-site-packages .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m localbridge

# Android (needs JDK 17+, Android SDK platform 37)
cd android && echo "sdk.dir=$ANDROID_HOME" > local.properties && ./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Firewall: LocalBridge never touches firewalld. Fedora Workstation's default zone allows unprivileged
ports; if you use a stricter zone and the phone can't connect, allow it yourself on that network.

## Tests

```bash
cd fedora && .venv/bin/python -m pytest -q        # 77 tests
cd android && ./gradlew testDebugUnitTest         # 28 tests (2 need the interop script)
./scripts/interop_test.sh                         # Kotlin client <-> Python receiver, real TLS
```

## Repository layout

`fedora/` receiver + GTK app · `android/` Compose app · `protocol/openapi.yaml` · `docs/` ·
`packaging/` (draft RPM spec) · `scripts/`. Start with [docs/architecture.md](docs/architecture.md).

## Known limitations

* Hotspot compatibility unproven; some hotspots isolate clients and will block the connection.
* QR trust model cannot defend against someone swapping the QR on your screen — see
  [docs/threat-model.md](docs/threat-model.md).
* Transferred files are integrity-checked, **not** virus-scanned.
* Python receiver; GUI requires the system GTK4/Libadwaita bindings.

## Security reporting

Please report vulnerabilities privately to the maintainer (add a contact address / enable GitHub
private vulnerability reporting before publishing) rather than in a public issue.

## License

GPL-3.0-or-later ([LICENSE](LICENSE)). Check name/trademark availability for "LocalBridge" before any
public release.
