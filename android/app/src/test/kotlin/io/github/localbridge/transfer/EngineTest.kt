package io.github.localbridge.transfer

import io.github.localbridge.core.FileSpec
import io.github.localbridge.core.FileStatus
import io.github.localbridge.core.OutboxEntry
import io.github.localbridge.core.ReceiverException
import io.github.localbridge.core.ReceiverException.Kind
import io.github.localbridge.core.ReceiverGateway
import io.github.localbridge.core.RemoteFile
import io.github.localbridge.core.RemoteTransfer
import io.github.localbridge.core.UploadResult
import java.io.ByteArrayInputStream
import java.io.FileNotFoundException
import java.io.InputStream
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeFiles(val files: Map<String, FileInfo>) : FileAccess {
    override fun describe(ref: String) = files[ref] ?: throw FileNotFoundException(ref)
    override fun open(ref: String): InputStream = ByteArrayInputStream(ByteArray(0))
}

private class FakeGateway(var decision: String = "APPROVED", val gate: CompletableDeferred<Unit>? = null) : ReceiverGateway {
    var created: List<FileSpec>? = null
    var cancelled = false
    var pollsBeforeDecision = 2
    var polls = 0
    var uploadError: ReceiverException? = null
    val offsets = ArrayList<Long>()
    var failFirstUploadsWith: ReceiverException? = null // fail this many first uploads then succeed
    var failCount = 0
    var serverHas = 0L
    var running = 0
    var failTimes = 1
    var maxRunning = 0
    var pollError: ReceiverException? = null
    val uploaded = ArrayList<String>()
    val finalized = ArrayList<String>()

    override suspend fun createTransfer(files: List<FileSpec>): RemoteTransfer {
        created = files
        return RemoteTransfer("t1", "AWAITING_APPROVAL", null, files.mapIndexed { i, f -> RemoteFile("f$i", f.name, f.size, FileStatus.PENDING) })
    }

    override suspend fun getTransfer(transferId: String): RemoteTransfer {
        pollError?.let { throw it }
        polls++
        val state = if (polls >= pollsBeforeDecision) decision else "AWAITING_APPROVAL"
        return RemoteTransfer(transferId, state, null, created!!.mapIndexed { i, f -> RemoteFile("f$i", f.name, f.size, FileStatus.INTERRUPTED, serverHas) })
    }

    override suspend fun cancelTransfer(transferId: String) { cancelled = true }

    override suspend fun upload(transferId: String, fileId: String, declaredSize: Long?, offset: Long, open: () -> InputStream, onProgress: (Long) -> Unit): UploadResult {
        offsets += offset
        uploadError?.let { if (fileId == "f0") throw it }
        failFirstUploadsWith?.let { if (failCount < failTimes) { failCount++; throw it } }
        running++; maxRunning = maxOf(maxRunning, running)
        try { gate?.await(); kotlinx.coroutines.delay(50) } finally { running-- }
        val total = declaredSize ?: 1000L
        onProgress(total / 2); onProgress(total)
        uploaded += fileId
        return UploadResult("h$fileId", total)
    }

    override suspend fun sendMessage(text: String) {}
    override suspend fun listOutbox(): List<OutboxEntry> = emptyList()
    override suspend fun download(itemId: String, offset: Long, onChunk: (ByteArray, Int) -> Unit) {}
    override suspend fun outboxDone(itemId: String, status: String, detail: String?) {}

