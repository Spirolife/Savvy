package com.spirolife.savvy.ui.connect

import android.accounts.Account
import android.app.Activity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts.StartIntentSenderForResult
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavController
import com.google.android.gms.auth.api.identity.AuthorizationRequest
import com.google.android.gms.auth.api.identity.Identity
import com.google.android.gms.common.api.Scope
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.ApiException
import com.spirolife.savvy.data.api.GoogleAccount
import com.spirolife.savvy.ui.components.ErrorBox
import com.spirolife.savvy.ui.components.LoadingBox
import com.spirolife.savvy.ui.components.PillButton
import com.spirolife.savvy.ui.components.SavvyCard
import com.spirolife.savvy.ui.nav.Routes
import com.spirolife.savvy.ui.pair.ErrorNote
import com.spirolife.savvy.ui.pair.ReturnToPairingWhenUnpaired
import com.spirolife.savvy.ui.pair.fieldColors
import com.spirolife.savvy.ui.pair.resetTo
import com.spirolife.savvy.ui.theme.SavvyTheme
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import java.io.IOException

private val LABEL = Regex("[a-z0-9_-]{1,32}")

/** Turns what the user typed into a server label ("School 2" -> "school_2"), or null if it can't be one. */
internal fun toLabel(input: String): String? =
    input.trim().lowercase().replace(Regex("\\s+"), "_").takeIf { LABEL.matches(it) }

data class ConnectUiState(
    val loading: Boolean = true,
    val loadError: String? = null,
    val accounts: List<GoogleAccount> = emptyList(),
    /** Label being signed in right now, if any. */
    val signingIn: String? = null,
    val message: String? = null,
    val error: String? = null,
) {
    val anyConnected: Boolean get() = accounts.any { it.connected }
}

class ConnectViewModel : ViewModel() {
    private val state = MutableStateFlow(ConnectUiState())
    val ui: StateFlow<ConnectUiState> = state.asStateFlow()

    init { load() }

    fun load() {
        state.update { it.copy(loading = it.accounts.isEmpty(), loadError = null) }
        viewModelScope.launch {
            try {
                val status = Graph.api.status()
                state.update { it.copy(loading = false, accounts = status.google) }
            } catch (e: ApiException) {
                state.update { it.copy(loading = false, loadError = e.message) }
            } catch (e: IOException) {
                state.update { it.copy(loading = false, loadError = "Can't reach your PC. Check that Tailscale is on and Savvy is running.") }
            }
        }
    }

    /**
     * Starts signing [label] in: fetches the PC's Web client id and scopes and
     * builds the request, or reports why it can't (null).
     */
    suspend fun begin(label: String, account: Account?): AuthorizationRequest? {
        state.update { it.copy(signingIn = label, message = null, error = null) }
        return try {
            val config = Graph.api.googleConfig()
            AuthorizationRequest.builder()
                .setRequestedScopes(config.scopes.map { Scope(it) })
                .requestOfflineAccess(config.webClientId, /* forceCodeForRefreshToken = */ true)
                .apply { if (account != null) setAccount(account) }
                .build()
        } catch (e: ApiException) {
            fail(
                if (e.code == "no_web_client") {
                    "Your PC isn't set up for phone sign-in yet: it needs a Web OAuth client " +
                        "saved as credentials/web_client.json. See docs/android-setup.md."
                } else e.message,
            )
            null
        } catch (e: IOException) {
            fail("Can't reach your PC. Check that Tailscale is on and Savvy is running.")
            null
        }
    }

    /** Hands Google's one-time code to the PC, which keeps the login. */
    fun finish(serverAuthCode: String?) {
        val label = state.value.signingIn ?: return
        if (serverAuthCode.isNullOrEmpty()) {
            fail("Google didn't return a sign-in code. Try again.")
            return
        }
        viewModelScope.launch {
            try {
                val ok = Graph.api.googleConnect(label, serverAuthCode)
                state.update {
                    it.copy(signingIn = null, message = ok.message ?: "'$label' is signed in${ok.email?.let { e -> " as $e" } ?: ""}.")
                }
                load()
            } catch (e: ApiException) {
                fail(e.message) // wrong_account, label_clash and exchange_failed carry a readable message
            } catch (e: IOException) {
                fail("Lost the connection to your PC before it saved the login. Try again.")
            }
        }
    }

