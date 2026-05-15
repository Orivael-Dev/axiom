package dev.orivael.axiom.data

import android.content.Context
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "axiom_settings")

/**
 * DataStore-backed persistent settings for the Sovereign Phone client.
 *
 * Only two pieces of state live here today — the REST server URL and an
 * optional bearer token. The default server URL is `10.0.2.2:8000` so
 * the Android emulator reaches the host's loopback (where
 * `axiom_server.py` listens by default).
 *
 * The AXIOM_MASTER_KEY is **deliberately not stored here**. When the
 * phone-side HMAC verification slice ships, the key will go into Android
 * Keystore, not DataStore — Keystore guarantees the key bytes never leave
 * the secure element.
 */
class SettingsStore(private val context: Context) {

    private object Keys {
        val SERVER_URL   = stringPreferencesKey("server_url")
        val BEARER_TOKEN = stringPreferencesKey("bearer_token")
        val MASTER_KEY_CT = stringPreferencesKey("master_key_ct")
        val MASTER_KEY_IV = stringPreferencesKey("master_key_iv")
    }

    val serverUrl: Flow<String> = context.dataStore.data
        .map { it[Keys.SERVER_URL] ?: DEFAULT_SERVER_URL }

    val bearerToken: Flow<String> = context.dataStore.data
        .map { it[Keys.BEARER_TOKEN] ?: "" }

    /**
     * The AXIOM_MASTER_KEY stored as a Keystore-wrapped AES-GCM blob.
     * Plaintext never lives in DataStore — see
     * [dev.orivael.axiom.security.KeystoreManager] for the wrap/unwrap.
     */
    val masterKeyBlob: Flow<dev.orivael.axiom.security.KeystoreManager.Blob> =
        context.dataStore.data.map { prefs ->
            dev.orivael.axiom.security.KeystoreManager.Blob(
                ciphertextB64 = prefs[Keys.MASTER_KEY_CT] ?: "",
                ivB64         = prefs[Keys.MASTER_KEY_IV] ?: "",
            )
        }

    suspend fun setServerUrl(value: String) {
        context.dataStore.edit { it[Keys.SERVER_URL] = value }
    }

    suspend fun setBearerToken(value: String) {
        context.dataStore.edit { it[Keys.BEARER_TOKEN] = value }
    }

    suspend fun setMasterKeyBlob(blob: dev.orivael.axiom.security.KeystoreManager.Blob) {
        context.dataStore.edit { prefs ->
            prefs[Keys.MASTER_KEY_CT] = blob.ciphertextB64
            prefs[Keys.MASTER_KEY_IV] = blob.ivB64
        }
    }

    suspend fun clearMasterKey() {
        context.dataStore.edit { prefs ->
            prefs.remove(Keys.MASTER_KEY_CT)
            prefs.remove(Keys.MASTER_KEY_IV)
        }
    }

    companion object {
        const val DEFAULT_SERVER_URL: String = "http://10.0.2.2:8000"
    }
}
