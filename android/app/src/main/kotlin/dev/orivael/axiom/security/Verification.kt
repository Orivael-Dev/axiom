package dev.orivael.axiom.security

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import dev.orivael.axiom.data.SettingsStore

/**
 * Returns a [SignatureVerifier] backed by the currently-stored master
 * key, or null when no key is configured. Re-derived whenever the
 * stored blob changes — so toggling the master key in Settings
 * updates every consuming screen immediately.
 */
@Composable
fun rememberSignatureVerifier(): SignatureVerifier? {
    val context = LocalContext.current
    val store = remember { SettingsStore(context) }
    val km    = remember { KeystoreManager() }
    val blob by store.masterKeyBlob.collectAsState(initial = KeystoreManager.Blob.EMPTY)
    return remember(blob.ciphertextB64, blob.ivB64) {
        val hex = km.decrypt(blob) ?: return@remember null
        SignatureVerifier(hex)
    }
}

/**
 * Tiny coloured chip that shows the verification verdict next to each
 * signed response card. Three states matching [SignatureVerifier.VerificationResult]:
 *   - Verified    → green "✓ HMAC verified"
 *   - Invalid     → red   "✗ INVALID SIGNATURE"
 *   - Unconfigured → gray  "○ Unverified"
 */
@Composable
fun VerificationBadge(result: SignatureVerifier.VerificationResult?) {
    val scheme = MaterialTheme.colorScheme
    val (bg, fg, text) = when (result) {
        SignatureVerifier.VerificationResult.Verified ->
            Triple(scheme.tertiaryContainer ?: scheme.primaryContainer,
                   scheme.tertiary, "✓ HMAC verified")
        SignatureVerifier.VerificationResult.Invalid ->
            Triple(scheme.errorContainer, scheme.error,
                   "✗ INVALID SIGNATURE")
        SignatureVerifier.VerificationResult.Unconfigured, null ->
            Triple(scheme.surfaceVariant, scheme.onSurfaceVariant,
                   "○ Unverified")
    }
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        color = fg,
        modifier = Modifier
            .clip(RoundedCornerShape(4.dp))
            .background(bg)
            .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}
