package com.spirolife.savvy.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.spirolife.savvy.data.api.Fact
import com.spirolife.savvy.ui.components.ErrorBox
import com.spirolife.savvy.ui.components.Kicker
import com.spirolife.savvy.ui.components.LoadingBox
import com.spirolife.savvy.ui.components.SavvyCard
import com.spirolife.savvy.ui.pair.fieldColors
import com.spirolife.savvy.ui.theme.SavvyTheme

/** What Savvy remembers about you: search it, and delete what's wrong. */
@Composable
internal fun MemoryPane(state: MemoryUiState, onQuery: (String) -> Unit, onRetry: () -> Unit, onDelete: (Fact) -> Unit) {
    var confirm by remember { mutableStateOf<Fact?>(null) }
    LaunchedEffect(Unit) { onRetry() }

    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedTextField(
            value = state.query,
            onValueChange = onQuery,
            placeholder = { Text("Search facts and rules") },
            leadingIcon = { Icon(Icons.Outlined.Search, contentDescription = null, tint = SavvyTheme.colors.muted) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            colors = fieldColors(),
            modifier = Modifier.fillMaxWidth(),
        )
        when {
            state.loading -> LoadingBox()
            state.error != null -> ErrorBox(state.error, onRetry = onRetry)
            state.facts.isEmpty() -> Text(
                if (state.query.isBlank()) "Savvy hasn't saved any facts yet." else "Nothing matches \"${state.query}\".",
                color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodyMedium,
            )
            else -> LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(bottom = 24.dp)) {
                items(state.facts, key = { it.id }) { fact -> FactCard(fact, onDelete = { confirm = fact }) }
            }
        }
    }

    confirm?.let { fact ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            containerColor = SavvyTheme.colors.card,
            title = { Text("Forget this?", color = SavvyTheme.colors.deep) },
            text = { Text(fact.fact, color = SavvyTheme.colors.ink) },
            confirmButton = {
                TextButton(onClick = { onDelete(fact); confirm = null }) { Text("Delete", color = SavvyTheme.colors.warn) }
            },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("Keep", color = SavvyTheme.colors.ink) } },
        )
    }
}

@Composable
private fun FactCard(fact: Fact, onDelete: () -> Unit) {
    SavvyCard(Modifier.fillMaxWidth(), contentPadding = PaddingValues(start = 14.dp, top = 6.dp, bottom = 10.dp, end = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Kicker(listOfNotNull(fact.type.ifBlank { null }, fact.updated?.take(10)).joinToString(" · ").ifEmpty { "fact" },
                Modifier.weight(1f))
            IconButton(onClick = onDelete, modifier = Modifier.heightIn(min = 48.dp)) {
                Icon(Icons.Outlined.Delete, contentDescription = "Delete fact", tint = SavvyTheme.colors.muted)
            }
        }
        Text(fact.fact, color = SavvyTheme.colors.ink, style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.fillMaxWidth())
    }
}
