package io.github.localbridge.core

import java.net.URI

/** Contents of the pairing QR code. The secret is single-use and short-lived on the receiver. */
class PairingInfo(
    val host: String,
    val port: Int,
    val sessionId: String,
    val fingerprint: String,
    val secret: String,
    /** Random receiver id advertised over mDNS; lets the phone locate the computer. Optional. */
    val rid: String? = null,
) {
    fun at(host: String, port: Int) = PairingInfo(host, port, sessionId, fingerprint, secret, rid)

    // Never print the secret.
    override fun toString() = "PairingInfo(host=$host, port=$port)"

    companion object {
        private val IPV4 = Regex("""^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$""")
        private val ID = Regex("""^[A-Za-z0-9_-]{8,64}$""")
        private val FP = Regex("""^[0-9a-f]{64}$""")
        private val SECRET = Regex("""^[A-Za-z0-9_-]{16,128}$""")
        private val RID = Regex("""^[0-9a-f]{16}$""")

        /** True for private, link-local, CGNAT and loopback IPv4 (never public addresses). */
        fun isLocalAddress(ip: String): Boolean {
            val m = IPV4.matchEntire(ip) ?: return false
            val o = m.groupValues.drop(1).map { it.toInt() }
            if (o.any { it > 255 }) return false
            return o[0] == 10 || o[0] == 127 ||
                (o[0] == 172 && o[1] in 16..31) ||
                (o[0] == 192 && o[1] == 168) ||
                (o[0] == 169 && o[1] == 254) ||
                (o[0] == 100 && o[1] in 64..127)
        }

        fun parse(raw: String): Result<PairingInfo> = runCatching {
            val text = raw.trim()
            require(text.length <= 1024) { "Code is too long." }
            val uri = URI(text)
            require(uri.scheme == "localbridge" && uri.host == "pair") { "This is not a LocalBridge code." }
            val q = (uri.rawQuery ?: "").split('&').filter { it.isNotEmpty() }.associate {
                val i = it.indexOf('=')
                require(i > 0) { "Malformed code." }
                it.substring(0, i) to it.substring(i + 1)
            }
            require(q["v"] == "1") { "This code needs a newer or older LocalBridge version." }
            val host = q["host"].orEmpty()
            require(isLocalAddress(host)) { "The computer's address is not on a local network." }
            val port = q["port"]?.toIntOrNull() ?: throw IllegalArgumentException("Malformed code.")
            require(port in 1..65535) { "Malformed code." }
            val sid = q["sid"].orEmpty()
            val fp = q["fp"].orEmpty()
            val secret = q["secret"].orEmpty()
            require(ID.matches(sid) && FP.matches(fp) && SECRET.matches(secret)) { "Malformed code." }
            val rid = q["rid"]
            require(rid == null || RID.matches(rid)) { "Malformed code." }
            PairingInfo(host, port, sid, fp, secret, rid)
        }
    }
}
