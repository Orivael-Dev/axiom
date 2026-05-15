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
import dev.orivael.axiom.network.CmaaFleet
import dev.orivael.axiom.network.PhoneStatus
import dev.orivael.axiom.network.ShieldStatus
import kotlinx.coroutines.launch

@Composable
fun StatusScreen() {
    val context = LocalContext.current
    val store   = remember { SettingsStore(context) }
    val scope   = rememberCoroutineScope()

    val serverUrl   by store.serverUrl.collectAsState(initial = SettingsStore.DEFAULT_SERVER_URL)
    val bearerToken by store.bearerToken.collectAsState(initial = "")

    var phone  by remember { mutableStateOf<Result<PhoneStatus>?>(null) }
    var fleet  by remember { mutableStateOf<Result<CmaaFleet>?>(null) }
    var shield by remember { mutableStateOf<Result<ShieldStatus>?>(null) }
    var busy   by remember { mutableStateOf(false) }

    fun client() = AxiomClient(baseUrl = serverUrl, bearerToken = bearerToken)

    fun refresh() {
        if (busy) return
        scope.launch {
            busy = true
            val c = client()
            phone  = c.phoneStatus()
            fleet  = c.cmaaFleet()
            shield = c.shieldStatus()
            busy = false
        }
    }

    LaunchedEffect(serverUrl, bearerToken) { refresh() }

    Column(
        modifier = Modifier.fillMaxSize().padding(12.dp).verticalScroll(rememberScrollState()),
    ) {
        Button(
            onClick = ::refresh,
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (busy) "Refreshing…" else "Refresh") }

        Spacer(Modifier.height(12.dp))

        ResultCard(title = "Phone", body = phone) { p ->
            "device_fingerprint  ${p.deviceFingerprint}\n" +
            "trust_level          ${p.trustLevel}\n" +
            "memory_depth         ${p.memoryDepth}\n" +
            "events_suspended     ${p.eventsSuspended}\n" +
            "anf_calls            ${p.anfCalls}"
        }

        Spacer(Modifier.height(8.dp))

        ResultCard(title = "CMAA fleet", body = fleet) { f ->
            "trust_levels:\n" +
            f.trustLevels.entries.joinToString("\n") { (k, v) -> "  $k = TL$v" } + "\n" +
            "suspended            ${f.suspended}\n" +
            "review_queue         ${f.reviewQueue}"
        }

        Spacer(Modifier.height(8.dp))

        ResultCard(title = "OS Shield daemon", body = shield) { s ->
            "running              ${s.running}\n" +
            "ticks                ${s.ticks}\n" +
            "escalations          ${s.escalations}\n" +
            "dry_run              ${s.dryRun}\n" +
            "learning_complete    ${s.learningComplete}\n" +
            "suspended_pids       ${s.suspendedPids}\n" +
            "manifolds_tracked    ${s.manifoldsTracked}"
        }
    }
}

@Composable
private fun <T> ResultCard(
    title: String,
    body: Result<T>?,
    render: (T) -> String,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium,
                  color = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.height(4.dp))
            val text = when {
                body == null              -> "(not loaded yet)"
                body.isSuccess            -> render(body.getOrThrow())
                else                       -> "error: ${body.exceptionOrNull()?.message}"
            }
            Text(
                text,
                style = MaterialTheme.typography.bodySmall,
                fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
            )
        }
    }
}
