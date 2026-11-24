package org.platform.app.ui

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.provider.OpenableColumns
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.InsertDriveFile
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.serializer
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.RequestBody
import okio.BufferedSink
import okio.source
import org.platform.app.PlatformApp
import org.platform.app.R
import org.platform.app.api.Api
import org.platform.app.api.DownloadLink
import org.platform.app.api.FileNode
import org.platform.app.api.Listing

/** De bestanden van het platform: mappen openen, een bestand downloaden, iets uploaden. */
@Composable
fun FilesScreen(app: PlatformApp, folderId: String?, open: (String) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val path = if (folderId == null) "/api/v1/files/root" else "/api/v1/files/$folderId/children"
    Loaded(folderId, { app.api.get(path, Listing.serializer()) }) { listing, reload ->
        val uploaded = stringResource(R.string.uploaded)
        val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
            if (uris.isEmpty()) return@rememberLauncherForActivityResult
            scope.launch {
                try {
                    uris.forEach { upload(app, context, listing.folder.id, it) }
                    Toast.makeText(context, uploaded, Toast.LENGTH_SHORT).show()
                    reload()
                } catch (e: Exception) {
                    Toast.makeText(context, e.message ?: e.toString(), Toast.LENGTH_LONG).show()
                }
            }
        }
        Scaffold(
            floatingActionButton = {
                if (listing.folder.permission != "read") {
                    ExtendedFloatingActionButton(
                        onClick = { pick.launch(arrayOf("*/*")) },
                        icon = { Icon(Icons.Filled.Upload, contentDescription = null) },
                        text = { Text(stringResource(R.string.upload)) },
                    )
                }
            },
        ) { padding ->
            LazyColumn(Modifier.fillMaxSize().padding(padding)) {
                item {
                    Text(
                        listing.path.joinToString(" / ") { it.name }.ifEmpty { listing.folder.name },
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.padding(16.dp),
                    )
                }
                if (listing.children.isEmpty()) {
                    item { Text(stringResource(R.string.empty_folder), modifier = Modifier.padding(16.dp)) }
                }
                items(listing.children, key = { it.id }) { n ->
                    ListItem(
                        leadingContent = {
                            Icon(
                                if (n.kind == "dir") Icons.Filled.Folder else Icons.AutoMirrored.Filled.InsertDriveFile,
                                contentDescription = null,
                            )
                        },
                        headlineContent = { Text(n.name) },
                        supportingContent = { if (n.kind != "dir") Text(size(n.size)) },
                        modifier = Modifier.clickable {
                            if (n.kind == "dir") open("files/${n.id}") else scope.launch { download(app, context, n) }
                        },
                    )
                }
            }
        }
    }
}

fun size(bytes: Long): String = when {
    bytes >= 1L shl 30 -> "%.1f GB".format(bytes / (1L shl 30).toDouble())
    bytes >= 1L shl 20 -> "%.1f MB".format(bytes / (1L shl 20).toDouble())
    bytes >= 1L shl 10 -> "%.0f kB".format(bytes / (1L shl 10).toDouble())
    else -> "$bytes B"
}

/** Downloaden met de DownloadManager van Android, naar Downloads (een kortlevende link). */
suspend fun download(app: PlatformApp, context: Context, node: FileNode) {
    try {
        val link = app.api.send(
            "POST", "/api/v1/files/${node.id}/download-link",
            Unit.serializer(), Unit, DownloadLink.serializer(),
        )!!
        val request = DownloadManager.Request(Uri.parse(app.api.url(link.url)))
            .setTitle(node.name)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, node.name)
        (context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
        Toast.makeText(context, context.getString(R.string.downloaded), Toast.LENGTH_SHORT).show()
    } catch (e: Exception) {
        Toast.makeText(context, e.message ?: e.toString(), Toast.LENGTH_LONG).show()
    }
}

/** Een bestand (content://) uploaden in map `folderId`; dezelfde naam: de server hernoemt. */
suspend fun upload(
    app: PlatformApp,
    context: Context,
    folderId: String,
    uri: Uri,
    conflict: String = "rename",
    name: String? = null,
    mtime: Long? = null,
): FileNode = withContext(Dispatchers.IO) {
    val resolver = context.contentResolver
    var display = name
    var length = -1L
    resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
        if (c.moveToFirst()) {
            if (display == null) display = c.getString(0)
            if (!c.isNull(1)) length = c.getLong(1)
        }
    }
    val type = resolver.getType(uri)?.toMediaTypeOrNull()
    val body = object : RequestBody() {
        override fun contentType() = type
        override fun contentLength() = length
        override fun writeTo(sink: BufferedSink) {
            resolver.openInputStream(uri)!!.source().use { sink.writeAll(it) }
        }
    }
    val q = buildString {
        append("?name=").append(Api.q(display ?: "upload"))
        append("&conflict=").append(conflict)
        if (mtime != null) append("&mtime=").append(mtime)
    }
    app.api.call("PUT", "/api/v1/files/$folderId/upload$q", body).use {
        app.api.json.decodeFromString(FileNode.serializer(), it.body!!.string())
    }
}
