package dev.orivael.axiom.incall

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log

/**
 * System-SpeechRecognizer-backed transcription. Same code path that
 * shipped in Slice 4, lifted into a [TranscriptionBackend] so the
 * service can swap to [VoskBackend] when the on-device model is
 * installed.
 *
 * Behaviour:
 *   - Asks for `EXTRA_PREFER_OFFLINE=true` so devices with a local
 *     STT engine never upload audio. The flag is a hint — devices
 *     without offline STT silently use the network.
 *   - Restarts recognition 150ms after every onResults / onError to
 *     make the one-shot API into an effectively-continuous stream.
 *   - Final results only — partial results are discarded.
 *
 * Stops itself cleanly on [stop]; subsequent restarts after stop are
 * suppressed via the stopping flag.
 */
class SpeechRecognizerBackend(private val context: Context) : TranscriptionBackend {

    override val label: String = "System SpeechRecognizer"

    private val mainHandler = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    @Volatile private var stopping = false
    private var onUtterance: ((String) -> Unit)? = null
    private var onError: ((String) -> Unit)? = null

    override fun start(onUtterance: (String) -> Unit, onError: (String) -> Unit) {
        if (!SpeechRecognizer.isRecognitionAvailable(context)) {
            onError("SpeechRecognizer is not available on this device")
            return
        }
        this.onUtterance = onUtterance
        this.onError = onError
        stopping = false
        mainHandler.post { startListening() }
    }

    override fun stop() {
        stopping = true
        mainHandler.post {
            recognizer?.destroy()
            recognizer = null
        }
    }

    private fun startListening() {
        if (stopping) return
        recognizer?.destroy()
        val r = SpeechRecognizer.createSpeechRecognizer(context)
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
                texts?.firstOrNull { it.isNotBlank() }?.let {
                    onUtterance?.invoke(it.trim())
                }
                restart()
            }

            override fun onError(error: Int) {
                Log.w(TAG, "SpeechRecognizer error=$error")
                // Common transient errors (timeout, no-match) are normal during
                // pauses — restart cleanly so the stream is effectively
                // continuous.
                restart()
            }
        })
        r.startListening(buildIntent())
    }

    private fun restart() {
        if (!stopping) mainHandler.postDelayed({ startListening() }, RESTART_DELAY_MS)
    }

    private fun buildIntent(): Intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
        putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
        putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
        putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName)
    }

    companion object {
        private const val TAG = "SpeechRecognizerBackend"
        private const val RESTART_DELAY_MS = 150L
    }
}
