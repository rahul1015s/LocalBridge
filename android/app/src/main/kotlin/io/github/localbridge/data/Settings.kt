package io.github.localbridge.data

import android.content.Context
import android.os.Build
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class ThemeMode { SYSTEM, LIGHT, DARK }

data class AppSettings(
    val deviceName: String,
    val theme: ThemeMode = ThemeMode.SYSTEM,
    val notifications: Boolean = true,
    val keepHistory: Boolean = true,
    /** Files sent at the same time (1-4). */
    val parallel: Int = 3,
    /** Keep photo GPS data (needs the media-location permission). */
    val keepLocation: Boolean = false,
)

/** Plain preferences only. No credentials are ever persisted: sessions live in memory. */
class SettingsStore(context: Context) {
    private val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)
    private val _state = MutableStateFlow(load())
    val state: StateFlow<AppSettings> = _state.asStateFlow()

    private fun load() = AppSettings(
        deviceName = prefs.getString("device_name", null)?.takeIf { it.isNotBlank() } ?: (Build.MODEL ?: "Android"),
        theme = runCatching { ThemeMode.valueOf(prefs.getString("theme", "SYSTEM")!!) }.getOrDefault(ThemeMode.SYSTEM),
        notifications = prefs.getBoolean("notifications", true),
        keepHistory = prefs.getBoolean("keep_history", true),
        parallel = prefs.getInt("parallel", 3).coerceIn(1, 4),
        keepLocation = prefs.getBoolean("keep_location", false),
    )

    fun update(f: (AppSettings) -> AppSettings) {
        val n = f(_state.value).let { it.copy(deviceName = it.deviceName.trim().take(64).ifBlank { Build.MODEL ?: "Android" }) }
        prefs.edit()
            .putString("device_name", n.deviceName).putString("theme", n.theme.name)
            .putBoolean("notifications", n.notifications).putBoolean("keep_history", n.keepHistory)
            .putInt("parallel", n.parallel.coerceIn(1, 4)).putBoolean("keep_location", n.keepLocation)
            .apply()
        _state.value = n
    }
}
