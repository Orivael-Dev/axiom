package dev.orivael.axiom.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * Thin OkHttp wrapper for the AXIOM REST surface.
 *
 * One [Sealed] result type per call — the server can return either the
 * happy-path decision or a [SovereignAlertEnvelope] with HTTP 403. The
 * caller pattern-matches on the sealed class rather than checking
 * exceptions.
 *
 * Every call runs on the IO dispatcher so the UI thread is never
 * blocked. Timeouts are tight (5s connect, 10s read) so a dead server
 * surfaces as an error quickly instead of hanging the UI.
 */
class AxiomClient(
    private val baseUrl: String,
    private val bearerToken: String = "",
) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .build()

    private val json = Json {
        ignoreUnknownKeys = true     // server may add fields; client tolerates
        coerceInputValues = true
        explicitNulls = false
    }

    /**
     * Every Ok / Blocked variant carries [rawJson] so callers can run
     * the canonical HMAC verifier client-side ([dev.orivael.axiom
     * .security.SignatureVerifier]) without re-serialising the parsed
     * decision (which would lose float-precision parity with the
     * Python side).
     */
    sealed interface OutboundResult {
        data class Ok(
            val decision: OutboundDecision,
            val rawJson:  String,
        ) : OutboundResult
        data class Blocked(
            val alert:    SovereignAlert,
            val rawJson:  String,
        ) : OutboundResult
        data class Failed(val message: String) : OutboundResult
    }

    sealed interface InboundResult {
        data class Ok(
            val decision: InboundDecision,
            val rawJson:  String,
        ) : InboundResult
        data class Blocked(
            val alert:    SovereignAlert,
            val rawJson:  String,
        ) : InboundResult
        data class Failed(val message: String) : InboundResult
    }

    suspend fun phoneOutbound(
        text: String,
        sessionId: String? = null,
    ): OutboundResult = withContext(Dispatchers.IO) {
        val body = json.encodeToString(
            PhoneOutboundRequest(text = text, sessionId = sessionId)
        )
        runCatching {
            http.newCall(post("/phone/outbound", body)).execute().use { resp ->
                val raw = resp.body?.string().orEmpty()
                when (resp.code) {
                    200 -> OutboundResult.Ok(json.decodeFromString(raw), raw)
                    403 -> {
                        val envelope = json.decodeFromString<SovereignAlertEnvelope>(raw)
                        // The verifier wants the alert JSON, not the wrapping envelope.
                        val alertJson = extractAlertJson(raw)
                        OutboundResult.Blocked(envelope.alert, alertJson)
                    }
                    else -> OutboundResult.Failed("HTTP ${resp.code} — $raw")
                }
            }
        }.getOrElse { OutboundResult.Failed("${it::class.simpleName}: ${it.message}") }
    }

    suspend fun phoneInbound(
        text: String,
        sessionId: String? = null,
    ): InboundResult = withContext(Dispatchers.IO) {
        val body = json.encodeToString(
            PhoneInboundRequest(text = text, sessionId = sessionId)
        )
        runCatching {
            http.newCall(post("/phone/inbound", body)).execute().use { resp ->
                val raw = resp.body?.string().orEmpty()
                when (resp.code) {
                    200 -> InboundResult.Ok(json.decodeFromString(raw), raw)
                    403 -> {
                        val envelope = json.decodeFromString<SovereignAlertEnvelope>(raw)
                        val alertJson = extractAlertJson(raw)
                        InboundResult.Blocked(envelope.alert, alertJson)
                    }
                    else -> InboundResult.Failed("HTTP ${resp.code} — $raw")
                }
            }
        }.getOrElse { InboundResult.Failed("${it::class.simpleName}: ${it.message}") }
    }

    /** Pull the inner alert JSON out of `{"error":"…","alert":{…}}`. */
    private fun extractAlertJson(raw: String): String = runCatching {
        val element = kotlinx.serialization.json.Json.parseToJsonElement(raw)
        val alert = (element as kotlinx.serialization.json.JsonObject)["alert"]
        alert?.toString() ?: raw
    }.getOrElse { raw }

    suspend fun phoneStatus(): Result<PhoneStatus> = simpleGet("/phone/status")
    suspend fun cmaaFleet():   Result<CmaaFleet>   = simpleGet("/cmaa/fleet")
    suspend fun shieldStatus(): Result<ShieldStatus> = simpleGet("/shield/status")

    private suspend inline fun <reified T> simpleGet(path: String): Result<T> =
        withContext(Dispatchers.IO) {
            runCatching {
                http.newCall(get(path)).execute().use { resp ->
                    val raw = resp.body?.string().orEmpty()
                    if (resp.code != 200) {
                        throw RuntimeException("HTTP ${resp.code} — $raw")
                    }
                    json.decodeFromString<T>(raw)
                }
            }
        }

    private fun get(path: String): Request =
        Request.Builder()
            .url(baseUrl.trimEnd('/') + path)
            .also(::applyAuth)
            .get()
            .build()

    private fun post(path: String, body: String): Request =
        Request.Builder()
            .url(baseUrl.trimEnd('/') + path)
            .also(::applyAuth)
            .post(body.toRequestBody(JSON_MEDIA))
            .build()

    private fun applyAuth(builder: Request.Builder) {
        if (bearerToken.isNotBlank()) {
            builder.header("Authorization", "Bearer $bearerToken")
        }
    }

    companion object {
        private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
    }
}
