package io.github.localbridge.transfer

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import androidx.core.app.NotificationCompat
import io.github.localbridge.LocalBridgeApp
import io.github.localbridge.MainActivity
import io.github.localbridge.R
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/**
 * Foreground service (type dataSync) that exists only while a transfer is running, so Android doesn't
 * kill the process mid-file. It shows honest progress and a Cancel action, then stops itself.
 */
class TransferService : Service() {
    private var job: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val container = (application as LocalBridgeApp).container
        if (intent?.action == ACTION_CANCEL) {
            container.engine.cancelAll()
        }
        startForeground(ID, build("Preparing…", null), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        job?.cancel()
        job = container.scope.launch {
            container.engine.batches.collectLatest { batches ->
                val active = batches.filter { it.active }
                if (active.isEmpty()) {
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf()
                    return@collectLatest
                }
                val items = active.flatMap { it.items }
                val total = items.sumOf { it.size ?: 0L }
                val sent = items.sumOf { it.sent }
                val known = items.all { it.size != null } && total > 0
                val done = items.count { it.state == ItemState.DONE }
                val title = "Sending $done of ${items.size}"
                val nm = getSystemService(android.app.NotificationManager::class.java)
                if (container.settings.state.value.notifications) {
                    nm.notify(ID, build(title, if (known) (sent * 100 / total).toInt() else null))
                }
            }
        }
        return START_NOT_STICKY
    }

    private fun build(text: String, percent: Int?): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val cancel = PendingIntent.getService(
            this, 1, Intent(this, TransferService::class.java).setAction(ACTION_CANCEL), PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, LocalBridgeApp.CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_transfer)
            .setContentTitle("LocalBridge")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setProgress(100, percent ?: 0, percent == null) // indeterminate when size unknown
            .addAction(0, "Cancel", cancel)
            .build()
    }

    override fun onTimeout(startId: Int, fgsType: Int) {
        // Android limits dataSync services; stop gracefully instead of being killed.
        (application as LocalBridgeApp).container.engine.cancelAll()
        stopSelf()
    }

    override fun onDestroy() {
        job?.cancel()
        super.onDestroy()
    }

    companion object {
        const val ID = 1001
        const val ACTION_CANCEL = "io.github.localbridge.CANCEL"
    }
}
