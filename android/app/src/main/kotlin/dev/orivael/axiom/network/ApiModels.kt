package dev.orivael.axiom.network

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Wire types for the AXIOM REST surface. Field names mirror the
 * server's `dataclasses.asdict` output exactly so the JSON contract is
 * shared between the Python emulator and the Kotlin client.
 *
 * @Serializable classes need kotlinx.serialization on the classpath
 * (see app/build.gradle.kts) plus the `kotlin.plugin.serialization`
 * Gradle plugin.
 */

// ── /phone/outbound + /phone/inbound ───────────────────────────────────
@Serializable
data class PhoneOutboundRequest(
    val text: String,
    @SerialName("session_id") val sessionId: String? = null,
)

@Serializable
data class PhoneInboundRequest(
    val text: String,
    @SerialName("session_id") val sessionId: String? = null,
    @SerialName("redacted_categories") val redactedCategories: List<String>? = null,
)

@Serializable
data class OutboundDecision(
    @SerialName("query_id") val queryId: String,
    @SerialName("redacted_text") val redactedText: String,
    @SerialName("intent_class") val intentClass: String,
    val confidence: Double,
    @SerialName("pii_categories") val piiCategories: List<String> = emptyList(),
    @SerialName("anf_distance") val anfDistance: Double,
    @SerialName("anf_cores_active") val anfCoresActive: Int,
    @SerialName("anf_gate_fired") val anfGateFired: Boolean,
    @SerialName("anf_signature") val anfSignature: String,
    val timestamp: String,
    val signature: String,
)

@Serializable
data class InboundDecision(
    @SerialName("response_id") val responseId: String,
    @SerialName("intent_class") val intentClass: String,
    val confidence: Double,
    @SerialName("monotonic_pass") val monotonicPass: Boolean,
    @SerialName("privacy_injection") val privacyInjection: Boolean,
    val timestamp: String,
    val signature: String,
)

@Serializable
data class SovereignAlert(
    val gate: String,
    @SerialName("intent_class") val intentClass: String,
    val confidence: Double,
    val level: Int,
    val reason: String,
    val timestamp: String,
    val signature: String,
)

@Serializable
data class SovereignAlertEnvelope(
    val error: String,
    val alert: SovereignAlert,
)

// ── /phone/status ──────────────────────────────────────────────────────
@Serializable
data class PhoneStatus(
    @SerialName("device_fingerprint") val deviceFingerprint: String,
    @SerialName("memory_depth") val memoryDepth: Int,
    @SerialName("events_suspended") val eventsSuspended: List<String> = emptyList(),
    @SerialName("anf_calls") val anfCalls: Int,
    @SerialName("trust_level") val trustLevel: Int,
)

// ── /cmaa/fleet ────────────────────────────────────────────────────────
@Serializable
data class CmaaFleet(
    @SerialName("trust_levels") val trustLevels: Map<String, Int>,
    val suspended: List<String> = emptyList(),
    @SerialName("review_queue") val reviewQueue: Int,
    val timestamp: String,
)

// ── /shield/status ─────────────────────────────────────────────────────
@Serializable
data class ShieldStatus(
    val running: Boolean,
    @SerialName("started_at") val startedAt: String? = null,
    val ticks: Int,
    val escalations: Int,
    @SerialName("poll_interval_ms") val pollIntervalMs: Int,
    @SerialName("learning_seconds") val learningSeconds: Int,
    @SerialName("learning_complete") val learningComplete: Boolean,
    @SerialName("dry_run") val dryRun: Boolean,
    @SerialName("suspended_pids") val suspendedPids: List<Int> = emptyList(),
    @SerialName("manifolds_tracked") val manifoldsTracked: Int,
)
