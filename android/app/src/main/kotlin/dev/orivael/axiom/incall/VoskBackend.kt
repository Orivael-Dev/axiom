package dev.orivael.axiom.incall

import android.content.Context
import android.util.Log
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.RecognitionListener
import org.vosk.android.SpeechService

/**
 * On-device speech recognition via Vosk (Kaldi-based).
 *
 * Reads PCM 16-bit mono audio at 16 kHz from the microphone, decodes
 * locally, and emits JSON like `{"text": "<final utterance>"}` for
 * each speech segment. **The audio never leaves the device** — this
 * is the privacy promise from ORVL-019 made real.
 *
 * Lifecycle:
 *   - [start] opens the Vosk [Model] from the path
 *     [VoskModelManager.installRoot] and starts a [SpeechService]
 *     that owns its own AudioRecord. The recognizer is reused for
 *     the entire session so transition-state isn't lost between
 *     pauses.
 *   - [stop] tears down the SpeechService, releases the Recognizer,
 *     and closes the Model. Calling stop on a backend that never
 *     started is a no-op.
 *
 * Reliability notes baked into the implementation:
 *   - We DO NOT keep a static cached Model. Vosk's Model wraps a
 *     native handle; releasing + reloading per session is cheap (a
 *     few hundred ms for the small English model) and avoids
 *     subtle teardown races between back-to-back call sessions.
 *   - Partial results are discarded — the AXIOM gate only sees text
 *     once Vosk has finalised an utterance. Keeps the audit trail
 *     deterministic.
 *   - Errors from the AudioRecord or decoder surface via [onError]
 *     and STOP the session. Re-arming is the caller's responsibility
 *     (the foreground service re-creates the backend on its next
 *     onStartCommand).
 */
class VoskBackend(private val context: Context) : TranscriptionBackend {

    override val label: String = "Vosk (offline)"

    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

    private var model: Model? = null
    private var recognizer: Recognizer? = null
    private var speechService: SpeechService? = null

    override fun start(onUtterance: (String) -> Unit, onError: (String) -> Unit) {
        try {
            val modelPath = VoskModelManager.installRoot(context).absolutePath
            val m = Model(modelPath)
            val r = Recognizer(m, SAMPLE_RATE)
            val svc = SpeechService(r, SAMPLE_RATE)
            model = m
            recognizer = r
            speechService = svc

            svc.startListening(object : RecognitionListener {
                override fun onPartialResult(hypothesis: String?) {
                    // Discard — AXIOM gate only acts on finalised text.
                }

                override fun onResult(hypothesis: String?) {
                    val text = extractText(hypothesis)
                    if (text.isNotBlank()) onUtterance(text)
                }

                override fun onFinalResult(hypothesis: String?) {
                    val text = extractText(hypothesis)
                    if (text.isNotBlank()) onUtterance(text)
                }

                override fun onError(exception: Exception?) {
                    Log.w(TAG, "vosk error: ${exception?.message}")
                    onError("Vosk error: ${exception?.message ?: "unknown"}")
                    stop()
                }

                override fun onTimeout() {
                    // Restart by re-calling startListening on the existing
                    // recognizer — Vosk keeps its own state for continuous
                    // recognition across timeouts.
                    speechService?.startListening(this)
                }
            })
        } catch (t: Throwable) {
            Log.e(TAG, "vosk init failed", t)
            onError("Vosk init failed: ${t.message ?: t::class.simpleName}")
            stop()
        }
    }

    override fun stop() {
        speechService?.stop()
        speechService?.shutdown()
        speechService = null
        recognizer?.close()
        recognizer = null
        model?.close()
        model = null
    }

    private fun extractText(rawJson: String?): String {
        if (rawJson.isNullOrBlank()) return ""
        return runCatching {
            val obj = json.parseToJsonElement(rawJson) as JsonObject
            obj["text"]?.jsonPrimitive?.content
                ?: obj["partial"]?.jsonPrimitive?.content
                ?: ""
        }.getOrDefault("")
    }

    companion object {
        private const val TAG = "VoskBackend"
        // Vosk's small English model is trained at 16 kHz mono.
        // Other sample rates are upsampled internally and lose accuracy.
        private const val SAMPLE_RATE = 16_000f
    }
}
