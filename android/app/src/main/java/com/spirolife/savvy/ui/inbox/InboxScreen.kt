package com.spirolife.savvy.ui.inbox

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.Clear
import androidx.compose.material.icons.outlined.MarkEmailRead
import androidx.compose.material.icons.outlined.MarkEmailUnread
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Star
import androidx.compose.material.icons.outlined.StarOutline
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavController
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.Mail
import com.spirolife.savvy.ui.components.ErrorBox
import com.spirolife.savvy.ui.components.Kicker
import com.spirolife.savvy.ui.components.LoadingBox
import com.spirolife.savvy.ui.components.SavvyCard
import com.spirolife.savvy.ui.components.ScreenTitle
import com.spirolife.savvy.ui.nav.Routes
import com.spirolife.savvy.ui.theme.PillShape
import com.spirolife.savvy.ui.theme.SavvyTheme

/** OWNER: chat-inbox agent. */
@Composable
fun InboxScreen(nav: NavController) {
    val vm: InboxViewModel = viewModel { InboxViewModel(Graph.api) }
    val state by vm.state.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(state.notice) {
        val notice = state.notice ?: return@LaunchedEffect
        val result = snackbar.showSnackbar(
            notice.text,
            actionLabel = if (notice.undo != null) "Undo" else null,
            duration = SnackbarDuration.Short,
        )
        vm.noticeShown(notice)
        if (result == SnackbarResult.ActionPerformed) vm.undo(notice)
    }

    Scaffold(
        containerColor = SavvyTheme.colors.bg,
        snackbarHost = { SnackbarHost(snackbar) },
        contentWindowInsets = androidx.compose.foundation.layout.WindowInsets(0),
    ) { padding ->
        InboxContent(
            state = state,
            modifier = Modifier.padding(padding),
            onQuery = vm::setQuery,
            onSearch = vm::searchNow,
            onAccount = vm::selectAccount,
            onRefresh = vm::load,
            onOpen = { nav.navigate(Routes.thread(it.account, it.threadId)) },
            onAction = vm::act,
        )
    }
}

