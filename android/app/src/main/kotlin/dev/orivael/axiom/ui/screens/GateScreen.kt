package dev.orivael.axiom.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import dev.orivael.axiom.data.SettingsStore
import dev.orivael.axiom.network.AxiomClient
import dev.orivael.axiom.network.InboundDecision
import dev.orivael.axiom.network.OutboundDecision
import dev.orivael.axiom.network.SovereignAlert
import dev.orivael.axiom.security.SignatureVerifier
import dev.orivael.axiom.security.VerificationBadge
import dev.orivael.axiom.security.rememberSignatureVerifier
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * Drives `/phone/outbound` and `/phone/inbound`.
 *
 * Each tap fires a request and prepends the signed result (or sovereign
 * alert) to a scrolling history. Session ID is generated once per
 * screen instance so the L1 → L2 → L3 escalation works across
 * consecutive blocks within one demo session.
 */
@Composable
fun GateScreen() {
    val context = LocalContext.current
    val store   = remember { SettingsStore(context) }
    val scope   = rememberCoroutineScope()

    val serverUrl   by store.serverUrl.collectAsState(initial = SettingsStore.DEFAULT_SERVER_URL)
    val bearerToken by store.bearerToken.collectAsState(initial = "")
    val verifier    = rememberSignatureVerifier()

    var input by remember { mutableStateOf("") }
    var sessionId by remember { mutableStateOf(UUID.randomUUID().toString()) }
    var history by remember { mutableStateOf<List<GateEntry>>(emptyList()) }
    var busy by remember { mutableStateOf(false) }

    fun client() = AxiomClient(baseUrl = serverUrl, bearerToken = bearerToken)

    fun verify(rawJson: String): SignatureVerifier.VerificationResult =
        verifier?.verify(rawJson)
            ?: SignatureVerifier.VerificationResult.Unconfigured

    fun submitOutbound() {
        if (input.isBlank() || busy) return
        val text = input
        scope.launch {
            busy = true
            val r = client().phoneOutbound(text, sessionId = sessionId)
            history = listOf(GateEntry.fromOutbound(text, r, ::verify)) + history
            input = ""
            busy = false
        }
    }

    fun submitInbound() {
        if (input.isBlank() || busy) return
        val text = input
        scope.launch {
            busy = true
            val r = client().phoneInbound(text, sessionId = sessionId)
            history = listOf(GateEntry.fromInbound(text, r, ::verify)) + history
            input = ""
            busy = false
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(12.dp)) {
        // Session bar — id + reset
        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            Text(
                "Session ${sessionId.take(8)}…",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.width(8.dp))
            TextButton(
                onClick = {
                    sessionId = UUID.randomUUID().toString()
                    history = emptyList()
                },
            ) { Text("New session") }
        }

        Spacer(Modifier.height(6.dp))

        OutlinedTextField(
            value = input,
            onValueChange = { input = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Message") },
            placeholder = { Text("Type a message to classify…") },
            singleLine = false,
            minLines = 2,
            maxLines = 4,
        )

        Spacer(Modifier.height(8.dp))

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Button(
                onClick = ::submitOutbound,
                enabled = !busy && input.isNotBlank(),
                modifier = Modifier.weight(1f),
            ) { Text(if (busy) "…" else "Outbound") }
            Button(
                onClick = ::submitInbound,
                enabled = !busy && input.isNotBlank(),
                modifier = Modifier.weight(1f),
            ) { Text(if (busy) "…" else "Inbound") }
        }

        Spacer(Modifier.height(12.dp))

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(history) { entry -> GateEntryCard(entry) }
        }
    }
}

private sealed interface GateEntry {
    val source: String       // "out" or "in"
    val text: String
    val verification: SignatureVerifier.VerificationResult

    data class Ok(
        override val source: String,
        override val text: String,
        override val verification: SignatureVerifier.VerificationResult,
        val intentClass: String,
        val confidence: Double,
        val signature: String,
        val annotation: String,
    ) : GateEntry

