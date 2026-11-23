package org.platform.app.media

import android.app.Activity
import android.content.pm.ActivityInfo
import android.view.WindowManager
import androidx.annotation.OptIn
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.runBlocking
import org.platform.app.PlatformApp
import org.platform.app.R
import org.platform.app.api.PlayIn
import org.platform.app.api.PlayOut
import org.platform.app.api.ProgressIn
import org.platform.app.ui.errorText

/**
 * Afspelen met Media3/ExoPlayer. POST /api/v1/media/{node}/play geeft de sessie: direct (het
 * bestand) of HLS (remux of transcode, master.m3u8). Het profiel "android-exo" staat op de
 * server; de stream-adressen hebben de sleutel in het pad en hebben geen login nodig.
 * De voortgang gaat elke 10 s naar het platform, en bij pauze, stoppen en het einde.
 */
@OptIn(UnstableApi::class)
@Composable
fun PlayerScreen(app: PlatformApp, node: String, onDone: () -> Unit) {
    val context = LocalContext.current
    var play by remember(node) { mutableStateOf<PlayOut?>(null) }
    var error by remember(node) { mutableStateOf<Throwable?>(null) }
    var showSkip by remember { mutableStateOf(false) }
    val player = remember { ExoPlayer.Builder(context).build() }
    val seq = remember { java.util.concurrent.atomic.AtomicLong(0) }

    fun report(event: String) {
        val p = play ?: return
        val pos = player.currentPosition.coerceAtLeast(0)
        val dur = if (player.duration > 0) player.duration else p.duration_ms
        val body = ProgressIn(pos, dur, event, app.session.deviceId, seq.getAndIncrement(), p.started_at)
        // Bij stoppen kan het scherm al weg zijn: dan in een eigen draad, kort
        Thread {
            runCatching {
                runBlocking { app.api.send("PUT", "/api/v1/media/progress/$node", ProgressIn.serializer(), body, null) }
            }
        }.start()
    }

    LaunchedEffect(node) {
        try {
            val out = app.api.send("POST", "/api/v1/media/$node/play", PlayIn.serializer(), PlayIn(), PlayOut.serializer())!!
            play = out
            val url = out.urls.master ?: out.urls.file ?: out.urls.audio ?: error("Geen adres om af te spelen")
            val item = MediaItem.Builder().setUri(app.api.url(url)).apply {
                if (out.urls.master != null) setMimeType(MimeTypes.APPLICATION_M3U8)
            }.build()
            player.setMediaItem(item, out.start_ms)
            player.prepare()
            player.playWhenReady = true
        } catch (e: Exception) {
            error = e
        }
    }

    // Elke 10 s de voortgang; en de knop "Intro overslaan" tijdens de intro
    LaunchedEffect(play) {
        val p = play ?: return@LaunchedEffect
        var last = 0L
        while (isActive) {
            delay(1000)
            val intro = p.intro_ms
            showSkip = intro != null && intro.size == 2 && player.currentPosition in intro[0]..(intro[1] - 1000)
            if (player.isPlaying && System.currentTimeMillis() - last >= 10_000) {
                last = System.currentTimeMillis()
                report("tick")
            }
        }
    }

    DisposableEffect(player) {
        val activity = context as? Activity
        activity?.window?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (!isPlaying && player.playbackState == Player.STATE_READY) report("pause")
            }

            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_ENDED) {
                    report("end")
                    onDone()
                }
            }
        }
        player.addListener(listener)
        onDispose {
            if (player.playbackState != Player.STATE_ENDED) report("stop")
            player.removeListener(listener)
            player.release()
            activity?.window?.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        }
    }

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        val err = error
        when {
            err != null -> Text(errorText(err), color = Color.White, modifier = Modifier.align(Alignment.Center).padding(24.dp))
            play == null -> CircularProgressIndicator(Modifier.align(Alignment.Center))
            else -> AndroidView(
                factory = { ctx -> PlayerView(ctx).apply { this.player = player } },
                modifier = Modifier.fillMaxSize(),
            )
        }
        if (showSkip) {
            Button(
                onClick = { play?.intro_ms?.getOrNull(1)?.let { player.seekTo(it) } },
                modifier = Modifier.align(Alignment.BottomEnd).padding(32.dp),
            ) { Text(stringResource(R.string.player_skip_intro), style = MaterialTheme.typography.labelLarge) }
        }
    }
}
