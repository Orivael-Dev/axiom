package dev.orivael.axiom.telephony

import android.os.Build
import android.os.Bundle
import android.telecom.Call
import android.telecom.CallScreeningService
import android.util.Log
import androidx.annotation.RequiresApi
import dev.orivael.axiom.data.SettingsStore
import dev.orivael.axiom.network.AxiomClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * AXIOM Sovereign Phone — system-level call screening.
 *
 * Registered in the manifest under `BIND_SCREENING_SERVICE`. When the
 * user grants the CALL_SCREENING role (via Settings → role request),
 * Android invokes [onScreenCall] for every incoming call BEFORE the
 * phone rings. We forward the caller metadata to the AXIOM REST server
 * (`/phone/outbound`) and append a record to [CallScreeningStore] so
 * the UI can show what happened.
 *
 * This slice deliberately **allows every call through** — the screening
 * decision is recorded for the user to inspect, but no calls are
 * silenced or rejected yet. The next slice will:
 *  - Add a Settings policy: "block on L2+", "warn on L1+", etc.
 *  - Add audio capture + on-device transcription so the gate sees the
 *    actual conversation, not just the caller ID.
 *
 * Compile-safe back to minSdk 26; the screening API is API 24+. Pre-29
 * devices have a different role-grant flow but the service interface
 * is the same.
 */
class AxiomCallScreeningService : CallScreeningService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onScreenCall(callDetails: Call.Details) {
        val number = callDetails.handle?.schemeSpecificPart
            ?: callDetails.handle?.toString()
            ?: "unknown"
        val direction = if (
            callDetails.callDirection == Call.Details.DIRECTION_INCOMING ||
            Build.VERSION.SDK_INT < Build.VERSION_CODES.Q
        ) "incoming" else "outgoing"

        Log.i(TAG, "screen call $direction $number")

        // Always allow the call through for this slice — we are
        // observing + recording, not acting. The next slice will let
        // the user configure a policy threshold.
        val response = CallResponse.Builder()
            .setDisallowCall(false)
            .setRejectCall(false)
            .setSilenceCall(false)
            .setSkipCallLog(false)
            .setSkipNotification(false)
            .build()
        respondToCall(callDetails, response)

        // In parallel, post the screening event to the AXIOM REST
        // server. The classifier won't have anything intelligent to
        // say about a bare phone number, but the integration point is
        // wired so the next slice (audio transcription) drops in
        // cleanly. Result is captured into the in-memory log either
        // way for the Settings tab to display.
        scope.launch {
            runCatching {
                val store = SettingsStore(this@AxiomCallScreeningService)
                val baseUrl = store.serverUrl.first()
                val token   = store.bearerToken.first()
                val client = AxiomClient(baseUrl, token)
                val text = "Incoming call from $number"
                val r = client.phoneOutbound(text, sessionId = "callscreen-${UUID.randomUUID()}")
                val entry = CallScreeningStore.Entry(
                    timestampMs = System.currentTimeMillis(),
                    direction = direction,
                    number = number,
                    verdict = when (r) {
                        is AxiomClient.OutboundResult.Ok      ->
                            "DELIVERED · ${r.decision.intentClass}"
                        is AxiomClient.OutboundResult.Blocked ->
                            "BLOCKED L${r.alert.level} · ${r.alert.intentClass}"
                        is AxiomClient.OutboundResult.Failed  ->
                            "FAILED · ${r.message}"
                    },
                )
                CallScreeningStore.append(entry)
            }.onFailure { Log.w(TAG, "axiom forward failed: ${it.message}") }
        }
    }

    companion object {
        private const val TAG = "AxiomCallScreening"
    }
}
