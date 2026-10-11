package io.github.localbridge.transfer

import io.github.localbridge.core.FileSpec
import io.github.localbridge.core.OutboxEntry
import io.github.localbridge.core.ReceiverException
import io.github.localbridge.core.ReceiverException.Kind
import io.github.localbridge.core.ReceiverGateway
import io.github.localbridge.core.RemoteTransfer
import io.github.localbridge.core.UploadResult
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

private fun sha(b: ByteArray) = MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) }

private class MemSink : FileSink {
    val out = ByteArrayOutputStream()
    var committed = false
    var aborted = false
    var created = 0
    override fun create(displayName: String): FileSink.Target {
        created++
        return object : FileSink.Target {
            override val stream: OutputStream = out
            override fun commit(): String { committed = true; return "content://saved/1" }
            override fun abort() { aborted = true }
        }
    }
}

private class OutboxGateway(val entries: MutableList<OutboxEntry>, val data: ByteArray) : ReceiverGateway {
    val done = ArrayList<Pair<String, String>>()
    val offsets = ArrayList<Long>()
    var dropAfter: Int? = null // bytes to deliver before the first connection drop
    var corrupt = false
    override suspend fun listOutbox() = entries.toList()
    override suspend fun download(itemId: String, offset: Long, onChunk: (ByteArray, Int) -> Unit) {
        offsets += offset
        val payload = if (corrupt) data.copyOf().also { it[0] = (it[0] + 1).toByte() } else data
        val limit = dropAfter?.also { dropAfter = null }
        var pos = offset.toInt()
        while (pos < payload.size) {
            val n = minOf(64, payload.size - pos)
            if (limit != null && pos + n > limit) throw ReceiverException(Kind.UNREACHABLE, null, "Connection lost.")
            onChunk(payload.copyOfRange(pos, pos + n), n)
            pos += n
        }
    }
    override suspend fun outboxDone(itemId: String, status: String, detail: String?) { done += itemId to status }
    override suspend fun createTransfer(files: List<FileSpec>): RemoteTransfer = error("unused")
    override suspend fun getTransfer(transferId: String): RemoteTransfer = error("unused")
    override suspend fun cancelTransfer(transferId: String) {}
    override suspend fun upload(transferId: String, fileId: String, declaredSize: Long?, offset: Long, open: () -> InputStream, onProgress: (Long) -> Unit): UploadResult = error("unused")
    override suspend fun finalize(transferId: String, fileId: String, sha256: String, size: Long): RemoteTransfer = error("unused")
    override suspend fun sendMessage(text: String) {}
}

@OptIn(ExperimentalCoroutinesApi::class)
class IncomingTest {
    private val data = ByteArray(1000) { (it * 7).toByte() }
    private fun entry(hash: String? = sha(data)) = OutboxEntry("i1", "file", "report.pdf", data.size.toLong(), hash, null)

    @Test fun nothingIsSavedUntilTheOfferIsAccepted() = runTest {
        val sink = MemSink(); val gw = OutboxGateway(mutableListOf(entry()), data)
        val e = IncomingEngine(this, sink, pollMs = 100, retryDelayMs = { 1 })
        e.start(gw); advanceTimeBy(350); runCurrent()
        assertEquals(InState.OFFERED, e.items.value.single().state)
        assertEquals(0, sink.created); assertTrue(gw.offsets.isEmpty())
        e.stop()
    }

    @Test fun acceptDownloadsVerifiesAndPublishes() = runTest {
        val sink = MemSink(); val gw = OutboxGateway(mutableListOf(entry()), data)
        val e = IncomingEngine(this, sink, pollMs = 100, retryDelayMs = { 1 })
        e.start(gw); advanceTimeBy(150); runCurrent()
        e.accept("i1"); advanceTimeBy(500); runCurrent()
        assertEquals(InState.SAVED, e.items.value.single().state)
        assertTrue(sink.committed); assertFalse(sink.aborted)
        assertArrayEquals(data, sink.out.toByteArray())
        assertEquals(listOf("i1" to "saved"), gw.done)
        e.stop()
    }

    @Test fun corruptedDownloadIsNeverPublished() = runTest {
        val sink = MemSink(); val gw = OutboxGateway(mutableListOf(entry()), data).apply { corrupt = true }
        val e = IncomingEngine(this, sink, pollMs = 100, retryDelayMs = { 1 })
        e.start(gw); advanceTimeBy(150); runCurrent(); e.accept("i1"); advanceTimeBy(500); runCurrent()
        assertEquals(InState.FAILED, e.items.value.single().state)
        assertTrue(sink.aborted); assertFalse(sink.committed)
        assertEquals(listOf("i1" to "failed"), gw.done)
        e.stop()
    }

    @Test fun networkDropResumesWithRangeAndStillVerifies() = runTest {
        val sink = MemSink(); val gw = OutboxGateway(mutableListOf(entry()), data).apply { dropAfter = 300 }
        val e = IncomingEngine(this, sink, pollMs = 100, retryDelayMs = { 1 })
        e.start(gw); advanceTimeBy(150); runCurrent(); e.accept("i1"); advanceTimeBy(500); runCurrent()
        assertEquals(InState.SAVED, e.items.value.single().state)
        assertEquals(2, gw.offsets.size); assertTrue(gw.offsets[1] in 1..300)
        assertArrayEquals(data, sink.out.toByteArray())
        e.stop()
    }

    @Test fun declineTellsTheComputerAndDownloadsNothing() = runTest {
        val sink = MemSink(); val gw = OutboxGateway(mutableListOf(entry()), data)
        val e = IncomingEngine(this, sink, pollMs = 100)
        e.start(gw); advanceTimeBy(150); runCurrent(); e.decline("i1"); advanceTimeBy(100); runCurrent()
        assertEquals(listOf("i1" to "declined"), gw.done)
        assertEquals(0, sink.created)
        e.stop()
    }

    @Test fun sameOfferIsNotListedTwice() = runTest {
        val gw = OutboxGateway(mutableListOf(entry()), data)
        val e = IncomingEngine(this, MemSink(), pollMs = 50)
        e.start(gw); advanceTimeBy(500); runCurrent()
        assertEquals(1, e.items.value.size)
        e.stop()
    }

    @Test fun textOffersNeedNoDownload() = runTest {
        val sink = MemSink()
        val gw = OutboxGateway(mutableListOf(OutboxEntry("t1", "text", "Text", 5, null, "hello")), data)
        val e = IncomingEngine(this, sink, pollMs = 100)
        e.start(gw); advanceTimeBy(150); runCurrent(); e.accept("t1"); advanceTimeBy(100); runCurrent()
        assertEquals(InState.SAVED, e.items.value.single().state)
        assertEquals(0, sink.created)
        e.stop()
    }
}

class SafeFileNameTest {
    @Test fun stripsPathsControlCharsAndTricks() {
        assertEquals("passwd", safeFileName("../../etc/passwd"))
        assertEquals("x.dll", safeFileName("..\\..\\win\\x.dll"))
        assertEquals("ab.txt", safeFileName("a\u0000b.txt"))
        assertEquals("file", safeFileName(".."))
        assertEquals("file", safeFileName("   "))
        assertEquals("evilgnp.exe", safeFileName("evil\u202egnp.exe"))
    }

    @Test fun longNamesKeepTheExtension() {
        val n = safeFileName("a".repeat(500) + ".mp4")
        assertTrue(n.length <= 200 && n.endsWith(".mp4"))
    }
}
