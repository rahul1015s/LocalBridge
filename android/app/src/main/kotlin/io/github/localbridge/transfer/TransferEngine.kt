package io.github.localbridge.transfer

import io.github.localbridge.core.FileSpec
import io.github.localbridge.core.ReceiverException
import io.github.localbridge.core.ReceiverException.Kind
import io.github.localbridge.core.ReceiverGateway
import java.io.IOException
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class ItemState { QUEUED, WAITING, SENDING, VERIFYING, DONE, FAILED, CANCELLED, REJECTED;
    val finished get() = this == DONE || this == FAILED || this == CANCELLED || this == REJECTED
}

enum class Phase { PREPARING, WAITING_APPROVAL, SENDING, FINISHED }

data class QueueItem(
    val id: String,
    val ref: String,
    val name: String,
    val size: Long?,
    val state: ItemState = ItemState.QUEUED,
    val sent: Long = 0,
    /** Bytes/second measured over a sliding window; null until enough data exists. */
    val speedBps: Long? = null,
    val error: String? = null,
    /** Transient status such as "Reconnecting…"; cleared when sending continues. */
    val note: String? = null,
    /** Relative folder on the receiver, e.g. "Trip/day1"; null for loose files. */
    val dir: String? = null,
) {
    /** Only meaningful when the size is actually known. */
    val progress: Float? get() = if (size != null && size > 0) (sent.toFloat() / size).coerceIn(0f, 1f) else null
    val etaSeconds: Long? get() = if (size != null && speedBps != null && speedBps > 0 && state == ItemState.SENDING)
        ((size - sent) / speedBps).coerceAtLeast(0) else null
}

data class Batch(
    val id: String,
    val receiverName: String,
    val items: List<QueueItem>,
    val phase: Phase = Phase.PREPARING,
    val message: String? = null,
) {
    val active get() = phase != Phase.FINISHED
}

fun interface ItemListener {
    fun onFinished(item: QueueItem, receiverName: String)
}

