package io.github.localbridge.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import io.github.localbridge.data.ThemeMode

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(vm: AppViewModel) {
    val s by vm.settings.state.collectAsState()
    val session by vm.session.collectAsState()
    val history by vm.history.entries.collectAsState()
    var name by remember(s.deviceName) { mutableStateOf(s.deviceName) }
    val version = remember {
        val ctx = vm.getApplication<android.app.Application>()
        runCatching { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName }.getOrNull() ?: "?"
    }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = { TopAppBar(title = { Text("Settings") }, navigationIcon = { BackButton { vm.back() } }) },
    ) { pad ->
        Column(
            Modifier.padding(pad).verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            SurfaceCard {
                Text("This phone", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = name, onValueChange = { name = it.take(64); vm.settings.update { c -> c.copy(deviceName = name) } },
                    label = { Text("Device name") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                    supportingText = { Text("Shown on the computer when you pair.") },
                )
            }
            SurfaceCard {
                Text("Appearance", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(8.dp))
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                    ThemeMode.entries.forEachIndexed { i, m ->
                        SegmentedButton(
                            selected = s.theme == m, onClick = { vm.settings.update { c -> c.copy(theme = m) } },
                            shape = SegmentedButtonDefaults.itemShape(i, ThemeMode.entries.size),
                        ) { Text(m.name.lowercase().replaceFirstChar { it.uppercase() }) }
                    }
                }
            }
            SurfaceCard {
                Text("Sending", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(8.dp))
                Text("Files sent at the same time: ${s.parallel}", style = MaterialTheme.typography.bodyLarge)
                androidx.compose.material3.Slider(
                    value = s.parallel.toFloat(), onValueChange = { v -> vm.settings.update { it.copy(parallel = v.toInt().coerceIn(1, 4)) } },
                    valueRange = 1f..4f, steps = 2,
                )
                Text("Files are always sent exactly as stored — never resized or recompressed — and only over your local network.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                val gps = androidx.activity.compose.rememberLauncherForActivityResult(androidx.activity.result.contract.ActivityResultContracts.RequestPermission()) { ok ->
                    vm.settings.update { it.copy(keepLocation = ok) }
                }
                ToggleRow("Keep photo location (GPS)", "Android hides it by default. Needs the media-location permission.", s.keepLocation) { v ->
                    if (v) gps.launch(android.Manifest.permission.ACCESS_MEDIA_LOCATION) else vm.settings.update { it.copy(keepLocation = false) }
                }
            }
            SurfaceCard {
                Text("Connection", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(6.dp))
                val c = session
                if (c == null) Text("Not connected. Pairing lasts for one session and is never saved.",
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                else {
                    Text("${c.receiverName} · ${c.host}", style = MaterialTheme.typography.bodyLarge)
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(onClick = { vm.disconnect() }) { Text("Disconnect") }
                }
            }
            SurfaceCard {
                Text("Notifications & privacy", style = MaterialTheme.typography.titleMedium)
                ToggleRow("Show progress notification", "While files are being sent.", s.notifications) { v -> vm.settings.update { it.copy(notifications = v) } }
                ToggleRow("Keep transfer history", "File names and sizes, stored only on this phone.", s.keepHistory) { v -> vm.settings.update { it.copy(keepHistory = v) } }
                OutlinedButton(onClick = { vm.history.clear() }, enabled = history.isNotEmpty()) { Text("Clear history") }
            }
            SurfaceCard {
                Text("About", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(6.dp))
                Text("LocalBridge $version · free and open source (GPL-3.0)", style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(4.dp))
                Text(
                    "Files go straight from this phone to your computer, encrypted with TLS and checked with SHA-256. " +
                        "Free to use, modify and share under the GPL — no account, no cloud, no ads, no analytics. Pairing is verified with the code on your computer's screen; " +
                        "this app has not been independently security audited.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun ToggleRow(title: String, sub: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(sub, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}
