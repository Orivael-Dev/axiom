package dev.orivael.axiom.telephony

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * Process-scoped log of recent call-screening events.
 *
 * Intentionally in-memory and process-scoped — the next slice will
 * promote this to Room or DataStore-Proto for persistence across
 * process death. For now the operator opens the Settings tab while
 * the service runs and sees the last N events; if the OS kills the
 * service, the log resets.
 *
 * Capped to MAX_ENTRIES so a noisy demo phone doesn't blow memory.
 * Newest entry first.
 */
object CallScreeningStore {

    data class Entry(
        val timestampMs: Long,
        val direction: String,     // "incoming" / "outgoing"
        val number: String,
        val verdict: String,        // human-readable verdict line
    )

    private const val MAX_ENTRIES = 50

    private val _entries = MutableStateFlow<List<Entry>>(emptyList())
    val entries: StateFlow<List<Entry>> = _entries.asStateFlow()

    fun append(entry: Entry) {
        _entries.update { prev ->
            (listOf(entry) + prev).take(MAX_ENTRIES)
        }
    }

    fun clear() { _entries.value = emptyList() }
}
