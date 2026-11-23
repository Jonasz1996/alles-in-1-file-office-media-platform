package org.platform.app.api

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.util.UUID

/**
 * Het adres van het platform en de tokens, versleuteld met een sleutel uit de Android Keystore.
 * Het refresh-token werkt één keer (de server geeft bij elke refresh een nieuw paar).
 */
class Session(context: Context) {
    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        context,
        "platform.session",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    var server: String?
        get() = prefs.getString("server", null)
        set(value) = prefs.edit().putString("server", value).apply()

    var accessToken: String?
        get() = prefs.getString("access", null)
        private set(value) = prefs.edit().putString("access", value).apply()

    var refreshToken: String?
        get() = prefs.getString("refresh", null)
        private set(value) = prefs.edit().putString("refresh", value).apply()

    var userName: String?
        get() = prefs.getString("user", null)
        private set(value) = prefs.edit().putString("user", value).apply()

    /** Het token van het gekozen mediaprofiel (X-Media-Profile), of null: het account zelf. */
    var mediaProfile: String?
        get() = prefs.getString("media_profile", null)
        set(value) = prefs.edit().putString("media_profile", value).apply()

    /** Een vaste id van dit toestel: de origin van de kijkvoortgang. */
    val deviceId: String
        get() = prefs.getString("device", null) ?: UUID.randomUUID().toString().also {
            prefs.edit().putString("device", it).apply()
        }

    val loggedIn: Boolean get() = server != null && refreshToken != null

    fun store(tokens: Tokens) {
        prefs.edit()
            .putString("access", tokens.access_token)
            .putString("refresh", tokens.refresh_token)
            .putString("user", tokens.user.display_name)
            .apply()
    }

    fun clear() {
        prefs.edit().remove("access").remove("refresh").remove("user").remove("media_profile").apply()
    }
}
