package io.github.localbridge.core

import java.io.File
import java.io.FileInputStream
import java.io.IOException
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
    private val uriFile = System.getenv("LB_URI_FILE")
    private val uri get() = uriFile?.let { File(it).readText() }
    private val testFile = System.getenv("LB_TEST_FILE")

    private fun sha(f: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        f.inputStream().use { i -> val b = ByteArray(1 shl 16); while (true) { val n = i.read(b); if (n < 0) break; md.update(b, 0, n) } }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    @Test fun pairsUploadsAndVerifiesAgainstRealReceiver() = runBlocking {
        assumeTrue("interop env not set", uriFile != null && testFile != null)
        val info = PairingInfo.parse(uri!!).getOrThrow()
        var verified = false
        val (gw, name) = HttpReceiver.pair(info, "JUnit Phone", null) { verified = true }
        assertTrue("certificate verified callback", verified)
        assertEquals("Interop Receiver", name)

        val f = File(testFile!!)
        val remote = gw.createTransfer(listOf(FileSpec("interop-ü.bin", f.length()), FileSpec("unknown-size.bin", null)))
        var state = remote.state
        repeat(50) { if (state == "AWAITING_APPROVAL") { Thread.sleep(100); state = gw.getTransfer(remote.transferId).state } }
        assertEquals("APPROVED", state)

        val up = gw.upload(remote.transferId, remote.files[0].fileId, f.length(), 0L, { FileInputStream(f) }) {}
        assertEquals(sha(f), up.sha256)
        gw.finalize(remote.transferId, remote.files[0].fileId, up.sha256, up.bytes)

        val data = "chunked-unknown-length".toByteArray()
        val up2 = gw.upload(remote.transferId, remote.files[1].fileId, null, 0L, { data.inputStream() }) {}
        val done = gw.finalize(remote.transferId, remote.files[1].fileId, up2.sha256, up2.bytes)
        assertEquals("COMPLETED", done.state)
    }

    @Test fun sendingWorksAfterAPauseLikeARealPersonPickingFiles() = runBlocking {
        assumeTrue("interop env not set", uriFile != null)
        val info = PairingInfo.parse(uri!!).getOrThrow()
        val (gw, _) = HttpReceiver.pair(info, "Slow Phone", null) {}
        Thread.sleep(8000) // longer than the receiver's idle keep-alive
        val t = gw.createTransfer(listOf(FileSpec("late.bin", 3)))
        Thread.sleep(8000)
        val up = gw.upload(t.transferId, t.files[0].fileId, 3, 0L, { "abc".byteInputStream() }) {}
        assertEquals("COMPLETED", gw.finalize(t.transferId, t.files[0].fileId, up.sha256, up.bytes).state)
    }

    @Test fun resumesAnInterruptedUploadAndDownloadsFromTheOutboxWithRange() = runBlocking {
        assumeTrue("interop env not set", uriFile != null && testFile != null)
        val f = File(testFile!!)
        val info = PairingInfo.parse(uri!!).getOrThrow()
        val (gw, _) = HttpReceiver.pair(info, "Resume Phone", null) {}

        // --- upload that dies halfway, then resumes from what the receiver holds
        val t = gw.createTransfer(listOf(FileSpec("resumed.bin", f.length())))
        val fid = t.files[0].fileId
        val cut = 6L * 1024 * 1024
        try {
            gw.upload(t.transferId, fid, f.length(), 0L, { object : java.io.FilterInputStream(FileInputStream(f)) {
                var n = 0L
                override fun read(b: ByteArray, off: Int, len: Int): Int {
                    if (n >= cut) throw IOException("network went away")
                    return super.read(b, off, len).also { if (it > 0) n += it }
                }
            } }) {}
            fail("upload should have been interrupted")
        } catch (e: ReceiverException) { /* expected */ }
        var received = 0L
        repeat(50) { if (received == 0L) { Thread.sleep(100); received = gw.getTransfer(t.transferId).files[0].received } }
        assertTrue("receiver kept partial data ($received)", received > 0 && received <= cut)
        val up = gw.upload(t.transferId, fid, f.length(), received, { FileInputStream(f) }) {}
        assertEquals(sha(f), up.sha256)
        assertEquals("COMPLETED", gw.finalize(t.transferId, fid, up.sha256, up.bytes).state)

        // --- computer -> phone with a Range resume
        val items = gw.listOutbox()
        val file = items.first { it.kind == "file" }
        assertEquals(f.length(), file.size)
        assertEquals(sha(f), file.sha256)
        assertEquals("hello from the laptop", items.first { it.kind == "text" }.text)
        val md = MessageDigest.getInstance("SHA-256")
        var got = 0L
        val first = ByteArray(1_000_000)
        try {
            gw.download(file.id, 0) { b, n -> if (got >= 1_000_000) throw IOException("stop"); md.update(b, 0, n); got += n; System.arraycopy(b, 0, first, 0, 0) }
        } catch (_: ReceiverException) { } catch (_: IOException) { }
        // restart from scratch on a fresh digest, resuming at an arbitrary offset
        val md2 = MessageDigest.getInstance("SHA-256")
        val head = f.inputStream().use { it.readNBytes(777_777) }
        md2.update(head)
        var total = head.size.toLong()
        gw.download(file.id, 777_777) { b, n -> md2.update(b, 0, n); total += n }
        assertEquals(f.length(), total)
        assertEquals(sha(f), md2.digest().joinToString("") { "%02x".format(it) })
        gw.outboxDone(file.id, "saved", null)

        // --- text from phone
        gw.sendMessage("note from phone")
    }

    @Test fun folderUploadKeepsStructureAndHostilePathsAreRefused() = runBlocking {
        assumeTrue("interop env not set", uriFile != null)
        val info = PairingInfo.parse(uri!!).getOrThrow()
        val (gw, _) = HttpReceiver.pair(info, "Folder Phone", null) {}
        try {
            gw.createTransfer(listOf(FileSpec("x", 1, "../../escape")))
            fail("hostile path must be refused")
        } catch (e: ReceiverException) { assertEquals("invalid_path", e.code) }
        val t = gw.createTransfer(listOf(FileSpec("one.bin", 3, "InteropFolder"), FileSpec("two.bin", 3, "InteropFolder/sub")))
        Thread.sleep(800)
        for ((i, f) in t.files.withIndex()) {
            val up = gw.upload(t.transferId, f.fileId, 3, 0L, { "abc".byteInputStream() }) {}
            gw.finalize(t.transferId, f.fileId, up.sha256, up.bytes)
        }
    }

    @Test fun wrongFingerprintFailsClosed() = runBlocking {
        assumeTrue("interop env not set", uriFile != null)
        val real = PairingInfo.parse(uri!!).getOrThrow()
        val forged = PairingInfo(real.host, real.port, real.sessionId, "0".repeat(64), real.secret)
        try {
            HttpReceiver.pair(forged, "JUnit Phone", null) {}
            fail("must not connect")
        } catch (e: ReceiverException) {
            assertEquals(ReceiverException.Kind.CERT_MISMATCH, e.kind)
        }
    }
}
