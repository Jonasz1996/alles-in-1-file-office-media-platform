package org.platform.app.live

import android.app.Activity
import android.content.pm.ActivityInfo
import android.view.WindowManager
import androidx.annotation.OptIn
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import androidx.media3.ui.PlayerView
import coil.compose.AsyncImage
import kotlinx.coroutines.launch
import org.platform.app.PlatformApp
import org.platform.app.R
import org.platform.app.api.Api
import org.platform.app.api.LiveChannel
import org.platform.app.api.LiveChannels
import org.platform.app.ui.Loaded
import org.platform.app.ui.errorText

/**
 * Live tv (IPTV): de kanalen met wat er nu op is, zoeken en alleen favorieten. Een kanaal
 * kiezen opent LivePlayerScreen. De lijst komt van GET /api/v1/iptv/channels.
 */
@Composable
fun LiveScreen(app: PlatformApp, open: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    var query by remember { mutableStateOf("") }
    var onlyFav by remember { mutableStateOf(false) }
    Loaded(Unit, { app.api.get("/api/v1/iptv/channels", LiveChannels.serializer()) }) { data, reload ->
        var channels by remember(data) { mutableStateOf(data.channels) }
        if (channels.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(stringResource(R.string.live_empty), modifier = Modifier.padding(24.dp))
            }
            return@Loaded
        }
        val q = query.trim().lowercase()
        val shown = channels.filter {
            (q.isEmpty() || it.name.lowercase().contains(q) || it.number?.toString() == q) && (!onlyFav || it.favorite)
        }
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = query,
                    onValueChange = { query = it },
                    singleLine = true,
                    label = { Text(stringResource(R.string.live_search)) },
                    modifier = Modifier.weight(1f),
                )
                FilterChip(
                    selected = onlyFav,
                    onClick = { onlyFav = !onlyFav },
                    label = { Text(stringResource(R.string.live_favorites)) },
                    modifier = Modifier.padding(start = 8.dp),
                )
            }
            LazyColumn(Modifier.fillMaxSize()) {
                items(shown, key = { it.id }) { c ->
                    ListItem(
                        modifier = Modifier.clickable { open("live/${c.id}") },
                        leadingContent = {
                            Box(Modifier.width(56.dp).height(36.dp)) {
                                c.logo?.let {
                                    AsyncImage(
                                        model = app.api.url(it),
                                        contentDescription = null,
                                        contentScale = ContentScale.Fit,
                                        modifier = Modifier.fillMaxSize(),
                                    )
                                }
                            }
                        },
                        headlineContent = {
                            Text(listOfNotNull(c.number?.toString(), c.name).joinToString(" "), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        },
                        supportingContent = c.now?.let { now ->
                            { Text(now.title, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                        },
                        trailingContent = {
                            TextButton(onClick = {
                                val on = !c.favorite
                                channels = channels.map { if (it.key == c.key) it.copy(favorite = on) else it }
                                scope.launch {
                                    runCatching {
                                        app.api.call(if (on) "PUT" else "DELETE", "/api/v1/iptv/favorites/${Api.q(c.key)}").close()
                                    }.onFailure { reload() }
                                }
                            }) { Text(if (c.favorite) "★" else "☆", style = MaterialTheme.typography.titleLarge) }
                        },
                    )
                }
            }
        }
    }
}

/**
 * Eén kanaal live. De server geeft fMP4 zonder einde (GET /api/v1/iptv/channels/{id}/live); de
 * stroom heeft het token nodig, dus gaat het mee als kop. Eerst de lijst ophalen: dat ververst
 * zo nodig het token. Valt de stroom weg, dan "Opnieuw".
 */
@OptIn(UnstableApi::class)
@Composable
fun LivePlayerScreen(app: PlatformApp, channelId: String, onDone: () -> Unit) {
    val context = LocalContext.current
    var channel by remember(channelId) { mutableStateOf<LiveChannel?>(null) }
    var error by remember(channelId) { mutableStateOf<Throwable?>(null) }
    var round by remember { mutableStateOf(0) }
    val player = remember { ExoPlayer.Builder(context).build() }

    LaunchedEffect(channelId, round) {
        error = null
        try {
            val list = app.api.get("/api/v1/iptv/channels", LiveChannels.serializer())
            channel = list.channels.firstOrNull { it.id == channelId }
            val token = app.session.accessToken
            val source = DefaultHttpDataSource.Factory()
                .setAllowCrossProtocolRedirects(false)
                .setReadTimeoutMs(30_000)
                .setDefaultRequestProperties(if (token != null) mapOf("Authorization" to "Bearer $token") else emptyMap())
            val item = MediaItem.fromUri(app.api.url("/api/v1/iptv/channels/${Api.q(channelId)}/live"))
            player.setMediaSource(ProgressiveMediaSource.Factory(source).createMediaSource(item))
            player.prepare()
            player.playWhenReady = true
        } catch (e: Exception) {
            error = e
        }
    }

    DisposableEffect(player) {
        val activity = context as? Activity
        activity?.window?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        val listener = object : Player.Listener {
            override fun onPlayerError(e: PlaybackException) {
                error = e
            }
        }
        player.addListener(listener)
        onDispose {
            player.removeListener(listener)
            player.release()
            activity?.window?.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        }
    }

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        AndroidView(
            factory = { ctx -> PlayerView(ctx).apply { this.player = player } },
            modifier = Modifier.fillMaxSize(),
        )
        val err = error
        if (err != null) {
            Column(Modifier.align(Alignment.Center).padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text(errorText(err), color = Color.White)
                Row {
                    Button(onClick = { round++ }, modifier = Modifier.padding(8.dp)) { Text(stringResource(R.string.retry)) }
                    Button(onClick = onDone, modifier = Modifier.padding(8.dp)) { Text(stringResource(R.string.live_back)) }
                }
            }
        } else if (player.playbackState == Player.STATE_IDLE && channel == null) {
            CircularProgressIndicator(Modifier.align(Alignment.Center))
        }
        channel?.let { c ->
            Text(
                listOfNotNull(c.name, c.now?.title).joinToString(" · "),
                color = Color.White,
                modifier = Modifier.align(Alignment.TopStart).fillMaxWidth().padding(16.dp),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}
