package com.spirolife.savvy.ui.plan

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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavController
import com.spirolife.savvy.data.api.Goal
import com.spirolife.savvy.data.api.Meal
import com.spirolife.savvy.data.api.MealWeek
import com.spirolife.savvy.data.api.Plan
import com.spirolife.savvy.data.api.Project
import com.spirolife.savvy.ui.components.ErrorBox
import com.spirolife.savvy.ui.components.Kicker
import com.spirolife.savvy.ui.components.LoadingBox
import com.spirolife.savvy.ui.components.PillButton
import com.spirolife.savvy.ui.components.PillOutlineButton
import com.spirolife.savvy.ui.components.SavvyCard
import com.spirolife.savvy.ui.components.ScreenTitle
import com.spirolife.savvy.ui.theme.PillShape
import com.spirolife.savvy.ui.theme.SavvyTheme
import com.spirolife.savvy.ui.today.parseTime
import java.time.Duration
import java.time.OffsetDateTime

/** OWNER: today-plan-diary agent. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PlanScreen(nav: NavController, vm: PlanViewModel = viewModel()) {
    val state by vm.state.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    var progressFor by remember { mutableStateOf<Goal?>(null) }
    var addingMeal by rememberSaveable { mutableStateOf(false) }

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
            val plan = state.plan
            when {
                plan != null -> PullToRefreshBox(isRefreshing = state.refreshing, onRefresh = vm::refresh,
                    modifier = Modifier.fillMaxSize()) {
                    PlanContent(plan, onLog = { vm.clearDialogError(); progressFor = it },
                        onAddMeal = { vm.clearDialogError(); addingMeal = true },
                        onRemoveMeal = vm::deleteMeal, onProject = vm::openProject)
                }
                state.loading -> LoadingBox()
                else -> Column(Modifier.fillMaxSize().padding(16.dp)) {
                    ScreenTitle("Plan")
                    ErrorBox(state.error ?: "Something went wrong", onRetry = vm::load)
                }
            }
        }
    }

    progressFor?.let { goal ->
        ProgressDialog(goal, state.saving, state.dialogError, onDismiss = { progressFor = null }) { note, value ->
            vm.logProgress(goal, note, value) { progressFor = null }
        }
    }
    if (addingMeal) {
        MealDialog(state.plan?.meals?.diet, state.saving, state.dialogError, onDismiss = { addingMeal = false }) { main, kind, sides ->
            vm.addMeal(main, kind, sides) { addingMeal = false }
        }
    }
    state.sheet?.let { sheet ->
        ModalBottomSheet(onDismissRequest = vm::closeProject, sheetState = rememberModalBottomSheetState(),
            containerColor = SavvyTheme.colors.card) {
            ProjectTasks(sheet)
        }
    }
}

@Composable
private fun PlanContent(
    plan: Plan,
    onLog: (Goal) -> Unit,
    onAddMeal: () -> Unit,
    onRemoveMeal: (Long) -> Unit,
    onProject: (Project) -> Unit,
) {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { ScreenTitle("Plan", Modifier.semantics { heading() }) }

        item { Section("Goals") }
        if (plan.goals.isEmpty()) item { Muted("No active goals. Tell Savvy about one in Chat.") }
        items(plan.goals, key = { "g${it.id}" }) { GoalCard(it, onLog) }

        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Section("Meals this week", Modifier.weight(1f))
                plan.meals.diet?.takeIf { it.isNotBlank() }?.let { DietChip(it) }
            }
        }
        item { MealGrid(plan.meals, onRemoveMeal) }
        item { PillOutlineButton("Add a meal", onAddMeal, Modifier.fillMaxWidth()) }

        item { Section("Todoist projects") }
        if (plan.projects.isEmpty()) item { Muted("No projects (or Todoist isn't connected).") }
        else item {
            SavvyCard(contentPadding = PaddingValues(vertical = 4.dp)) {
                plan.projects.forEachIndexed { i, p ->
                    if (i > 0) HorizontalDivider(color = SavvyTheme.colors.line)
                    ProjectRow(p, onProject)
                }
            }
        }
    }
}

@Composable
private fun Section(text: String, modifier: Modifier = Modifier) {
    Text(text, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.titleMedium,
        modifier = modifier.padding(top = 4.dp).semantics { heading() })
}

@Composable
private fun Muted(text: String) = Text(text, color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodyMedium)

@Composable
private fun DietChip(diet: String) {
    Text(diet, color = SavvyTheme.colors.deep, style = MaterialTheme.typography.labelMedium,
        modifier = Modifier.padding(start = 8.dp)
            .background(SavvyTheme.colors.band, PillShape)
            .padding(horizontal = 10.dp, vertical = 4.dp))
}

@Composable
private fun GoalCard(goal: Goal, onLog: (Goal) -> Unit) {
    SavvyCard(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(goal.title, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.titleSmall)
                goal.target?.let { Text(it, color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall) }
            }
            TextButton(onClick = { onLog(goal) }, modifier = Modifier.heightIn(min = 48.dp)) {
                Text("Log progress", color = SavvyTheme.colors.accent, style = MaterialTheme.typography.labelMedium)
            }
        }
        val fraction = goalFraction(goal)
        if (fraction != null) {
            LinearProgressIndicator(
                progress = { fraction },
                modifier = Modifier.fillMaxWidth().semantics(mergeDescendants = true) {},
                color = SavvyTheme.colors.accent,
                trackColor = SavvyTheme.colors.band,
                drawStopIndicator = {},
            )
        }
        val latest = listOfNotNull(
            goal.progressNote?.takeIf { fraction == null || it.isNotBlank() },
            goal.lastLogged?.let { "Last logged ${ago(it)}" },
        ).joinToString(" · ")
        Text(latest.ifEmpty { "Nothing logged yet" }, color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
    }
}

private fun ago(iso: String): String {
    val then = parseTime(iso) ?: return iso
    val days = Duration.between(then, OffsetDateTime.now()).toDays()
    return when {
        days < 1 -> "today"
        days == 1L -> "yesterday"
        days < 14 -> "$days days ago"
        days < 60 -> "${days / 7} weeks ago"
        else -> "${days / 30} months ago"
    }
}

@Composable
private fun MealGrid(week: MealWeek, onRemove: (Long) -> Unit) {
    if (week.items.isEmpty()) {
        Muted("Nothing planned this week yet.")
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        week.items.chunked(2).forEach { pair ->
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                pair.forEach { MealCard(it, onRemove, Modifier.weight(1f)) }
                if (pair.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun MealCard(meal: Meal, onRemove: (Long) -> Unit, modifier: Modifier) {
    SavvyCard(modifier, contentPadding = PaddingValues(start = 14.dp, top = 10.dp, bottom = 12.dp, end = 0.dp)) {
        Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f).padding(top = 4.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                if (meal.kind == "drink") Kicker("Drink")
                Text(meal.main, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.titleSmall)
                if (meal.sides.isNotEmpty()) {
                    Text(meal.sides.joinToString(" · "), color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
                }
            }
            IconButton(onClick = { onRemove(meal.id) }, modifier = Modifier.size(48.dp)) {
                Icon(Icons.Outlined.Close, contentDescription = "Remove ${meal.main}", tint = SavvyTheme.colors.muted,
                    modifier = Modifier.size(18.dp))
            }
        }
    }
}

@Composable
private fun ProjectRow(p: Project, onOpen: (Project) -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 52.dp)
            .clickable(role = Role.Button, onClickLabel = "Show tasks") { onOpen(p) }
            .padding(horizontal = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(p.name, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
        Text("${p.openCount} open", color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
        Icon(Icons.AutoMirrored.Outlined.KeyboardArrowRight, contentDescription = null, tint = SavvyTheme.colors.muted)
    }
}

@Composable
private fun ProjectTasks(sheet: ProjectSheet) {
    Column(Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, bottom = 32.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(sheet.project.name, color = SavvyTheme.colors.deep, style = MaterialTheme.typography.headlineSmall,
            modifier = Modifier.semantics { heading() })
        when {
            sheet.error != null -> Muted(sheet.error)
            sheet.tasks == null -> Box(Modifier.fillMaxWidth().heightIn(min = 120.dp)) { LoadingBox() }
            sheet.tasks.isEmpty() -> Muted("No open tasks.")
            else -> LazyColumn(Modifier.heightIn(max = 480.dp)) {
                items(sheet.tasks, key = { it.id }) { t ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Text(t.title, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyLarge,
                            modifier = Modifier.weight(1f))
                        if (t.priority in 1..3) Kicker("P${t.priority}", color = SavvyTheme.colors.accent)
                    }
                    HorizontalDivider(color = SavvyTheme.colors.line)
                }
            }
        }
    }
}

@Composable
private fun ProgressDialog(goal: Goal, saving: Boolean, error: String?, onDismiss: () -> Unit, onSave: (String, String) -> Unit) {
    var note by rememberSaveable { mutableStateOf("") }
    var value by rememberSaveable { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = SavvyTheme.colors.card,
        title = { Text("Log progress", color = SavvyTheme.colors.deep) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(goal.title, color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodyMedium)
                OutlinedTextField(note, { note = it }, label = { Text("What did you do?") }, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value, { value = it }, label = { Text("Measurement (optional)") },
                    placeholder = { Text("e.g. 3 pull-ups") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                error?.let { Text(it, color = SavvyTheme.colors.warn, style = MaterialTheme.typography.bodySmall) }
            }
        },
        confirmButton = { PillButton("Save", { onSave(note, value) }, enabled = note.isNotBlank() && !saving) },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = SavvyTheme.colors.ink) } },
    )
}

@Composable
private fun MealDialog(diet: String?, saving: Boolean, error: String?, onDismiss: () -> Unit,
                       onSave: (String, String, List<String>) -> Unit) {
    var main by rememberSaveable { mutableStateOf("") }
    var kind by rememberSaveable { mutableStateOf("meal") }
    var side1 by rememberSaveable { mutableStateOf("") }
    var side2 by rememberSaveable { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = SavvyTheme.colors.card,
        title = { Text("Add a meal", color = SavvyTheme.colors.deep) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("meal" to "Meal", "drink" to "Drink").forEach { (k, label) ->
                        FilterChip(selected = kind == k, onClick = { kind = k }, label = { Text(label) },
                            modifier = Modifier.heightIn(min = 48.dp),
                            colors = FilterChipDefaults.filterChipColors(selectedContainerColor = SavvyTheme.colors.band,
                                selectedLabelColor = SavvyTheme.colors.deep))
                    }
                }
                OutlinedTextField(main, { main = it }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                    label = { Text(if (kind == "drink") "Drink" else "Main dish") })
                if (kind == "meal") {
                    OutlinedTextField(side1, { side1 = it }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                        label = { Text("Side") })
                    OutlinedTextField(side2, { side2 = it }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                        label = { Text("Second side (optional)") })
                }
                diet?.takeIf { it.isNotBlank() }?.let {
                    Text("Diet this week: $it", color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
                }
                error?.let { Text(it, color = SavvyTheme.colors.warn, style = MaterialTheme.typography.bodySmall) }
            }
        },
        confirmButton = {
            PillButton("Add", { onSave(main, kind, if (kind == "meal") listOf(side1, side2) else emptyList()) },
                enabled = main.isNotBlank() && !saving)
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = SavvyTheme.colors.ink) } },
    )
}
