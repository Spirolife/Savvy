package com.spirolife.savvy.ui.today

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AccountCircle
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavController
import com.spirolife.savvy.data.api.Event
import com.spirolife.savvy.data.api.NextUp
import com.spirolife.savvy.data.api.Reminder
import com.spirolife.savvy.data.api.RestSuggestion
import com.spirolife.savvy.data.api.Task
import com.spirolife.savvy.data.api.Today
import com.spirolife.savvy.ui.components.ErrorBox
import com.spirolife.savvy.ui.components.Kicker
import com.spirolife.savvy.ui.components.LoadingBox
import com.spirolife.savvy.ui.components.PillButton
import com.spirolife.savvy.ui.components.PillOutlineButton
import com.spirolife.savvy.ui.components.SavvyCard
import com.spirolife.savvy.ui.components.ScreenTitle
import com.spirolife.savvy.ui.nav.Routes
import com.spirolife.savvy.ui.theme.CardShape
import com.spirolife.savvy.ui.theme.SavvyTheme
import com.spirolife.savvy.ui.theme.eventColors
import java.time.LocalDate

/** OWNER: today-plan-diary agent. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TodayScreen(nav: NavController, vm: TodayViewModel = viewModel()) {
    val state by vm.state.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }

    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { vm.load() }
    LaunchedEffect(state.message) {
        state.message?.let { snackbar.showSnackbar(it); vm.messageShown() }
    }

    Scaffold(
        containerColor = SavvyTheme.colors.bg,
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            val today = state.today
            when {
                today != null -> PullToRefreshBox(
                    isRefreshing = state.refreshing,
                    onRefresh = vm::refresh,
                    modifier = Modifier.fillMaxSize(),
                ) {
                    TodayContent(
                        today = today,
                        state = state,
                        onSettings = { nav.navigate(Routes.SETTINGS) },
                        onSignIn = { nav.navigate(Routes.CONNECT) },
                        onPlanRest = vm::planRestDay,
                        onDismissRest = vm::dismissRest,
                        onTaskDone = vm::setTaskDone,
                    )
                }
                state.loading -> LoadingBox()
                else -> Column(Modifier.fillMaxSize().padding(16.dp)) {
                    Header(LocalDate.now().toString()) { nav.navigate(Routes.SETTINGS) }
                    ErrorBox(state.error ?: "Something went wrong", onRetry = vm::load)
                }
            }
        }
    }
}

@Composable
private fun TodayContent(
    today: Today,
    state: TodayUiState,
    onSettings: () -> Unit,
    onSignIn: () -> Unit,
    onPlanRest: () -> Unit,
    onDismissRest: () -> Unit,
    onTaskDone: (Task, Boolean) -> Unit,
) {
    val time = rememberTimeFormatter()
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { Header(today.date, onSettings) }
        if (today.signinNeeded.isNotEmpty()) {
            item { SignInWarning(today.signinNeeded, onSignIn) }
        }
        val rest = today.rest
        if (rest != null && rest.suggested && !state.restDismissed) {
            item { RestCard(rest, state.planningRest, onPlanRest, onDismissRest) }
        }
        today.next?.let { next -> item { NextUpCard(next, time) } }

        item { SectionTitle("Schedule") }
        if (today.events.isEmpty()) {
            item { Muted("Nothing on the calendar today.") }
        } else {
            itemsIndexed(today.events, key = { i, e -> "e$i-${e.id}" }) { _, e -> EventRow(e, time) }
        }

        item { SectionTitle("Tasks due today") }
        if (today.tasks.isEmpty()) {
            item { Muted("No tasks due today.") }
        } else {
            itemsIndexed(today.tasks, key = { i, t -> "t$i-${t.id}" }) { _, t -> TaskRow(t, onTaskDone) }
        }

        if (today.reminders.isNotEmpty()) {
            item { SectionTitle("Reminders") }
            reminders(today.reminders, time)
        }
    }
}

@Composable
private fun Header(date: String, onSettings: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(longDate(date), color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
            ScreenTitle("Today", Modifier.semantics { heading() })
        }
        Surface(shape = RoundedCornerShape(percent = 50), color = SavvyTheme.colors.card, shadowElevation = 2.dp) {
            IconButton(onClick = onSettings, modifier = Modifier.size(48.dp)) {
                Icon(Icons.Outlined.AccountCircle, contentDescription = "Settings", tint = SavvyTheme.colors.ink)
            }
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.titleSmall,
        modifier = Modifier.padding(top = 4.dp).semantics { heading() })
}

@Composable
private fun Muted(text: String) {
    Text(text, color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodyMedium)
}

@Composable
private fun WarmCard(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = CardShape,
        color = SavvyTheme.colors.warnBg,
        border = BorderStroke(1.dp, SavvyTheme.colors.warnLine),
    ) { content() }
}

@Composable
private fun SignInWarning(labels: List<String>, onSignIn: () -> Unit) {
    WarmCard(Modifier.clickable(role = Role.Button, onClickLabel = "Sign in", onClick = onSignIn)) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 14.dp).heightIn(min = 24.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Icon(Icons.Outlined.WarningAmber, contentDescription = null, tint = SavvyTheme.colors.warn)
            Text("Google needs you to sign in again: ${labels.joinToString(", ")}",
                color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable
private fun RestCard(rest: RestSuggestion, busy: Boolean, onPlan: () -> Unit, onDismiss: () -> Unit) {
    WarmCard {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Kicker("Rest day suggested", color = SavvyTheme.colors.warn)
            Text(rest.message, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (rest.day != null) PillButton("Plan rest day", onPlan, enabled = !busy)
                PillOutlineButton("Not now", onDismiss)
            }
        }
    }
}

@Composable
private fun NextUpCard(next: NextUp, time: (String) -> String) {
    val ink = SavvyTheme.colors.accentInk
    Box(
        Modifier.fillMaxWidth()
            .background(SavvyTheme.colors.gradient, CardShape)
            .padding(horizontal = 16.dp, vertical = 14.dp)
            .semantics(mergeDescendants = true) {},
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            val at = if (next.event.allDay) "All day" else time(next.event.start)
            Kicker("Next up · $at", color = ink)
            Text(next.event.title, color = ink, style = MaterialTheme.typography.titleLarge)
            next.event.location?.let { Text(it, color = ink, style = MaterialTheme.typography.bodySmall) }
            if (next.leaveBy != null) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Icon(Icons.Outlined.Schedule, contentDescription = null, tint = ink, modifier = Modifier.size(16.dp))
                    val travel = buildString {
                        append("Leave by ${time(next.leaveBy)}")
                        next.travelMinutes?.let { append(" · $it min") }
                        next.travelMode?.let { append(" by $it") }
                    }
                    Text(travel, color = ink, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
    }
}

@Composable
private fun EventRow(e: Event, time: (String) -> String) {
    val palette = eventColors(SavvyTheme.colors.isDark)
    val bar = palette[Math.floorMod("${e.account}/${e.calendar}".hashCode(), palette.size)]
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp).semantics(mergeDescendants = true) {},
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(if (e.allDay) "All day" else time(e.start), color = SavvyTheme.colors.muted,
            style = MaterialTheme.typography.bodySmall, modifier = Modifier.width(56.dp).padding(top = 2.dp))
        Box(Modifier.width(4.dp).height(40.dp).background(bar, RoundedCornerShape(2.dp)))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(e.title, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyLarge)
            val meta = listOfNotNull(e.calendar.takeIf { it.isNotBlank() }, e.location,
                if (!e.allDay) "until ${time(e.end)}" else null).joinToString(" · ")
            if (meta.isNotEmpty()) Text(meta, color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun TaskRow(t: Task, onDone: (Task, Boolean) -> Unit) {
    SavvyCard(contentPadding = PaddingValues(horizontal = 8.dp, vertical = 2.dp)) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 48.dp)
                .toggleable(value = t.completed, role = Role.Checkbox, onValueChange = { onDone(t, it) }),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Checkbox(checked = t.completed, onCheckedChange = null, modifier = Modifier.padding(12.dp),
                colors = CheckboxDefaults.colors(checkedColor = SavvyTheme.colors.accent,
                    checkmarkColor = SavvyTheme.colors.accentInk, uncheckedColor = SavvyTheme.colors.muted))
            Column(Modifier.weight(1f).padding(vertical = 8.dp)) {
                Text(t.title, color = if (t.completed) SavvyTheme.colors.muted else SavvyTheme.colors.ink,
                    style = MaterialTheme.typography.bodyLarge,
                    textDecoration = if (t.completed) TextDecoration.LineThrough else null)
                val meta = listOfNotNull(t.project, t.due?.let { overdueLabel(it) }).joinToString(" · ")
                if (meta.isNotEmpty()) Text(meta, color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
            }
            Spacer(Modifier.width(8.dp))
        }
    }
}

private fun overdueLabel(due: String): String? = runCatching {
    if (LocalDate.parse(due).isBefore(LocalDate.now())) "overdue" else null
}.getOrNull()

private fun LazyListScope.reminders(list: List<Reminder>, time: (String) -> String) {
    itemsIndexed(list, key = { i, r -> "r$i-${r.id}" }) { _, r ->
        Row(Modifier.fillMaxWidth().padding(vertical = 6.dp).semantics(mergeDescendants = true) {},
            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(time(r.at), color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.width(56.dp).padding(top = 2.dp))
            Text(r.text, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.weight(1f))
        }
    }
}
