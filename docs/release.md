# Release

Nothing here has been run as a release yet; items are the intended process.

## Versioning
Semantic versions. Android `versionName`/`versionCode` in `android/app/build.gradle.kts`; Fedora in
`fedora/pyproject.toml` and `localbridge/__init__.py` (keep in sync). The protocol version is
independent (`/v1`).

## Android
```bash
cd android
./gradlew testDebugUnitTest assembleRelease      # produces app-release-unsigned.apk (R8 enabled)
apksigner sign --ks <your.jks> --out LocalBridge-<ver>.apk app/build/outputs/apk/release/app-release-unsigned.apk
apksigner verify --print-certs LocalBridge-<ver>.apk
```
Keep the keystore out of the repo (`*.jks` is git-ignored). F-Droid builds from source; ZXing/OkHttp/
AndroidX/Compose are all open source (no Play Services dependency).

## Fedora
`packaging/localbridge.spec` + `packaging/io.github.localbridge.LocalBridge.desktop` are drafts:
```bash
sudo dnf install rpm-build python3-devel pyproject-rpm-macros
rpmbuild -ba packaging/localbridge.spec     # NOT yet run
```
The package declares dependencies on `python3-gobject`, `libadwaita`, `python3-fastapi`,
`python3-uvicorn`, `python3-cryptography`, `python3-qrcode`; it installs no service, opens no firewall
port and needs no privileges. Check that these Fedora package names exist before building.

## Pre-release checklist
- [ ] All suites green (docs/testing.md) · [ ] physical-device hotspot test recorded
- [ ] Threat model re-read against changes · [ ] dependency versions and licences reviewed
- [ ] Trademark/name search for "LocalBridge" done · [ ] known limitations in README current
