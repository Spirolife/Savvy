package com.spirolife.savvy

import android.content.Context
import com.spirolife.savvy.data.api.SavvyApi

/**
 * The app's few singletons, created once in [SavvyApp.onCreate].
 *
 * OWNER: app-shell agent. Implement [init] (prefs + ApiClient) and [isPaired].
 * Screens read `Graph.api`; nothing else should construct clients.
 */
object Graph {
    lateinit var api: SavvyApi
        private set

    fun init(context: Context) {
        // Stub until the app-shell agent wires AppPrefs and ApiClient.
    }

    /** True once a device token is stored; decides whether the app opens on pairing. */
    val isPaired: Boolean get() = false

    internal fun setApiForTests(api: SavvyApi) { this.api = api }
}
