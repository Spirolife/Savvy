package com.spirolife.savvy.ui.nav

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.outlined.Book
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.Mail
import androidx.compose.material.icons.outlined.WbSunny
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.navigation.NavController
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.spirolife.savvy.ui.chat.ChatScreen
import com.spirolife.savvy.ui.connect.ConnectScreen
import com.spirolife.savvy.ui.diary.DiaryScreen
import com.spirolife.savvy.ui.inbox.InboxScreen
import com.spirolife.savvy.ui.inbox.ThreadScreen
import com.spirolife.savvy.ui.pair.PairScreen
import com.spirolife.savvy.ui.plan.PlanScreen
import com.spirolife.savvy.ui.settings.SettingsScreen
import com.spirolife.savvy.ui.theme.SavvyTheme
import com.spirolife.savvy.ui.today.TodayScreen

object Routes {
    const val PAIR = "pair"
    const val CONNECT = "connect"
    const val TODAY = "today"
    const val CHAT = "chat"
    const val INBOX = "inbox"
    const val THREAD = "inbox/{account}/{threadId}"
    const val PLAN = "plan"
    const val DIARY = "diary"
    const val SETTINGS = "settings"

    fun thread(account: String, threadId: String) = "inbox/$account/$threadId"
}

private data class Tab(val route: String, val label: String, val icon: ImageVector)

private val tabs = listOf(
    Tab(Routes.TODAY, "Today", Icons.Outlined.WbSunny),
    Tab(Routes.CHAT, "Chat", Icons.AutoMirrored.Outlined.Chat),
    Tab(Routes.INBOX, "Inbox", Icons.Outlined.Mail),
    Tab(Routes.PLAN, "Plan", Icons.Outlined.Checklist),
    Tab(Routes.DIARY, "Diary", Icons.Outlined.Book),
)

/** Switch tabs the standard way: one copy of each tab, state kept when switching. */
fun NavController.openTab(route: String) = navigate(route) {
    popUpTo(graph.findStartDestination().id) { saveState = true }
    launchSingleTop = true
    restoreState = true
}

@Composable
fun SavvyNavHost(startRoute: String, pendingRoute: String?, onPendingRouteHandled: () -> Unit) {
    val nav = rememberNavController()
    com.spirolife.savvy.ui.pair.ReturnToPairingWhenUnpaired(nav)   // the PC dropped this phone's token
    val entry by nav.currentBackStackEntryAsState()
    val current = entry?.destination?.route

    LaunchedEffect(pendingRoute) {
        if (pendingRoute != null && startRoute != Routes.PAIR) {
            nav.openTab(pendingRoute)
            onPendingRouteHandled()
        }
    }

    Scaffold(
        containerColor = SavvyTheme.colors.bg,
        bottomBar = {
            if (tabs.any { it.route == current }) {
                NavigationBar(containerColor = SavvyTheme.colors.card) {
                    tabs.forEach { tab ->
                        NavigationBarItem(
                            selected = current == tab.route,
                            onClick = { nav.openTab(tab.route) },
                            icon = { Icon(tab.icon, contentDescription = null) },
                            label = { Text(tab.label) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = SavvyTheme.colors.deep,
                                selectedTextColor = SavvyTheme.colors.deep,
                                indicatorColor = SavvyTheme.colors.band,
                                unselectedIconColor = SavvyTheme.colors.muted,
                                unselectedTextColor = SavvyTheme.colors.muted,
                            ),
                        )
                    }
                }
            }
        },
    ) { padding ->
        NavHost(nav, startDestination = startRoute, modifier = Modifier.padding(padding)) {
            composable(Routes.PAIR) { PairScreen(nav) }
            composable(Routes.CONNECT) { ConnectScreen(nav) }
            composable(Routes.TODAY) { TodayScreen(nav) }
            composable(Routes.CHAT) { ChatScreen(nav) }
            composable(Routes.INBOX) { InboxScreen(nav) }
            composable(
                Routes.THREAD,
                arguments = listOf(navArgument("account") { type = NavType.StringType },
                    navArgument("threadId") { type = NavType.StringType }),
            ) { e ->
                ThreadScreen(nav, e.arguments?.getString("account").orEmpty(), e.arguments?.getString("threadId").orEmpty())
            }
            composable(Routes.PLAN) { PlanScreen(nav) }
            composable(Routes.DIARY) { DiaryScreen(nav) }
            composable(Routes.SETTINGS) { SettingsScreen(nav) }
        }
    }
}
