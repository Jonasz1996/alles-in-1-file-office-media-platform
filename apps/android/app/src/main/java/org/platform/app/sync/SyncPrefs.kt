package org.platform.app.sync

import android.content.Context
import android.content.SharedPreferences

/** De instellingen en de stand van het synchroniseren (geen geheimen: gewone opslag). */
class SyncPrefs(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("platform.sync", Context.MODE_PRIVATE)

    var camera: Boolean
        get() = prefs.getBoolean("camera", false)
        set(v) = prefs.edit().putBoolean("camera", v).apply()

    /** De map op het platform voor de camera (id), of null: maakt de worker zelf ("Camera-uploads"). */
    var cameraFolder: String?
        get() = prefs.getString("camera_folder", null)
        set(v) = prefs.edit().putString("camera_folder", v).apply()

    /** Tot waar de camera al geüpload is: DATE_ADDED (s) en de id bij gelijke tijd. */
    var cameraSince: Long
        get() = prefs.getLong("camera_since", -1)
        set(v) = prefs.edit().putLong("camera_since", v).apply()

    var cameraLastId: Long
        get() = prefs.getLong("camera_last_id", 0)
        set(v) = prefs.edit().putLong("camera_last_id", v).apply()

    /** De map van het toestel (SAF-boom) die met het platform meeloopt. */
    var folderUri: String?
        get() = prefs.getString("folder_uri", null)
        set(v) = prefs.edit().putString("folder_uri", v).apply()

    var folderRemote: String?
        get() = prefs.getString("folder_remote", null)
        set(v) = prefs.edit().putString("folder_remote", v).apply()

    /** De stand na de laatste sync (JSON, zie FolderSync.State). */
    var folderState: String?
        get() = prefs.getString("folder_state", null)
        set(v) = prefs.edit().putString("folder_state", v).apply()

    var wifiOnly: Boolean
        get() = prefs.getBoolean("wifi_only", true)
        set(v) = prefs.edit().putBoolean("wifi_only", v).apply()

    var lastRun: Long
        get() = prefs.getLong("last_run", 0)
        set(v) = prefs.edit().putLong("last_run", v).apply()

    var lastError: String?
        get() = prefs.getString("last_error", null)
        set(v) = prefs.edit().putString("last_error", v).apply()
}
