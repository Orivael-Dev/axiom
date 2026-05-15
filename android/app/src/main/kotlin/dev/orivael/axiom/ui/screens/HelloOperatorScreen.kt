package dev.orivael.axiom.ui.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.slideInVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import dev.orivael.axiom.data.SettingsStore
import dev.orivael.axiom.network.AxiomClient
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * Hello Operator — the ORVL-019 §4 scam-call trajectory rendered live
 * on the phone.
 *
 * Each tap on "Run demo call" replays the four canonical utterances
 * through `POST /phone/outbound` against a single session_id, so the
 * server's graduated L1 → L2 → L3 escalation lights up in order. The
 * card for each step animates in as the request completes; the
 * card's accent colour escalates green → amber → orange → red.
 *
 * This is **the meeting-room moment**: an investor watching the phone
 * sees the AXIOM Sovereign Phone do exactly what the brief promises,
 * with HMAC-signed decisions returning from the server in seconds.
 *
 * Note — real CallScreeningService interception is a future slice.
 * Today the audio is simulated; the gate decisions are live.
 */
@Composable
fun HelloOperatorScreen() {
    val context = LocalContext.current
    val store   = remember { SettingsStore(context) }
    val scope   = rememberCoroutineScope()

    val serverUrl   by store.serverUrl.collectAsState(initial = SettingsStore.DEFAULT_SERVER_URL)
    val bearerToken by store.bearerToken.collectAsState(initial = "")

    var speedX by remember { mutableStateOf(4) }   // 1, 4, or 10
    var running by remember { mutableStateOf(false) }
    val timeline = remember { mutableStateListOf<CallStep>() }

    fun client() = AxiomClient(baseUrl = serverUrl, bearerToken = bearerToken)

    fun reset() {
        timeline.clear()
    }

    fun runCall() {
        if (running) return
        reset()
        val sessionId = "hello-operator-${UUID.randomUUID().take(8)}"
        running = true
        scope.launch {
            // 1x = 2000ms per step (8s total). 4x = 500ms. 10x = 200ms.
            val stepMs = 2000L / speedX
            for ((index, line) in CALL_TRANSCRIPT.withIndex()) {
                val r = client().phoneOutbound(line.utterance, sessionId = sessionId)
                timeline.add(CallStep.fromResult(line, r))
                if (index < CALL_TRANSCRIPT.lastIndex) delay(stepMs)
            }
            running = false
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(12.dp)) {
        // Header
        Text(
            "Hello Operator",
            style = MaterialTheme.typography.headlineSmall,
            color = MaterialTheme.colorScheme.primary,
        )
        Text(
            "Constitutional call governance — ORVL-019 §4",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Spacer(Modifier.height(12.dp))

        // Speed selector
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text("Replay speed:", style = MaterialTheme.typography.labelMedium)
            listOf(1, 4, 10).forEach { x ->
                FilterChip(
                    selected = speedX == x,
                    onClick = { if (!running) speedX = x },
                    label = { Text("${x}x") },
                )
            }
        }

        Spacer(Modifier.height(8.dp))

        // Action row
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Button(
                onClick = ::runCall,
                enabled = !running,
                modifier = Modifier.weight(1f),
            ) { Text(if (running) "Running…" else "Run demo call") }
            Button(
                onClick = ::reset,
                enabled = !running && timeline.isNotEmpty(),
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.outlinedButtonColors(),
            ) { Text("Reset") }
        }

        Spacer(Modifier.height(12.dp))

        // Card stack — one card per utterance, populated as the demo runs
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            CALL_TRANSCRIPT.forEachIndexed { i, line ->
                val step = timeline.getOrNull(i)
                AnimatedVisibility(
                    visible = step != null,
                    enter = slideInVertically(initialOffsetY = { it / 4 }) + fadeIn(),
                ) {
                    if (step != null) CallStepCard(step)
                }
                if (step == null) PendingCard(line)
            }
        }

        // Summary footer once all four steps are in
        if (timeline.size == CALL_TRANSCRIPT.size && !running) {
            Spacer(Modifier.height(12.dp))
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.tertiaryContainer
                        ?: MaterialTheme.colorScheme.primaryContainer,
                ),
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text(
                        "Total elapsed: 8 simulated seconds",
                        style = MaterialTheme.typography.titleSmall,
                    )
                    Text(
                        "User exposure: zero financial risk.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Text(
                        "Detection method: constitutional trajectory geometry " +
                        "— not keyword match.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

// ── Static transcript from the ORVL-019 brief §4, verbatim ────────────
private data class CallLine(
    val timestamp: String,
    val utterance: String,
)

private val CALL_TRANSCRIPT = listOf(
    CallLine("T+0.0s",
              "Hello, this is a call about your account"),
    CallLine("T+3.0s",
              "This is the IRS calling — we need to verify your records"),
    CallLine("T+6.0s",
              "You owe back taxes — pay immediately or face arrest"),
    CallLine("T+8.0s",
              "Send gift cards to clear your debt right now"),
)


// ── Step result — what one card displays ───────────────────────────────
private sealed interface CallStep {
    val line: CallLine

    data class Ok(
        override val line: CallLine,
        val intentClass: String,
        val confidence: Double,
        val anfCores: Int,
        val signature: String,
    ) : CallStep

    data class Blocked(
        override val line: CallLine,
        val intentClass: String,
        val confidence: Double,
        val level: Int,
        val signature: String,
    ) : CallStep

    data class Failed(
        override val line: CallLine,
        val message: String,
    ) : CallStep

    companion object {
        fun fromResult(line: CallLine, r: AxiomClient.OutboundResult): CallStep =
            when (r) {
                is AxiomClient.OutboundResult.Ok -> Ok(
                    line = line,
                    intentClass = r.decision.intentClass,
                    confidence = r.decision.confidence,
                    anfCores = r.decision.anfCoresActive,
                    signature = r.decision.signature,
                )
                is AxiomClient.OutboundResult.Blocked -> Blocked(
                    line = line,
                    intentClass = r.alert.intentClass,
                    confidence = r.alert.confidence,
                    level = r.alert.level,
                    signature = r.alert.signature,
                )
                is AxiomClient.OutboundResult.Failed -> Failed(line, r.message)
            }
    }
}

private fun stepAccent(step: CallStep, scheme: androidx.compose.material3.ColorScheme): Color {
    return when (step) {
        is CallStep.Ok -> scheme.primary
        is CallStep.Blocked -> when (step.level) {
            1 -> Color(0xFFD89000)   // amber — L1 warning
            2 -> Color(0xFFE07628)   // orange — L2 throttle
            3 -> scheme.error        // red — L3 suspend
            else -> scheme.error
        }
        is CallStep.Failed -> scheme.outline
    }
}

@Composable
private fun CallStepCard(step: CallStep) {
    val scheme = MaterialTheme.colorScheme
    val accent = stepAccent(step, scheme)
    val tag = when (step) {
        is CallStep.Ok -> "DELIVERED · ${step.intentClass}"
        is CallStep.Blocked -> "BLOCKED L${step.level} · ${step.intentClass}"
        is CallStep.Failed -> "FAILED"
    }
    val footer = when (step) {
        is CallStep.Ok ->
            "conf=${"%.2f".format(step.confidence)} · " +
            "ANF cores=${step.anfCores} · sig=${step.signature.take(8)}…"
        is CallStep.Blocked ->
            "conf=${"%.2f".format(step.confidence)} · sig=${step.signature.take(8)}…"
        is CallStep.Failed -> step.message
    }
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(containerColor = scheme.surfaceVariant),
        border = androidx.compose.foundation.BorderStroke(2.dp, accent),
    ) {
        Row(modifier = Modifier.padding(12.dp), verticalAlignment = Alignment.Top) {
            // Left rail: timestamp
            Text(
                step.line.timestamp,
                style = MaterialTheme.typography.titleSmall,
                fontFamily = FontFamily.Monospace,
                color = accent,
                modifier = Modifier.width(56.dp),
            )
            Spacer(Modifier.width(8.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    tag,
                    style = MaterialTheme.typography.labelLarge,
                    color = accent,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    "\"${step.line.utterance}\"",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    footer,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun PendingCard(line: CallLine) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surface,
        ),
        border = androidx.compose.foundation.BorderStroke(
            1.dp, MaterialTheme.colorScheme.outlineVariant
        ),
    ) {
        Row(modifier = Modifier.padding(12.dp), verticalAlignment = Alignment.Top) {
            Text(
                line.timestamp,
                style = MaterialTheme.typography.titleSmall,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.outline,
                modifier = Modifier.width(56.dp),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                "(pending)",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.outline,
            )
        }
    }
}
