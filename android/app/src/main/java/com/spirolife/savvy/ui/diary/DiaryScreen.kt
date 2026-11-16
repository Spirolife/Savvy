package com.spirolife.savvy.ui.diary

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavController
import com.spirolife.savvy.data.api.DiaryEntry
import com.spirolife.savvy.data.api.NoteSummary
import com.spirolife.savvy.ui.components.ErrorBox
import com.spirolife.savvy.ui.components.Kicker
import com.spirolife.savvy.ui.components.LoadingBox
import com.spirolife.savvy.ui.components.PillButton
import com.spirolife.savvy.ui.components.PillOutlineButton
import com.spirolife.savvy.ui.components.SavvyCard
import com.spirolife.savvy.ui.components.ScreenTitle
import com.spirolife.savvy.ui.theme.SavvyTheme
import com.spirolife.savvy.ui.today.parseTime
import com.spirolife.savvy.ui.today.rememberTimeFormatter
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** OWNER: today-plan-diary agent. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DiaryScreen(nav: NavController, vm: DiaryViewModel = viewModel()) {
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
        Column(Modifier.padding(padding).fillMaxSize()) {
            Column(Modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 8.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                ScreenTitle("Diary", Modifier.semantics { heading() })
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                    DiaryTab.entries.forEachIndexed { i, tab ->
                        SegmentedButton(
                            selected = state.tab == tab,
                            onClick = { vm.selectTab(tab) },
                            shape = SegmentedButtonDefaults.itemShape(i, DiaryTab.entries.size),
                            modifier = Modifier.heightIn(min = 48.dp),
                            colors = SegmentedButtonDefaults.colors(
                                activeContainerColor = SavvyTheme.colors.band,
                                activeContentColor = SavvyTheme.colors.deep,
                                inactiveContainerColor = SavvyTheme.colors.card,
                                inactiveContentColor = SavvyTheme.colors.muted,
                                activeBorderColor = SavvyTheme.colors.line,
                                inactiveBorderColor = SavvyTheme.colors.line,
                            ),
                        ) { Text(tab.name) }
                    }
                }
            }
            PullToRefreshBox(isRefreshing = state.refreshing, onRefresh = vm::refresh, modifier = Modifier.fillMaxSize()) {
                when (state.tab) {
                    DiaryTab.Entries -> EntriesTab(state, vm)
                    DiaryTab.Notes -> NotesTab(state, vm)
                }
            }
        }
    }

    state.sheet?.let { sheet ->
        ModalBottomSheet(onDismissRequest = vm::closeNote, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
            containerColor = SavvyTheme.colors.card) {
            Column(Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, bottom = 32.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(sheet.name, color = SavvyTheme.colors.deep, style = MaterialTheme.typography.headlineSmall,
                    modifier = Modifier.semantics { heading() })
                when {
                    sheet.error != null -> Text(sheet.error, color = SavvyTheme.colors.muted)
                    sheet.note == null -> Box(Modifier.fillMaxWidth().heightIn(min = 120.dp)) { LoadingBox() }
                    else -> Text(sheet.note.text.ifBlank { "(empty)" }, color = SavvyTheme.colors.ink,
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.heightIn(max = 560.dp).verticalScroll(rememberScrollState()))
                }
            }
        }
    }
}

@Composable
private fun EntriesTab(state: DiaryUiState, vm: DiaryViewModel) {
    val time = rememberTimeFormatter()
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { PromptCard(state, vm) }
        val entries = state.entries
        when {
            entries == null && state.entriesError != null ->
                item { ErrorBox(state.entriesError, onRetry = vm::refresh) }
            entries == null -> item { Box(Modifier.fillMaxWidth().heightIn(min = 160.dp)) { LoadingBox() } }
            entries.isEmpty() -> item {
                Text("No entries in the last two weeks.", color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodyMedium)
            }
            else -> items(entries, key = { it.id }) { EntryCard(it, time) }
        }
    }
}

@Composable
private fun PromptCard(state: DiaryUiState, vm: DiaryViewModel) {
    val p = state.prompt
    SavvyCard(Modifier.fillMaxWidth()) {
        Kicker(p.kicker)
        Text(p.question, color = SavvyTheme.colors.deep, style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.semantics { heading() })
        OutlinedTextField(
            value = state.draft,
            onValueChange = vm::setDraft,
            label = { Text(p.fieldLabel) },
            minLines = 3,
            modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            PillButton("Save", vm::save, enabled = state.draft.isNotBlank() && !state.saving)
            PillOutlineButton("It was a bad day", vm::badDay, enabled = !state.saving)
        }
    }
}

private data class EntryStyle(val label: String, val color: @Composable () -> Color)

private fun styleFor(type: String) = when (type) {
    "bod" -> EntryStyle("Morning plan") { SavvyTheme.colors.deep }
    "eod" -> EntryStyle("Evening reflection") { SavvyTheme.colors.accent }
    "bad_day" -> EntryStyle("Bad day") { SavvyTheme.colors.warn }
    else -> EntryStyle("Entry") { SavvyTheme.colors.muted }
}

@Composable
private fun EntryCard(e: DiaryEntry, time: (String) -> String) {
    val style = styleFor(e.type)
    SavvyCard(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Kicker(style.label, Modifier.weight(1f), color = style.color())
            Text(whenLabel(e.ts, time), color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
        }
        Text(e.text, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyMedium)
    }
}

/** "Today 8:34", "Yesterday 22:10", "Thursday", "Nov 3". */
private fun whenLabel(ts: String, time: (String) -> String): String {
    val at = parseTime(ts)?.atZoneSameInstant(ZoneId.systemDefault()) ?: return ts
    val days = LocalDate.now().toEpochDay() - at.toLocalDate().toEpochDay()
    return when {
        days == 0L -> "Today ${time(ts)}"
        days == 1L -> "Yesterday ${time(ts)}"
        days in 2..6 -> at.format(DateTimeFormatter.ofPattern("EEEE", Locale.getDefault()))
        else -> at.format(DateTimeFormatter.ofPattern("MMM d", Locale.getDefault()))
    }
}

@Composable
private fun NotesTab(state: DiaryUiState, vm: DiaryViewModel) {
    val notes = state.notes
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 24.dp),
    ) {
        when {
            notes == null && state.notesError != null -> item { ErrorBox(state.notesError, onRetry = vm::refresh) }
            notes == null -> item { Box(Modifier.fillMaxWidth().heightIn(min = 160.dp)) { LoadingBox() } }
            notes.isEmpty() -> item {
                Text("No notes yet. Ask Savvy to save one.", color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodyMedium)
            }
            else -> item {
                SavvyCard(contentPadding = PaddingValues(vertical = 4.dp)) {
                    notes.forEachIndexed { i, n ->
                        if (i > 0) HorizontalDivider(color = SavvyTheme.colors.line)
                        NoteRow(n) { vm.openNote(n.name) }
                    }
                }
            }
        }
    }
}

@Composable
private fun NoteRow(n: NoteSummary, onOpen: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 52.dp)
            .clickable(role = Role.Button, onClickLabel = "Open note", onClick = onOpen)
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(n.name, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyLarge)
            n.updated?.let { u ->
                parseTime(u)?.let {
                    Text("Updated ${it.atZoneSameInstant(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("MMM d", Locale.getDefault()))}",
                        color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        Icon(Icons.AutoMirrored.Outlined.KeyboardArrowRight, contentDescription = null, tint = SavvyTheme.colors.muted)
    }
}
