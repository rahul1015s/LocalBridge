package io.github.localbridge.transfer

import io.github.localbridge.core.OutboxEntry
import io.github.localbridge.core.ReceiverException
import io.github.localbridge.core.ReceiverException.Kind
import io.github.localbridge.core.ReceiverGateway
import java.io.IOException
import java.io.OutputStream
import java.security.MessageDigest
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class InState { OFFERED, DOWNLOADING, SAVED, FAILED, DECLINED, DISMISSED }

data class IncomingItem(
    val entry: OutboxEntry,
    val state: InState = InState.OFFERED,
    val received: Long = 0,
    val note: String? = null,
    val error: String? = null,
    /** Where the file ended up (a content:// URI), once saved. */
    val savedUri: String? = null,
    val speedBps: Long? = null,
) {
    val progress: Float? get() = if (entry.size > 0) (received.toFloat() / entry.size).coerceIn(0f, 1f) else null
}

/** Where received files are written. Android: MediaStore Downloads/LocalBridge. */
interface FileSink {
    interface Target {
        val stream: OutputStream
        /** Finish and publish; returns a URI string the person can open. */
        fun commit(): String
        /** Throw the partial file away. */
        fun abort()
    }

    fun create(displayName: String): Target
}

/**
 * Files and text the computer chose to send. Nothing is saved until the person accepts on the phone;
 * downloads resume after network drops and are SHA-256 verified before being published.
 */
class IncomingEngine(
    private val scope: CoroutineScope,
    private val sink: FileSink,
    private val pollMs: Long = 2500,
    private val maxResumeAttempts: Int = 40,
    private val retryDelayMs: (Int) -> Long = { minOf(1500L * it, 10_000L) },
) {
    private val _items = MutableStateFlow<List<IncomingItem>>(emptyList())
    val items: StateFlow<List<IncomingItem>> = _items.asStateFlow()
    private var pollJob: Job? = null
    private val jobs = HashMap<String, Job>()
    private var gateway: ReceiverGateway? = null

    fun start(gw: ReceiverGateway) {
        stop()
        gateway = gw
        pollJob = scope.launch {
            var failures = 0
            while (isActive) {
                try {
                    val list = gw.listOutbox()
                    failures = 0
                    val known = _items.value.map { it.entry.id }.toSet()
                    val fresh = list.filter { it.id !in known }
                    if (fresh.isNotEmpty()) _items.update { fresh.map { IncomingItem(it) } + it }
                } catch (e: ReceiverException) {
                    if (e.kind == Kind.UNAUTHORIZED || ++failures > 20) return@launch
                }
                delay(pollMs)
            }
        }
    }

    fun stop() {
        pollJob?.cancel()
        jobs.values.forEach { it.cancel() }
        jobs.clear()
        _items.value = emptyList()
        gateway = null
    }

    private fun update(id: String, f: (IncomingItem) -> IncomingItem) =
        _items.update { l -> l.map { if (it.entry.id == id) f(it) else it } }

    fun accept(id: String) {
        val gw = gateway ?: return
        val item = _items.value.firstOrNull { it.entry.id == id } ?: return
        if (item.state != InState.OFFERED && item.state != InState.FAILED) return
        jobs[id] = scope.launch {
            try {
                if (item.entry.kind == "text") {
                    update(id) { it.copy(state = InState.SAVED) }
                    runCatching { gw.outboxDone(id, "saved", null) }
                } else {
                    download(gw, item)
                }
            } catch (e: CancellationException) {
                throw e
            } finally {
                jobs.remove(id)
            }
        }
    }

    fun decline(id: String) {
        val gw = gateway ?: return
        jobs.remove(id)?.cancel()
        update(id) { it.copy(state = InState.DECLINED) }
        scope.launch { runCatching { gw.outboxDone(id, "declined", null) } }
    }

    fun dismiss(id: String) = update(id) { it.copy(state = InState.DISMISSED) }

    private suspend fun download(gw: ReceiverGateway, item: IncomingItem) {
        val e = item.entry
        update(e.id) { it.copy(state = InState.DOWNLOADING, received = 0, error = null, note = null) }
        val target = try {
            sink.create(e.name)
        } catch (ex: IOException) {
            update(e.id) { it.copy(state = InState.FAILED, error = "Can't create the file on this phone.") }
            return
        }
        val digest = MessageDigest.getInstance("SHA-256")
        var got = 0L
        var attempt = 0
        val t0 = System.nanoTime()
        try {
            while (true) {
                try {
                    gw.download(e.id, got) { buf, n ->
                        target.stream.write(buf, 0, n)
                        digest.update(buf, 0, n)
                        got += n
                        val secs = (System.nanoTime() - t0) / 1e9
                        update(e.id) { it.copy(received = got, note = null, speedBps = if (secs >= 1) (got / secs).toLong() else null) }
                    }
                    break
                } catch (ex: ReceiverException) {
                    val transient = ex.kind == Kind.UNREACHABLE || ex.kind == Kind.TIMEOUT
                    if (!transient || ++attempt > maxResumeAttempts) throw ex
                    update(e.id) { it.copy(note = "Connection lost — reconnecting ($attempt)…") }
                    delay(retryDelayMs(attempt))
                }
            }
            val hex = digest.digest().joinToString("") { "%02x".format(it) }
            if (got != e.size || (e.sha256 != null && hex != e.sha256)) {
                target.abort()
                update(e.id) { it.copy(state = InState.FAILED, error = "The file was damaged in transit and wasn't saved.") }
                runCatching { gw.outboxDone(e.id, "failed", "Integrity check failed") }
                return
            }
            val uri = target.commit()
            update(e.id) { it.copy(state = InState.SAVED, savedUri = uri, received = got) }
            runCatching { gw.outboxDone(e.id, "saved", null) }
        } catch (ex: CancellationException) {
            withContext(NonCancellable) { target.abort() }
            throw ex
        } catch (ex: ReceiverException) {
            target.abort()
            update(e.id) { it.copy(state = InState.FAILED, error = ex.message, note = null) }
            runCatching { gw.outboxDone(e.id, "failed", ex.message) }
        } catch (ex: IOException) {
            target.abort()
            update(e.id) { it.copy(state = InState.FAILED, error = "Couldn't write the file on this phone.", note = null) }
            runCatching { gw.outboxDone(e.id, "failed", "Could not write file") }
        }
    }
}