    override suspend fun finalize(transferId: String, fileId: String, sha256: String, size: Long): RemoteTransfer {
        finalized += fileId
        return RemoteTransfer(transferId, "COMPLETED", null, emptyList())
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class EngineTest {
    private val files = FakeFiles(mapOf("a" to FileInfo("a.mp4", 100), "b" to FileInfo("b.mp3", 200), "u" to FileInfo("stream.bin", null)))

    private fun TestScope.engine(listener: ItemListener? = null) =
        TransferEngine(this, files, nowMs = { testScheduler.currentTime }, pollIntervalMs = 100, maxPollFailures = 3, parallel = { par }, maxResumeAttempts = 3, retryDelayMs = { 10L }, listener = listener)
    private var par = 3

    @Test fun nothingIsSentBeforeApproval_thenAllFilesVerified() = runTest {
        val gw = FakeGateway().apply { pollsBeforeDecision = 5 }
        val finished = ArrayList<QueueItem>()
        val e = engine { item, _ -> finished += item }
        val id = e.enqueue(gw, "Laptop", listOf("a", "b"))!!
        runCurrent(); advanceTimeBy(250)
        assertEquals(Phase.WAITING_APPROVAL, e.batches.value.first().phase)
        assertTrue("no upload before approval", gw.uploaded.isEmpty())
        advanceUntilIdle()
        val b = e.batches.value.first { it.id == id }
        assertEquals(Phase.FINISHED, b.phase)
        assertTrue(b.items.all { it.state == ItemState.DONE })
        assertEquals(listOf("f0", "f1"), gw.finalized)
        assertEquals(2, finished.size)
    }

    @Test fun rejectionSendsNothing() = runTest {
        val gw = FakeGateway(decision = "REJECTED")
        val e = engine(); e.enqueue(gw, "Laptop", listOf("a")); advanceUntilIdle()
        assertTrue(gw.uploaded.isEmpty())
        assertEquals(ItemState.REJECTED, e.batches.value.first().items.first().state)
    }

    @Test fun expiredApprovalFails() = runTest {
        val gw = FakeGateway(decision = "EXPIRED")
        val e = engine(); e.enqueue(gw, "L", listOf("a")); advanceUntilIdle()
        assertEquals(ItemState.FAILED, e.batches.value.first().items.first().state)
    }

    @Test fun cancelMidTransferNotifiesReceiverAndMarksCancelled() = runTest {
        val gate = CompletableDeferred<Unit>()
        val gw = FakeGateway(gate = gate)
        val e = engine(); val id = e.enqueue(gw, "L", listOf("a", "b"))!!
        advanceTimeBy(500); runCurrent()
        e.cancel(id); advanceUntilIdle()
        assertTrue("receiver told to cancel", gw.cancelled)
        val b = e.batches.value.first()
        assertEquals(Phase.FINISHED, b.phase)
        assertTrue(b.items.all { it.state == ItemState.CANCELLED })
        assertEquals(0, e.activeCount.value)
    }

    @Test fun persistentNetworkLossEventuallyFailsFileAndAbortsRest() = runTest {
        par = 1
        val gw = FakeGateway().apply { uploadError = ReceiverException(Kind.UNREACHABLE, null, "Connection lost.") }
        val e = engine(); e.enqueue(gw, "L", listOf("a", "b")); advanceUntilIdle()
        val items = e.batches.value.first().items
        assertEquals(ItemState.FAILED, items[0].state)
        assertEquals("Connection lost.", items[0].error)
        assertEquals(ItemState.FAILED, items[1].state)
        assertTrue(gw.uploaded.isEmpty())
        assertEquals(4, gw.offsets.size) // 1 try + 3 resumes for the first file
    }

    @Test fun dropMidTransferResumesFromTheReceiversOffset() = runTest {
        val gw = FakeGateway().apply {
            failFirstUploadsWith = ReceiverException(Kind.UNREACHABLE, null, "Connection lost."); failTimes = 1; serverHas = 60
        }
        val e = engine(); e.enqueue(gw, "L", listOf("a")); advanceUntilIdle()
        assertEquals(listOf(0L, 60L), gw.offsets)
        assertEquals(ItemState.DONE, e.batches.value.first().items.first().state)
    }

    @Test fun offsetMismatchTriggersResyncNotFailure() = runTest {
        val gw = FakeGateway().apply {
            failFirstUploadsWith = ReceiverException(Kind.OTHER, "offset_mismatch", "Resume from byte 40."); serverHas = 40
        }
        val e = engine(); e.enqueue(gw, "L", listOf("a")); advanceUntilIdle()
        assertEquals(listOf(0L, 40L), gw.offsets)
        assertEquals(ItemState.DONE, e.batches.value.first().items.first().state)
    }

    @Test fun filesAreSentInParallelUpToTheConfiguredLimit() = runTest {
        par = 2
        val many = FakeFiles((0 until 5).associate { "f$it" to FileInfo("n$it", 10) })
        val gw = FakeGateway()
        val e = TransferEngine(this, many, nowMs = { testScheduler.currentTime }, pollIntervalMs = 100, parallel = { par })
        e.enqueue(gw, "L", (0 until 5).map { "f$it" }); advanceUntilIdle()
        assertEquals(2, gw.maxRunning)
        assertTrue(e.batches.value.first().items.all { it.state == ItemState.DONE })
    }

    @Test fun integrityFailureOnOneFileDoesNotAbortTheOthers() = runTest {
        val gw = FakeGateway().apply { uploadError = ReceiverException(Kind.INTEGRITY, "integrity_failed", "The file was corrupted in transit.") }
        val e = engine(); e.enqueue(gw, "L", listOf("a", "b")); advanceUntilIdle()
        val items = e.batches.value.first().items
        assertEquals(ItemState.FAILED, items[0].state)
        assertEquals(ItemState.DONE, items[1].state)
    }

    @Test fun pollingGivesUpAfterRepeatedNetworkFailures() = runTest {
        val gw = FakeGateway().apply { pollError = ReceiverException(Kind.TIMEOUT, null, "The computer didn't respond.") }
        val e = engine(); e.enqueue(gw, "L", listOf("a")); advanceUntilIdle()
        assertEquals(ItemState.FAILED, e.batches.value.first().items.first().state)
        assertEquals(Phase.FINISHED, e.batches.value.first().phase)
    }

    @Test fun unreadableFileFailsAloneAndMetadataIsInvalidatedGracefully() = runTest {
        val gw = FakeGateway()
        val e = engine(); e.enqueue(gw, "L", listOf("gone", "a")); advanceUntilIdle()
        val items = e.batches.value.first().items
        assertEquals(ItemState.FAILED, items[0].state)
        assertEquals(ItemState.DONE, items[1].state)
        assertEquals(1, gw.created!!.size) // the unreadable file was never declared
    }

    @Test fun allUnreadableMeansNoNetworkCall() = runTest {
        val gw = FakeGateway()
        val e = engine(); e.enqueue(gw, "L", listOf("gone")); advanceUntilIdle()
        assertNull(gw.created)
        assertEquals(Phase.FINISHED, e.batches.value.first().phase)
    }

    @Test fun unknownSizeHasNoFakePercentage() = runTest {
        val gate = CompletableDeferred<Unit>()
        val gw = FakeGateway(gate = gate)
        val e = engine(); e.enqueue(gw, "L", listOf("u")); advanceTimeBy(500); runCurrent()
        val item = e.batches.value.first().items.first()
        assertNull(item.size); assertNull(item.progress); assertNull(item.etaSeconds)
        assertNull(gw.created!!.first().size)
        gate.complete(Unit); advanceUntilIdle()
        assertEquals(ItemState.DONE, e.batches.value.first().items.first().state)
    }

    @Test fun duplicateInFlightFilesAreNotQueuedTwice() = runTest {
        val gate = CompletableDeferred<Unit>()
        val e = engine(); val gw = FakeGateway(gate = gate)
        assertNotNull(e.enqueue(gw, "L", listOf("a")))
        assertNull(e.enqueue(gw, "L", listOf("a")))
        assertEquals(1, e.batches.value.size)
        gate.complete(Unit); advanceUntilIdle()
    }

    @Test fun speedMeterReportsNothingUntilMeasurable() {
        var t = 0L
        val m = SpeedMeter { t }
        assertNull(m.sample(500)); t = 500; assertNull(m.sample(900))
        t = 1000; assertEquals(1000L, m.sample(1000))
    }

    @Test fun progressIsOnlyDefinedWithKnownSize() {
        assertEquals(0.5f, QueueItem("i", "r", "n", 200, sent = 100).progress)
        assertNull(QueueItem("i", "r", "n", null, sent = 100).progress)
        assertNull(QueueItem("i", "r", "n", 0, sent = 0).progress)
    }
}
