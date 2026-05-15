package dev.orivael.axiom.ui.screens

import android.app.role.RoleManager
import android.content.Context
import android.os.Build
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import dev.orivael.axiom.data.SettingsStore
import dev.orivael.axiom.network.AxiomClient
import dev.orivael.axiom.telephony.CallScreeningStore
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

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

        Spacer(Modifier.height(12.dp))
        CallScreeningSection()
    }
}

/**
 * Call-screening role + log surface.
 *
 * Shows whether AXIOM is currently the system's default call screener,
 * exposes a one-tap launcher for the OS role-request flow, and renders
 * the in-memory [CallScreeningStore] so the user can see what the
 * service has observed since launch.
 */
@Composable
private fun CallScreeningSection() {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val entries by CallScreeningStore.entries.collectAsState()

    var isHolder by remember { mutableStateOf(currentlyHoldsCallScreeningRole(context)) }
    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        // Role grant may have flipped — re-check.
        isHolder = currentlyHoldsCallScreeningRole(context)
    }

    // Re-check when the user comes back from the role-grant Activity.
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                isHolder = currentlyHoldsCallScreeningRole(context)
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                "Call screening (ORVL-019 Hello Operator)",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            Spacer(Modifier.height(4.dp))
            val statusColor =
                if (isHolder) MaterialTheme.colorScheme.tertiary
                else MaterialTheme.colorScheme.onSurfaceVariant
            val statusText =
                if (isHolder) "✓ Active — AXIOM is the default call screener"
                else "○ Not active — calls are not being screened by AXIOM"
            Text(
                statusText,
                style = MaterialTheme.typography.bodyMedium,
                color = statusColor,
            )

            Spacer(Modifier.height(8.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = {
                        val intent = createCallScreeningRequestIntent(context)
                        if (intent != null) launcher.launch(intent)
                    },
                    enabled = !isHolder && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q,
                    modifier = Modifier.weight(1f),
                ) {
                    Text(
                        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q)
                            "Needs Android 10+"
                        else if (isHolder) "Already active"
                        else "Set as default call screener"
                    )
                }
                Button(
                    onClick = { CallScreeningStore.clear() },
                    enabled = entries.isNotEmpty(),
                    colors = ButtonDefaults.outlinedButtonColors(),
                    modifier = Modifier.weight(1f),
                ) { Text("Clear log") }
            }

            Spacer(Modifier.height(8.dp))

            Text(
                "Recent screening events (${entries.size}):",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (entries.isEmpty()) {
                Spacer(Modifier.height(4.dp))
                Text(
                    "(none yet — make a test call after granting the role)",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                val fmt = remember { SimpleDateFormat("HH:mm:ss", Locale.US) }
                entries.take(8).forEach { e ->
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "${fmt.format(Date(e.timestampMs))}  ${e.direction}  " +
                            "${e.number}  →  ${e.verdict}",
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace,
                    )
                }
            }

            Spacer(Modifier.height(8.dp))
            Text(
                "This slice records calls but does not block or silence them. " +
                "The next slice adds audio capture + on-device transcription " +
                "so the gate sees the call content, not just the number.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun currentlyHoldsCallScreeningRole(context: Context): Boolean {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return false
    val rm = context.getSystemService(RoleManager::class.java) ?: return false
    return rm.isRoleHeld(RoleManager.ROLE_CALL_SCREENING)
}

private fun createCallScreeningRequestIntent(context: Context): android.content.Intent? {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return null
    val rm = context.getSystemService(RoleManager::class.java) ?: return null
    return rm.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING)
}
