package com.spirolife.savvy.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.delay
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavController
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.ChatEvent
import com.spirolife.savvy.ui.components.ErrorBox
import com.spirolife.savvy.ui.components.Kicker
import com.spirolife.savvy.ui.components.PillButton
import com.spirolife.savvy.ui.components.PillOutlineButton
import com.spirolife.savvy.ui.theme.CardShape
import com.spirolife.savvy.ui.theme.PillShape
import com.spirolife.savvy.ui.theme.SavvyFonts
import com.spirolife.savvy.ui.theme.SavvyTheme

/** OWNER: chat-inbox agent. */
@Composable
fun ChatScreen(nav: NavController) {
    val vm: ChatViewModel = viewModel { ChatViewModel(Graph.api) }
    val state by vm.state.collectAsStateWithLifecycle()
    val prefill by ChatPrefill.text.collectAsStateWithLifecycle()

    LaunchedEffect(prefill) {
        if (prefill != null) ChatPrefill.take()?.let(vm::setDraft)
    }

    // Pick up what was said on the desktop or Signal: on every return to the
    // app, then every 30 s while it's on screen (never in the background).
    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(lifecycleOwner) {
        lifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                vm.catchUp()
                delay(30_000)
            }
        }
    }

    ChatContent(
        state = state,
        onDraft = vm::setDraft,
        onSend = vm::send,
        onRetry = vm::retry,
        onRetryHistory = vm::loadHistory,
    )

    state.confirm?.let { ConfirmSheet(it, onAnswer = vm::answer) }
}

@Composable
private fun ChatContent(
    state: ChatUiState,
    onDraft: (String) -> Unit,
    onSend: () -> Unit,
    onRetry: () -> Unit,
    onRetryHistory: () -> Unit,
) {
    val c = SavvyTheme.colors
    val list = rememberLazyListState()
    val last = state.items.lastOrNull()
    val lastLength = (last as? ChatItem.Assistant)?.text?.length ?: 0

    // Follow the conversation as it grows and as text streams in.
    LaunchedEffect(state.items.size, lastLength, state.error, state.running) {
        val count = list.layoutInfo.totalItemsCount
        if (count > 0) list.animateScrollToItem(count - 1)
    }

    Column(Modifier.fillMaxSize().background(c.bg).imePadding()) {
        Header()
        Box(Modifier.weight(1f).fillMaxWidth()) {
            when {
                state.loadingHistory && state.items.isEmpty() ->
                    CircularProgressIndicator(Modifier.align(Alignment.Center), color = c.accent)
                else -> LazyColumn(
                    state = list,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    state.historyError?.let { msg ->
                        item(key = "history-error") { ErrorBox("Couldn't load earlier messages. $msg", onRetryHistory) }
                    }
                    if (state.items.isEmpty() && state.historyError == null) {
                        item(key = "empty") {
                            Text("Ask about your day, your mail or your plans.", color = c.muted,
                                style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 24.dp))
                        }
                    }
                    items(state.items, key = { it.key }) { item ->
                        when (item) {
                            is ChatItem.User -> UserBubble(item.text)
                            is ChatItem.Assistant -> AssistantBubble(item.text)
                            is ChatItem.ToolCard -> ToolCardRow(item)
                        }
                    }
                    if (state.running && (state.reconnecting || last !is ChatItem.Assistant)) {
                        item(key = "thinking") { Thinking(state.reconnecting) }
                    }
                    state.error?.let { msg ->
                        item(key = "error") { ErrorRow(msg, canRetry = state.retryText != null && !state.running, onRetry) }
                    }
                }
            }
        }
        InputRow(state.draft, enabled = !state.running, onDraft, onSend)
    }
}

@Composable
private fun Header() {
    val c = SavvyTheme.colors
    Row(
        Modifier.fillMaxWidth().statusBarsPadding().padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(Modifier.size(40.dp).background(c.accent, PillShape), contentAlignment = Alignment.Center) {
            Text("S", color = c.accentInk, fontFamily = SavvyFonts.display, fontWeight = FontWeight.Bold)
        }
        Column {
            Text("Savvy", color = c.ink, style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.semantics { heading() })
            Text("Calendar, email, tasks and memory", color = c.muted, style = MaterialTheme.typography.bodySmall)
        }
    }
    Box(Modifier.fillMaxWidth().heightIn(min = 1.dp).background(c.line))
}

@Composable
private fun UserBubble(text: String) {
    val c = SavvyTheme.colors
    Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
        Text(
            text,
            color = c.bg,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier
                .widthIn(max = 300.dp)
                .background(c.deep, RoundedCornerShape(18.dp, 18.dp, 4.dp, 18.dp))
                .padding(horizontal = 14.dp, vertical = 10.dp),
        )
    }
}

