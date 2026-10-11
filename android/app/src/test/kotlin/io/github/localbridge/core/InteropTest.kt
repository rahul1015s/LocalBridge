package io.github.localbridge.core

import java.io.File
import java.io.FileInputStream
import java.security.MessageDigest
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Assume.assumeTrue
import org.junit.Test

/**
 * Real Kotlin client against the real Python receiver over TLS on loopback.
 * Runs only when scripts/interop_test.sh provides LB_PAIRING_URI + LB_TEST_FILE.
 * NOTE: loopback proves protocol/pinning interoperability, NOT hotspot behaviour.
 */
class InteropTest {
    private val uri = System.getenv("LB_PAIRING_URI")
    private val testFile = System.getenv("LB_TEST_FILE")

    private fun sha(f: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        f.inputStream().use { i -> val b = ByteArray(1 shl 16); while (true) { val n = i.read(b); if (n < 0) break; md.update(b, 0, n) } }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    @Test fun pairsUploadsAndVerifiesAgainstRealReceiver() = runBlocking {
        assumeTrue("interop env not set", uri != null && testFile != null)
        val info = PairingInfo.parse(uri!!).getOrThrow()
        var verified = false
        val (gw, name) = HttpReceiver.pair(info, "JUnit Phone") { verified = true }
        assertTrue("certificate verified callback", verified)
        assertEquals("Interop Receiver", name)

        val f = File(testFile!!)
        val remote = gw.createTransfer(listOf(FileSpec("interop-ü.bin", f.length()), FileSpec("unknown-size.bin", null)))
        var state = remote.state
        repeat(50) { if (state == "AWAITING_APPROVAL") { Thread.sleep(100); state = gw.getTransfer(remote.transferId).state } }
        assertEquals("APPROVED", state)

        val up = gw.upload(remote.transferId, remote.files[0].fileId, f.length(), { FileInputStream(f) }) {}
        assertEquals(sha(f), up.sha256)
        gw.finalize(remote.transferId, remote.files[0].fileId, up.sha256, up.bytes)

        val data = "chunked-unknown-length".toByteArray()
        val up2 = gw.upload(remote.transferId, remote.files[1].fileId, null, { data.inputStream() }) {}
        val done = gw.finalize(remote.transferId, remote.files[1].fileId, up2.sha256, up2.bytes)
        assertEquals("COMPLETED", done.state)
    }

    @Test fun wrongFingerprintFailsClosed() = runBlocking {
        assumeTrue("interop env not set", uri != null)
        val real = PairingInfo.parse(uri!!).getOrThrow()
        val forged = PairingInfo(real.host, real.port, real.sessionId, "0".repeat(64), real.secret)
        try {
            HttpReceiver.pair(forged, "JUnit Phone") {}
            fail("must not connect")
        } catch (e: ReceiverException) {
            assertEquals(ReceiverException.Kind.CERT_MISMATCH, e.kind)
        }
    }
}
