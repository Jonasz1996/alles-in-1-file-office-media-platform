package org.platform.app.sync

import org.platform.app.PlatformApp
import org.platform.app.api.ApiException
import org.platform.app.api.FileNode
import org.platform.app.api.Listing
import org.platform.app.api.NewFolder

/** Kleine helpers bovenop de API van de bestanden, voor het synchroniseren. */
object Remote {
    suspend fun root(app: PlatformApp): Listing = app.api.get("/api/v1/files/root", Listing.serializer())

    /** Alle kinderen van een map (zonder limit geeft de server alles in één keer). */
    suspend fun children(app: PlatformApp, id: String): List<FileNode> =
        app.api.get("/api/v1/files/$id/children", Listing.serializer()).children

    /** De map `name` in `parent`: bestaat hij al, dan die. */
    suspend fun folder(app: PlatformApp, parent: String, name: String): FileNode {
        children(app, parent).firstOrNull { it.kind == "dir" && it.name == name }?.let { return it }
        return try {
            app.api.send("POST", "/api/v1/files/$parent/folders", NewFolder.serializer(), NewFolder(name), FileNode.serializer())!!
        } catch (e: ApiException) {
            if (e.code != "name_taken") throw e
            children(app, parent).first { it.kind == "dir" && it.name == name }
        }
    }

    /** Naar de prullenbak van het platform (terug te zetten). */
    suspend fun trash(app: PlatformApp, id: String) {
        app.api.call("DELETE", "/api/v1/files/$id").close()
    }
}
