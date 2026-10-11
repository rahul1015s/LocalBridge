package io.github.localbridge.core

import java.io.ByteArrayInputStream
import java.io.IOException
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

private fun cert(name: String): X509Certificate =
    CertificateFactory.getInstance("X.509").generateCertificate(
        object {}.javaClass.getResourceAsStream("/$name.pem")!!,
    ) as X509Certificate

private fun fp(c: X509Certificate) = MessageDigest.getInstance("SHA-256").digest(c.encoded).joinToString("") { "%02x".format(it) }

class PinnedTrustManagerTest {
    @Test fun acceptsExactlyThePinnedCertificate() {
        val c = cert("valid")
        PinnedTrustManager(fp(c)).checkServerTrusted(arrayOf(c), "ECDHE_ECDSA")
    }

    @Test fun rejectsAnyOtherCertificateEvenIfSelfSignedAndValid() {
        try {
            PinnedTrustManager(fp(cert("valid"))).checkServerTrusted(arrayOf(cert("other")), "ECDHE_ECDSA")
            fail("must fail closed")
        } catch (e: PinMismatchException) {
            assertTrue(PinnedTls.isPinFailure(e))
        }
    }

    @Test fun rejectsExpiredCertificateEvenWhenFingerprintMatches() {
        val c = cert("expired")
        try {
            PinnedTrustManager(fp(c)).checkServerTrusted(arrayOf(c), "ECDHE_ECDSA")
            fail("expired must be rejected")
        } catch (e: CertificateException) { /* ok */ }
    }

    @Test fun rejectsEmptyChainAndClientAuth() {
        val tm = PinnedTrustManager(fp(cert("valid")))
        try { tm.checkServerTrusted(emptyArray(), "x"); fail() } catch (_: CertificateException) {}
        try { tm.checkClientTrusted(arrayOf(cert("valid")), "x"); fail() } catch (_: CertificateException) {}
        assertEquals(0, tm.acceptedIssuers.size)
    }
}

class PairingInfoTest {
    private val fp = "a".repeat(64)
    private fun uri(host: String = "192.168.43.1", extra: String = "", v: String = "1", port: String = "40000") =
        "localbridge://pair?v=$v&host=$host&port=$port&sid=abcdEFGH1234&fp=$fp&secret=${"s".repeat(43)}$extra"

    @Test fun parsesValid() {
        val i = PairingInfo.parse(uri()).getOrThrow()
        assertEquals("192.168.43.1", i.host); assertEquals(40000, i.port); assertEquals(fp, i.fingerprint)
    }

    @Test fun neverPrintsSecret() = assertFalse(PairingInfo.parse(uri()).getOrThrow().toString().contains("sss"))

    @Test fun rejectsPublicAndMalformedHosts() {
        listOf("8.8.8.8", "example.com", "300.1.1.1", "192.168.1", "172.32.0.1", "").forEach {
            assertTrue("$it should be rejected", PairingInfo.parse(uri(host = it)).isFailure)
        }
    }

    @Test fun acceptsPrivateRanges() {
        listOf("10.1.2.3", "172.16.0.5", "172.31.255.1", "192.168.0.9", "169.254.1.1", "100.64.0.1", "127.0.0.1").forEach {
            assertTrue("$it should be accepted", PairingInfo.parse(uri(host = it)).isSuccess)
        }
    }

    @Test fun rejectsBadVersionPortSchemeAndFingerprint() {
        assertTrue(PairingInfo.parse(uri(v = "2")).isFailure)
        assertTrue(PairingInfo.parse(uri(port = "0")).isFailure)
        assertTrue(PairingInfo.parse(uri(port = "70000")).isFailure)
        assertTrue(PairingInfo.parse("https://pair?v=1").isFailure)
        assertTrue(PairingInfo.parse(uri().replace(fp, "zz")).isFailure)
        assertTrue(PairingInfo.parse(uri().replace(fp, "A".repeat(64))).isFailure) // fingerprint must be lowercase hex
        assertTrue(PairingInfo.parse("garbage").isFailure)
        assertTrue(PairingInfo.parse("x".repeat(5000)).isFailure)
    }
}

class HashingStreamBodyTest {
    @Test fun streamsAndHashesWithBoundedBuffer() {
        val data = ByteArray(3 * HashingStreamBody.BUFFER + 17) { (it % 251).toByte() }
        val progress = ArrayList<Long>()
        val body = HashingStreamBody({ ByteArrayInputStream(data) }, data.size.toLong()) { progress += it }
        val sink = Buffer(); body.writeTo(sink)
        assertEquals(data.size.toLong(), sink.size)
        assertEquals(MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }, body.sha256Hex)
        assertEquals(data.size.toLong(), progress.last())
        assertTrue(progress.zipWithNext().all { (a, b) -> b > a })
    }

    @Test fun unknownSizeUsesChunkedAndStillHashes() {
        val body = HashingStreamBody({ ByteArrayInputStream("abc".toByteArray()) }, null) {}
        assertEquals(-1L, body.contentLength())
        body.writeTo(Buffer())
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", body.sha256Hex)
    }

    @Test fun detectsFileChangingSizeWhileSending() {
        try { HashingStreamBody({ ByteArrayInputStream(ByteArray(5)) }, 10) {}.writeTo(Buffer()); fail() } catch (_: IOException) {}
        try { HashingStreamBody({ ByteArrayInputStream(ByteArray(15)) }, 10) {}.writeTo(Buffer()); fail() } catch (_: IOException) {}
    }

    @Test fun errorMappingNeverLeaksAsRawExceptions() {
        assertNotNull(mapIo(java.net.ConnectException("refused")).message)
        assertEquals(ReceiverException.Kind.TIMEOUT, mapIo(java.net.SocketTimeoutException()).kind)
        assertEquals(ReceiverException.Kind.CERT_MISMATCH, mapIo(javax.net.ssl.SSLHandshakeException("x").apply { initCause(PinMismatchException("p")) }).kind)
    }
}
