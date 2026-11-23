package org.platform.app.media

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import kotlinx.serialization.builtins.serializer
import org.platform.app.PlatformApp
import org.platform.app.R
import org.platform.app.api.Card
import org.platform.app.api.DownloadLink
import org.platform.app.ui.size
import java.io.File

/**
 * Offline kijken: een film of aflevering als bestand op dit toestel (de map Movies van de app;
 * weg als de app weg is). Afspelen gaat dan zonder het platform; de voortgang telt dan niet mee.
 */
object Offline {
    fun dir(context: Context): File =
        (context.getExternalFilesDir(Environment.DIRECTORY_MOVIES) ?: File(context.filesDir, "movies")).also { it.mkdirs() }

    fun files(context: Context): List<File> =
        dir(context).listFiles()?.filter { it.isFile && !it.name.endsWith(".part") }?.sortedBy { it.name.lowercase() } ?: emptyList()

    private fun safe(name: String) = name.replace(Regex("""[\\/:*?"<>|]"""), "_").take(120)

    suspend fun download(app: PlatformApp, context: Context, card: Card) {
        val node = card.node_id ?: return
        try {
            val link = app.api.send("POST", "/api/v1/files/$node/download-link", Unit.serializer(), Unit, DownloadLink.serializer())!!
            val title = buildString {
                append(card.title)
                if (card.season != null && card.episode != null) append(" S%02dE%02d".format(card.season, card.episode))
                else if (card.year != null) append(" (${card.year})")
            }
            val ext = Uri.parse(link.url).lastPathSegment?.substringAfterLast('.', "")?.takeIf { it.length in 2..4 } ?: "mkv"
            val target = File(dir(context), "${safe(title)}.$ext")
            val request = DownloadManager.Request(Uri.parse(app.api.url(link.url)))
                .setTitle(title)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationUri(Uri.fromFile(target))
            (context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
            Toast.makeText(context, context.getString(R.string.offline_started), Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Toast.makeText(context, e.message ?: e.toString(), Toast.LENGTH_LONG).show()
        }
    }
}

@Composable
fun OfflineScreen(open: (String) -> Unit) {
    val context = LocalContext.current
    var files by remember { mutableStateOf(Offline.files(context)) }
    if (files.isEmpty()) {
        Text(stringResource(R.string.offline_empty), modifier = Modifier)
        return
    }
    LazyColumn(Modifier.fillMaxSize()) {
        items(files, key = { it.name }) { f ->
            ListItem(
                headlineContent = { Text(f.nameWithoutExtension) },
                supportingContent = { Text(size(f.length())) },
                trailingContent = {
                    IconButton(onClick = { f.delete(); files = Offline.files(context) }) {
                        Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.offline_delete))
                    }
                },
                modifier = Modifier.clickable { open("offline/${Uri.encode(f.name)}") },
            )
        }
    }
}

@Composable
fun OfflinePlayer(name: String) {
    val context = LocalContext.current
    val player = remember { ExoPlayer.Builder(context).build() }
    DisposableEffect(name) {
        val file = File(Offline.dir(context), name)
        player.setMediaItem(MediaItem.fromUri(Uri.fromFile(file)))
        player.prepare()
        player.playWhenReady = true
        onDispose { player.release() }
    }
    Box(Modifier.fillMaxSize().background(Color.Black)) {
        AndroidView(factory = { PlayerView(it).apply { this.player = player } }, modifier = Modifier.fillMaxSize())
    }
}
