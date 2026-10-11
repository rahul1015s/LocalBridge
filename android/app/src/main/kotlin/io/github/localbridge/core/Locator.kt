package io.github.localbridge.core

data class Endpoint(val host: String, val port: Int)

/** Finds a receiver announced over mDNS by its random id. Untrusted: the pinned certificate decides. */
fun interface Locator {
    suspend fun find(rid: String): Endpoint?
}

/**
 * Pair at the address from the QR code; if it can't be reached, look the computer up by receiver id
 * and try once at the announced address. A *certificate mismatch is never retried*: that's an
 * identity failure, not a connectivity one. Whatever address mDNS returns must still present the
 * pinned certificate, and only private IPv4 addresses are accepted.
 */
suspend fun <T> pairWithLocator(
    info: PairingInfo,
    locator: Locator?,
    attempt: suspend (PairingInfo) -> T,
): T {
    try {
        return attempt(info)
    } catch (e: ReceiverException) {
        val retryable = e.kind == ReceiverException.Kind.UNREACHABLE || e.kind == ReceiverException.Kind.TIMEOUT
        val rid = info.rid
        if (!retryable || rid == null || locator == null) throw e
        val ep = locator.find(rid) ?: throw e
        if (!PairingInfo.isLocalAddress(ep.host) || ep.port !in 1..65535) throw e
        if (ep.host == info.host && ep.port == info.port) throw e
        return attempt(info.at(ep.host, ep.port))
    }
}
