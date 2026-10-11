package io.github.localbridge.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Inbox
import androidx.compose.material.icons.rounded.Schedule
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.localbridge.transfer.Batch
import io.github.localbridge.transfer.ItemState
import io.github.localbridge.transfer.Phase
import io.github.localbridge.transfer.QueueItem

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TransfersScreen(vm: AppViewModel) {
    val batches by vm.engine.batches.collectAsState()
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("Transfers") },
                navigationIcon = { BackButton { vm.back() } },
                actions = { if (batches.any { !it.active }) TextButton(onClick = { vm.engine.clearFinished() }) { Text("Clear finished") } },
            )
        },
    ) { pad ->
        if (batches.isEmpty()) {
            Column(Modifier.padding(pad).padding(16.dp)) {
                EmptyState(Icons.Rounded.Inbox, "No transfers", "Pick files from the home screen to start.")
            }
        } else {
            LazyColumn(Modifier.padding(pad), contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                items(batches, key = { it.id }) { BatchCard(it, vm) }
            }
        }
    }
}

@Composable
private fun BatchCard(b: Batch, vm: AppViewModel) {
    SurfaceCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("To ${b.receiverName}", style = MaterialTheme.typography.titleMedium)
                val done = b.items.count { it.state == ItemState.DONE }
                Text(
                    when (b.phase) {
                        Phase.PREPARING -> "Preparing…"
                        Phase.WAITING_APPROVAL -> b.message ?: "Waiting for approval…"
                        Phase.SENDING -> "Sending · $done of ${b.items.size} done"
                        Phase.FINISHED -> if (done == b.items.size) "All ${b.items.size} sent and verified" else b.message ?: "$done of ${b.items.size} sent"
                    },
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (b.active) OutlinedButton(onClick = { vm.engine.cancel(b.id) }) { Text("Cancel") }
        }
        Spacer(Modifier.height(12.dp))
        b.items.forEachIndexed { i, item -> ItemRow(item); if (i < b.items.lastIndex) Spacer(Modifier.height(14.dp)) }
        if (!b.active && b.items.any { it.state == ItemState.FAILED }) {
            Spacer(Modifier.height(8.dp))
            TextButton(onClick = { vm.retry(b.id) }) { Text("Retry failed files") }
        }
    }
}

@Composable
private fun ItemRow(item: QueueItem) {
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            val (icon, tint) = when (item.state) {
                ItemState.DONE -> Icons.Rounded.CheckCircle to MaterialTheme.colorScheme.primary
                ItemState.FAILED, ItemState.REJECTED, ItemState.CANCELLED -> Icons.Rounded.ErrorOutline to MaterialTheme.colorScheme.error
                else -> Icons.Rounded.Schedule to MaterialTheme.colorScheme.onSurfaceVariant
            }
            Icon(icon, contentDescription = item.state.name.lowercase(), tint = tint)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(item.name, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                val detail = when (item.state) {
                    ItemState.QUEUED -> "Queued"
                    ItemState.WAITING -> "Waiting for approval · ${formatBytes(item.size)}"
                    ItemState.SENDING -> item.note ?: listOfNotNull(
                        if (item.size != null) "${formatBytes(item.sent)} of ${formatBytes(item.size)}" else "${formatBytes(item.sent)} sent",
                        formatSpeed(item.speedBps), formatEta(item.etaSeconds),
                    ).joinToString(" · ")
                    ItemState.VERIFYING -> "Verifying integrity…"
                    ItemState.DONE -> "${formatBytes(item.size ?: item.sent)} · SHA-256 verified"
                    ItemState.FAILED, ItemState.REJECTED, ItemState.CANCELLED -> item.error ?: item.state.name.lowercase()
                }
                Text(detail, style = MaterialTheme.typography.bodySmall,
                    color = if (item.state == ItemState.FAILED || item.state == ItemState.REJECTED) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        if (item.state == ItemState.SENDING) {
            Spacer(Modifier.height(8.dp))
            val p = item.progress // null => size unknown => honest indeterminate bar
            if (p != null) LinearProgressIndicator(progress = { p }, modifier = Modifier.fillMaxWidth())
            else LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
        }
    }
}
