package org.platform.app.media

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AssistChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import kotlinx.coroutines.launch
import org.platform.app.PlatformApp
import org.platform.app.R
import org.platform.app.api.Api
import org.platform.app.api.Card
import org.platform.app.api.CardPage
import org.platform.app.api.MediaHome
import org.platform.app.api.ShowDetail
import org.platform.app.ui.Loaded

/** Waar een kaart heen gaat: een serie naar haar pagina, de rest meteen afspelen. */
fun target(card: Card): String? = when {
    card.kind == "show" && card.show_id != null -> "media/show/${card.show_id}"
    card.node_id != null -> "play/${card.node_id}"
    else -> null
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun Poster(app: PlatformApp, card: Card, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    // Lang drukken: downloaden om offline te kijken (films, afleveringen, video's)
    val offline = card.node_id != null && card.kind in setOf("movie", "episode", "video", "extra")
    Column(
        modifier.combinedClickable(
            onClick = onClick,
            onLongClick = if (offline) ({ scope.launch { Offline.download(app, context, card) }; Unit }) else null,
        ),
    ) {
        Box(Modifier.fillMaxWidth().aspectRatio(2f / 3f).clip(RoundedCornerShape(8.dp))) {
            Surface(color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.fillMaxSize()) {}
            card.poster?.let {
                AsyncImage(
                    model = app.api.url(it),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
            card.progress?.let { p ->
                if (p.duration_ms > 0) {
                    LinearProgressIndicator(
                        progress = { (p.position_ms.toFloat() / p.duration_ms).coerceIn(0f, 1f) },
                        modifier = Modifier.fillMaxWidth().height(4.dp).align(androidx.compose.ui.Alignment.BottomCenter),
                    )
                }
            }
        }
        Text(
            card.title,
            style = MaterialTheme.typography.bodyMedium,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 4.dp),
        )
        val sub = when {
            card.season != null && card.episode != null -> "S%02dE%02d".format(card.season, card.episode)
            card.year != null -> card.year.toString()
            else -> null
        }
        sub?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}

@Composable
private fun CardRow(app: PlatformApp, title: String, cards: List<Card>, open: (String) -> Unit) {
    if (cards.isEmpty()) return
    Column(Modifier.padding(vertical = 8.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
        LazyRow(contentPadding = PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            items(cards, key = { "${it.kind}-${it.node_id}-${it.show_id}" }) { c ->
                Poster(app, c, Modifier.width(120.dp)) { target(c)?.let(open) }
            }
        }
    }
}

@Composable
fun MediaHomeScreen(app: PlatformApp, open: (String) -> Unit, onLoggedOut: () -> Unit) {
    val scope = rememberCoroutineScope()
    var round by remember { mutableStateOf(0) }
    val reloadHome = { round++ }
    Loaded(round, { app.api.get("/api/v1/media/home", MediaHome.serializer()) }) { home, _ ->
        LazyColumn(Modifier.fillMaxSize()) {
            item {
                Row(Modifier.padding(16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(stringResource(R.string.media), style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
                    ProfileButton(app) { reloadHome() }
                    TextButton(onClick = { open("media/offline") }) { Text(stringResource(R.string.offline)) }
                    TextButton(onClick = { scope.launch { app.api.logout(); onLoggedOut() } }) {
                        Text(stringResource(R.string.logout))
                    }
                }
            }
            item {
                LazyRow(contentPadding = PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(home.libraries.filter { it.kind != "music" }, key = { it.id }) { l ->
                        AssistChip(onClick = { open("media/library/${l.id}") }, label = { Text(l.name) })
                    }
                }
            }
            item { CardRow(app, stringResource(R.string.row_continue), home.continue_, open) }
            item { CardRow(app, stringResource(R.string.row_next), home.next_up, open) }
            item { CardRow(app, stringResource(R.string.row_recent), home.recent, open) }
            item { CardRow(app, stringResource(R.string.row_released), home.released, open) }
            items(home.library_rows, key = { it.library_id }) { r -> CardRow(app, r.name, r.cards, open) }
        }
    }
}

@Composable
fun LibraryScreen(app: PlatformApp, id: String, open: (String) -> Unit) {
    val cards = remember(id) { mutableStateListOf<Card>() }
    var next by remember(id) { mutableStateOf<String?>("") }
    var loading by remember(id) { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    fun more() {
        val cursor = next ?: return
        if (loading) return
        loading = true
        scope.launch {
            try {
                val q = if (cursor.isEmpty()) "" else "&cursor=${Api.q(cursor)}"
                val page = app.api.get("/api/v1/media/libraries/$id/items?limit=60$q", CardPage.serializer())
                cards.addAll(page.items)
                next = page.next
            } catch (_: Exception) {
                next = null
            } finally {
                loading = false
            }
        }
    }
    LaunchedEffect(id) { more() }
    LazyVerticalGrid(
        columns = GridCells.Adaptive(110.dp),
        contentPadding = PaddingValues(16.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        itemsIndexed(cards) { i, c ->
            if (i >= cards.size - 12) LaunchedEffect(cards.size) { more() }
            Poster(app, c) { target(c)?.let(open) }
        }
    }
}

@Composable
fun ShowScreen(app: PlatformApp, id: String, open: (String) -> Unit) {
    Loaded(id, { app.api.get("/api/v1/media/shows/$id", ShowDetail.serializer()) }) { detail, _ ->
        LazyColumn(Modifier.fillMaxSize()) {
            item {
                Column(Modifier.padding(16.dp)) {
                    Text(detail.show.title, style = MaterialTheme.typography.headlineSmall)
                    detail.show.overview?.let { Text(it, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 8.dp)) }
                }
            }
            detail.seasons.forEach { s ->
                item {
                    Text(
                        s.season?.let { stringResource(R.string.season, it) } ?: stringResource(R.string.season_none),
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                    )
                }
                items(s.episodes, key = { "ep-${it.node_id}" }) { e ->
                    ListItem(
                        headlineContent = { Text(e.title) },
                        supportingContent = {
                            e.episode?.let { n -> Text("E%02d".format(n) + if (e.watched) " ✓" else "") }
                        },
                        modifier = Modifier.clickable { target(e)?.let(open) },
                    )
                }
            }
        }
    }
}
