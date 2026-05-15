package dev.orivael.axiom.incall

import dev.orivael.axiom.security.SignatureVerifier
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * Process-scoped shared state between [InCallTranscriptionService] and
 * the Hello Operator UI.
 *
 * The service writes here as each utterance is transcribed and gated;
 * the UI's "Live Call Mode" panel collects from here. Two flows:
 *
 *   - [isActive] — true while the foreground service is running. UI
 *     uses this to flip the toggle and show the recording indicator.
 *   - [events]   — newest-first ring of the last 50 transcription
 *     events with their verdicts. UI renders this as a scrolling
 *     transcript feed.
 *
 * The next slice will promote the events to DataStore-Proto + a
 * per-call rollup so the user can review prior calls. For now this is
 * memory-only; a process restart resets the feed.
 */
object TranscriptionStore {

    data class Event(
        val timestampMs:  Long,
        val utterance:    String,
        val verdict:      String,                     // human-readable verdict line
        val intentClass:  String,                     // "INFORM" / "HARM" / etc.
        val level:        Int,                        // 0 = no block, 1..3 = sovereign level
        val verification: SignatureVerifier.VerificationResult,
    )

    private val _isActive = MutableStateFlow(false)
    val isActive: StateFlow<Boolean> = _isActive.asStateFlow()

    private val _events = MutableStateFlow<List<Event>>(emptyList())
    val events: StateFlow<List<Event>> = _events.asStateFlow()

    private val _backendLabel = MutableStateFlow("")
    val backendLabel: StateFlow<String> = _backendLabel.asStateFlow()

    fun setActive(active: Boolean) { _isActive.value = active }

    fun setBackendLabel(label: String) { _backendLabel.value = label }

    fun append(event: Event) {
        _events.update { prev -> (listOf(event) + prev).take(MAX_EVENTS) }
    }

    fun clear() { _events.value = emptyList() }

    private const val MAX_EVENTS = 50
}
