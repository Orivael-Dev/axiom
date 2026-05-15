package dev.orivael.axiom.incall

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.getSystemService
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import dev.orivael.axiom.MainActivity
import dev.orivael.axiom.R
import dev.orivael.axiom.data.SettingsStore
import dev.orivael.axiom.network.AxiomClient
import dev.orivael.axiom.security.KeystoreManager
import dev.orivael.axiom.security.SignatureVerifier
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * Foreground service that captures microphone audio during an active
 * call, transcribes it, and routes every utterance through the AXIOM
 * `/phone/inbound` gate in real time.
 *
 * The transcription engine is selected at service start by
 * [TranscriptionBackendFactory]:
 *
 *   - [VoskBackend] when the user has downloaded the on-device Vosk
 *     model (Settings → "Vosk on-device model" card). Audio never
 *     leaves the device — the privacy promise from ORVL-019 made real.
 *   - [SpeechRecognizerBackend] otherwise. The OS decides whether to
 *     run offline or upload audio to Google's STT servers.
 *
 * The backend's label is included in the active-banner the UI shows so
 * the operator can see which engine is running.
 *
 * Android won't expose call audio to non-system apps. The mic captures
 * both sides only when the user puts the call on speakerphone — the UI
 * makes this explicit.
 */
class InCallTranscriptionService : LifecycleService() {

    private var backend: TranscriptionBackend? = null
    private var sessionId: String = ""

    override fun onCreate() {
        super.onCreate()
        ensureNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        ServiceCompat.startForeground(
            this, NOTIF_ID, buildNotification(),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            else 0,
        )

        sessionId = "incall-${UUID.randomUUID()}"
        val chosen = TranscriptionBackendFactory.create(this)
        backend = chosen
        TranscriptionStore.setBackendLabel(chosen.label)
        TranscriptionStore.setActive(true)

        chosen.start(
            onUtterance = ::handleUtterance,
            onError = { msg ->
                Log.w(TAG, "backend error: $msg")
                TranscriptionStore.append(TranscriptionStore.Event(
                    timestampMs = System.currentTimeMillis(),
                    utterance = "(backend error)",
                    verdict = "ERROR · $msg",
                    intentClass = "UNCERTAIN",
                    level = 0,
                    verification = SignatureVerifier.VerificationResult.Unconfigured,
                ))
                stopSelf()
            },
        )
        return START_STICKY
    }

    override fun onDestroy() {
        backend?.stop()
        backend = null
        TranscriptionStore.setActive(false)
        super.onDestroy()
    }

    // ── Utterance handling — single shared path for both backends ─────
    private fun handleUtterance(text: String) {
        Log.i(TAG, "utterance (${backend?.label}): ${text.take(60)}")
        lifecycleScope.launch {
            val store   = SettingsStore(this@InCallTranscriptionService)
            val baseUrl = store.serverUrl.first()
            val token   = store.bearerToken.first()
            val client = AxiomClient(baseUrl, token)
            val blob = store.masterKeyBlob.first()
            val verifier: SignatureVerifier? = KeystoreManager().decrypt(blob)
                ?.let { SignatureVerifier(it) }

            val r = client.phoneInbound(text, sessionId = sessionId)
            val (verdict, intentClass, level, rawJson) = when (r) {
                is AxiomClient.InboundResult.Ok      -> Quadruple(
                    "DISPLAYED · ${r.decision.intentClass}",
                    r.decision.intentClass, 0, r.rawJson,
                )
                is AxiomClient.InboundResult.Blocked -> Quadruple(
                    "BLOCKED L${r.alert.level} · ${r.alert.intentClass}",
                    r.alert.intentClass, r.alert.level, r.rawJson,
                )
                is AxiomClient.InboundResult.Failed  -> Quadruple(
                    "FAILED · ${r.message}", "UNCERTAIN", 0, null,
                )
            }
            val verification = rawJson?.let { verifier?.verify(it) }
                ?: SignatureVerifier.VerificationResult.Unconfigured

            TranscriptionStore.append(TranscriptionStore.Event(
                timestampMs  = System.currentTimeMillis(),
                utterance    = text,
                verdict      = verdict,
                intentClass  = intentClass,
                level        = level,
                verification = verification,
            ))
        }
    }

    // ── Foreground notification ───────────────────────────────────────
    private fun ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "AXIOM Call Screening",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Active while AXIOM is transcribing an in-progress call."
                setShowBadge(false)
            }
            getSystemService<NotificationManager>()?.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(): Notification {
        val tap = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_speakerphone)
            .setContentTitle(getString(R.string.app_name))
            .setContentText("Live call mode — transcribing audio for AXIOM gate")
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(tap)
            .build()
    }

    // Kotlin only ships Pair / Triple.
    private data class Quadruple<A, B, C, D>(
        val a: A, val b: B, val c: C, val d: D,
    )
    private operator fun <A, B, C, D> Quadruple<A, B, C, D>.component1(): A = a
    private operator fun <A, B, C, D> Quadruple<A, B, C, D>.component2(): B = b
    private operator fun <A, B, C, D> Quadruple<A, B, C, D>.component3(): C = c
    private operator fun <A, B, C, D> Quadruple<A, B, C, D>.component4(): D = d

    companion object {
        private const val TAG          = "InCallTranscription"
        private const val CHANNEL_ID   = "axiom_incall"
        private const val NOTIF_ID     = 4711

        fun start(context: Context) {
            val intent = Intent(context, InCallTranscriptionService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, InCallTranscriptionService::class.java))
        }
    }
}
