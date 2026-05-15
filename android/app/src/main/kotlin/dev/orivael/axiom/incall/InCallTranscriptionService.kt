package dev.orivael.axiom.incall

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
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
 * call, transcribes it via the system [SpeechRecognizer], and routes
 * every utterance through the AXIOM `/phone/inbound` gate so the
 * Hello Operator product can act on the call content in real time.
 *
 * ## Android constraints we live with
 *
 * On non-system apps, the OS does **not** allow direct access to the
 * call's audio stream (the [AudioSource.VOICE_CALL] source requires
 * the platform-signed `CAPTURE_AUDIO_OUTPUT` permission). The
 * practical workaround used by every consumer Hello-Operator-style
 * app on the Play Store: the user puts the call on speakerphone, and
 * the device microphone picks up both sides through the air.
 *
 * The Settings screen surfaces this requirement; we don't try to
 * force speakerphone on programatically.
 *
 * ## What the service does, step by step
 *
 *   1. On [onStartCommand] it posts the persistent foreground
 *      notification and asks for `FOREGROUND_SERVICE_MICROPHONE` —
 *      required on API 34+. The notification deep-links back to the
 *      Hello Op screen.
 *   2. It generates a fresh `session_id` and pushes "active=true"
 *      into [TranscriptionStore] so the UI lights up.
 *   3. SpeechRecognizer is created on the main looper, configured
 *      with `EXTRA_PREFER_OFFLINE=true`, and asked for partial
 *      results. The service restarts recognition after every
 *      end-of-speech so the stream is effectively continuous.
 *   4. Each final result becomes one utterance. The service builds an
 *      [AxiomClient] from the persisted settings, posts the utterance
 *      to `/phone/inbound` under the call's `session_id`, runs the
 *      response through [SignatureVerifier] if the master key is set,
 *      and appends a [TranscriptionStore.Event].
 *   5. On stop / process death, the foreground notification is
 *      removed and `active=false` propagates to the UI.
 *
 * ## Failure modes handled gracefully
 *
 *   - [SpeechRecognizer.isRecognitionAvailable] is false on roughly
 *     a quarter of Android devices (especially AOSP / no-GMS builds).
 *     Service detects this on start, posts a one-shot
 *     "asr_unavailable" event, and stops itself. The UI shows a
 *     compact "ASR unavailable on this device" notice.
 *   - REST failures don't kill the service — the event is logged with
 *     the failure message and the next utterance is processed
 *     normally.
 */
class InCallTranscriptionService : LifecycleService() {

    private val mainHandler = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var sessionId: String = ""
    @Volatile private var stopping = false

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
        TranscriptionStore.setActive(true)
        sessionId = "incall-${UUID.randomUUID()}"
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            Log.w(TAG, "SpeechRecognizer unavailable on this device")
            TranscriptionStore.append(TranscriptionStore.Event(
                timestampMs = System.currentTimeMillis(),
                utterance   = "(speech recognition unavailable)",
                verdict     = "ASR_UNAVAILABLE",
                intentClass = "UNCERTAIN",
                level       = 0,
                verification = SignatureVerifier.VerificationResult.Unconfigured,
            ))
            stopSelf()
            return START_NOT_STICKY
        }
        mainHandler.post { startRecognizer() }
        return START_STICKY
    }

    override fun onDestroy() {
        stopping = true
        mainHandler.post {
            recognizer?.destroy()
            recognizer = null
        }
        TranscriptionStore.setActive(false)
        super.onDestroy()
    }

    // ── Recognizer lifecycle ──────────────────────────────────────────
    private fun startRecognizer() {
        if (stopping) return
        recognizer?.destroy()
        val r = SpeechRecognizer.createSpeechRecognizer(this)
        recognizer = r
        r.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}

            override fun onResults(results: Bundle?) {
                val texts = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                texts?.firstOrNull { it.isNotBlank() }?.let { handleUtterance(it.trim()) }
                if (!stopping) mainHandler.postDelayed({ startRecognizer() }, RESTART_DELAY_MS)
            }

            override fun onError(error: Int) {
                Log.w(TAG, "SpeechRecognizer error=$error")
                // Common errors (timeout, no-match) are expected during pauses;
                // restart cleanly so the stream is effectively continuous.
                if (!stopping) mainHandler.postDelayed({ startRecognizer() }, RESTART_DELAY_MS)
            }
        })
        r.startListening(buildRecognizerIntent())
    }

    private fun buildRecognizerIntent(): Intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
        putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
        // Prefer offline so the call audio doesn't leak to Google's servers.
        putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
        putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, packageName)
    }

    // ── Utterance handling ────────────────────────────────────────────
    private fun handleUtterance(text: String) {
        Log.i(TAG, "utterance: ${text.take(60)}")
        lifecycleScope.launch {
            val store = SettingsStore(this@InCallTranscriptionService)
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

    // 4-tuple alias since Kotlin only ships Pair / Triple.
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
        private const val RESTART_DELAY_MS = 150L

        /** Start the service (caller is responsible for RECORD_AUDIO permission). */
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
