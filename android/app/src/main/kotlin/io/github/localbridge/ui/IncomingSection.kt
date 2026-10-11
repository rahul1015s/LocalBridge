package io.github.localbridge.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import io.github.localbridge.transfer.InState
import io.github.localbridge.transfer.IncomingItem

/** Files and text the computer wants to send to this phone. Nothing is saved until Accept. */
@Composable
fun IncomingSection(vm: AppViewModel) {
    val items by vm.incoming.items.collectAsState()
    val visible = items.filter { it.state != InState.DISMISSED && it.state != InState.DECLINED }
    if (visible.isEmpty()) return
    SectionTitle("From your computer")
    val grouped = visible.filter { it.entry.group != null }.groupBy { it.entry.group!! }
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        grouped.forEach { (g, members) -> FolderCard(g, members, vm) }
        visible.filter { it.entry.group == null }.forEach { IncomingCard(it, vm) }
    }
}

@Composable
private fun IncomingCard(item: IncomingItem, vm: AppViewModel) {
    val ctx = LocalContext.current
    val e = item.entry
    SurfaceCard {
        if (e.kind == "text") {
            Text("Text from your computer", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.height(4.dp))
            Text(e.text.orEmpty(), style = MaterialTheme.typography.bodyLarge, maxLines = 6)
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("LocalBridge", e.text.orEmpty()))
                    vm.incoming.accept(e.id)
                }) { Text("Copy") }
                val t = e.text.orEmpty()
                if (t.startsWith("https://") || t.startsWith("http://")) {
                    OutlinedButton(onClick = { runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(t)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } }) { Text("Open link") }
                }
                TextButton(onClick = { vm.incoming.decline(e.id) }) { Text("Dismiss") }
            }
            return@SurfaceCard
        }
        Text(e.name, style = MaterialTheme.typography.titleMedium, maxLines = 2)
        Text(
            when (item.state) {
                InState.OFFERED -> "${formatBytes(e.size)} · your computer wants to send this"
                InState.DOWNLOADING -> item.note ?: listOfNotNull(
                    "${formatBytes(item.received)} of ${formatBytes(e.size)}", formatSpeed(item.speedBps),
                ).joinToString(" · ")
                InState.SAVED -> "Saved to Downloads/LocalBridge · SHA-256 verified"
                InState.FAILED -> item.error ?: "Failed"
                else -> ""
            },
            style = MaterialTheme.typography.bodySmall,
            color = if (item.state == InState.FAILED) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (item.state == InState.DOWNLOADING) {
            Spacer(Modifier.height(8.dp))
            val p = item.progress
            if (p != null) LinearProgressIndicator(progress = { p }, modifier = Modifier.fillMaxWidth()) else LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
        }
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            when (item.state) {
                InState.OFFERED, InState.FAILED -> {
                    Button(onClick = { vm.incoming.accept(e.id) }) { Text(if (item.state == InState.FAILED) "Try again" else "Accept") }
                    TextButton(onClick = { vm.incoming.decline(e.id) }) { Text("Decline") }
                }
                InState.DOWNLOADING -> TextButton(onClick = { vm.incoming.decline(e.id) }) { Text("Cancel") }
                InState.SAVED -> {
                    item.savedUri?.let { u ->
                        Button(onClick = { runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(u)).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)) } }) { Text("Open") }
                    }
                    TextButton(onClick = { vm.incoming.dismiss(e.id) }) { Text("Done") }
                }
                else -> {}
            }
        }
    }
}

/** One card for a whole folder sent from the computer, with Accept all / Decline all. */
@Composable
private fun FolderCard(group: String, members: List<IncomingItem>, vm: AppViewModel) {
    val top = members.first().entry.dir?.substringBefore('/') ?: "Folder"
    val total = members.sumOf { it.entry.size }
    val offered = members.count { it.state == InState.OFFERED }
    val saved = members.count { it.state == InState.SAVED }
    val failed = members.count { it.state == InState.FAILED }
    val active = members.any { it.state == InState.DOWNLOADING }
    val got = members.sumOf { if (it.state == InState.SAVED) it.entry.size else it.received }
    SurfaceCard {
        Text("Folder “$top”", style = MaterialTheme.typography.titleMedium)
        Text(
            "${members.size} files · ${formatBytes(total)}" +
                (if (saved > 0) " · $saved saved" else "") + (if (failed > 0) " · $failed failed" else ""),
            style = MaterialTheme.typography.bodySmall,
            color = if (failed > 0) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (active || (saved + failed in 1 until members.size)) {
            Spacer(Modifier.height(8.dp))
            LinearProgressIndicator(progress = { if (total > 0) (got.toFloat() / total).coerceIn(0f, 1f) else 0f }, modifier = Modifier.fillMaxWidth())
        }
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (offered > 0) {
                Button(onClick = { vm.incoming.acceptGroup(group) }) { Text("Accept all ($offered)") }
                TextButton(onClick = { vm.incoming.declineGroup(group) }) { Text("Decline") }
            }
            if (failed > 0 && !active) {
                Button(onClick = { members.filter { it.state == InState.FAILED }.forEach { vm.incoming.accept(it.entry.id) } }) { Text("Retry failed") }
            }
            if (offered == 0 && !active) {
                TextButton(onClick = { members.forEach { vm.incoming.dismiss(it.entry.id) } }) { Text("Done") }
            }
        }
        if (saved == members.size) {
            Text("Saved to Downloads/LocalBridge/$top · each file SHA-256 verified", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
        }
    }
}
