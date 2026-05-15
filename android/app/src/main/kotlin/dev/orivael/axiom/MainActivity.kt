package dev.orivael.axiom

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Insights
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import dev.orivael.axiom.ui.screens.GateScreen
import dev.orivael.axiom.ui.screens.SettingsScreen
import dev.orivael.axiom.ui.screens.StatusScreen
import dev.orivael.axiom.ui.theme.AxiomTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            AxiomTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    SovereignPhoneApp()
                }
            }
        }
    }
}

private sealed class Tab(val route: String, val labelRes: Int, val icon: @Composable () -> Unit) {
    object Gate     : Tab("gate",     R.string.tab_gate,     { Icon(Icons.Outlined.Shield,   null) })
    object Status   : Tab("status",   R.string.tab_status,   { Icon(Icons.Outlined.Insights, null) })
    object Settings : Tab("settings", R.string.tab_settings, { Icon(Icons.Outlined.Settings, null) })
}

private val TABS = listOf(Tab.Gate, Tab.Status, Tab.Settings)

@Composable
private fun SovereignPhoneApp() {
    val nav = rememberNavController()
    val backStack by nav.currentBackStackEntryAsState()
    val currentRoute = backStack?.destination?.route

    Scaffold(
        bottomBar = {
            NavigationBar {
                TABS.forEach { tab ->
                    val selected = currentRoute?.let {
                        backStack?.destination?.hierarchy?.any { d -> d.route == tab.route } == true
                    } == true
                    NavigationBarItem(
                        selected = selected,
                        onClick = {
                            nav.navigate(tab.route) {
                                popUpTo(nav.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = tab.icon,
                        label = { Text(stringResource(tab.labelRes)) },
                    )
                }
            }
        }
    ) { padding ->
        NavHost(
            navController = nav,
            startDestination = Tab.Gate.route,
            modifier = Modifier.padding(padding),
        ) {
            composable(Tab.Gate.route)     { GateScreen() }
            composable(Tab.Status.route)   { StatusScreen() }
            composable(Tab.Settings.route) { SettingsScreen() }
        }
    }
}

@Composable
private fun stringResource(id: Int): String =
    androidx.compose.ui.res.stringResource(id = id)