    data class Blocked(
        override val source: String,
        override val text: String,
        override val verification: SignatureVerifier.VerificationResult,
        val alert: SovereignAlert,
    ) : GateEntry

    data class Failed(
        override val source: String,
        override val text: String,
        val message: String,
    ) : GateEntry {
        override val verification: SignatureVerifier.VerificationResult =
            SignatureVerifier.VerificationResult.Unconfigured
    }

    companion object {
        fun fromOutbound(
            text: String,
            r: AxiomClient.OutboundResult,
            verify: (String) -> SignatureVerifier.VerificationResult,
        ): GateEntry = when (r) {
            is AxiomClient.OutboundResult.Ok -> Ok(
                source = "out", text = text,
                verification = verify(r.rawJson),
                intentClass = r.decision.intentClass,
                confidence = r.decision.confidence,
                signature = r.decision.signature,
                annotation = if (r.decision.piiCategories.isNotEmpty())
                    "PII redacted: ${r.decision.piiCategories.joinToString()}"
                else "ANF cores=${r.decision.anfCoresActive}",
            )
            is AxiomClient.OutboundResult.Blocked -> Blocked(
                source = "out", text = text,
                verification = verify(r.rawJson),
                alert = r.alert,
            )
            is AxiomClient.OutboundResult.Failed -> Failed("out", text, r.message)
        }

        fun fromInbound(
            text: String,
            r: AxiomClient.InboundResult,
            verify: (String) -> SignatureVerifier.VerificationResult,
        ): GateEntry = when (r) {
            is AxiomClient.InboundResult.Ok -> Ok(
                source = "in", text = text,
                verification = verify(r.rawJson),
                intentClass = r.decision.intentClass,
                confidence = r.decision.confidence,
                signature = r.decision.signature,
                annotation = "monotonic=${r.decision.monotonicPass}",
            )
            is AxiomClient.InboundResult.Blocked -> Blocked(
                source = "in", text = text,
                verification = verify(r.rawJson),
                alert = r.alert,
            )
            is AxiomClient.InboundResult.Failed -> Failed("in", text, r.message)
        }
    }
}

@Composable
private fun GateEntryCard(entry: GateEntry) {
    val border = when (entry) {
        is GateEntry.Blocked -> MaterialTheme.colorScheme.error
        is GateEntry.Failed  -> MaterialTheme.colorScheme.outline
        is GateEntry.Ok      -> MaterialTheme.colorScheme.primary
    }
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant,
        ),
        border = androidx.compose.foundation.BorderStroke(2.dp, border),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            val tag = when (entry) {
                is GateEntry.Blocked -> "BLOCKED L${entry.alert.level} ${entry.alert.intentClass}"
                is GateEntry.Failed  -> "FAILED"
                is GateEntry.Ok      -> "${if (entry.source == "out") "DELIVERED" else "DISPLAYED"} " +
                                          entry.intentClass
            }
            androidx.compose.foundation.layout.Row(
                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
            ) {
                Text(
                    "${entry.source.uppercase()}  ·  $tag",
                    style = MaterialTheme.typography.labelLarge,
                    color = border,
                    modifier = Modifier.weight(1f),
                )
                if (entry !is GateEntry.Failed) VerificationBadge(entry.verification)
            }
            Spacer(Modifier.height(4.dp))
            Text(
                entry.text,
                style = MaterialTheme.typography.bodyMedium,
            )
            Spacer(Modifier.height(4.dp))
            val footer = when (entry) {
                is GateEntry.Ok      -> "conf=${"%.2f".format(entry.confidence)}  " +
                                          "${entry.annotation}  sig=${entry.signature.take(8)}…"
                is GateEntry.Blocked -> "${entry.alert.reason}  sig=${entry.alert.signature.take(8)}…"
                is GateEntry.Failed  -> entry.message
            }
            Text(
                footer,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
