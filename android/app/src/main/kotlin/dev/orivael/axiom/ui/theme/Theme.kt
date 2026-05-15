package dev.orivael.axiom.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val AxiomTeal      = Color(0xFF2C8A90)
private val AxiomTealDark  = Color(0xFF0D4F54)
private val AxiomTealLight = Color(0xFFE6F4F5)
private val AxiomNavy      = Color(0xFF1D3557)
private val AxiomAlert     = Color(0xFFC03A2B)   // SovereignAlert accents
private val AxiomOk        = Color(0xFF2E7D32)

private val LightScheme = lightColorScheme(
    primary       = AxiomTeal,
    onPrimary     = Color.White,
    primaryContainer = AxiomTealLight,
    onPrimaryContainer = AxiomTealDark,
    secondary     = AxiomNavy,
    error         = AxiomAlert,
    tertiary      = AxiomOk,
)

private val DarkScheme = darkColorScheme(
    primary       = AxiomTealLight,
    onPrimary     = AxiomTealDark,
    primaryContainer = AxiomTealDark,
    onPrimaryContainer = AxiomTealLight,
    secondary     = Color(0xFFAAB7C4),
    error         = Color(0xFFFF8A80),
    tertiary      = Color(0xFFA5D6A7),
)

@Composable
fun AxiomTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val scheme = if (darkTheme) DarkScheme else LightScheme
    MaterialTheme(colorScheme = scheme, content = content)
}
