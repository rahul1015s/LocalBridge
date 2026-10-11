package io.github.localbridge.core

import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager
import okhttp3.ConnectionSpec
import okhttp3.EventListener
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.TlsVersion

class PinMismatchException(msg: String) : CertificateException(msg)

/**
 * Trusts exactly one certificate: the one whose SHA-256 (over the DER encoding) equals the
 * fingerprint delivered through the QR code. No chain building, no system CA store, and no
 * "trust on first use" — anything else fails closed. Used identically in debug and release.
 */
class PinnedTrustManager(expectedFingerprintHex: String) : X509TrustManager {
    private val expected: ByteArray = hexToBytes(expectedFingerprintHex)

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        throw CertificateException("Client certificates are not used")
    }

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull() ?: throw CertificateException("Empty certificate chain")
        leaf.checkValidity()
        val actual = MessageDigest.getInstance("SHA-256").digest(leaf.encoded)
        if (!MessageDigest.isEqual(actual, expected)) {
            throw PinMismatchException("Receiver certificate does not match the scanned code")
        }
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()

    companion object {
        fun hexToBytes(hex: String): ByteArray {
            require(hex.length % 2 == 0) { "bad hex" }
            return ByteArray(hex.length / 2) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
        }
    }
}

object PinnedTls {
    /** Interface so UI can show "certificate verified" the moment the handshake succeeds. */
    fun interface HandshakeListener {
        fun onVerified()
    }

    fun client(
        fingerprintHex: String,
        socketFactory: javax.net.SocketFactory? = null,
        onVerified: HandshakeListener? = null,
    ): OkHttpClient {
        val tm = PinnedTrustManager(fingerprintHex)
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf(tm), null)
        val spec = ConnectionSpec.Builder(ConnectionSpec.RESTRICTED_TLS)
            .tlsVersions(TlsVersion.TLS_1_3, TlsVersion.TLS_1_2)
            .build()
        return OkHttpClient.Builder()
            .apply { if (socketFactory != null) socketFactory(socketFactory) }
            .sslSocketFactory(ctx.socketFactory, tm)
            .connectionSpecs(listOf(spec)) // no cleartext fallback
            .protocols(listOf(Protocol.HTTP_1_1))
            .followRedirects(false)
            .followSslRedirects(false)
            // Retry transparently when a pooled connection was closed by the peer while idle, and
            // never keep idle connections longer than the receiver would.
            .retryOnConnectionFailure(true)
            .connectionPool(okhttp3.ConnectionPool(2, 3, TimeUnit.SECONDS))
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS) // per-call timeouts are set where needed
            .writeTimeout(60, TimeUnit.SECONDS)
            .callTimeout(0, TimeUnit.SECONDS)
            .eventListener(object : EventListener() {
                override fun secureConnectEnd(call: okhttp3.Call, handshake: okhttp3.Handshake?) {
                    if (handshake != null) onVerified?.onVerified()
                }
            })
            .build()
    }

    fun isPinFailure(t: Throwable): Boolean {
        var c: Throwable? = t
        while (c != null) {
            if (c is PinMismatchException) return true
            c = c.cause
        }
        return false
    }
}
