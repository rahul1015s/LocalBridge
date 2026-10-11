package io.github.localbridge.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.History
import androidx.compose.material.icons.rounded.Laptop
import androidx.compose.material.icons.rounded.Send
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material.icons.rounded.UploadFile
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import java.text.DateFormat
import java.util.Date

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(vm: AppViewModel) {
    val session by vm.session.collectAsState()
    val history by vm.history.entries.collectAsState()
    val settings by vm.settings.state.collectAsState()
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNotEmpty()) vm.send(uris.map { it.toString() })
    }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("LocalBridge", style = MaterialTheme.typography.headlineMedium, color = MaterialTheme.colorScheme.primary) },
                actions = {
                    IconButton(onClick = { vm.navigate(Screen.SETTINGS) }) {
                        Icon(Icons.Rounded.Settings, contentDescription = "Settings")
                    }
                },
                colors = androidx.compose.material3.TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
    ) { pad ->
        Column(
            Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            val s = session
            if (s != null) {
                SurfaceCard(container = MaterialTheme.colorScheme.primaryContainer) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        IconBadge(Icons.Rounded.Laptop, MaterialTheme.colorScheme.onPrimary, MaterialTheme.colorScheme.primary)
                        Spacer(Modifier.width(14.dp))
                        Column(Modifier.weight(1f)) {
                            Text("Connected", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onPrimaryContainer)
                            Text(s.receiverName, style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onPrimaryContainer)
                            Text("${s.host} · encrypted · this session only", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onPrimaryContainer)
                        }
                    }
                    Spacer(Modifier.height(16.dp))
                    Button(
                        onClick = { picker.launch(arrayOf("*/*")) },
                        modifier = Modifier.fillMaxWidth().height(56.dp),
                    ) {
                        Icon(Icons.Rounded.UploadFile, null)
                        Spacer(Modifier.width(8.dp))
                        Text("Send files", style = MaterialTheme.typography.titleMedium)
                    }
                    TextButton(onClick = { vm.disconnect() }, modifier = Modifier.align(Alignment.End)) {
                        Text("Disconnect", color = MaterialTheme.colorScheme.onPrimaryContainer)
                    }
                }
            } else {
                SurfaceCard {
                    Text("Send files to your computer", style = MaterialTheme.typography.headlineMedium)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "Fast, private and direct. Videos, music, photos and documents travel over your own Wi-Fi or hotspot — no cloud, no account.",
                        style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(18.dp))
                    Button(onClick = { vm.navigate(Screen.PAIR) }, modifier = Modifier.fillMaxWidth().height(56.dp)) {
                        Icon(Icons.Rounded.Laptop, null)
                        Spacer(Modifier.width(8.dp))
                        Text("Connect to computer", style = MaterialTheme.typography.titleMedium)
                    }
                }
            }

            Row(verticalAlignment = Alignment.CenterVertically) {
                SectionTitle("Recent", Modifier.weight(1f))
                if (history.isNotEmpty()) TextButton(onClick = { vm.navigate(Screen.HISTORY) }) { Text("See all") }
            }
            if (!settings.keepHistory) {
                EmptyState(Icons.Rounded.History, "History is off", "Turn it on in Settings if you'd like a list of past transfers.")
            } else if (history.isEmpty()) {
                EmptyState(Icons.Rounded.Send, "Nothing sent yet", "Connect to your computer, then pick the files to send.")
            } else {
                SurfaceCard { history.take(5).forEachIndexed { i, e -> HistoryRow(e); if (i < minOf(4, history.lastIndex)) Spacer(Modifier.height(12.dp)) } }
            }
        }
    }
}

@Composable
fun HistoryRow(e: io.github.localbridge.data.HistoryEntry) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(
            if (e.ok) Icons.Rounded.CheckCircle else Icons.Rounded.ErrorOutline,
            contentDescription = if (e.ok) "Sent" else "Failed",
            tint = if (e.ok) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
        )
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(e.name, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
            val time = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date(e.atMs))
            Text(
                listOfNotNull(formatBytes(e.size).takeIf { e.size != null }, "to ${e.receiver}", time, e.detail?.takeIf { !e.ok }).joinToString(" · "),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HistoryScreen(vm: AppViewModel) {
    val history by vm.history.entries.collectAsState()
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("History") },
                navigationIcon = { BackButton { vm.back() } },
                actions = { if (history.isNotEmpty()) TextButton(onClick = { vm.history.clear() }) { Text("Clear") } },
            )
        },
    ) { pad ->
        Column(Modifier.padding(pad).padding(16.dp).verticalScroll(rememberScrollState())) {
            if (history.isEmpty()) EmptyState(Icons.Rounded.History, "No history", "Completed and failed transfers appear here, on this phone only.")
            else SurfaceCard { history.forEachIndexed { i, e -> HistoryRow(e); if (i < history.lastIndex) Spacer(Modifier.height(14.dp)) } }
        }
    }
}

@Composable
fun BackButton(onClick: () -> Unit) {
    IconButton(onClick = onClick) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back") }
}
