package org.platform.app.sync

import android.content.ContentUris
import android.content.Context
import android.provider.MediaStore
import org.platform.app.PlatformApp
import org.platform.app.api.ApiException
import org.platform.app.ui.upload

/**
 * Camera-upload: nieuwe foto's en video's van het toestel naar de map "Camera-uploads" op het
 * platform (of een gekozen map). De eerste keer alleen wat vanaf dan bijkomt; daarna op volgorde
 * van DATE_ADDED, en na elk bestand onthoudt de app tot waar ze is (hervatten na een onderbreking).
 * Zelfde naam op het platform: de server hernoemt ("foto (2).jpg").
 */
object CameraSync {
    private data class Shot(val id: Long, val added: Long, val taken: Long, val video: Boolean)

    suspend fun run(app: PlatformApp, context: Context, prefs: SyncPrefs, progress: (String) -> Unit): Int {
        if (!prefs.camera) return 0
        if (prefs.cameraSince < 0) {
            // Eerste keer: vanaf nu (geen hele fotorol in één keer)
            prefs.cameraSince = System.currentTimeMillis() / 1000
            prefs.cameraLastId = 0
            return 0
        }
        val folder = prefs.cameraFolder ?: Remote.folder(app, Remote.root(app).folder.id, "Camera-uploads").id.also {
            prefs.cameraFolder = it
        }
        val shots = query(context, prefs.cameraSince, prefs.cameraLastId)
        var done = 0
        for (s in shots) {
            val base = if (s.video) MediaStore.Video.Media.EXTERNAL_CONTENT_URI else MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            val uri = ContentUris.withAppendedId(base, s.id)
            progress("${done + 1}/${shots.size}")
            try {
                upload(app, context, folder, uri, conflict = "rename", mtime = s.taken.takeIf { it > 0 })
            } catch (e: ApiException) {
                if (e.status == 404) {
                    // De map is weg op het platform: de volgende keer een nieuwe
                    prefs.cameraFolder = null
                }
                throw e
            } catch (e: java.io.FileNotFoundException) {
                // Intussen weg op het toestel: overslaan
            }
            prefs.cameraSince = s.added
            prefs.cameraLastId = s.id
            done++
        }
        return done
    }

    private fun query(context: Context, since: Long, lastId: Long): List<Shot> {
        val out = mutableListOf<Shot>()
        for (video in listOf(false, true)) {
            val uri = if (video) MediaStore.Video.Media.EXTERNAL_CONTENT_URI else MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            val cols = arrayOf(MediaStore.MediaColumns._ID, MediaStore.MediaColumns.DATE_ADDED, MediaStore.MediaColumns.DATE_TAKEN)
            val where = "(${MediaStore.MediaColumns.DATE_ADDED} > ? OR (${MediaStore.MediaColumns.DATE_ADDED} = ? AND ${MediaStore.MediaColumns._ID} > ?))"
            val args = arrayOf(since.toString(), since.toString(), lastId.toString())
            context.contentResolver.query(uri, cols, where, args, "${MediaStore.MediaColumns.DATE_ADDED} ASC")?.use { c ->
                while (c.moveToNext()) {
                    out += Shot(c.getLong(0), c.getLong(1), if (c.isNull(2)) 0 else c.getLong(2), video)
                }
            }
        }
        return out.sortedWith(compareBy({ it.added }, { it.id }))
    }
}
