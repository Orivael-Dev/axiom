package dev.orivael.axiom.incall

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.util.zip.ZipInputStream
import java.util.concurrent.TimeUnit

/**
 * Downloads + manages the on-device Vosk model used by [VoskBackend].
 *
 * The model is a ~50 MB zip from alphacephei.com. We extract it into
 * the app's private files dir and surface the install state to the
 * Settings UI via [progress]. Once installed,
 * [TranscriptionBackendFactory] auto-prefers Vosk over the system
 * SpeechRecognizer so call audio never leaves the device.
 *
 * The model URL is pinned to a specific release so a remote bump
 * can't silently break verification — when Vosk publishes a new
 * model we change one line in code, ship a new APK, done.
 *
 * Footprint:
 *   - Download: ~40 MB compressed
 *   - Extracted: ~50 MB on disk
 *   - Free RAM at runtime: ~120 MB while a session is active
 *
 * Cleared via [remove] — the Settings UI exposes this so users can
 * reclaim storage without uninstalling the app.
 */
object VoskModelManager {

    /** Stable English small-model URL. Replace when Vosk publishes a newer release. */
    const val MODEL_URL: String =
        "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

    /** Marker file inside the extracted model — its presence is what
     *  [isInstalled] checks. Picking a real Kaldi file means a half-
     *  extracted directory won't fool the loader. */
    private const val MARKER_RELATIVE: String = "am/final.mdl"

    private const val DIR_NAME: String = "vosk-model"

    private val _progress = MutableStateFlow<DownloadState>(DownloadState.Idle)
    val progress: StateFlow<DownloadState> = _progress.asStateFlow()

    sealed interface DownloadState {
        object Idle : DownloadState
        data class Downloading(val bytesSoFar: Long, val totalBytes: Long) : DownloadState
        object Extracting : DownloadState
        object Installed  : DownloadState
        data class Failed(val message: String) : DownloadState
    }

    fun installRoot(context: Context): File =
        File(context.filesDir, DIR_NAME)

    fun isInstalled(context: Context): Boolean {
        val marker = File(installRoot(context), MARKER_RELATIVE)
        return marker.exists() && marker.length() > 0
    }

    fun sizeOnDisk(context: Context): Long = installRoot(context).walk()
        .filter { it.isFile }.map { it.length() }.sum()

    /** Wipe the model. Idempotent — safe to call on a non-installed phone. */
    fun remove(context: Context) {
        installRoot(context).deleteRecursively()
        _progress.value = DownloadState.Idle
    }

    /**
     * Download + extract the model. Runs on Dispatchers.IO; caller
     * (Settings UI) wraps in a coroutine scope. State updates flow via
     * [progress]; the function returns once the marker file exists or
     * an exception bubbles out.
     */
    suspend fun download(context: Context) = withContext(Dispatchers.IO) {
        try {
            if (isInstalled(context)) {
                _progress.value = DownloadState.Installed
                return@withContext
            }

            val target = installRoot(context)
            target.deleteRecursively()
            target.mkdirs()

            val http = OkHttpClient.Builder()
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .build()
            val req = Request.Builder().url(MODEL_URL).get().build()

            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) {
                    throw RuntimeException("download HTTP ${resp.code}")
                }
                val body = resp.body ?: throw RuntimeException("empty body")
                val total = body.contentLength().takeIf { it > 0 } ?: -1L

                val tmpZip = File(context.cacheDir, "$DIR_NAME.zip")
                tmpZip.outputStream().use { out ->
                    body.byteStream().use { src ->
                        val buf = ByteArray(64 * 1024)
                        var read = src.read(buf)
                        var soFar = 0L
                        var lastReport = 0L
                        while (read >= 0) {
                            out.write(buf, 0, read)
                            soFar += read
                            // Throttle progress emissions to ~10 per MB so
                            // the StateFlow doesn't flood the UI.
                            if (soFar - lastReport > 256 * 1024) {
                                _progress.value = DownloadState.Downloading(soFar, total)
                                lastReport = soFar
                            }
                            read = src.read(buf)
                        }
                    }
                }

                _progress.value = DownloadState.Extracting
                extractZip(tmpZip, target)
                tmpZip.delete()
            }

            if (!isInstalled(context)) {
                // Extraction succeeded but the marker file is missing — the
                // archive layout might have changed upstream.
                throw RuntimeException(
                    "marker file '$MARKER_RELATIVE' missing after extraction"
                )
            }
            _progress.value = DownloadState.Installed
        } catch (t: Throwable) {
            Log.e(TAG, "vosk model install failed", t)
            installRoot(context).deleteRecursively()
            _progress.value = DownloadState.Failed(t.message ?: t::class.simpleName ?: "?")
        }
    }

    /**
     * Vosk's zip wraps the model in a top-level folder
     * (e.g. `vosk-model-small-en-us-0.15/`). We strip that prefix so
     * the on-disk layout is `<filesDir>/vosk-model/am/...` regardless
     * of the model release.
     */
    private fun extractZip(zip: File, target: File) {
        ZipInputStream(zip.inputStream()).use { z ->
            var entry = z.nextEntry
            while (entry != null) {
                val name = entry.name
                // Strip the leading top-level folder, if any.
                val slash = name.indexOf('/')
                val rel = if (slash >= 0) name.substring(slash + 1) else name
                if (rel.isBlank()) {
                    entry = z.nextEntry; continue
                }
                val out = File(target, rel)
                // Path-traversal guard — Zip Slip.
                if (!out.canonicalPath.startsWith(target.canonicalPath)) {
                    throw RuntimeException("zip slip: $name")
                }
                if (entry.isDirectory) {
                    out.mkdirs()
                } else {
                    out.parentFile?.mkdirs()
                    FileOutputStream(out).use { sink -> z.copyTo(sink) }
                }
                entry = z.nextEntry
            }
        }
    }

    private const val TAG = "VoskModelManager"
}
