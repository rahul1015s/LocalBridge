# LocalBridge protocol v1

Status: implemented by `fedora/localbridge/server.py` (receiver) and
`android/.../core/HttpReceiver.kt` (sender). Machine-readable summary: `protocol/openapi.yaml`.

## Roles

* **Receiver** (Fedora): runs an HTTPS server bound to **one** chosen local IPv4 address, on an
  ephemeral port, only while the user has pressed *Start receiving*.
* **Sender** (Android): connects to the receiver, requests a transfer, waits for approval, uploads.

## Transport and trust

* HTTPS only (TLS 1.2+/1.3 negotiated by the platform libraries). No cleartext listener exists.
* The receiver generates an **ephemeral self-signed ECDSA P-256 certificate on every start**
  (valid 1 day, SAN = bound IP). It is never written outside a 0700 temp directory.
* The sender trusts **only** the certificate whose SHA-256 (over its DER encoding) equals the
  fingerprint in the QR code. No system CA store, no trust-on-first-use. Mismatch => connection
  fails closed. Same behaviour in debug and release builds.

## Pairing

1. User presses *Start receiving*. The receiver creates a **pairing offer**:
   `session_id` (96-bit random), `secret` (256-bit random), TTL 120 s, single use.
2. QR payload (also available as a copyable link):

   ```
   localbridge://pair?v=1&host=<IPv4>&port=<port>&sid=<session_id>&fp=<sha256 hex>&secret=<secret>
   ```

   The sender only accepts private/link-local/CGNAT/loopback IPv4 hosts.
