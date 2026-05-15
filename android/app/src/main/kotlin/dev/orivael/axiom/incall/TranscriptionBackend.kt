package dev.orivael.axiom.incall

import android.content.Context

/**
 * Abstraction over "an ASR engine that emits final utterances".
 *
 * Two implementations:
 *   - [SpeechRecognizerBackend] uses Android's system SpeechRecognizer.
 *     Free, no model to download, but the OS decides whether to run
 *     offline or upload audio to Google's STT servers. About 25% of
 *     Android devices ship without an offline STT engine.
 *   - [VoskBackend] uses on-device Kaldi via the Vosk AAR. Audio
 *     never leaves the device. Requires a one-time ~50MB English
 *     model download via [VoskModelManager].
 *
 * [TranscriptionBackendFactory] picks Vosk when the model is
 * installed and falls back to SpeechRecognizer otherwise.
 *
 * Backends emit only **final** utterances — partial / streaming
 * results are discarded so the AXIOM gate sees stable text. They
 * also surface their own description so the Live Call Mode banner
 * can label which engine is running.
 */
interface TranscriptionBackend {
    /** Short label rendered in the UI (e.g. "Vosk (offline)"). */
    val label: String

    /** Begin recognition. [onUtterance] fires for each non-empty final
     *  result; [onError] for unrecoverable failures. Restart cadence is
     *  the backend's responsibility — it should keep streaming until
     *  [stop] is called. */
    fun start(onUtterance: (String) -> Unit, onError: (String) -> Unit)

    /** Release every native resource (audio recorder, decoder, etc.). */
    fun stop()
}

object TranscriptionBackendFactory {

    /** Picks the privacy-preferred backend that's actually usable today.
     *  Vosk wins when the model is installed; otherwise we fall back to
     *  the OS SpeechRecognizer so the slice still functions out of the
     *  box. */
    fun create(context: Context): TranscriptionBackend =
        if (VoskModelManager.isInstalled(context))
            VoskBackend(context)
        else
            SpeechRecognizerBackend(context)
}
