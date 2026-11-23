package org.platform.app.media

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Person
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import kotlinx.coroutines.launch
import org.platform.app.PlatformApp
import org.platform.app.R
import org.platform.app.api.ApiException
import org.platform.app.api.MediaProfile
import org.platform.app.api.MediaProfiles
import org.platform.app.api.SelectIn
import org.platform.app.api.SelectOut

/**
 * "Wie kijkt er?": het profiel van dit toestel kiezen (met de pincode als het profiel er een
 * heeft). Het token gaat mee met elk verzoek naar /api/v1/media (X-Media-Profile); de server
 * past het kinderslot en de eigen voortgang van het profiel toe. Profielen beheren gaat op de
 * website.
 */
@Composable
fun ProfileButton(app: PlatformApp, onSwitched: () -> Unit) {
    var data by remember { mutableStateOf<MediaProfiles?>(null) }
    var open by remember { mutableStateOf(false) }
    var tick by remember { mutableStateOf(0) }
    LaunchedEffect(tick) {
        data = runCatching { app.api.get("/api/v1/media/profiles", MediaProfiles.serializer()) }.getOrNull()
    }
    val d = data ?: return
    val current = d.profiles.firstOrNull { it.id == d.current }
    TextButton(onClick = { open = true }) {
        Icon(Icons.Filled.Person, contentDescription = null)
        Text(current?.name ?: stringResource(R.string.profiles_title))
    }
    if (open) {
        ProfilesDialog(app, d, onClose = { open = false }) {
            open = false
            tick++
            onSwitched()
        }
    }
}

@Composable
private fun ProfilesDialog(app: PlatformApp, data: MediaProfiles, onClose: () -> Unit, onSwitched: () -> Unit) {
    val scope = rememberCoroutineScope()
    var pinFor by remember { mutableStateOf<MediaProfile?>(null) }
    var pin by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }

    fun choose(p: MediaProfile, code: String?) {
        scope.launch {
            try {
                val out = app.api.send("POST", "/api/v1/media/profiles/${p.id}/select", SelectIn.serializer(), SelectIn(code), SelectOut.serializer())!!
                app.session.mediaProfile = out.token
                onSwitched()
            } catch (e: ApiException) {
                error = e.message
            } catch (e: Exception) {
                error = e.message ?: e.toString()
            }
        }
    }

    AlertDialog(
        onDismissRequest = onClose,
        title = { Text(stringResource(R.string.profiles_title)) },
        text = {
            Column {
                val who = pinFor
                if (who == null) {
                    LazyColumn {
                        items(data.profiles, key = { it.id }) { p ->
                            ListItem(
                                headlineContent = { Text(p.name + if (p.id == data.current) " ✓" else "") },
                                supportingContent = { if (p.has_pin) Text(stringResource(R.string.profiles_locked)) },
                                modifier = Modifier.clickable {
                                    when {
                                        p.id == data.current -> onClose()
                                        p.has_pin -> { pinFor = p; pin = ""; error = null }
                                        else -> choose(p, null)
                                    }
                                },
                            )
                        }
                    }
                } else {
                    Text(stringResource(R.string.profiles_pin_for, who.name))
                    OutlinedTextField(
                        value = pin,
                        onValueChange = { pin = it.filter(Char::isDigit).take(8) },
                        singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            pinFor?.let { p -> TextButton(onClick = { choose(p, pin) }) { Text(stringResource(R.string.profiles_choose)) } }
        },
        dismissButton = { TextButton(onClick = onClose) { Text(stringResource(R.string.cancel)) } },
    )
}
