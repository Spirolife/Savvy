package com.spirolife.savvy.ui.inbox

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.MarkEmailUnread
import androidx.compose.material.icons.outlined.Star
import androidx.compose.material.icons.outlined.StarOutline
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavController
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.ThreadMessage
import com.spirolife.savvy.ui.chat.ChatPrefill
import com.spirolife.savvy.ui.components.ErrorBox
import com.spirolife.savvy.ui.components.Kicker
import com.spirolife.savvy.ui.components.LoadingBox
import com.spirolife.savvy.ui.components.PillButton
import com.spirolife.savvy.ui.components.SavvyCard
import com.spirolife.savvy.ui.nav.Routes
import com.spirolife.savvy.ui.nav.openTab
import com.spirolife.savvy.ui.theme.SavvyTheme

/** OWNER: chat-inbox agent. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ThreadScreen(nav: NavController, account: String, threadId: String) {
    val vm: ThreadViewModel = viewModel(key = "thread/$account/$threadId") { ThreadViewModel(Graph.api, account, threadId) }
    val state by vm.state.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val c = SavvyTheme.colors

    LaunchedEffect(state.archived) { if (state.archived) nav.popBackStack() }
    LaunchedEffect(state.notice) {
        state.notice?.let { snackbar.showSnackbar(it); vm.noticeShown() }
    }

    Scaffold(
        containerColor = c.bg,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = {},
                navigationIcon = {
                    IconButton(onClick = { nav.popBackStack() }) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back to inbox")
                    }
                },
                actions = {
                    val enabled = state.thread != null && !state.busy
                    IconButton(onClick = vm::archive, enabled = enabled) {
                        Icon(Icons.Outlined.Archive, contentDescription = "Archive")
                    }
                    IconButton(onClick = vm::toggleStar, enabled = enabled) {
                        Icon(if (state.starred) Icons.Outlined.Star else Icons.Outlined.StarOutline,
                            contentDescription = if (state.starred) "Unstar" else "Star")
                    }
                    IconButton(onClick = vm::markUnread, enabled = enabled) {
                        Icon(Icons.Outlined.MarkEmailUnread, contentDescription = "Mark unread")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = c.bg, navigationIconContentColor = c.ink, actionIconContentColor = c.ink,
                ),
            )
        },
    ) { padding ->
        val thread = state.thread
        when {
            state.loading -> LoadingBox(Modifier.padding(padding))
            thread == null -> ErrorBox(state.error ?: "Couldn't open that conversation.", vm::load, Modifier.padding(padding))
            else -> LazyColumn(
                Modifier.fillMaxSize().padding(padding),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                item(key = "subject") {
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Kicker(accountLabel(account))
                        Text(thread.subject, color = c.deep, style = MaterialTheme.typography.headlineSmall,
                            modifier = Modifier.semantics { heading() })
                    }
                }
                items(thread.messages, key = { it.id }) { MessageCard(it) }
                item(key = "reply") {
                    PillButton(
                        "Ask Savvy to reply",
                        onClick = {
                            ChatPrefill.set(replyPrompt(account, thread))
                            nav.openTab(Routes.CHAT)
                        },
                        modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun MessageCard(m: ThreadMessage) {
    val c = SavvyTheme.colors
    SavvyCard(Modifier.fillMaxWidth()) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(senderName(m.from), color = c.ink, style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
            Text(m.date, color = c.muted, style = MaterialTheme.typography.bodySmall)
        }
        if (m.to.isNotBlank()) Text("To ${m.to}", color = c.muted, style = MaterialTheme.typography.bodySmall)
        SelectionContainer {
            Text(m.bodyText.trim().ifEmpty { "(no text)" }, color = c.ink, style = MaterialTheme.typography.bodyMedium)
        }
    }
}
