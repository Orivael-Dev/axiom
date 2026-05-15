package dev.orivael.axiom

import android.app.Application

/**
 * Application singleton.
 *
 * Kept minimal in this slice — the [SettingsStore] is created lazily by
 * each composable that needs it, and the [network.AxiomClient] is
 * constructed per-call from the current settings snapshot. Future slices
 * will add a single ViewModel-scoped client + Android Keystore for the
 * AXIOM_MASTER_KEY.
 */
class AxiomApp : Application()
