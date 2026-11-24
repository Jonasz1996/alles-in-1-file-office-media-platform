package org.platform.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import org.platform.app.R
import org.platform.app.api.ApiException
import java.io.IOException

/** Een fout als tekst voor de gebruiker. */
@Composable
fun errorText(e: Throwable): String = when {
    e is ApiException && e.code == "media_off" -> stringResource(R.string.media_off)
    e is ApiException && e.status > 0 -> e.message ?: "HTTP ${e.status}"
    e is IOException -> stringResource(R.string.error_network)
    else -> e.message ?: e.toString()
}

/** Laadt `load` en toont laden, een fout met Opnieuw, of de inhoud. */
@Composable
fun <T> Loaded(key: Any?, load: suspend () -> T, content: @Composable (T, reload: () -> Unit) -> Unit) {
    var tick by remember { mutableIntStateOf(0) }
    var value by remember(key) { mutableStateOf<T?>(null) }
    var error by remember(key) { mutableStateOf<Throwable?>(null) }
    LaunchedEffect(key, tick) {
        error = null
        try {
            value = load()
        } catch (e: Exception) {
            error = e
        }
    }
    val v = value
    val err = error
    when {
        v != null -> content(v) { tick++ }
        err != null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(errorText(err), color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(16.dp))
                Button(onClick = { tick++ }) { Text(stringResource(R.string.retry)) }
            }
        }
        else -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
    }
}
