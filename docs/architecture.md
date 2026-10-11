# Architecture

```
 Android app (Kotlin/Compose)                     Fedora app (Python/GTK4)
 ┌──────────────────────────┐                     ┌─────────────────────────────┐
 │ ui/  Compose screens, VM │                     │ ui/  Libadwaita window      │
 │ transfer/ TransferEngine │   HTTPS, pinned     │ app.py, settings.py         │
 │ core/ PinnedTls,         │ ◄─────────────────► │ ─────────── (no UI imports) │
 │       HttpReceiver       │   fingerprint from  │ server.py  FastAPI + uvicorn│
 │ data/ settings, history  │   the QR code       │ pairing.py transfers.py     │
 │ TransferService (FGS)    │                     │ storage.py security.py      │
 └──────────────────────────┘                     │ certificates.py network.py  │
                                                  └─────────────────────────────┘
```

## Components

**Receiver core (`fedora/localbridge`, no GTK imports).** `PairingManager` (offers, pending
confirmations, sessions), `TransferManager` (state machine, limits, approval binding),
`Destination` (sanitising, atomic no-overwrite saves, partial files), `create_app` (FastAPI routes),
`ReceiverService` (TLS identity + uvicorn thread; `stop()` closes the socket and wipes state). All
mutable state is lock-guarded because the UI thread and the server thread share it. The core uses
callbacks (`subscribe`) so any UI can observe it; the GTK UI marshals them onto the main loop with
`GLib.idle_add`.

**Desktop UI (`localbridge/ui`).** One Libadwaita window: status card + Start/Stop, pairing QR (only
while an unexpired offer exists), receiver settings, connected phones, transfers, and modal
approval dialogs for pairing and incoming transfers.

**Android.** `PairingInfo` validates the QR. `PinnedTls` builds an OkHttp client that trusts exactly
one certificate. `HttpReceiver` implements `ReceiverGateway`; uploads use a bounded 256 KiB buffer
and hash while sending. `TransferEngine` is platform-free (a `FileAccess` interface hides
`ContentResolver`), so its behaviour (approval wait, cancel, failure, retry, duplicate prevention) is
unit tested with a fake gateway. `TransferService` is a foreground service that exists only while a
transfer is active. State lives in `AppViewModel` (navigation stack, pairing state) and the
engine's `StateFlow`s, so rotation does not lose it.

## Connection flow

1. Desktop: *Start receiving* → bind the chosen interface IP on an ephemeral port → generate TLS
   identity → create pairing offer → render QR.
2. Phone: scan → validate (local IPv4 only) → pinned TLS connect → `POST /v1/pair`.
3. Desktop: dialog "Pair with this phone?" → Pair → phone gets a session token.
4. Phone: pick files → `POST /v1/transfers` → desktop dialog lists names/sizes/destination →
   Accept → phone streams each file → `finalize` with sender-computed SHA-256 → file appears.

## Decisions

* **Approval is local-only** (no remote approve endpoint) so a sender cannot authorise itself.
* **Ephemeral cert per start + fingerprint pinning** avoids a long-lived key store and any CA trust;
  cost: a new QR each time the receiver starts.
* **Ephemeral port, user-chosen interface IP**: nothing listens on all interfaces, and no fixed port
  needs a firewall rule. Fedora Workstation's default zone allows unprivileged ports (≥1024); stricter
  zones may require the user to allow it — the app never changes firewalld.
* **One-pass upload + `finalize`** gives sender-side hashing without reading files twice.
* **No resumption in v1**: interrupted files are discarded, never mistaken for complete files.
* **ZXing instead of ML Kit** for scanning: fully open-source, no Play Services dependency.
* **Python/FastAPI for the receiver** as required; measured at ~400 MB/s on loopback, so it is not the
  bottleneck on Wi-Fi (see docs/testing.md).
* **Sequential file sends** keep progress simple and predictable; parallel streams are a later
  optimisation to be justified by measurement on real hotspots.

## Deviations from the proposed repo layout

`fedora/localbridge/settings.py` and `fedora/tools/` (test-only headless receiver) were added;
`android/app/src/main` uses `kotlin/` source dir and package `io.github.localbridge`.