class TransferEngine(
    private val scope: CoroutineScope,
    private val files: FileAccess,
    private val nowMs: () -> Long = System::currentTimeMillis,
    private val pollIntervalMs: Long = 700,
    private val maxPollFailures: Int = 5,
    private val parallel: () -> Int = { 3 },
    private val maxResumeAttempts: Int = 40,
    private val retryDelayMs: (Int) -> Long = { minOf(1500L * it, 10_000L) },
    private val listener: ItemListener? = null,
) {
    private val _batches = MutableStateFlow<List<Batch>>(emptyList())
    val batches: StateFlow<List<Batch>> = _batches.asStateFlow()
    val activeCount: StateFlow<Int> get() = _active
    private val _active = MutableStateFlow(0)
    private val jobs = HashMap<String, Job>()
    private val remoteIds = HashMap<String, String>()

    /** Queue files. Duplicate in-flight references are ignored to prevent accidental double sends. */
    fun enqueue(
        gateway: ReceiverGateway,
        receiverName: String,
        refs: List<String>,
        dirs: Map<String, String> = emptyMap(),
    ): String? {
        val inFlight = _batches.value.filter { it.active }.flatMap { it.items }.map { it.ref }.toSet()
        val fresh = refs.distinct().filter { it !in inFlight }
        if (fresh.isEmpty()) return null
        val items = fresh.map { QueueItem(UUID.randomUUID().toString(), it, it.substringAfterLast('/'), null, dir = dirs[it]) }
        val batch = Batch(UUID.randomUUID().toString(), receiverName, items)
        _batches.update { listOf(batch) + it }
        publishActive()
        jobs[batch.id] = scope.launch {
            try {
                run(gateway, batch.id)
            } catch (e: CancellationException) {
                withContext(NonCancellable) { onCancelled(gateway, batch.id) }
                throw e
            } finally {
                jobs.remove(batch.id)
                publishActive()
            }
        }
        return batch.id
    }

    fun cancel(batchId: String) {
        jobs[batchId]?.cancel()
    }

    fun cancelAll() = jobs.values.toList().forEach { it.cancel() }

    fun clearFinished() = _batches.update { list -> list.filter { it.active } }

    fun batchFlow(id: String) = _batches.map { l -> l.firstOrNull { it.id == id } }

    private fun publishActive() {
        _active.value = _batches.value.count { it.active }
    }

    // -- state helpers --------------------------------------------------------------------
    private fun updateBatch(id: String, f: (Batch) -> Batch) =
        _batches.update { l -> l.map { if (it.id == id) f(it) else it } }

    private fun updateItem(id: String, itemId: String, f: (QueueItem) -> QueueItem) =
        updateBatch(id) { b -> b.copy(items = b.items.map { if (it.id == itemId) f(it) else it }) }

    private fun batch(id: String) = _batches.value.first { it.id == id }

    private fun finishItem(id: String, itemId: String, state: ItemState, error: String? = null) {
        updateItem(id, itemId) { it.copy(state = state, error = error, speedBps = null) }
        val b = batch(id)
        listener?.onFinished(b.items.first { it.id == itemId }, b.receiverName)
    }

    // -- the transfer ------------------------------------------------------------------------
    private suspend fun run(gateway: ReceiverGateway, id: String) {
        // 1. Resolve metadata; unreadable files fail individually without blocking the rest.
        val readable = ArrayList<QueueItem>()
        for (item in batch(id).items) {
            try {
                val info = files.describe(item.ref)
                updateItem(id, item.id) { it.copy(name = info.name, size = info.size) }
                readable += item.copy(name = info.name, size = info.size)
            } catch (e: IOException) {
                finishItem(id, item.id, ItemState.FAILED, "This file can't be read any more.")
            }
        }
        if (readable.isEmpty()) return finish(id, "None of the selected files could be read.")

        // 2. Declare the transfer. Nothing is sent until the person approves it on the computer.
        val remote = try {
            gateway.createTransfer(readable.map { FileSpec(it.name, it.size, it.dir) })
        } catch (e: ReceiverException) {
            readable.forEach { finishItem(id, it.id, ItemState.FAILED, e.message) }
            return finish(id, e.message)
        }
        remoteIds[id] = remote.transferId
        check(remote.files.size == readable.size) { "receiver returned a different file list" }

        readable.forEach { updateItem(id, it.id) { i -> i.copy(state = ItemState.WAITING) } }
        updateBatch(id) { it.copy(phase = Phase.WAITING_APPROVAL, message = "Waiting for approval on the computer…") }

        // 3. Poll for the decision.
        var failures = 0
        var state = remote.state
        while (state == "AWAITING_APPROVAL") {
            delay(pollIntervalMs)
            try {
                state = gateway.getTransfer(remote.transferId).state
                failures = 0
            } catch (e: ReceiverException) {
                if (e.kind == Kind.UNAUTHORIZED || ++failures >= maxPollFailures) {
                    readable.forEach { finishItem(id, it.id, ItemState.FAILED, e.message) }
                    return finish(id, e.message)
                }
            }
        }
        if (state != "APPROVED") {
            val (st, msg) = when (state) {
                "REJECTED" -> ItemState.REJECTED to "The computer declined this transfer."
                "EXPIRED" -> ItemState.FAILED to "The request timed out before it was approved."
                "CANCELLED" -> ItemState.CANCELLED to "Cancelled."
                else -> ItemState.FAILED to "Transfer ended ($state)."
            }
            readable.forEach { finishItem(id, it.id, st, msg) }
            return finish(id, msg)
        }

        // 4. Send files (several at once), streaming and hashing as we go. A dropped connection
        //    resumes from the byte the receiver already holds instead of starting over.
        updateBatch(id) { it.copy(phase = Phase.SENDING, message = null) }
        val abortReason = java.util.concurrent.atomic.AtomicReference<String?>(null)
        val gate = kotlinx.coroutines.sync.Semaphore(parallel().coerceIn(1, 4))
        coroutineScope {
            readable.forEachIndexed { index, item ->
                launch {
                    gate.withPermit { sendOne(gateway, id, remote.transferId, remote.files[index], item, abortReason) }
                }
            }
        }
        finish(id, null)
    }

    private suspend fun sendOne(
        gateway: ReceiverGateway,
        id: String,
        transferId: String,
        rf: io.github.localbridge.core.RemoteFile,
        item: QueueItem,
        abortReason: java.util.concurrent.atomic.AtomicReference<String?>,
    ) {
        abortReason.get()?.let { return finishItem(id, item.id, ItemState.FAILED, it) }
        updateItem(id, item.id) { it.copy(state = ItemState.SENDING, sent = 0) }
        val meter = SpeedMeter(nowMs)
        var lastPublish = 0L
        var offset = 0L
        var attempt = 0
        try {
            while (true) {
                try {
                    val up = gateway.upload(transferId, rf.fileId, item.size, offset, { files.open(item.ref) }) { sent ->
                        val t = nowMs()
                        if (t - lastPublish >= 100) {
                            lastPublish = t
                            val speed = meter.sample(sent)
                            updateItem(id, item.id) { it.copy(sent = sent, speedBps = speed, note = null) }
                        }
                    }
                    updateItem(id, item.id) { it.copy(state = ItemState.VERIFYING, sent = up.bytes, size = it.size ?: up.bytes, note = null) }
                    gateway.finalize(transferId, rf.fileId, up.sha256, up.bytes)
                    updateItem(id, item.id) { it.copy(sent = up.bytes) }
                    return finishItem(id, item.id, ItemState.DONE)
                } catch (e: ReceiverException) {
                    val resumable = e.kind == Kind.UNREACHABLE || e.kind == Kind.TIMEOUT ||
                        e.code == "offset_mismatch" || e.code == "file_state"
                    if (!resumable || ++attempt > maxResumeAttempts) throw e
                    updateItem(id, item.id) { it.copy(note = "Connection lost — reconnecting ($attempt)…", speedBps = null) }
                    delay(retryDelayMs(attempt))
                    // Ask the receiver how much it really has; keep waiting while the network is down.
                    offset = try {
                        gateway.getTransfer(transferId).files.firstOrNull { it.fileId == rf.fileId }?.received ?: offset
                    } catch (_: ReceiverException) {
                        offset
                    }
                }
            }
        } catch (e: ReceiverException) {
            finishItem(id, item.id, ItemState.FAILED, e.message)
            if (e.kind in setOf(Kind.UNREACHABLE, Kind.TIMEOUT, Kind.UNAUTHORIZED, Kind.CERT_MISMATCH, Kind.STORAGE)) {
                abortReason.compareAndSet(null, e.message)
            }
        } catch (e: IOException) {
            // Local read failure (file vanished mid-send, permission revoked).
            finishItem(id, item.id, ItemState.FAILED, "Couldn't read this file while sending.")
        }
    }

    private fun finish(id: String, message: String?) =
        updateBatch(id) { it.copy(phase = Phase.FINISHED, message = message) }

    private suspend fun onCancelled(gateway: ReceiverGateway, id: String) {
        remoteIds[id]?.let { runCatching { gateway.cancelTransfer(it) } }
        _batches.value.firstOrNull { it.id == id }?.items?.forEach {
            if (!it.state.finished) finishItem(id, it.id, ItemState.CANCELLED, "Cancelled.")
        }
        finish(id, "Cancelled.")
    }
}

/** Rolling speed estimate: exponential average over >=1s windows. Returns null until measurable. */
class SpeedMeter(private val nowMs: () -> Long) {
    private var t0 = nowMs()
    private var b0 = 0L
    private var ema: Double? = null
    private var shown: Long? = null

    fun sample(bytes: Long): Long? {
        val now = nowMs()
        val dt = now - t0
        if (dt >= 1000) {
            val inst = (bytes - b0) * 1000.0 / dt
            ema = ema?.let { it * 0.6 + inst * 0.4 } ?: inst
            shown = ema!!.toLong()
            t0 = now
            b0 = bytes
        }
        return shown
    }
}
