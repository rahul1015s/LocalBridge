package io.github.localbridge.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.border
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.GppMaybe
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.icons.rounded.QrCodeScanner
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import io.github.localbridge.core.ReceiverException

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PairScreen(vm: AppViewModel) {
    val state by vm.pair.collectAsState()
    val context = LocalContext.current
    var granted by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted = it }
    var manual by rememberSaveable { mutableStateOf("") }
    var showManual by rememberSaveable { mutableStateOf(false) }
    val busy = state is PairState.Connecting || state is PairState.CertificateVerified

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("Connect to computer") },
                navigationIcon = { BackButton { vm.cancelPairing(); vm.back() } },
            )
        },
    ) { pad ->
        Column(
            Modifier.padding(pad).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            SurfaceCard {
                Text("On your computer", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(8.dp))
                StepRow(1, "Turn on this phone's hotspot, or join the same Wi-Fi as your computer.")
                StepRow(2, "Connect the computer to it.")
                StepRow(3, "Open LocalBridge there and press Start receiving.")
                StepRow(4, "Scan the code it shows.")
            }

            if (busy) {
                SurfaceCard(container = MaterialTheme.colorScheme.primaryContainer) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 3.dp)
                        Spacer(Modifier.width(16.dp))
                        Column {
                            if (state is PairState.CertificateVerified) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Icon(Icons.Rounded.Lock, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
                                    Spacer(Modifier.width(6.dp))
                                    Text("Computer verified", style = MaterialTheme.typography.titleMedium)
                                }
                                Text("Approve “${vm.settings.state.value.deviceName}” on the computer to finish pairing.", style = MaterialTheme.typography.bodyMedium)
                            } else {
                                Text("Connecting securely…", style = MaterialTheme.typography.titleMedium)
                                Text("Checking the computer's identity.", style = MaterialTheme.typography.bodyMedium)
                            }
                        }
                    }
                    TextButton(onClick = { vm.cancelPairing() }, modifier = Modifier.align(Alignment.End)) { Text("Cancel") }
                }
            } else {
                if (state is PairState.Failed) FailureCard(state as PairState.Failed) { vm.resetPairError() }

                if (granted) {
                    Column {
                        Column(
                            Modifier.fillMaxWidth().aspectRatio(1f).clip(RoundedCornerShape(24.dp))
                                .border(2.dp, MaterialTheme.colorScheme.primary, RoundedCornerShape(24.dp)),
                        ) { QrScanner(onCode = { vm.connect(it) }, modifier = Modifier.fillMaxWidth().aspectRatio(1f)) }
                        Spacer(Modifier.height(8.dp))
                        Text("Point the camera at the code on your computer.", style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                } else {
                    SurfaceCard {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            IconBadge(Icons.Rounded.QrCodeScanner, MaterialTheme.colorScheme.primary, MaterialTheme.colorScheme.primaryContainer)
                            Spacer(Modifier.width(14.dp))
                            Column {
                                Text("Camera needed to scan", style = MaterialTheme.typography.titleMedium)
                                Text("Only used to read the pairing code. Nothing is recorded or saved.", style = MaterialTheme.typography.bodyMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        Spacer(Modifier.height(12.dp))
                        Button(onClick = { permission.launch(Manifest.permission.CAMERA) }, Modifier.fillMaxWidth()) { Text("Allow camera") }
                    }
                }

                if (!showManual) {
                    TextButton(onClick = { showManual = true }, modifier = Modifier.align(Alignment.CenterHorizontally)) {
                        Text("Can't scan? Paste the pairing link")
                    }
                } else {
                    SurfaceCard {
                        Text("Paste pairing link", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(4.dp))
                        Text("Use “Copy pairing link” on the computer. The link is just as private as the QR code — don't share it.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.height(10.dp))
                        OutlinedTextField(
                            value = manual, onValueChange = { manual = it }, modifier = Modifier.fillMaxWidth(),
                            label = { Text("localbridge://pair?…") }, maxLines = 3,
                        )
                        Spacer(Modifier.height(10.dp))
                        Button(onClick = { vm.connect(manual) }, enabled = manual.isNotBlank(), modifier = Modifier.fillMaxWidth()) { Text("Connect") }
                    }
                }
            }
        }
    }
}

@Composable
private fun FailureCard(f: PairState.Failed, onDismiss: () -> Unit) {
    val mismatch = f.kind == ReceiverException.Kind.CERT_MISMATCH
    SurfaceCard(container = MaterialTheme.colorScheme.errorContainer) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Rounded.GppMaybe, null, tint = MaterialTheme.colorScheme.onErrorContainer)
            Spacer(Modifier.width(10.dp))
            Text(if (mismatch) "Couldn't verify this computer" else "Couldn't connect", style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onErrorContainer)
        }
        Spacer(Modifier.height(6.dp))
        Text(f.message, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onErrorContainer)
        val tips = when (f.kind) {
            ReceiverException.Kind.UNREACHABLE, ReceiverException.Kind.TIMEOUT -> listOf(
                "Both devices must be on the same network (or the computer on this phone's hotspot).",
                "Make sure “Start receiving” is still on.",
                "Some hotspots block devices from talking to each other (client isolation). Try a shared Wi-Fi network instead.",
                "Check that the computer's firewall allows incoming connections on that network.",
            )
            ReceiverException.Kind.CERT_MISMATCH -> listOf(
                "Rescan the code directly from your own computer's screen.",
                "If this keeps happening on a network you don't control, someone may be interfering. Don't continue.",
            )
            ReceiverException.Kind.UNAUTHORIZED -> listOf("Press “New pairing code” on the computer and scan again.")
            else -> emptyList()
        }
        tips.forEach { Text("• $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onErrorContainer, modifier = Modifier.padding(top = 4.dp)) }
        TextButton(onClick = onDismiss, modifier = Modifier.align(Alignment.End)) { Text("Dismiss", color = MaterialTheme.colorScheme.onErrorContainer) }
    }
}
