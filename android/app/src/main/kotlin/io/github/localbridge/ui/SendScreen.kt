package io.github.localbridge.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Description
import androidx.compose.material.icons.rounded.Folder
import androidx.compose.material.icons.rounded.FolderOpen
import androidx.compose.material.icons.rounded.MusicNote
import androidx.compose.material.icons.rounded.PhotoLibrary
import androidx.compose.material.icons.rounded.Send
import androidx.compose.material3.Button
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

/** Choose what to send, review it, then send. Nothing leaves the phone until "Send" is pressed. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SendScreen(vm: AppViewModel) {
    val selection by vm.selection.collectAsState()
    val session by vm.session.collectAsState()

    val media = rememberLauncherForActivityResult(ActivityResultContracts.PickMultipleVisualMedia()) { uris ->
        vm.addToSelection(uris.map { it.toString() })
    }
    val docs = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        vm.addToSelection(uris.map { it.toString() })
    }
    val folder = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) vm.addFolder(uri)
    }
    val total = selection.filter { it.readable }.sumOf { it.size ?: 0L }
    val anyUnknown = selection.any { it.readable && it.size == null }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("Send files") },
                navigationIcon = { BackButton { vm.back() } },
                actions = { if (selection.isNotEmpty()) TextButton(onClick = { vm.clearSelection() }) { Text("Clear") } },
            )
        },
        bottomBar = {
            Column(Modifier.padding(16.dp)) {
                Button(
                    onClick = { vm.sendSelection() },
                    enabled = selection.any { it.readable } && session != null,
                    modifier = Modifier.fillMaxWidth().height(56.dp),
                ) {
                    Icon(Icons.Rounded.Send, null)
                    Spacer(Modifier.width(8.dp))
                    val n = selection.count { it.readable }
                    Text(
                        if (n == 0) "Choose files to send" else "Send $n file${if (n == 1) "" else "s"}" +
                            (if (total > 0) " · ${formatBytes(total)}${if (anyUnknown) "+" else ""}" else ""),
                        style = MaterialTheme.typography.titleMedium,
                    )
                }
                if (session != null) Text(
                    "To ${session!!.receiverName}. You'll approve it on the computer next.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
        },
    ) { pad ->
        LazyColumn(
            Modifier.padding(pad),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                SectionTitle("Add from")
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    SourceButton(Icons.Rounded.PhotoLibrary, "Photos & videos", Modifier.weight(1f)) {
                        media.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageAndVideo))
                    }
                    SourceButton(Icons.Rounded.MusicNote, "Audio", Modifier.weight(1f)) { docs.launch(arrayOf("audio/*")) }
                }
                Spacer(Modifier.height(12.dp))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    SourceButton(Icons.Rounded.Description, "Documents", Modifier.weight(1f)) {
                        docs.launch(arrayOf("application/*", "text/*"))
                    }
                    SourceButton(Icons.Rounded.FolderOpen, "Any file", Modifier.weight(1f)) { docs.launch(arrayOf("*/*")) }
                }
                Spacer(Modifier.height(12.dp))
                SourceButton(Icons.Rounded.Folder, "Whole folder (keeps its structure)", Modifier.fillMaxWidth()) { folder.launch(null) }
                Spacer(Modifier.height(12.dp))
                var text by androidx.compose.runtime.saveable.rememberSaveable { androidx.compose.runtime.mutableStateOf("") }
                val status by vm.textStatus.collectAsState()
                SurfaceCard {
                    Text("Text or link", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(8.dp))
                    androidx.compose.material3.OutlinedTextField(
                        value = text, onValueChange = { text = it.take(20000); vm.clearTextStatus() },
                        modifier = Modifier.fillMaxWidth(), label = { Text("Paste or type") }, maxLines = 4,
                    )
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(onClick = { vm.sendText(text); text = "" }, enabled = text.isNotBlank() && session != null) { Text("Send text") }
                    status?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(top = 6.dp)) }
                }
                Spacer(Modifier.height(8.dp))
                SectionTitle(if (selection.isEmpty()) "Selected" else "Selected (${selection.size})")
            }
            if (selection.isEmpty()) {
                item { EmptyState(Icons.Rounded.FolderOpen, "Nothing selected", "Choose a source above. You can add from several places before sending.") }
            } else {
                val folders = selection.filter { it.dir != null }.groupBy { it.dir!!.substringBefore('/') }
                items(folders.keys.toList(), key = { "folder:$it" }) { top ->
                    val fs = folders.getValue(top)
                    SurfaceCard {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Rounded.Folder, null, tint = MaterialTheme.colorScheme.primary)
                            Spacer(Modifier.width(12.dp))
                            Column(Modifier.weight(1f)) {
                                Text(top, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text("${fs.size} files · ${formatBytes(fs.sumOf { it.size ?: 0L })}", style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            IconButton(onClick = { fs.forEach { vm.removeFromSelection(it.ref) } }) { Icon(Icons.Rounded.Close, contentDescription = "Remove folder $top") }
                        }
                    }
                }
                items(selection.filter { it.dir == null }, key = { it.ref }) { f ->
                    SurfaceCard {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(f.name, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(
                                    if (!f.readable) "Can't be read — it will be skipped" else formatBytes(f.size),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = if (f.readable) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error,
                                )
                            }
                            IconButton(onClick = { vm.removeFromSelection(f.ref) }) { Icon(Icons.Rounded.Close, contentDescription = "Remove ${f.name}") }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SourceButton(icon: ImageVector, label: String, modifier: Modifier, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, modifier = modifier.height(72.dp), shape = MaterialTheme.shapes.medium) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(icon, null)
            Text(label, style = MaterialTheme.typography.labelLarge)
        }
    }
}
