package com.spirolife.savvy.ui.pair

import android.os.Build
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavController
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.ApiException
import com.spirolife.savvy.data.DEFAULT_PORT
import com.spirolife.savvy.data.normalizeBaseUrl
import com.spirolife.savvy.ui.components.Kicker
import com.spirolife.savvy.ui.components.PillButton
import com.spirolife.savvy.ui.components.SavvyCard
import com.spirolife.savvy.ui.nav.Routes
import com.spirolife.savvy.ui.theme.SavvyTheme
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.SerializationException
import java.io.IOException

data class PairUiState(
    val address: String = "",
    val code: String = "",
    val busy: Boolean = false,
    val error: String? = null,
    val paired: Boolean = false,
) {
    val canSubmit: Boolean get() = !busy && address.isNotBlank() && code.length == 6
}

class PairViewModel : ViewModel() {
    private val state = MutableStateFlow(PairUiState(address = Graph.prefs.baseUrl.orEmpty().removePrefix("http://")))
    val ui: StateFlow<PairUiState> = state.asStateFlow()

    fun setAddress(value: String) = state.update { it.copy(address = value.trim(), error = null) }
    fun setCode(value: String) = state.update { it.copy(code = value.filter(Char::isDigit).take(6), error = null) }

    fun pair() {
        val s = state.value
        if (!s.canSubmit) return
        state.update { it.copy(busy = true, error = null) }
        viewModelScope.launch {
            val error = try {
                val base = normalizeBaseUrl(s.address)
                checkReachable(base) ?: run {
                    val name = Graph.prefs.deviceName ?: Build.MODEL ?: "Android phone"
                    val token = Graph.api.pair(base, s.code, name).token
                    Graph.prefs.savePairing(base, token, name)
                    Graph.markPaired()
                    null
                }
            } catch (e: IllegalArgumentException) {
                e.message ?: "That doesn't look like an address."
            } catch (e: ApiException) {
                if (e.code == "bad_code" || e.status == 403) {
                    "That code is wrong or has expired. Make a new one on your PC (Savvy tray → Pair phone…) and enter it within 10 minutes."
                } else e.message
            } catch (e: IOException) {
                "Lost the connection to your PC while pairing. Try again."
            }
            state.update { it.copy(busy = false, error = error, paired = error == null) }
        }
    }

    /** Pings first so "can't reach the PC" and "wrong code" read as different problems. */
    private suspend fun checkReachable(base: String): String? = try {
        val ping = Graph.api.ping(base)
        if (ping.ok) null else notSavvy(base)
    } catch (e: IOException) {
        "Can't reach your PC at $base. Check that Tailscale is on on this phone and on the PC, and that Savvy is running there."
    } catch (e: ApiException) {
        notSavvy(base)
    } catch (e: SerializationException) {
        notSavvy(base)
    }

    private fun notSavvy(base: String) =
        "Something answered at $base, but it isn't Savvy. Check the port (usually $DEFAULT_PORT)."
}

/** First run: connect this phone to Savvy on the PC with a one-time code. */
@Composable
fun PairScreen(nav: NavController, vm: PairViewModel = viewModel()) {
    val ui by vm.ui.collectAsStateWithLifecycle()

    LaunchedEffect(ui.paired) {
        if (ui.paired) nav.resetTo(Routes.CONNECT)
    }

    Column(
        Modifier
            .fillMaxSize()
            .background(SavvyTheme.colors.bg)
            .statusBarsPadding()
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 28.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        Box(
            Modifier.size(52.dp).background(SavvyTheme.colors.accent, RoundedCornerShape(16.dp)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(Icons.Outlined.Link, contentDescription = null, tint = SavvyTheme.colors.accentInk, modifier = Modifier.size(28.dp))
        }
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Welcome to Savvy", color = SavvyTheme.colors.deep, style = MaterialTheme.typography.displaySmall)
            Text(
                "Savvy's brain runs on your PC. Pair this phone with it once, and it works from anywhere over Tailscale.",
                color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodyLarge,
            )
        }

        SavvyCard(Modifier.fillMaxWidth()) {
            Kicker("Get a code")
            Text(
                "On your PC, open the Savvy tray icon and choose Pair phone… " +
                    "(or run python src/app_server.py --pair). It shows a 6-digit code that works for 10 minutes.",
                color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyMedium,
            )
        }

        OutlinedTextField(
            value = ui.address,
            onValueChange = vm::setAddress,
            label = { Text("PC address") },
            placeholder = { Text("my-pc or 100.x.y.z:8765") },
            supportingText = { Text("Your PC's Tailscale name or 100.x.y.z address. Port 8765 is added if you leave it out.") },
            singleLine = true,
            enabled = !ui.busy,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next, autoCorrectEnabled = false),
            colors = fieldColors(),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = ui.code,
            onValueChange = vm::setCode,
            label = { Text("Pairing code") },
            placeholder = { Text("123456") },
            singleLine = true,
            enabled = !ui.busy,
            textStyle = MaterialTheme.typography.titleLarge,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { vm.pair() }),
            colors = fieldColors(),
            modifier = Modifier.fillMaxWidth(),
        )

        ui.error?.let { ErrorNote(it) }

        PillButton(
            text = if (ui.busy) "Pairing…" else "Pair",
            onClick = vm::pair,
            enabled = ui.canSubmit,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

/** A warning-coloured note for a failed action. */
@Composable
internal fun ErrorNote(message: String, modifier: Modifier = Modifier) {
    SavvyCard(modifier.fillMaxWidth(), color = SavvyTheme.colors.warnBg) {
        Text(message, color = SavvyTheme.colors.warn, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
internal fun fieldColors() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = SavvyTheme.colors.accent,
    unfocusedBorderColor = SavvyTheme.colors.line,
    focusedLabelColor = SavvyTheme.colors.accent,
    cursorColor = SavvyTheme.colors.accent,
    focusedContainerColor = SavvyTheme.colors.card,
    unfocusedContainerColor = SavvyTheme.colors.card,
    disabledContainerColor = SavvyTheme.colors.card,
)
