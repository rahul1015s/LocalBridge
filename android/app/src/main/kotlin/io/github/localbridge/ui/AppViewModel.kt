package io.github.localbridge.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.localbridge.LocalBridgeApp
import io.github.localbridge.Session
import io.github.localbridge.core.HttpReceiver
import io.github.localbridge.core.PairingInfo
import io.github.localbridge.core.ReceiverException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class Screen { HOME, PAIR, SEND, TRANSFERS, HISTORY, SETTINGS }

data class Picked(val ref: String, val name: String, val size: Long?, val readable: Boolean)

sealed interface PairState {
    data object Idle : PairState
    data object Connecting : PairState
    /** TLS handshake succeeded and the certificate matched the scanned fingerprint. */
    data object CertificateVerified : PairState
    data class Failed(val message: String, val kind: ReceiverException.Kind?) : PairState
}

class AppViewModel(app: Application) : AndroidViewModel(app) {
    private val container = (app as LocalBridgeApp).container
    val engine = container.engine
    val settings = container.settings
    val history = container.history
    val session: StateFlow<Session?> = container.session

    private val _stack = MutableStateFlow(listOf(Screen.HOME))
    val stack: StateFlow<List<Screen>> = _stack.asStateFlow()

    private val _pair = MutableStateFlow<PairState>(PairState.Idle)
    val pair: StateFlow<PairState> = _pair.asStateFlow()

    private val _selection = MutableStateFlow<List<Picked>>(emptyList())
    val selection: StateFlow<List<Picked>> = _selection.asStateFlow()

    /** Files shared into the app from another app, waiting for a connection. */
    private var pendingShare: List<String> = emptyList()
    private var pairJob: Job? = null

    fun navigate(s: Screen) = _stack.update { if (it.last() == s) it else it + s }
    fun back(): Boolean {
        if (_stack.value.size <= 1) return false
        _stack.update { it.dropLast(1) }
        return true
    }

    fun connect(raw: String) {
        if (_pair.value is PairState.Connecting || _pair.value is PairState.CertificateVerified) return
        val info = PairingInfo.parse(raw).getOrElse {
            _pair.value = PairState.Failed(it.message ?: "That code isn't valid.", null)
            return
        }
        _pair.value = PairState.Connecting
        pairJob = viewModelScope.launch {
            try {
                val (gw, name) = HttpReceiver.pair(info, settings.state.value.deviceName) {
                    _pair.value = PairState.CertificateVerified
                }
                container.setSession(Session(gw, name, info.host))
                _pair.value = PairState.Idle
                _stack.value = listOf(Screen.HOME)
                if (pendingShare.isNotEmpty()) {
                    val files = pendingShare
                    pendingShare = emptyList()
                    stage(files)
                }
            } catch (e: ReceiverException) {
                _pair.value = PairState.Failed(e.message, e.kind)
            }
        }
    }

    fun cancelPairing() {
        pairJob?.cancel()
        _pair.value = PairState.Idle
    }

    fun resetPairError() {
        if (_pair.value is PairState.Failed) _pair.value = PairState.Idle
    }

    fun disconnect() = container.setSession(null)

    /** Adds files to the selection (deduplicated) and opens the review screen. */
    fun stage(refs: List<String>) {
        if (session.value == null) {
            pendingShare = pendingShare + refs
            navigate(Screen.PAIR)
            return
        }
        addToSelection(refs)
        navigate(Screen.SEND)
    }

    fun addToSelection(refs: List<String>) {
        val have = _selection.value.map { it.ref }.toSet()
        val fresh = refs.distinct().filter { it !in have }
        viewModelScope.launch(kotlinx.coroutines.Dispatchers.IO) {
            val picked = fresh.map { r ->
                runCatching { container.files.describe(r) }
                    .map { Picked(r, it.name, it.size, true) }
                    .getOrElse { Picked(r, r.substringAfterLast('/'), null, false) }
            }
            _selection.update { it + picked }
        }
    }

    fun removeFromSelection(ref: String) = _selection.update { l -> l.filter { it.ref != ref } }
    fun clearSelection() { _selection.value = emptyList() }

    fun sendSelection() {
        val s = session.value ?: return navigate(Screen.PAIR)
        val refs = _selection.value.filter { it.readable }.map { it.ref }
        if (refs.isEmpty()) return
        engine.enqueue(s.gateway, s.receiverName, refs)
        clearSelection()
        _stack.update { it.filter { sc -> sc != Screen.SEND } + Screen.TRANSFERS }
    }

    fun retry(batchId: String) {
        val s = session.value ?: return navigate(Screen.PAIR)
        val failed = engine.batches.value.firstOrNull { it.id == batchId }?.items
            ?.filter { it.state.name in setOf("FAILED") }?.map { it.ref }.orEmpty()
        if (failed.isNotEmpty()) engine.enqueue(s.gateway, s.receiverName, failed)
    }
}
