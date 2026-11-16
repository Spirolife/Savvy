package com.spirolife.savvy.ui.pair

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.navigation.NavController
import com.spirolife.savvy.Graph
import com.spirolife.savvy.ui.nav.Routes

/**
 * Makes [route] the only screen on the back stack and the graph's start
 * destination, so `openTab` (which pops to the start destination) keeps working
 * after the app switches between pairing and the main tabs.
 */
fun NavController.resetTo(route: String) {
    graph.setStartDestination(route)
    navigate(route) {
        popUpTo(graph.id) { inclusive = true }
        launchSingleTop = true
    }
}

/** Sends the user back to pairing as soon as the PC rejects this phone's token. */
@Composable
fun ReturnToPairingWhenUnpaired(nav: NavController) {
    val unpaired by Graph.unpaired.collectAsState()
    LaunchedEffect(unpaired) {
        if (unpaired && nav.currentDestination?.route != Routes.PAIR) nav.resetTo(Routes.PAIR)
    }
}
