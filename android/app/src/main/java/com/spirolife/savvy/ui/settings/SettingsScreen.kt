package com.spirolife.savvy.ui.settings

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavController
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.Settings
import com.spirolife.savvy.data.api.Status
import com.spirolife.savvy.ui.components.ErrorBox
import com.spirolife.savvy.ui.components.LoadingBox
import com.spirolife.savvy.ui.nav.Routes
import com.spirolife.savvy.ui.pair.ReturnToPairingWhenUnpaired
import com.spirolife.savvy.ui.pair.resetTo
import com.spirolife.savvy.ui.theme.SavvyTheme
import kotlinx.coroutines.launch

/** Which picker is open. */
private enum class Picker { Bod, Checkins, Eod, ReviewDay, ReviewTime, QuietStart, QuietEnd, Unpair }

@Composable
fun SettingsScreen(nav: NavController, vm: SettingsViewModel = viewModel()) {
    ReturnToPairingWhenUnpaired(nav)
    val ui by vm.ui.collectAsStateWithLifecycle()
    val memory by vm.memory.collectAsStateWithLifecycle()
    var showMemory by rememberSaveable { mutableStateOf(false) }
    var picker by remember { mutableStateOf<Picker?>(null) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    BackHandler(enabled = showMemory) { showMemory = false }
    LaunchedEffect(ui.message) {
        ui.message?.let { snackbar.showSnackbar(it); vm.messageShown() }
    }

    Box(Modifier.fillMaxSize().background(SavvyTheme.colors.bg).statusBarsPadding()) {
        Column(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                IconButton(onClick = { if (showMemory) showMemory = false else nav.popBackStack() }, modifier = Modifier.size(48.dp)) {
                    Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back", tint = SavvyTheme.colors.ink)
                }
                Text(if (showMemory) "Memory" else "Settings", color = SavvyTheme.colors.deep, style = MaterialTheme.typography.headlineMedium)
            }
            when {
                showMemory -> MemoryPane(
                    memory,
                    onQuery = { vm.searchFacts(it, debounce = true) },
                    onRetry = { vm.searchFacts() },
                    onDelete = vm::deleteFact,
                )
                ui.loading -> LoadingBox()
                ui.settings == null -> ErrorBox(ui.loadError ?: "Couldn't load settings.", onRetry = vm::load)
                else -> SettingsList(
                    settings = ui.settings!!,
                    status = ui.status,
                    saving = ui.saving,
                    onAccounts = { nav.navigate(Routes.CONNECT) },
                    onPick = { picker = it },
                    onFlag = vm::setFlag,
                    onMemory = { showMemory = true },
                )
            }
        }
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter))
    }

    val s = ui.settings ?: return
    val close = { picker = null }
    when (picker) {
        Picker.Bod -> parseTime(s.bodTime).let { (h, m) ->
            TimeDialog("Morning plan", h, m, onDismiss = close) { nh, nm -> vm.setTime("bod_time", nh, nm); close() }
        }
        Picker.Eod -> parseTime(s.eodTime).let { (h, m) ->
            TimeDialog("Evening reflection", h, m, onDismiss = close) { nh, nm -> vm.setTime("eod_time", nh, nm); close() }
        }
        Picker.Checkins -> CheckinsDialog(s.checkinTimes, onDismiss = close) { vm.setCheckins(it); close() }
        Picker.ReviewDay -> DayDialog(s.weeklyReviewDay, onDismiss = close) { vm.setDay(it); picker = Picker.ReviewTime }
        Picker.ReviewTime -> parseTime(s.weeklyReviewTime).let { (h, m) ->
            TimeDialog("Weekly review time", h, m, onDismiss = close) { nh, nm -> vm.setTime("weekly_review_time", nh, nm); close() }
        }
        Picker.QuietStart -> TimeDialog("Quiet hours start", s.quietHoursStart, 0, hourOnly = true, onDismiss = close) { h, _ ->
            vm.setHour("quiet_hours_start", h); close()
        }
        Picker.QuietEnd -> TimeDialog("Quiet hours end", s.quietHoursEnd, 0, hourOnly = true, onDismiss = close) { h, _ ->
            vm.setHour("quiet_hours_end", h); close()
        }
        Picker.Unpair -> AlertDialog(
            onDismissRequest = close,
            containerColor = SavvyTheme.colors.card,
            title = { Text("Unpair this phone?", color = SavvyTheme.colors.deep) },
            text = {
                Text(
                    "The app forgets its key to your PC. Your Google logins stay on the PC. To use the app again, pair with a new code.",
                    color = SavvyTheme.colors.ink,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    close()
                    scope.launch {
                        Graph.unpair()
                        nav.resetTo(Routes.PAIR)
                    }
                }) { Text("Unpair", color = SavvyTheme.colors.warn) }
            },
            dismissButton = { TextButton(onClick = close) { Text("Cancel", color = SavvyTheme.colors.ink) } },
        )
        null -> Unit
    }
}

