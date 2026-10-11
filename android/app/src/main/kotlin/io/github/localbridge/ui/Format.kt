package io.github.localbridge.ui

import java.util.Locale

fun formatBytes(n: Long?): String {
    if (n == null) return "Unknown size"
    if (n < 1000) return "$n B"
    val units = arrayOf("KB", "MB", "GB", "TB")
    var v = n / 1000.0
    var i = 0
    while (v >= 1000 && i < units.lastIndex) { v /= 1000; i++ }
    return String.format(Locale.getDefault(), if (v >= 100) "%.0f %s" else "%.1f %s", v, units[i])
}

fun formatSpeed(bps: Long?): String? = bps?.let { "${formatBytes(it)}/s" }

fun formatEta(seconds: Long?): String? = seconds?.let {
    when {
        it < 60 -> "${it}s left"
        it < 3600 -> "${it / 60}m ${it % 60}s left"
        else -> "${it / 3600}h ${(it % 3600) / 60}m left"
    }
}
