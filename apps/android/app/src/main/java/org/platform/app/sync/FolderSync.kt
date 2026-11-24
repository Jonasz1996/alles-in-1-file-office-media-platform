package org.platform.app.sync

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import okio.buffer
import okio.sink
import org.platform.app.PlatformApp
import org.platform.app.api.FileNode
import org.platform.app.ui.upload
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Een map van het toestel in twee richtingen gelijk houden met een map op het platform, zoals de
 * Nextcloud-app. Per bestand onthoudt de app de stand na de vorige sync (grootte en tijd hier,
 * etag op het platform); daarmee ziet ze wat aan welke kant veranderde:
 *
 * - alleen hier veranderd of nieuw: uploaden (vervangen; het platform houdt de oude versie);
 * - alleen op het platform veranderd of nieuw: downloaden;
 * - aan beide kanten veranderd (of beide nieuw en verschillend): conflict. Het bestand van hier
 *   gaat als "naam (conflict <datum> Android).ext" naar het platform, en dat van het platform
 *   komt hier onder de gewone naam. Er gaat dus niets verloren;
 * - hier weg (en op het platform onveranderd): naar de prullenbak van het platform;
 * - op het platform weg (en hier onveranderd): hier ook weg. Hier veranderd: opnieuw uploaden.
 */
object FolderSync {
    @Serializable
    data class Seen(val size: Long, val mtime: Long, val etag: String)

    private val stateSerializer = MapSerializer(String.serializer(), Seen.serializer())

    class Counts {
        var up = 0
        var down = 0
        var conflicts = 0
        var removed = 0
    }

    suspend fun run(app: PlatformApp, context: Context, prefs: SyncPrefs, progress: suspend (String) -> Unit): Counts? {
        val tree = prefs.folderUri ?: return null
        val local = DocumentFile.fromTreeUri(context, Uri.parse(tree)) ?: return null
        val remoteRoot = prefs.folderRemote ?: Remote.folder(
            app,
            Remote.root(app).folder.id,
            local.name ?: "Telefoon",
        ).id.also { prefs.folderRemote = it }
        val old: Map<String, Seen> = prefs.folderState?.let {
            runCatching { app.api.json.decodeFromString(stateSerializer, it) }.getOrNull()
        } ?: emptyMap()
        val next = mutableMapOf<String, Seen>()
        val counts = Counts()
        walk(app, context, local, remoteRoot, "", old, next, counts, progress)
        prefs.folderState = app.api.json.encodeToString(stateSerializer, next)
        return counts
    }

    private suspend fun walk(
        app: PlatformApp,
        context: Context,
        dir: DocumentFile,
        remoteId: String,
        prefix: String,
        old: Map<String, Seen>,
        next: MutableMap<String, Seen>,
        counts: Counts,
        progress: suspend (String) -> Unit,
    ) {
        val locals = withContext(Dispatchers.IO) { dir.listFiles() }.filter { it.name != null }.associateBy { it.name!! }
        val remotes = Remote.children(app, remoteId).associateBy { it.name }
        for (name in (locals.keys + remotes.keys).sorted()) {
            val path = prefix + name
            val l = locals[name]
            val r = remotes[name]
            // Mappen: aan beide kanten zorgen dat ze er zijn, dan erin
            if (l?.isDirectory == true || r?.kind == "dir") {
                if (l != null && !l.isDirectory || r != null && r.kind != "dir") continue // een map en een bestand met dezelfde naam: overslaan
                val ld = l ?: dir.createDirectory(name) ?: continue
                val rd = r ?: Remote.folder(app, remoteId, name)
                walk(app, context, ld, rd.id, "$path/", old, next, counts, progress)
                continue
            }
            progress(path)
            val seen = old[path]
            when {
                l != null && r == null -> {
                    if (seen != null && same(l, seen)) {
                        // Op het platform weggegooid, hier onveranderd: hier ook weg
                        l.delete()
                        counts.removed++
                    } else {
                        val up = upload(app, context, remoteId, l.uri, conflict = "replace", name = name, mtime = l.lastModified())
                        next[path] = Seen(l.length(), l.lastModified(), up.etag)
                        counts.up++
                    }
                }
                l == null && r != null -> {
                    if (seen != null && seen.etag == r.etag) {
                        // Hier weggegooid, op het platform onveranderd: naar de prullenbak daar
                        Remote.trash(app, r.id)
                        counts.removed++
                    } else {
                        val made = dir.createFile(r.mime ?: "application/octet-stream", name) ?: continue
                        download(app, context, r, made)
                        next[path] = Seen(made.length(), made.lastModified(), r.etag)
                        counts.down++
                    }
                }
                l != null && r != null -> {
                    val hereChanged = seen == null || !same(l, seen)
                    val thereChanged = seen == null || seen.etag != r.etag
                    when {
                        seen == null && l.length() == r.size -> next[path] = Seen(l.length(), l.lastModified(), r.etag)
                        hereChanged && thereChanged -> {
                            upload(app, context, remoteId, l.uri, conflict = "rename", name = conflictName(name), mtime = l.lastModified())
                            download(app, context, r, l)
                            next[path] = Seen(l.length(), l.lastModified(), r.etag)
                            counts.conflicts++
                        }
                        hereChanged -> {
                            val up = upload(app, context, remoteId, l.uri, conflict = "replace", name = name, mtime = l.lastModified())
                            next[path] = Seen(l.length(), l.lastModified(), up.etag)
                            counts.up++
                        }
                        thereChanged -> {
                            download(app, context, r, l)
                            next[path] = Seen(l.length(), l.lastModified(), r.etag)
                            counts.down++
                        }
                        else -> next[path] = seen!!
                    }
                }
            }
        }
    }

    private fun same(l: DocumentFile, seen: Seen) = l.length() == seen.size && l.lastModified() == seen.mtime

    private fun conflictName(name: String): String {
        val stamp = SimpleDateFormat("yyyy-MM-dd HHmm", Locale.ROOT).format(Date())
        val dot = name.lastIndexOf('.')
        return if (dot > 0) "${name.substring(0, dot)} (conflict $stamp Android)${name.substring(dot)}"
        else "$name (conflict $stamp Android)"
    }

    /** De inhoud van het platform in dit bestand van het toestel (overschrijven). */
    private suspend fun download(app: PlatformApp, context: Context, r: FileNode, target: DocumentFile) {
        app.api.call("GET", "/api/v1/files/${r.id}/content").use { res ->
            withContext(Dispatchers.IO) {
                context.contentResolver.openOutputStream(target.uri, "wt")!!.sink().buffer().use { out ->
                    out.writeAll(res.body!!.source())
                }
            }
        }
    }
}
