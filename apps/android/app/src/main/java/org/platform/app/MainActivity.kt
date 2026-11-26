package org.platform.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import org.platform.app.live.LivePlayerScreen
import org.platform.app.live.LiveScreen
import org.platform.app.media.LibraryScreen
import org.platform.app.media.MediaHomeScreen
import org.platform.app.media.OfflinePlayer
import org.platform.app.media.OfflineScreen
import org.platform.app.media.PlayerScreen
import org.platform.app.media.ShowScreen
import org.platform.app.sync.SyncScreen
import org.platform.app.ui.FilesScreen
import org.platform.app.ui.LoginScreen
import org.platform.app.ui.ServerScreen

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val app = application as PlatformApp
        setContent {
            PlatformTheme {
                var stage by remember {
                    mutableStateOf(
                        when {
                            app.session.server == null -> "server"
                            !app.session.loggedIn -> "login"
                            else -> "main"
                        },
                    )
                }
                when (stage) {
                    "server" -> ServerScreen(app) { stage = "login" }
                    "login" -> LoginScreen(app, onOtherServer = { stage = "server" }) { stage = "main" }
                    else -> Main(app) { stage = "login" }
                }
            }
        }
    }
}

@Composable
fun PlatformTheme(content: @Composable () -> Unit) {
    val blue = Color(0xFF2F6FDE)
    val scheme = if (isSystemInDarkTheme()) darkColorScheme(primary = blue) else lightColorScheme(primary = blue)
    MaterialTheme(colorScheme = scheme, content = content)
}

private val TABS = listOf("media", "files", "sync")

@Composable
private fun Main(app: PlatformApp, onLoggedOut: () -> Unit) {
    val nav = rememberNavController()
    val entry by nav.currentBackStackEntryAsState()
    val route = entry?.destination?.route ?: "media"
    Scaffold(
        bottomBar = {
            if (!route.startsWith("play") && !route.startsWith("offline/") && !route.startsWith("live/")) {
                NavigationBar {
                    TABS.forEach { tab ->
                        NavigationBarItem(
                            selected = route.startsWith(tab),
                            onClick = { go(nav, tab) },
                            icon = {
                                Icon(
                                    when (tab) {
                                        "media" -> Icons.Filled.Movie
                                        "files" -> Icons.Filled.Folder
                                        else -> Icons.Filled.Sync
                                    },
                                    contentDescription = null,
                                )
                            },
                            label = {
                                Text(
                                    stringResource(
                                        when (tab) {
                                            "media" -> R.string.media
                                            "files" -> R.string.files
                                            else -> R.string.sync
                                        },
                                    ),
                                )
                            },
                        )
                    }
                }
            }
        },
    ) { padding ->
        NavHost(nav, startDestination = "media", modifier = Modifier.padding(padding)) {
            composable("media") {
                MediaHomeScreen(
                    app,
                    open = { nav.navigate(it) },
                    onLoggedOut = onLoggedOut,
                )
            }
            composable("media/library/{id}") { e ->
                LibraryScreen(app, e.arguments?.getString("id").orEmpty(), open = { nav.navigate(it) })
            }
            composable("media/show/{id}") { e ->
                ShowScreen(app, e.arguments?.getString("id").orEmpty(), open = { nav.navigate(it) })
            }
            composable("media/offline") { OfflineScreen(open = { nav.navigate(it) }) }
            composable("offline/{name}") { e ->
                OfflinePlayer(android.net.Uri.decode(e.arguments?.getString("name").orEmpty()))
            }
            composable("play/{node}") { e ->
                PlayerScreen(app, e.arguments?.getString("node").orEmpty(), onDone = { nav.popBackStack() })
            }
            composable("live") { LiveScreen(app, open = { nav.navigate(it) }) }
            composable("live/{id}") { e ->
                LivePlayerScreen(app, e.arguments?.getString("id").orEmpty(), onDone = { nav.popBackStack() })
            }
            composable("files") { FilesScreen(app, null, open = { nav.navigate(it) }) }
            composable("files/{id}") { e ->
                FilesScreen(app, e.arguments?.getString("id"), open = { nav.navigate(it) })
            }
            composable("sync") { SyncScreen(app) }
        }
    }
}

private fun go(nav: NavHostController, tab: String) {
    nav.navigate(tab) {
        popUpTo(nav.graph.startDestinationId) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }
}
