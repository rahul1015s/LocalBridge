# Threat model

LocalBridge has **not** been independently audited. This document states what the design is meant
to withstand, and what it is not.

## Assets

1. Contents of files being transferred (confidentiality, integrity).
2. The receiver's disk: only files the user approved, only in the chosen folder.
3. Pairing secret and session token (short-lived, in memory).
4. The user's attention at the approval prompt (it is the authorisation boundary).

## Trust boundaries

Phone app <-> local network <-> receiver HTTPS endpoint <-> desktop UI/process <-> filesystem.
The network is untrusted (shared Wi-Fi may contain hostile devices). The QR code/screen is the
trusted out-of-band channel.

## Attacker assumptions

* A. Another device on the same Wi-Fi/hotspot: can port-scan, connect, sniff, and inject packets.
* B. An attacker who can read the QR code (shoulder-surfing, screenshot, shared screen).
* C. A malicious or buggy sender that already holds a valid session.
* D. A hostile file name / metadata / oversized or slow upload.

Out of scope: malware on either device, a compromised OS or Android/GTK stack, physical access.

## Threats and mitigations

| Threat | Mitigation | Test |
|---|---|---|
| A: connects and sends files | Every endpoint needs a session token; token only issued after secret + **desktop confirmation** | `test_unauthenticated_requests_rejected`, `test_pairing_denied_by_user` |
| A: man-in-the-middle / impersonating the receiver | Sender pins SHA-256 of the receiver cert from the QR; mismatch fails closed | `PinnedTrustManagerTest`, `test_certificate_mismatch_fails_closed`, `InteropTest.wrongFingerprintFailsClosed` |
| A: brute-force pairing | 256-bit secret, single use, 120 s TTL, 5 failures/60 s/IP rate limit, bounded pending pairings | `test_failed_pairing_is_rate_limited` |
| A/B: replay of a captured QR/secret | Secret consumed on first valid use; new offer revokes the old; restart => new cert + new secrets | `test_pairing_secret_is_single_use`, `test_restart_gets_new_identity...` |
| C: sends data before approval | State machine refuses content unless `APPROVED`; approval is local-only | `test_content_before_approval...`, `test_sender_cannot_approve_remotely` |
| C: reads/cancels another session's transfer | Transfers bound to session; foreign access => 404 | `test_other_session_cannot_see_or_cancel_transfer` |
| D: path traversal / odd names | Filename reduced to one sanitised component; saved via `link()` into the chosen folder, never overwriting | `test_sanitize_filename`, `test_path_traversal_filename_is_confined`, `test_finalize_never_overwrites` |
| D: symlink tricks in partial dir | `O_EXCL|O_NOFOLLOW`, partial dir must be a real directory | `test_partial_perms_and_symlink_not_followed` |
| D: huge/lying sizes, slow disk fill | Declared + streamed size limits, free-space check, `ENOSPC` handling, bounded chunks (1 MiB), bounded concurrency | `test_file_size_limit_enforced_at_stream_time`, `test_insufficient_disk_space_refused` |
| D: corrupted/truncated upload | Receiver hashes bytes written; sender-supplied digest must match; partial discarded otherwise | `test_wrong_hash_fails...`, `test_interrupted_upload...` |
| D: oversized JSON | 1 MiB cap, strict models (`extra=forbid`), errors never echo input | `test_oversized_metadata_rejected`, `test_error_responses_do_not_echo_input` |
| Phone->computer link/text abuse | Text is displayed only; links open only on an explicit click; size capped at 64 KiB | `test_message_*` |
| Outbox abuse (fetching others' files / arbitrary paths) | Items bound to a session; ids unguessable; paths never sent to the phone; file change detection; phone must accept | `test_outbox_*` |
| Resume abuse (splicing data) | Offset must equal the bytes held; final SHA-256 covers the whole file | `test_wrong_offset_*`, interop resume test |
| Mobile-data use / leakage off the LAN | Only private IPv4 targets accepted; sockets bound to the Wi-Fi network when the receiver is on it | `PairingInfoTest`; binding is **not** unit-testable (see testing.md) |
| Receiver left running | Explicit Start/Stop; stop closes the socket, revokes sessions, wipes partials and the TLS dir | `test_receiver_stops_listening_and_cleans_up` |
| Secrets in logs | Access log disabled; secrets never logged; tokens kept as SHA-256 | review |
| Browser-origin attacks | No CORS headers, no cookies, bearer tokens, no docs/openapi endpoints | `test_no_docs_or_extra_web_endpoints` |

## What QR verification protects against — and what it can't

It binds the phone's connection to *the certificate shown on the screen it just scanned*, so a
network attacker cannot substitute their own endpoint. It **cannot** protect against someone who
replaces or overlays the QR code before you scan it (the attacker's code would be trusted), or
against a compromised screen. The desktop confirmation prompt shows the phone's name and IP so the
person can notice an unexpected pairing, but device names are self-reported and are **not** proof of
identity.

## Residual risks / unsupported guarantees

* No persistent device identity: every session is re-paired. (Safer than a weak trust store, less
  convenient.)
* A confirmed pairing from an attacker who also saw the QR within its 120 s window is possible if
  the user clicks *Pair* without checking.
* Approval dialogs show sender-declared file names/sizes; they are unverified claims.
* Transferred files are **not scanned for malware**. SHA-256 only proves integrity.
* Received files are never opened or executed by LocalBridge, but the user may open them.
* The cert fingerprint shown in the app is truncated for display only; pinning uses all 256 bits.
* Python/uvicorn/OpenSSL and Android TLS stacks are relied on for protocol correctness.
* Traffic metadata (IP addresses, sizes, timing) is visible to the local network.
* Hotspot client isolation can prevent connections entirely; that is an availability issue.
