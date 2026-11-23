@file:Suppress("PropertyName")

package org.platform.app.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// De vormen van de API van het platform (api/app/routers en api/app/media/types.py). Alleen de
// velden die de app gebruikt; onbekende velden slaat de JSON-lezer over.

@Serializable
data class Info(val name: String = "", val version: String = "", val mode: String = "")

@Serializable
data class UserOut(val id: String, val email: String = "", val display_name: String = "", val role: String = "user")

@Serializable
data class Tokens(
    val access_token: String,
    val refresh_token: String,
    val expires_in: Int = 900,
    val user: UserOut,
)

@Serializable
data class LoginIn(val email: String, val password: String, val code: String? = null, val device: String = "")

@Serializable
data class RefreshIn(val refresh_token: String)


// --- media ---

@Serializable
data class Library(
    val id: String,
    val name: String,
    val kind: String,
    val root_id: String,
    val item_count: Int = 0,
)

@Serializable
data class CardProgress(val position_ms: Long, val duration_ms: Long)

@Serializable
data class Card(
    val node_id: String? = null,
    val library_id: String? = null,
    val title: String,
    val year: Int? = null,
    val kind: String,
    val poster: String? = null,
    val progress: CardProgress? = null,
    val watched: Boolean = false,
    val show_id: String? = null,
    val season: Int? = null,
    val episode: Int? = null,
)

@Serializable
data class LibraryRow(val library_id: String, val name: String, val kind: String, val cards: List<Card> = emptyList())

@Serializable
data class MediaHome(
    val libraries: List<Library> = emptyList(),
    @SerialName("continue") val continue_: List<Card> = emptyList(),
    val next_up: List<Card> = emptyList(),
    val recent: List<Card> = emptyList(),
    val released: List<Card> = emptyList(),
    val library_rows: List<LibraryRow> = emptyList(),
)

@Serializable
data class CardPage(val items: List<Card> = emptyList(), val next: String? = null)

@Serializable
data class Show(
    val id: String,
    val title: String,
    val year: Int? = null,
    val overview: String? = null,
    val poster: String? = null,
    val backdrop: String? = null,
)

@Serializable
data class ShowSeason(val season: Int? = null, val episodes: List<Card> = emptyList())

@Serializable
data class ShowDetail(val show: Show, val seasons: List<ShowSeason> = emptyList())

@Serializable
data class ProfileClient(val kind: String = "android", val name: String = "", val app: String = "platform-android")

@Serializable
data class ClientProfile(val v: Int = 1, val base: String = "android-exo", val client: ProfileClient = ProfileClient())

@Serializable
data class PlayIn(
    val profile: ClientProfile = ClientProfile(),
    val audio: Int? = null,
    val subtitle: String? = null,
    val start_ms: Long = 0,
)

@Serializable
data class PlayUrls(val file: String? = null, val map: String? = null, val master: String? = null, val audio: String? = null)

@Serializable
data class SubtitleTrack(
    val id: String,
    val lang: String? = null,
    val label: String = "",
    val forced: Boolean = false,
    val default: Boolean = false,
    val state: String = "ready",
)

@Serializable
data class PlayTracks(val subtitles: List<SubtitleTrack> = emptyList())

@Serializable
data class PlayOut(
    val sid: String,
    val mode: String,
    val urls: PlayUrls,
    val mime: String = "",
    val duration_ms: Long = 0,
    val start_ms: Long = 0,
    val intro_ms: List<Long>? = null,
    val credits_ms: Long? = null,
    val tracks: PlayTracks = PlayTracks(),
    val started_at: String,
)

@Serializable
data class ProgressIn(
    val position_ms: Long,
    val duration_ms: Long,
    val event: String,
    val origin: String,
    val seq: Long,
    val started_at: String,
)

// --- bestanden ---

@Serializable
data class FileNode(
    val id: String,
    val name: String,
    val kind: String, // file | dir
    val size: Long = 0,
    val etag: String = "",
    val mime: String? = null,
    val modified_at: String? = null,
    val parent_id: String? = null,
    val permission: String = "owner",
)

@Serializable
data class Listing(
    val folder: FileNode,
    val path: List<FileNode> = emptyList(),
    val children: List<FileNode> = emptyList(),
    val next: String? = null,
    val total: Int = 0,
)

@Serializable
data class DownloadLink(val url: String)

@Serializable
data class NewFolder(val name: String)

// --- profielen (fase 11, stap 7) ---

@Serializable
data class MediaProfile(val id: String, val name: String, val max_age: Int? = null, val has_pin: Boolean = false)

@Serializable
data class MediaProfiles(val current: String, val max_age: Int? = null, val can_manage: Boolean = false, val profiles: List<MediaProfile> = emptyList())

@Serializable
data class SelectIn(val pin: String? = null)

@Serializable
data class SelectOut(val token: String)
