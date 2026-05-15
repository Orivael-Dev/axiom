package dev.orivael.axiom.security

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * HMAC-SHA256 verifier for AXIOM server-signed payloads.
 *
 * The Python stack derives per-surface keys from AXIOM_MASTER_KEY via
 *
 *     derive_key(salt) = HMAC-SHA256(master_key_bytes, salt)
 *
 * (see axiom_signing.derive_key). The Sovereign Phone surface uses
 * salt b"axiom-aspa-device-v1" — that's the key that signs every
 * [OutboundDecision], [InboundDecision], and [SovereignAlert] returned
 * by /phone/outbound and /phone/inbound.
 *
 * The verifier:
 *   1. Strips the `signature` field from the received JSON.
 *   2. Canonicalizes the remainder via [CanonicalJson.encode] —
 *      matches Python's `json.dumps(sort_keys=True, ensure_ascii=True,
 *      separators=(',', ':'))` byte-for-byte.
 *   3. HMACs that canonical string under the derived device key.
 *   4. Constant-time compares to the received hex signature.
 *
 * Returns [VerificationResult.Verified] on match, [Invalid] on
 * mismatch, [Unconfigured] when the master key is empty (no
 * verification possible — the UI renders "○ Unverified" without
 * marking the response as bad).
 */
class SignatureVerifier(masterKeyHex: String) {

    private val masterKeyBytes: ByteArray? = runCatching {
        require(masterKeyHex.length == 64) { "master key must be 64 hex chars" }
        hexToBytes(masterKeyHex)
    }.getOrNull()

    private val deviceKey: ByteArray? = masterKeyBytes?.let {
        hmac(it, "axiom-aspa-device-v1".toByteArray(Charsets.UTF_8))
    }

    /** Lenient JSON for parsing the response — server can add fields. */
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

    sealed interface VerificationResult {
        /** Key configured + signature matches the recomputed HMAC. */
        object Verified    : VerificationResult
        /** Key configured + signature does NOT match. */
        object Invalid     : VerificationResult
        /** No master key entered yet — UI should show neutral state. */
        object Unconfigured : VerificationResult
    }

    /** Verify a raw JSON response string against the device key. */
    fun verify(rawJson: String): VerificationResult {
        val key = deviceKey ?: return VerificationResult.Unconfigured
        val parsed = runCatching {
            json.parseToJsonElement(rawJson) as JsonObject
        }.getOrElse { return VerificationResult.Invalid }

        val sigHex = parsed["signature"]?.jsonPrimitive?.content
            ?: return VerificationResult.Invalid

        // Strip `signature` from the canonical payload — server always
        // signs the payload BEFORE adding the signature field.
        val payload = JsonObject(parsed.toMap().filterKeys { it != "signature" })
        val canonical = CanonicalJson.encode(payload).toByteArray(Charsets.UTF_8)
        val computedHex = bytesToHex(hmac(key, canonical))
        return if (constantTimeEquals(computedHex, sigHex))
            VerificationResult.Verified
        else
            VerificationResult.Invalid
    }

    // ── Helpers ──────────────────────────────────────────────────────
    private fun hmac(key: ByteArray, message: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        return mac.doFinal(message)
    }

    private fun hexToBytes(hex: String): ByteArray {
        require(hex.length % 2 == 0) { "odd-length hex" }
        val out = ByteArray(hex.length / 2)
        for (i in out.indices) {
            val hi = Character.digit(hex[2 * i],     16)
            val lo = Character.digit(hex[2 * i + 1], 16)
            require(hi >= 0 && lo >= 0) { "non-hex char" }
            out[i] = ((hi shl 4) or lo).toByte()
        }
        return out
    }

    private fun bytesToHex(bytes: ByteArray): String {
        val sb = StringBuilder(bytes.size * 2)
        for (b in bytes) {
            sb.append(HEX[(b.toInt() ushr 4) and 0xF])
            sb.append(HEX[b.toInt() and 0xF])
        }
        return sb.toString()
    }

    private fun constantTimeEquals(a: String, b: String): Boolean {
        if (a.length != b.length) return false
        var diff = 0
        for (i in a.indices) diff = diff or (a[i].code xor b[i].code)
        return diff == 0
    }

    companion object {
        private val HEX = "0123456789abcdef".toCharArray()
    }
}
