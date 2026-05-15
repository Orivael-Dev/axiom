package dev.orivael.axiom.security

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Keystore-wrapped storage for the AXIOM_MASTER_KEY.
 *
 * The phone's copy of AXIOM_MASTER_KEY is what verifies every signed
 * payload coming back from the server. Per ORVL-019: "The
 * AXIOM_MASTER_KEY never leaves the device." On Android, "never
 * leaves the device" means the bytes live in a Keystore-wrapped form
 * that the OS will not export to other apps and that the user can't
 * read off the filesystem.
 *
 * Strategy:
 *   - On first use, generate an AES-256-GCM key inside Android
 *     Keystore. The key is non-exportable; we only ever get a
 *     SecretKey handle that can be used for Cipher.doFinal but not
 *     for getEncoded().
 *   - The AXIOM_MASTER_KEY (a 64-char hex string) is encrypted under
 *     that wrapper key. Ciphertext + IV are stored as base64 strings
 *     in DataStore (see [dev.orivael.axiom.data.SettingsStore]).
 *   - To verify a signature, decrypt the master key and HMAC the
 *     canonical payload. The decrypted bytes live in memory only for
 *     the duration of one verification call.
 *
 * Backed by hardware-backed Keystore when the device supports it
 * (most devices Android 9+); pure software Keystore otherwise.
 * Either way the bytes do not leave the app sandbox.
 */
class KeystoreManager {

    private val keyStore: KeyStore = KeyStore.getInstance(ANDROID_KEYSTORE)
        .also { it.load(null) }

    /** AES-256-GCM ciphertext + nonce — both base64-encoded for DataStore. */
    data class Blob(val ciphertextB64: String, val ivB64: String) {
        fun isEmpty(): Boolean = ciphertextB64.isBlank() || ivB64.isBlank()

        companion object {
            val EMPTY = Blob("", "")
        }
    }

    /** Encrypt a UTF-8 string under the Keystore-wrapped AES-GCM key. */
    fun encrypt(plain: String): Blob {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateWrapperKey())
        val ct = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Blob(
            ciphertextB64 = Base64.encodeToString(ct,         Base64.NO_WRAP),
            ivB64         = Base64.encodeToString(cipher.iv, Base64.NO_WRAP),
        )
    }

    /** Decrypt a blob back to its plaintext. Returns null if the blob is
     *  empty (no master key has been entered yet) or if decryption fails. */
    fun decrypt(blob: Blob): String? {
        if (blob.isEmpty()) return null
        return runCatching {
            val iv = Base64.decode(blob.ivB64, Base64.NO_WRAP)
            val ct = Base64.decode(blob.ciphertextB64, Base64.NO_WRAP)
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(
                Cipher.DECRYPT_MODE,
                getOrCreateWrapperKey(),
                GCMParameterSpec(GCM_TAG_BITS, iv),
            )
            String(cipher.doFinal(ct), Charsets.UTF_8)
        }.getOrNull()
    }

    private fun getOrCreateWrapperKey(): SecretKey {
        keyStore.getEntry(WRAPPER_ALIAS, null)?.let {
            return (it as KeyStore.SecretKeyEntry).secretKey
        }
        val generator = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE,
        )
        generator.init(
            KeyGenParameterSpec.Builder(
                WRAPPER_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setRandomizedEncryptionRequired(true)   // forces a fresh IV
                .build()
        )
        return generator.generateKey()
    }

    companion object {
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val WRAPPER_ALIAS    = "axiom-master-key-wrapper-v1"
        private const val TRANSFORMATION   = "AES/GCM/NoPadding"
        private const val GCM_TAG_BITS     = 128
    }
}
