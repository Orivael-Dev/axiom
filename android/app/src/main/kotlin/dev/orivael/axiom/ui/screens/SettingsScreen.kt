package dev.orivael.axiom.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import dev.orivael.axiom.data.SettingsStore
import dev.orivael.axiom.network.AxiomClient
import kotlinx.coroutines.launch

@Composable
fun SettingsScreen() {
    val context = LocalContext.current
    val store   = remember { SettingsStore(context) }
    val scope   = rememberCoroutineScope()

    val persistedUrl   by store.serverUrl.collectAsState(initial = SettingsStore.DEFAULT_SERVER_URL)
    val persistedToken by store.bearerToken.collectAsState(initial = "")

    // Local edit buffers so the user can type freely without spamming DataStore.
    var url   by remember { mutableStateOf("") }
    var token by remember { mutableStateOf("") }
    LaunchedEffect(persistedUrl)   { url   = persistedUrl }
    LaunchedEffect(persistedToken) { token = persistedToken }

    var probe by remember { mutableStateOf<String?>(null) }
    var busy  by remember { mutableStateOf(false) }

    fun save() {
        scope.launch {
            store.setServerUrl(url.trim().ifBlank { SettingsStore.DEFAULT_SERVER_URL })
            store.setBearerToken(token.trim())
        }
    }

    fun test() {
        if (busy) return
        scope.launch {
            busy = true
            probe = null
            val client = AxiomClient(
                baseUrl = url.trim().ifBlank { SettingsStore.DEFAULT_SERVER_URL },
                bearerToken = token.trim(),
            )
            probe = client.phoneStatus().fold(
                onSuccess = { s -> "✓ reachable — fingerprint ${s.deviceFingerprint}, " +
                                    "anf_calls=${s.anfCalls}" },
                onFailure = { e -> "✗ ${e::class.simpleName}: ${e.message}" },
            )
            busy = false
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(12.dp).verticalScroll(rememberScrollState()),
    ) {
        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("Server URL") },
            placeholder = { Text("http://10.0.2.2:8000") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = token,
            onValueChange = { token = it },
            label = { Text("Bearer token (optional)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))

        Button(
            onClick = ::save,
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Save") }

        Spacer(Modifier.height(8.dp))

        Button(
            onClick = ::test,
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (busy) "Probing…" else "Test connection") }

        Spacer(Modifier.height(12.dp))

        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant,
            ),
        ) {
            Column(modifier = Modifier.padding(12.dp)) {
                Text("Notes", style = MaterialTheme.typography.titleMedium,
                      color = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.height(4.dp))
                Text(
                    "Emulator → host loopback resolves at 10.0.2.2.\n" +
                    "For a physical device on the same LAN, replace with your " +
                    "host's LAN IP and start axiom_server.py with " +
                    "AXIOM_API_TOKEN set + bound to the right interface.",
                    style = MaterialTheme.typography.bodySmall,
                )
                if (probe != null) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        probe!!,
                        style = MaterialTheme.typography.bodySmall,
                        color = if (probe!!.startsWith("✓"))
                            MaterialTheme.colorScheme.tertiary
                        else
                            MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}