3. `POST /v1/pair` `{protocol:1, session_id, secret, device_name}` over the pinned connection.
   * Wrong/expired/used/revoked credentials => `401 pairing_invalid` (identical for every cause).
   * The secret is **consumed on first valid use**, even if the user then denies (no replay).
   * The request then **blocks (<= 60 s) until the person confirms on the desktop** ("Pair with
     this phone?"). Denial or timeout => `403 pairing_denied`.
   * On accept: `200 {token, expires_in, receiver_name}`. `token` is a 256-bit bearer token,
     valid 1 h, held in sender memory only. The receiver keeps only its SHA-256.
4. Failed pairing attempts are rate-limited per client IP (5 / 60 s => `429`); at most 2 pairings
   may be pending and 4 sessions active.

Pairing creates a **temporary session**. No long-lived trusted-device store exists in v1.

## Authentication

Every endpoint except `GET /v1/health` requires `Authorization: Bearer <token>`.
Transfers are bound to the session that created them; another session receives `404`
(indistinguishable from "does not exist").

## Endpoints

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /v1/health` | no | `{status:"ok", protocol:1}` and nothing else |
| `POST /v1/pair` | pairing secret | see above |
| `POST /v1/transfers` | yes | declare files `[{name, size?, mime?}]` => `201` transfer (state `AWAITING_APPROVAL`). `size` may be omitted (unknown). Order of `files` in the response matches the request. |
| `GET /v1/transfers/{id}` | yes | state + per-file state/progress |
| `POST /v1/transfers/{id}/cancel` | yes | sender cancels (idempotent) |
| `PUT /v1/transfers/{id}/files/{file_id}/content` | yes | stream bytes; refused until `APPROVED` |
| `POST /v1/transfers/{id}/files/{file_id}/finalize` | yes | `{sha256, size}` computed by the sender; receiver verifies and atomically saves |
| `POST /v1/messages` | yes | `{text}` (<= 64 KiB) text/link from the phone; shown on the desktop, never opened automatically |
| `GET /v1/outbox` | yes | items the desktop user queued **for this session**: `{id, kind: file|text, name, size, sha256, text}` |
| `GET /v1/outbox/{id}/content` | yes | file bytes; supports a single `Range: bytes=N-` (resume) |
| `POST /v1/outbox/{id}/done` | yes | `{status: saved|declined|failed, detail?}` |

**Deviation from the original sketch:** there is no network `approve`/`reject` endpoint.
Approval is a *local desktop action*; a sender must never be able to approve its own request.
Content upload is per file (a transfer holds many files) and followed by an explicit `finalize`
so the sender can hash in a single pass while streaming.

### Resuming an interrupted upload

If the connection drops, the receiver **keeps the partial file and its running hash** and marks the
file `INTERRUPTED` with `received` = bytes safely written. The sender reads `received` from
`GET /v1/transfers/{id}` and re-sends the remainder with header `X-LocalBridge-Offset: <received>`
(`Content-Length` = remaining bytes). The offset must equal the receiver's byte count exactly,
otherwise `409 offset_mismatch` (partial kept). Offset 0 discards the old partial and starts over.
The sender re-reads and hashes the already-delivered prefix locally so `finalize` still covers the
whole file. A stream that has been silent for 8 s may be superseded by a new one for the same file
(half-open connections); an active stream is not (`409 file_state`). An interrupted transfer expires
after 15 minutes with no activity and its partials are deleted.

### Content upload

* Body: raw bytes. `Content-Length` when the size is known, otherwise chunked.
* If a size was declared, the body must match it exactly (`400 size_mismatch`).
* Receiver streams to a `0600` partial file in `<destination>/.localbridge-partial/`, in bounded
  chunks, hashing what it actually wrote. Response `{received, sha256}`.
* Sender compares that `sha256` with its own streamed hash.
* `finalize` re-checks the sender-supplied digest and size against the receiver's digest;
  on match the partial file is hard-linked to its final name (never overwriting an existing file;
  `name (1).ext` on collision) and the partial is unlinked. On mismatch => `422 integrity_failed`
  and nothing is saved.

A matching SHA-256 proves the bytes arrived as the sender read them. It says nothing about whether
the file is safe.

## Transfer lifecycle

```
CREATED -> AWAITING_APPROVAL -> APPROVED -> TRANSFERRING <-> VERIFYING -> COMPLETED
                 |                 |             |
                 +-> REJECTED      +-> EXPIRED   +-> FAILED / CANCELLED
```

`AWAITING_APPROVAL`/`APPROVED` expire after 180 s of inactivity. A transfer is `COMPLETED` only when
every file is verified and saved; otherwise it ends `FAILED` with per-file errors available.
Invalid transitions are rejected (`409 invalid_state`). `CANCELLED`, `REJECTED`, `FAILED`, `EXPIRED`
and `COMPLETED` are terminal.

## Limits (defaults, `Limits` dataclass; configurable in code)

| Limit | Default |
|---|---|
| Per-file size | 64 GiB |
| Aggregate per transfer | 256 GiB |
| Files per transfer | 1000 |
| Filename | 255 bytes (sanitised) |
| JSON body | 1 MiB |
| Concurrent transfers receiving / uploads | 2 / 4 |
| Pending approvals per session | 8 |
| Free-space margin after write | 64 MiB |

## Errors

`{"error": {"code": "<stable_code>", "message": "<safe text>"}}`. Messages never echo request
content. Notable codes: `unauthorized` 401, `pairing_invalid` 401, `pairing_denied` 403,
`not_found` 404, `not_approved` 409, `invalid_state` 409, `file_state` 409, `size_mismatch` 400,
`file_too_large`/`transfer_too_large`/`too_many_files`/`body_too_large` 413, `invalid_request` 422,
`integrity_failed` 422, `rate_limited`/`too_many_*` 429, `insufficient_storage`/`disk_full`/
`destination_*` 507.

## Folders

`FileSpec.dir` carries a relative folder (`"Trip/day1"`, `/`-separated, <= 16 levels). The receiver
**rejects the whole transfer** (`422 invalid_path`) if any component is `..`, `.`, empty, absolute, a
drive path, contains control/bidi characters or NULs, or ends in `.`/space; it never "repairs" a path.
Files are written with directory file descriptors and `O_NOFOLLOW`, so a symlink planted in the
destination cannot redirect a write (`409 unsafe_path`). The first folder component is created fresh
per transfer: if `Trip` already exists the transfer goes to `Trip (1)`, so nothing existing is merged
into. Empty folders are not transferred; duplicate file names get ` (n)`. Computer -> phone folders
use the same rules (`dir` and `group` fields on outbox items; the phone validates again before saving).

## Discovery (mDNS)

While receiving, the computer may advertise `_localbridge._tcp` bound to its chosen interface, with
TXT `{v:1, rid:<16 hex random>}` only. The QR carries the same `rid`. If the phone can't reach the
QR's address it looks up the `rid` and retries once at the announced private IPv4 address. Discovery
confers **no trust**: the pinned certificate and the QR secret are still required, and a certificate
mismatch is never retried.

## Computer -> phone

The desktop user adds files/text to an outbox addressed to the most recently paired phone. Nothing is
pushed: the phone polls `GET /v1/outbox` (so only phone->computer connections are ever needed, which
suits hotspots that block inbound connections to the phone). The phone shows each item and the
person must **Accept**; files are downloaded (resumable via `Range`), hashed, compared with the
advertised SHA-256, and only then published to Downloads/LocalBridge. Remote callers only ever supply
an item id; local file paths never leave the desktop, and a file modified after it was queued is
refused (`409 file_changed`).

## Not in v1

Saved/trusted devices, folder transfer,
reverse connections / relay, IPv6, mDNS discovery.

## Versioning

Path prefix `/v1`; QR carries `v=1`. A sender receiving another `v` refuses to pair.
