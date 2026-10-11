package io.github.localbridge

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Intent
import io.github.localbridge.core.ReceiverGateway
import io.github.localbridge.data.HistoryEntry
import io.github.localbridge.data.HistoryStore
import io.github.localbridge.data.SettingsStore
import io.github.localbridge.transfer.ContentResolverFileAccess
import io.github.localbridge.transfer.ItemState
import io.github.localbridge.transfer.TransferEngine
import io.github.localbridge.transfer.TransferService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** The currently paired computer. Held in memory only; closing the app ends the session. */
class Session(val gateway: ReceiverGateway, val receiverName: String, val host: String)

class AppContainer(private val app: Application) {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val settings = SettingsStore(app)
    val history = HistoryStore(app)
    private val _session = MutableStateFlow<Session?>(null)
    val session: StateFlow<Session?> = _session.asStateFlow()

    val files = ContentResolverFileAccess(app.contentResolver)
    val engine = TransferEngine(
        scope = scope,
        files = files,
        listener = { item, receiver ->
            if (settings.state.value.keepHistory) {
                history.add(
                    HistoryEntry(item.name, item.size, item.state == ItemState.DONE, item.error, receiver, System.currentTimeMillis()),
                )
            }
        },
    )

    init {
        // Keep the process alive (foreground service) only while transfers are active.
        scope.launch {
            engine.activeCount.collect { n ->
                if (n > 0) {
                    runCatching { app.startForegroundService(Intent(app, TransferService::class.java)) }
                }
            }
        }
    }

    fun setSession(s: Session?) {
        if (s == null) engine.cancelAll()
        _session.value = s
    }
}

class LocalBridgeApp : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, getString(R.string.channel_transfers), NotificationManager.IMPORTANCE_LOW),
        )
    }

    companion object {
        const val CHANNEL = "transfers"
    }
}