    fun cancelled() = state.update { it.copy(signingIn = null) }

    fun fail(message: String) = state.update { it.copy(signingIn = null, error = message) }
}

/** Connect (and re-connect) Google accounts with Android's own account picker. */
@Composable
fun ConnectScreen(nav: NavController, vm: ConnectViewModel = viewModel()) {
    ReturnToPairingWhenUnpaired(nav)
    val ui by vm.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var askLabel by remember { mutableStateOf(false) }
    val fromSettings = remember { nav.previousBackStackEntry != null }

    val launcher = rememberLauncherForActivityResult(StartIntentSenderForResult()) { result ->
        if (result.resultCode != Activity.RESULT_OK) {
            vm.cancelled()
            return@rememberLauncherForActivityResult
        }
        try {
            vm.finish(Identity.getAuthorizationClient(context).getAuthorizationResultFromIntent(result.data).serverAuthCode)
        } catch (e: Exception) {
            vm.fail("Google sign-in didn't finish: ${e.message ?: "unknown error"}")
        }
    }

    fun signIn(label: String, email: String?) {
        scope.launch {
            val client = Identity.getAuthorizationClient(context)
            val hint = email?.let { Account(it, "com.google") }
            val result = try {
                val request = vm.begin(label, hint) ?: return@launch
                try {
                    client.authorize(request).await()
                } catch (e: Exception) {
                    // The hinted account may not be on this phone; let Google show its picker instead.
                    if (hint == null) throw e
                    client.authorize(vm.begin(label, null) ?: return@launch).await()
                }
            } catch (e: Exception) {
                vm.fail("Google sign-in failed: ${e.message ?: "unknown error"}")
                return@launch
            }
            val pending = result.pendingIntent
            if (result.hasResolution() && pending != null) {
                launcher.launch(IntentSenderRequest.Builder(pending.intentSender).build())
            } else {
                vm.finish(result.serverAuthCode)
            }
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .background(SavvyTheme.colors.bg)
            .statusBarsPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        if (fromSettings) {
            IconButton(onClick = { nav.popBackStack() }, modifier = Modifier.size(48.dp)) {
                Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back", tint = SavvyTheme.colors.ink)
            }
        }
        Header()
        PairedCard()

        when {
            ui.loading -> LoadingBox(Modifier.heightIn(min = 160.dp))
            ui.loadError != null -> ErrorBox(ui.loadError!!, onRetry = vm::load)
            else -> Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                ui.accounts.forEach { account ->
                    AccountCard(
                        account,
                        busy = ui.signingIn == account.label,
                        enabled = ui.signingIn == null,
                        onSignIn = { signIn(account.label, account.email) },
                    )
                }
                AddAccountButton(enabled = ui.signingIn == null, onClick = { askLabel = true })
            }
        }

        ui.message?.let {
            SavvyCard(Modifier.fillMaxWidth()) { Text(it, color = SavvyTheme.colors.ok, style = MaterialTheme.typography.bodyMedium) }
        }
        ui.error?.let { ErrorNote(it) }

        Text(
            "Opens Android's own Google account picker, so nothing ever has to be finished on the PC.",
            color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall,
        )
        if (!fromSettings && ui.anyConnected) {
            PillButton("Continue", onClick = { nav.resetTo(Routes.TODAY) }, modifier = Modifier.fillMaxWidth())
        }
    }

    if (askLabel) {
        LabelDialog(
            taken = ui.accounts.map { it.label },
            onDismiss = { askLabel = false },
            onConfirm = { label -> askLabel = false; signIn(label, null) },
        )
    }
}

@Composable
private fun Header() {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Box(Modifier.size(52.dp).background(SavvyTheme.colors.accent, RoundedCornerShape(16.dp)), contentAlignment = Alignment.Center) {
            Icon(Icons.Outlined.Sync, contentDescription = null, tint = SavvyTheme.colors.accentInk, modifier = Modifier.size(28.dp))
        }
        Text("Connect your Google accounts", color = SavvyTheme.colors.deep, style = MaterialTheme.typography.headlineMedium)
        Text(
            "Savvy reads your calendars and email and manages tasks for each account. Sign in here; " +
                "your PC keeps the login so Savvy works while your phone sleeps.",
            color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodyMedium,
        )
    }
}

