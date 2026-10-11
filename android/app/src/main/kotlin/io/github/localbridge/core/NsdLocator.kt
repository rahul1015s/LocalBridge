package io.github.localbridge.core

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Handler
import android.os.Looper
import kotlin.coroutines.resume
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull

/** mDNS/DNS-SD via Android's NsdManager (no extra permissions on Android 10+). */
@Suppress("DEPRECATION")
class NsdLocator(context: Context, private val timeoutMs: Long = 4000) : Locator {
    private val nsd = context.getSystemService(NsdManager::class.java)
    private val main = Handler(Looper.getMainLooper())

    override suspend fun find(rid: String): Endpoint? = withTimeoutOrNull(timeoutMs) {
        suspendCancellableCoroutine { cont ->
            var finished = false
            fun done(ep: Endpoint?) { if (!finished) { finished = true; if (cont.isActive) cont.resume(ep) } }
            fun resolve(info: NsdServiceInfo, attempt: Int = 0) {
                nsd.resolveService(info, object : NsdManager.ResolveListener {
                    override fun onResolveFailed(i: NsdServiceInfo, code: Int) {
                        // Only one resolve may run at a time; try again shortly.
                        if (code == NsdManager.FAILURE_ALREADY_ACTIVE && attempt < 5 && !finished) main.postDelayed({ resolve(info, attempt + 1) }, 300)
                    }

                    override fun onServiceResolved(i: NsdServiceInfo) {
                        val got = i.attributes["rid"]?.toString(Charsets.UTF_8)
                        val host = i.host?.hostAddress
                        if (got == rid && host != null && PairingInfo.isLocalAddress(host)) done(Endpoint(host, i.port))
                    }
                })
            }
            val listener = object : NsdManager.DiscoveryListener {
                override fun onServiceFound(info: NsdServiceInfo) { if (info.serviceName.startsWith("LocalBridge")) resolve(info) }
                override fun onServiceLost(info: NsdServiceInfo) {}
                override fun onDiscoveryStarted(t: String) {}
                override fun onDiscoveryStopped(t: String) {}
                override fun onStartDiscoveryFailed(t: String, c: Int) = done(null)
                override fun onStopDiscoveryFailed(t: String, c: Int) {}
            }
            nsd.discoverServices(SERVICE, NsdManager.PROTOCOL_DNS_SD, listener)
            cont.invokeOnCancellation { runCatching { nsd.stopServiceDiscovery(listener) } }
        }
    }

    /** Number of LocalBridge receivers currently announcing themselves (diagnostic only). */
    fun nearby(): Flow<Int> = callbackFlow {
        val seen = HashSet<String>()
        val l = object : NsdManager.DiscoveryListener {
            override fun onServiceFound(i: NsdServiceInfo) { if (i.serviceName.startsWith("LocalBridge")) { seen += i.serviceName; trySend(seen.size) } }
            override fun onServiceLost(i: NsdServiceInfo) { seen -= i.serviceName; trySend(seen.size) }
            override fun onDiscoveryStarted(t: String) { trySend(0) }
            override fun onDiscoveryStopped(t: String) {}
            override fun onStartDiscoveryFailed(t: String, c: Int) { close() }
            override fun onStopDiscoveryFailed(t: String, c: Int) {}
        }
        runCatching { nsd.discoverServices(SERVICE, NsdManager.PROTOCOL_DNS_SD, l) }.onFailure { close(it) }
        awaitClose { runCatching { nsd.stopServiceDiscovery(l) } }
    }

    companion object {
        const val SERVICE = "_localbridge._tcp."
    }
}