@Composable
private fun AssistantBubble(text: String) {
    val c = SavvyTheme.colors
    val rendered = remember(text, c) { Markdown.render(text, MarkdownColors(c.accent, c.band), SavvyFonts.mono) }
    Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterStart) {
        Text(
            rendered,
            color = c.ink,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier
                .widthIn(max = 340.dp)
                .background(c.card, RoundedCornerShape(18.dp, 18.dp, 18.dp, 4.dp))
                .padding(horizontal = 14.dp, vertical = 10.dp),
        )
    }
}

@Composable
private fun ToolCardRow(item: ChatItem.ToolCard) {
    val c = SavvyTheme.colors
    val bad = item.state == "error" || item.state == "denied"
    Column(
        Modifier.widthIn(max = 340.dp).background(c.card, RoundedCornerShape(10.dp)).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                item.state.uppercase(),
                color = if (bad) c.warn else c.deep,
                style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.background(if (bad) c.warnBg else c.band, RoundedCornerShape(4.dp))
                    .padding(horizontal = 6.dp, vertical = 2.dp),
            )
            val detail = if (item.detail.isBlank()) item.name else "${item.name} → ${item.detail}"
            Text(detail, color = c.muted, fontFamily = SavvyFonts.mono, style = MaterialTheme.typography.labelSmall)
        }
        item.warning?.takeIf { it.isNotBlank() }?.let {
            Text("⚠ $it", color = c.warn, fontFamily = SavvyFonts.mono, style = MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
private fun Thinking(reconnecting: Boolean) {
    Text(if (reconnecting) "Reconnecting to your PC…" else "Savvy is thinking…", color = SavvyTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun ErrorRow(message: String, canRetry: Boolean, onRetry: () -> Unit) {
    val c = SavvyTheme.colors
    Row(
        Modifier.fillMaxWidth().background(c.warnBg, CardShape).border(1.dp, c.warnLine, CardShape)
            .padding(start = 14.dp, end = 6.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(message, color = c.warn, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
        if (canRetry) {
            TextButton(onClick = onRetry, modifier = Modifier.heightIn(min = 48.dp)) {
                Text("Retry", color = c.warn, style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

@Composable
private fun InputRow(draft: String, enabled: Boolean, onDraft: (String) -> Unit, onSend: () -> Unit) {
    val c = SavvyTheme.colors
    val canSend = enabled && draft.isNotBlank()
    Row(
        Modifier.fillMaxWidth().background(c.bg).padding(start = 12.dp, end = 12.dp, top = 10.dp, bottom = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        OutlinedTextField(
            value = draft,
            onValueChange = onDraft,
            modifier = Modifier.weight(1f).heightIn(min = 48.dp).semantics { contentDescription = "Message Savvy" },
            placeholder = { Text("Message Savvy") },
            shape = RoundedCornerShape(24.dp),
            maxLines = 5,
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { if (canSend) onSend() }),
            colors = OutlinedTextFieldDefaults.colors(
                focusedContainerColor = c.card, unfocusedContainerColor = c.card,
                focusedBorderColor = c.accent, unfocusedBorderColor = c.line,
                focusedTextColor = c.ink, unfocusedTextColor = c.ink, cursorColor = c.accent,
                focusedPlaceholderColor = c.muted, unfocusedPlaceholderColor = c.muted,
            ),
        )
        FilledIconButton(
            onClick = onSend,
            enabled = canSend,
            modifier = Modifier.size(48.dp),
            colors = IconButtonDefaults.filledIconButtonColors(
                containerColor = c.accent, contentColor = c.accentInk,
                disabledContainerColor = c.band, disabledContentColor = c.muted,
            ),
        ) {
            if (!enabled) CircularProgressIndicator(Modifier.size(20.dp), color = c.muted, strokeWidth = 2.dp)
            else Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send")
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ConfirmSheet(confirm: ChatEvent.Confirm, onAnswer: (Boolean) -> Unit) {
    val c = SavvyTheme.colors
    val copy = remember(confirm) { confirmCopy(confirm.name, confirm.level, confirm.args) }
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(
        onDismissRequest = { onAnswer(false) },     // swiping it away is a "no"
        sheetState = sheet,
        containerColor = c.card,
    ) {
        Column(
            Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Kicker(copy.kicker, color = c.warn)
            Text(copy.title, color = c.ink, style = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.semantics { heading() })
            if (copy.fields.isNotEmpty() || copy.body != null) {
                Column(
                    Modifier.fillMaxWidth().background(c.bg, RoundedCornerShape(12.dp)).padding(14.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    copy.fields.forEach { (k, v) ->
                        Text(
                            androidx.compose.ui.text.buildAnnotatedString {
                                pushStyle(androidx.compose.ui.text.SpanStyle(fontWeight = FontWeight.Bold))
                                append(k)
                                pop()
                                append("  ")
                                append(v)
                            },
                            color = c.ink, style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    copy.body?.let { Text(it, color = c.muted, style = MaterialTheme.typography.bodyMedium) }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                PillOutlineButton(copy.denyLabel, onClick = { onAnswer(false) }, modifier = Modifier.weight(1f))
                PillButton(copy.allowLabel, onClick = { onAnswer(true) }, modifier = Modifier.weight(1f))
            }
        }
    }
}
