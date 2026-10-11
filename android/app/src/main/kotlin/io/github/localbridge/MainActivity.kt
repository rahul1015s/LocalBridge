package io.github.localbridge

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import io.github.localbridge.ui.AppViewModel
import io.github.localbridge.ui.HistoryScreen
import io.github.localbridge.ui.HomeScreen
import io.github.localbridge.ui.LocalBridgeTheme
import io.github.localbridge.ui.PairScreen
import io.github.localbridge.ui.Screen
import io.github.localbridge.ui.SendScreen
import io.github.localbridge.ui.SettingsScreen
import io.github.localbridge.ui.TransfersScreen

class MainActivity : ComponentActivity() {
    private val vm: AppViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (savedInstanceState == null) handleShare(intent)
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            registerForActivityResult(androidx.activity.result.contract.ActivityResultContracts.RequestPermission()) { }
                .launch(Manifest.permission.POST_NOTIFICATIONS) // optional: transfers work without it
        }
        setContent {
            val settings by vm.settings.state.collectAsState()
            val stack by vm.stack.collectAsState()
            LocalBridgeTheme(settings.theme) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    BackHandler(enabled = stack.size > 1) { vm.back() }
                    when (stack.last()) {
                        Screen.HOME -> HomeScreen(vm)
                        Screen.PAIR -> PairScreen(vm)
                        Screen.SEND -> SendScreen(vm)
                        Screen.TRANSFERS -> TransfersScreen(vm)
                        Screen.HISTORY -> HistoryScreen(vm)
                        Screen.SETTINGS -> SettingsScreen(vm)
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShare(intent)
    }

    /** "Share → LocalBridge" from the gallery or a file manager. */
    private fun handleShare(intent: Intent?) {
        val i = intent ?: return
        val uris: List<Uri> = when (i.action) {
            Intent.ACTION_SEND -> listOfNotNull(streamExtra(i))
            Intent.ACTION_SEND_MULTIPLE -> streamListExtra(i)
            else -> return
        }
        if (uris.isNotEmpty()) vm.stage(uris.map { it.toString() })
    }

    @Suppress("DEPRECATION")
    private fun streamExtra(i: Intent): Uri? =
        if (Build.VERSION.SDK_INT >= 33) i.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        else i.getParcelableExtra(Intent.EXTRA_STREAM)

    @Suppress("DEPRECATION")
    private fun streamListExtra(i: Intent): List<Uri> =
        (if (Build.VERSION.SDK_INT >= 33) i.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
        else i.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)) ?: emptyList()
}
