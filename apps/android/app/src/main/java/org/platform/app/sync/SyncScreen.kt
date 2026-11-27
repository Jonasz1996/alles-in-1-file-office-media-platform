package org.platform.app.sync

import android.Manifest
import android.content.Intent
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import org.platform.app.PlatformApp
import org.platform.app.R
import java.text.DateFormat
import java.util.Date

/** Synchroniseren: camera-upload aan of uit, een map van het toestel kiezen, alleen via wifi. */
@Composable
fun SyncScreen(app: PlatformApp) {
    val context = LocalContext.current
    val prefs = remember { SyncPrefs(context) }
    var camera by remember { mutableStateOf(prefs.camera) }
    var folder by remember { mutableStateOf(prefs.folderUri) }
    var wifi by remember { mutableStateOf(prefs.wifiOnly) }

    val media = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        if (granted.values.any { it }) {
            prefs.camera = true
            camera = true
            SyncWorker.schedule(context)
        }
    }
    val tree = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            context.contentResolver.takePersistableUriPermission(
                uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
            )
            prefs.folderUri = uri.toString()
            prefs.folderRemote = null
            prefs.folderState = null
            folder = uri.toString()
            SyncWorker.schedule(context)
            SyncWorker.now(context)
        }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(stringResource(R.string.sync_title), style = MaterialTheme.typography.headlineSmall)
        Setting(stringResource(R.string.sync_camera), camera) { on ->
            if (on) {
                val perms = if (Build.VERSION.SDK_INT >= 33) {
                    arrayOf(Manifest.permission.READ_MEDIA_IMAGES, Manifest.permission.READ_MEDIA_VIDEO, Manifest.permission.POST_NOTIFICATIONS)
                } else {
                    arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
                }
                media.launch(perms)
            } else {
                prefs.camera = false
                camera = false
                SyncWorker.schedule(context)
            }
        }
        Text(stringResource(R.string.sync_folder), style = MaterialTheme.typography.titleMedium)
        Text(folder?.let { android.net.Uri.parse(it).lastPathSegment } ?: stringResource(R.string.sync_folder_none))
        OutlinedButton(onClick = { tree.launch(null) }) { Text(stringResource(R.string.sync_folder_pick)) }
        Setting(stringResource(R.string.sync_wifi), wifi) { on ->
            prefs.wifiOnly = on
            wifi = on
            SyncWorker.schedule(context)
        }
        Button(onClick = { SyncWorker.now(context) }) { Text(stringResource(R.string.sync_now)) }
        Text(
            if (prefs.lastRun > 0) stringResource(R.string.sync_last, DateFormat.getDateTimeInstance().format(Date(prefs.lastRun)))
            else stringResource(R.string.sync_never),
            style = MaterialTheme.typography.bodySmall,
        )
        prefs.lastError?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
    }
}

@Composable
private fun Setting(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange)
    }
}