@Composable
private fun PairedCard() {
    SavvyCard(Modifier.fillMaxWidth(), contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Box(Modifier.size(10.dp).background(SavvyTheme.colors.ok, CircleShape))
            Column(Modifier.weight(1f)) {
                Text("Paired with Savvy on your PC", color = SavvyTheme.colors.ink, style = MaterialTheme.typography.titleSmall)
                Text(
                    Graph.prefs.baseUrl?.removePrefix("http://") ?: "Over Tailscale",
                    color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

@Composable
private fun AccountCard(account: GoogleAccount, busy: Boolean, enabled: Boolean, onSignIn: () -> Unit) {
    val c = SavvyTheme.colors
    val name = account.label.replaceFirstChar { it.uppercase() }
    val modifier = if (account.connected) Modifier.fillMaxWidth()
    else Modifier.fillMaxWidth().border(1.5.dp, c.warnLine, RoundedCornerShape(18.dp))
    SavvyCard(modifier) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Box(
                Modifier.size(40.dp).background(if (account.connected) c.band else c.warnBg, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Text(name.take(1), color = if (account.connected) c.deep else c.warn, style = MaterialTheme.typography.titleMedium)
            }
            Column(Modifier.weight(1f)) {
                Text(name, color = c.ink, style = MaterialTheme.typography.titleMedium)
                account.email?.let { Text(it, color = c.muted, style = MaterialTheme.typography.bodySmall) }
            }
            if (account.connected) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Icon(Icons.Outlined.Check, contentDescription = null, tint = c.ok, modifier = Modifier.size(16.dp))
                    Text("Connected", color = c.ok, style = MaterialTheme.typography.labelMedium)
                }
            } else {
                Text("Signed out", color = c.warn, style = MaterialTheme.typography.labelMedium)
            }
        }
        if (account.connected) {
            Text("Calendar · Gmail · Tasks", color = c.muted, style = MaterialTheme.typography.bodySmall)
        } else {
            Text(
                (account.reason?.let { "Google says: $it. " } ?: "") +
                    "Its calendar and email are hidden from Savvy until you sign in.",
                color = c.muted, style = MaterialTheme.typography.bodyMedium,
            )
        }
        if (!account.connected || busy) {
            if (busy) {
                Box(Modifier.fillMaxWidth().heightIn(min = 48.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = c.accent, modifier = Modifier.size(24.dp))
                }
            } else {
                PillButton("Sign in again", onClick = onSignIn, enabled = enabled, modifier = Modifier.fillMaxWidth())
            }
        }
    }
}

@Composable
private fun AddAccountButton(enabled: Boolean, onClick: () -> Unit) {
    val line = SavvyTheme.colors.line
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 52.dp)
            .drawBehind {
                drawRoundRect(
                    color = line,
                    style = Stroke(width = 1.5.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 8f))),
                    cornerRadius = CornerRadius(18.dp.toPx()),
                )
            }
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Outlined.Add, contentDescription = null, tint = SavvyTheme.colors.ink, modifier = Modifier.size(18.dp))
        Spacer(Modifier.size(8.dp))
        Text("Add another account", color = if (enabled) SavvyTheme.colors.ink else SavvyTheme.colors.muted, style = MaterialTheme.typography.labelLarge)
    }
}

@Composable
private fun LabelDialog(taken: List<String>, onDismiss: () -> Unit, onConfirm: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    val label = toLabel(text)
    val error = when {
        text.isBlank() -> null
        label == null -> "Use up to 32 letters, numbers, - or _."
        label in taken -> "'$label' is already here; use its Sign in again button."
        else -> null
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = SavvyTheme.colors.card,
        title = { Text("Name this account", color = SavvyTheme.colors.deep) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    "A short label Savvy uses for it, like work or school. You'll pick the Google account next.",
                    color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodyMedium,
                )
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it.take(32) },
                    label = { Text("Label") },
                    singleLine = true,
                    isError = error != null,
                    supportingText = error?.let { { Text(it) } },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii, imeAction = ImeAction.Done, autoCorrectEnabled = false),
                    colors = fieldColors(),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { label?.let(onConfirm) }, enabled = label != null && error == null) {
                Text("Choose account", color = if (label != null && error == null) SavvyTheme.colors.accent else SavvyTheme.colors.muted)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel", color = SavvyTheme.colors.ink) }
        },
    )
}
