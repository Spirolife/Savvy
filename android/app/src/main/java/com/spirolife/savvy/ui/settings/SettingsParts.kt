package com.spirolife.savvy.ui.settings

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.TimePickerDefaults
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.toMutableStateList
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.spirolife.savvy.ui.components.Kicker
import com.spirolife.savvy.ui.components.SavvyCard
import com.spirolife.savvy.ui.theme.SavvyTheme

/** A titled group of rows in one card, as in Settings.dc.html. */
@Composable
internal fun SettingsGroup(title: String, content: @Composable () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Kicker(title, Modifier.padding(horizontal = 4.dp), color = SavvyTheme.colors.muted)
        SavvyCard(Modifier.fillMaxWidth(), contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)) {
            Column { content() }
        }
    }
}

/** Label on the left, value on the right; tappable when [onClick] is set. */
@Composable
internal fun SettingsRow(label: String, value: String, valueColor: Color = SavvyTheme.colors.muted, onClick: (() -> Unit)? = null) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
            .padding(horizontal = 14.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        Text(value, color = valueColor, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.End)
    }
    RowDivider()
}

@Composable
internal fun SwitchRow(label: String, checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .toggleable(value = checked, enabled = enabled, role = Role.Switch, onValueChange = onChange)
            .padding(horizontal = 14.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        Switch(
            checked = checked, onCheckedChange = null, enabled = enabled,
            colors = SwitchDefaults.colors(
                checkedTrackColor = SavvyTheme.colors.accent, checkedThumbColor = SavvyTheme.colors.accentInk,
                uncheckedTrackColor = SavvyTheme.colors.band, uncheckedBorderColor = SavvyTheme.colors.line,
                uncheckedThumbColor = SavvyTheme.colors.muted,
            ),
        )
    }
    RowDivider()
}

@Composable
private fun RowDivider() = HorizontalDivider(color = SavvyTheme.colors.band, thickness = 1.dp)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun TimeDialog(
    title: String,
    hour: Int,
    minute: Int,
    hourOnly: Boolean = false,
    onDismiss: () -> Unit,
    onConfirm: (hour: Int, minute: Int) -> Unit,
) {
    val picker = rememberTimePickerState(initialHour = hour, initialMinute = minute, is24Hour = true)
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = SavvyTheme.colors.card,
        title = { Text(title, color = SavvyTheme.colors.deep) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (hourOnly) Text("Only the hour is used.", color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
                TimePicker(
                    state = picker,
                    colors = TimePickerDefaults.colors(
                        clockDialColor = SavvyTheme.colors.band,
                        selectorColor = SavvyTheme.colors.accent,
                        timeSelectorSelectedContainerColor = SavvyTheme.colors.band,
                        timeSelectorSelectedContentColor = SavvyTheme.colors.deep,
                        timeSelectorUnselectedContainerColor = SavvyTheme.colors.bg,
                        timeSelectorUnselectedContentColor = SavvyTheme.colors.ink,
                    ),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(picker.hour, if (hourOnly) 0 else picker.minute) }) {
                Text("Save", color = SavvyTheme.colors.accent)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = SavvyTheme.colors.ink) } },
    )
}

@Composable
internal fun DayDialog(selected: String, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = SavvyTheme.colors.card,
        title = { Text("Weekly review day", color = SavvyTheme.colors.deep) },
        text = {
            Column {
                WEEK_DAYS.forEach { day ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .heightIn(min = 48.dp)
                            .selectable(selected = day == selected, role = Role.RadioButton, onClick = { onPick(day) }),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(
                            selected = day == selected, onClick = null,
                            colors = RadioButtonDefaults.colors(selectedColor = SavvyTheme.colors.accent),
                        )
                        Text(day, color = SavvyTheme.colors.ink, modifier = Modifier.padding(start = 12.dp))
                    }
                }
            }
        },
        confirmButton = {},
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = SavvyTheme.colors.ink) } },
    )
}

/** Edit the list of check-in times: tap one to change it, x to remove, + to add. */
@Composable
internal fun CheckinsDialog(times: List<String>, onDismiss: () -> Unit, onSave: (List<String>) -> Unit) {
    val list = remember { times.toMutableStateList() }
    var editing by remember { mutableStateOf<Int?>(null) } // index, or -1 for a new one
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = SavvyTheme.colors.card,
        title = { Text("Check-ins", color = SavvyTheme.colors.deep) },
        text = {
            Column {
                if (list.isEmpty()) Text("No check-ins.", color = SavvyTheme.colors.muted)
                list.forEachIndexed { i, t ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                        TextButton(onClick = { editing = i }, modifier = Modifier.weight(1f)) {
                            Text(displayTime(t), color = SavvyTheme.colors.ink, style = MaterialTheme.typography.titleMedium,
                                modifier = Modifier.fillMaxWidth())
                        }
                        IconButton(onClick = { list.removeAt(i) }) {
                            Icon(Icons.Outlined.Close, contentDescription = "Remove ${displayTime(t)} check-in", tint = SavvyTheme.colors.muted)
                        }
                    }
                }
                TextButton(onClick = { editing = -1 }, modifier = Modifier.heightIn(min = 48.dp)) {
                    Icon(Icons.Outlined.Add, contentDescription = null, tint = SavvyTheme.colors.accent)
                    Text("Add a check-in", color = SavvyTheme.colors.accent, modifier = Modifier.padding(start = 6.dp))
                }
            }
        },
        confirmButton = { TextButton(onClick = { onSave(list.toList()) }) { Text("Save", color = SavvyTheme.colors.accent) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = SavvyTheme.colors.ink) } },
    )
    editing?.let { index ->
        val (h, m) = parseTime(list.getOrNull(index) ?: "12:00")
        TimeDialog(
            title = if (index < 0) "New check-in" else "Check-in",
            hour = h, minute = m,
            onDismiss = { editing = null },
            onConfirm = { nh, nm ->
                val value = wireTime(nh, nm)
                if (index < 0) list.add(value) else list[index] = value
                list.sort()
                editing = null
            },
        )
    }
}