@Composable
private fun SettingsList(
    settings: Settings,
    status: Status?,
    saving: Boolean,
    onAccounts: () -> Unit,
    onPick: (Picker) -> Unit,
    onFlag: (String, Boolean) -> Unit,
    onMemory: () -> Unit,
) {
    val c = SavvyTheme.colors
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        SettingsGroup("Accounts") {
            if (status == null) {
                SettingsRow("Google accounts", "Manage", onClick = onAccounts)
            } else {
                status.google.forEach { a ->
                    SettingsRow(
                        "${a.label.replaceFirstChar { it.uppercase() }} (Google)",
                        if (a.connected) "Connected" else "Sign in again",
                        if (a.connected) c.ok else c.warn,
                        onClick = onAccounts,
                    )
                }
                if (status.google.isEmpty()) SettingsRow("Google", "Add an account", c.accent, onClick = onAccounts)
                SettingsRow("Todoist", if (status.todoist) "Connected" else "Not set up", if (status.todoist) c.ok else c.muted)
            }
        }
        SettingsGroup("Notifications") {
            SettingsRow("Morning plan", displayTime(settings.bodTime), onClick = { onPick(Picker.Bod) })
            SettingsRow(
                "Check-ins",
                settings.checkinTimes.joinToString(" · ") { displayTime(it) }.ifEmpty { "None" },
                onClick = { onPick(Picker.Checkins) },
            )
            SettingsRow("Evening reflection", displayTime(settings.eodTime), onClick = { onPick(Picker.Eod) })
            SettingsRow(
                "Weekly review",
                "${settings.weeklyReviewDay} ${displayTime(settings.weeklyReviewTime)}",
                onClick = { onPick(Picker.ReviewDay) },
            )
            SettingsRow("Quiet hours start", displayHour(settings.quietHoursStart), onClick = { onPick(Picker.QuietStart) })
            SettingsRow("Quiet hours end", displayHour(settings.quietHoursEnd), onClick = { onPick(Picker.QuietEnd) })
        }
        SettingsGroup("Savvy") {
            SettingsRow("Memory", "Facts and rules", onClick = onMemory)
            SwitchRow("Rest-day warnings", settings.restDayWarnings, enabled = !saving) { onFlag("rest_day_warnings", it) }
        }
        SettingsGroup("Your PC") {
            SettingsRow(
                "Savvy server",
                if (status != null) "Running" else "Can't reach",
                if (status != null) c.ok else c.warn,
            )
            SettingsRow("Address", Graph.prefs.baseUrl?.removePrefix("http://") ?: "Not paired")
            status?.let {
                SettingsRow("Memory search (Ollama)", if (it.ollama) "Running" else "Off", if (it.ollama) c.ok else c.muted)
            }
            SwitchRow("Signal fallback", settings.signalFallback, enabled = !saving) { onFlag("signal_fallback", it) }
            SettingsRow("Unpair this phone", "", onClick = { onPick(Picker.Unpair) })
        }
    }
}
