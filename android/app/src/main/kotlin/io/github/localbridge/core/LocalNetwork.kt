package io.github.localbridge.core

import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import java.net.InetAddress
import javax.net.SocketFactory

/**
 * Keeps transfers on the local network. If the receiver's address is on-link on a Wi-Fi/Ethernet
 * network, sockets are bound to that network so the traffic can never leave over mobile data (even if
 * Android would otherwise prefer another route). When the phone itself is the hotspot there is no
 * such network object; the receiver then sits on the phone's own tethering interface, which Android
 * routes locally, and the system default is used.
 */
object LocalNetwork {
    @Suppress("DEPRECATION")
    fun socketFactoryFor(cm: ConnectivityManager, host: String): SocketFactory? = runCatching {
        val target = InetAddress.getByName(host)
        cm.allNetworks.firstOrNull { net ->
            val caps = cm.getNetworkCapabilities(net) ?: return@firstOrNull false
            val local = caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
            local && !caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) &&
                (cm.getLinkProperties(net)?.routes.orEmpty().any { !it.isDefaultRoute && it.matches(target) })
        }?.socketFactory
    }.getOrNull()
}