@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun InboxContent(
    state: InboxUiState,
    modifier: Modifier,
    onQuery: (String) -> Unit,
    onSearch: () -> Unit,
    onAccount: (String) -> Unit,
    onRefresh: () -> Unit,
    onOpen: (Mail) -> Unit,
    onAction: (Mail, String) -> Unit,
) {
    val c = SavvyTheme.colors
    Column(modifier.fillMaxSize().background(c.bg).statusBarsPadding()) {
        Column(Modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            ScreenTitle("Inbox")
            SearchField(state.query, onQuery, onSearch)
            AccountChips(state, onAccount)
        }
        PullToRefreshBox(
            isRefreshing = state.loading && state.messages.isNotEmpty(),
            onRefresh = onRefresh,
            modifier = Modifier.weight(1f).fillMaxWidth(),
        ) {
            when {
                state.loading && state.messages.isEmpty() -> LoadingBox()
                state.error != null && state.messages.isEmpty() -> ErrorBox(state.error, onRefresh)
                else -> LazyColumn(
                    Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(0.dp),
                ) {
                    state.summary?.let { summary ->
                        item(key = "summary") {
                            SavvyCard(Modifier.fillMaxWidth().padding(bottom = 12.dp)) {
                                Kicker("Savvy's summary")
                                Text(summary, color = c.ink, style = MaterialTheme.typography.bodyMedium)
                            }
                        }
                    }
                    if (state.messages.isEmpty()) {
                        item(key = "empty") {
                            Text(if (state.query.isBlank()) "Nothing in your inbox." else "No mail matches that search.",
                                color = c.muted, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(vertical = 24.dp))
                        }
                    }
                    items(state.messages, key = { "${it.account}/${it.id}" }) { mail ->
                        SwipeRow(mail, state.accounts.size > 1, onOpen, onAction)
                        HorizontalDivider(color = c.band)
                    }
                    item(key = "hint") {
                        Text("Swipe right to archive, left to mark read or unread. Tap to read the thread.",
                            color = c.muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 12.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun SearchField(query: String, onQuery: (String) -> Unit, onSearch: () -> Unit) {
    val c = SavvyTheme.colors
    OutlinedTextField(
        value = query,
        onValueChange = onQuery,
        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).semantics { contentDescription = "Search mail" },
        placeholder = { Text("Search all accounts") },
        leadingIcon = { Icon(Icons.Outlined.Search, contentDescription = null) },
        trailingIcon = if (query.isNotEmpty()) {
            { IconButton(onClick = { onQuery(""); onSearch() }) { Icon(Icons.Outlined.Clear, contentDescription = "Clear search") } }
        } else null,
        singleLine = true,
        shape = PillShape,
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
        keyboardActions = KeyboardActions(onSearch = { onSearch() }),
        colors = OutlinedTextFieldDefaults.colors(
            focusedContainerColor = c.card, unfocusedContainerColor = c.card,
            focusedBorderColor = c.accent, unfocusedBorderColor = c.line,
            focusedTextColor = c.ink, unfocusedTextColor = c.ink, cursorColor = c.accent,
            focusedLeadingIconColor = c.muted, unfocusedLeadingIconColor = c.muted,
            focusedPlaceholderColor = c.muted, unfocusedPlaceholderColor = c.muted,
        ),
    )
}

@Composable
private fun AccountChips(state: InboxUiState, onAccount: (String) -> Unit) {
    if (state.accounts.isEmpty()) return
    val c = SavvyTheme.colors
    val all = listOf("all") + state.accounts
    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        items(all, key = { it }) { account ->
            val count = if (account == "all") state.unread.values.sum() else state.unread[account] ?: 0
            val label = (if (account == "all") "All" else accountLabel(account)) + if (count > 0) " · $count" else ""
            FilterChip(
                selected = state.account == account,
                onClick = { onAccount(account) },
                label = { Text(label, style = MaterialTheme.typography.labelMedium) },
                modifier = Modifier.heightIn(min = 48.dp),
                shape = PillShape,
                colors = FilterChipDefaults.filterChipColors(
                    containerColor = c.card, labelColor = c.ink,
                    selectedContainerColor = c.deep, selectedLabelColor = c.bg,
                ),
                border = FilterChipDefaults.filterChipBorder(
                    enabled = true, selected = state.account == account,
                    borderColor = c.line, selectedBorderColor = c.deep,
                ),
            )
        }
    }
}

@Composable
private fun SwipeRow(mail: Mail, showAccount: Boolean, onOpen: (Mail) -> Unit, onAction: (Mail, String) -> Unit) {
    val c = SavvyTheme.colors
    val swipe = rememberSwipeToDismissBoxState()
    val readAction = if (mail.unread) "read" else "unread"

    LaunchedEffect(swipe.currentValue) {
        when (swipe.currentValue) {
            SwipeToDismissBoxValue.StartToEnd -> { onAction(mail, "archive"); swipe.snapTo(SwipeToDismissBoxValue.Settled) }
            SwipeToDismissBoxValue.EndToStart -> { onAction(mail, readAction); swipe.snapTo(SwipeToDismissBoxValue.Settled) }
            SwipeToDismissBoxValue.Settled -> Unit
        }
    }

    SwipeToDismissBox(
        state = swipe,
        backgroundContent = {
            val direction = swipe.dismissDirection
            val (icon, text, alignment) = when (direction) {
                SwipeToDismissBoxValue.StartToEnd -> Triple(Icons.Outlined.Archive, "Archive", Alignment.CenterStart)
                else -> Triple(if (mail.unread) Icons.Outlined.MarkEmailRead else Icons.Outlined.MarkEmailUnread,
                    if (mail.unread) "Mark read" else "Mark unread", Alignment.CenterEnd)
            }
            Box(Modifier.fillMaxSize().background(c.band).padding(horizontal = 20.dp), contentAlignment = alignment) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(icon, contentDescription = null, tint = c.deep)
                    Text(text, color = c.deep, style = MaterialTheme.typography.labelMedium)
                }
            }
        },
    ) {
        MailRow(mail, showAccount, onOpen, onAction, readAction)
    }
}

@Composable
private fun MailRow(mail: Mail, showAccount: Boolean, onOpen: (Mail) -> Unit, onAction: (Mail, String) -> Unit, readAction: String) {
    val c = SavvyTheme.colors
    val weight = if (mail.unread) FontWeight.Bold else FontWeight.Normal
    val starAction = if (mail.starred) "unstar" else "star"
    Row(
        Modifier
            .fillMaxWidth()
            .background(c.card)
            .clickable(onClickLabel = "Open thread") { onOpen(mail) }
            .semantics {
                customActions = listOf(
                    CustomAccessibilityAction("Archive") { onAction(mail, "archive"); true },
                    CustomAccessibilityAction(if (mail.unread) "Mark read" else "Mark unread") { onAction(mail, readAction); true },
                    CustomAccessibilityAction(if (mail.starred) "Unstar" else "Star") { onAction(mail, starAction); true },
                )
            }
            .padding(start = 14.dp, top = 12.dp, bottom = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(
            Modifier.padding(top = 7.dp).size(8.dp)
                .background(if (mail.unread) c.accent else c.card, PillShape)
                .semantics { if (mail.unread) contentDescription = "Unread" },
        )
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(senderName(mail.from), color = c.ink, fontWeight = weight, style = MaterialTheme.typography.bodyMedium,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                Text(shortTime(mail.date), color = c.muted, style = MaterialTheme.typography.bodySmall)
            }
            Text(mail.subject, color = c.ink, fontWeight = weight, style = MaterialTheme.typography.bodyMedium,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (mail.snippet.isNotBlank()) {
                Text(mail.snippet, color = c.muted, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            if (showAccount) Text(accountLabel(mail.account), color = c.muted, style = MaterialTheme.typography.bodySmall)
        }
        IconButton(onClick = { onAction(mail, starAction) }, modifier = Modifier.size(48.dp)) {
            Icon(
                if (mail.starred) Icons.Outlined.Star else Icons.Outlined.StarOutline,
                contentDescription = if (mail.starred) "Unstar" else "Star",
                tint = if (mail.starred) c.accent else c.muted,
            )
        }
    }
}
