package com.spirolife.savvy

import android.content.Context
import com.spirolife.savvy.data.ApiClient
import com.spirolife.savvy.data.AppPrefs
import com.spirolife.savvy.data.api.SavvyApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * The app's few singletons, created once in [SavvyApp.onCreate].
 * Screens read `Graph.api`; nothing else should construct clients.
 */
object Graph {
    lateinit var api: SavvyApi
        private set
    lateinit var prefs: AppPrefs
        private set

    /** Work that should outlive any one screen (saving prefs after a 401, for example). */
    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val unpairedState = MutableStateFlow(false)

    /**
     * Becomes true when the PC rejects this phone's token (HTTP 401). The UI
     * should then go back to the pairing screen; pairing again resets it.
     */
    val unpaired: StateFlow<Boolean> = unpairedState.asStateFlow()

    fun init(context: Context) {
        prefs = AppPrefs(context).also { it.load() }
        api = ApiClient(baseUrl = { prefs.baseUrl }, token = { prefs.token }, onUnpaired = ::onUnpaired)
    }

    /** True once a device token is stored; decides whether the app opens on pairing. */
    val isPaired: Boolean get() = this::prefs.isInitialized && prefs.token != null && prefs.baseUrl != null

    /** Called after a successful pairing. */
    fun markPaired() {
        unpairedState.value = false
    }

    /** "Unpair this phone": forget the token locally. */
    suspend fun unpair() {
        prefs.unpair()
    }

    private fun onUnpaired() {
        if (!this::prefs.isInitialized) return
        unpairedState.value = true
        appScope.launch { prefs.clearToken() }
    }

    internal fun setApiForTests(api: SavvyApi) { this.api = api }
}
