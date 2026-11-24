package org.platform.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import org.platform.app.PlatformApp
import org.platform.app.R
import org.platform.app.api.ApiException

/** Het adres van het platform kiezen; de app kijkt na of daar een platform antwoordt. */
@Composable
fun ServerScreen(app: PlatformApp, onDone: () -> Unit) {
    var url by remember { mutableStateOf(app.session.server.orEmpty()) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val none = stringResource(R.string.server_none)
    Column(
        Modifier.fillMaxSize().imePadding().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp, androidx.compose.ui.Alignment.CenterVertically),
    ) {
        Text(stringResource(R.string.server_title), style = MaterialTheme.typography.headlineMedium)
        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text(stringResource(R.string.server_hint)) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Button(
            enabled = !busy && url.isNotBlank(),
            onClick = {
                var base = url.trim().trimEnd('/')
                if (!base.startsWith("http://") && !base.startsWith("https://")) base = "https://$base"
                busy = true
                scope.launch {
                    try {
                        app.api.info(base)
                        app.session.server = base
                        onDone()
                    } catch (e: Exception) {
                        error = none
                    } finally {
                        busy = false
                    }
                }
            },
        ) { Text(stringResource(R.string.server_connect)) }
    }
}

/** Inloggen met e-mail, wachtwoord en (als 2FA aan staat) de code. */
@Composable
fun LoginScreen(app: PlatformApp, onOtherServer: () -> Unit, onDone: () -> Unit) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var needCode by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val network = stringResource(R.string.error_network)
    Column(
        Modifier.fillMaxSize().imePadding().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp, androidx.compose.ui.Alignment.CenterVertically),
    ) {
        Text(stringResource(R.string.login_title), style = MaterialTheme.typography.headlineMedium)
        Text(app.session.server.orEmpty(), style = MaterialTheme.typography.bodySmall)
        OutlinedTextField(
            value = email, onValueChange = { email = it }, singleLine = true,
            label = { Text(stringResource(R.string.login_email)) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = password, onValueChange = { password = it }, singleLine = true,
            label = { Text(stringResource(R.string.login_password)) },
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
        )
        if (needCode) {
            OutlinedTextField(
                value = code, onValueChange = { code = it.trim() }, singleLine = true,
                label = { Text(stringResource(R.string.login_code)) },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Button(
            enabled = !busy && email.isNotBlank() && password.isNotEmpty(),
            onClick = {
                busy = true
                error = null
                scope.launch {
                    try {
                        app.api.login(email.trim(), password, code.ifBlank { null })
                        onDone()
                    } catch (e: ApiException) {
                        if (e.code == "totp_required") needCode = true else error = e.message
                    } catch (e: Exception) {
                        error = network
                    } finally {
                        busy = false
                    }
                }
            },
        ) { Text(stringResource(R.string.login_go)) }
        TextButton(onClick = onOtherServer) { Text(stringResource(R.string.login_other_server)) }
    }
}
