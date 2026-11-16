package com.spirolife.savvy.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.runBlocking

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "savvy")

/**
 * The app's small persistent state, in DataStore preferences.
 *
 * Values are read from disk once ([load], called from Graph.init) and then kept
 * in memory, so [current] can be read synchronously anywhere (MainActivity
 * decides pair vs. Today from it at startup). Every write goes to disk and
 * updates the in-memory copy.
 */
class AppPrefs(context: Context) {
    data class Values(
        val baseUrl: String? = null,
        val token: String? = null,
        val deviceName: String? = null,
        val lastNotificationId: Long = 0,
    )

    private val store = context.applicationContext.dataStore
    private val state = MutableStateFlow(Values())

    /** Live view of the stored values. */
    val values: StateFlow<Values> = state.asStateFlow()
    val current: Values get() = state.value

    val baseUrl: String? get() = current.baseUrl
    val token: String? get() = current.token
    val deviceName: String? get() = current.deviceName

    /** Highest notification id the app has already shown (0 = none yet). */
    val lastNotificationId: Long get() = current.lastNotificationId

    /**
     * Reads the file once. Blocking, but it is a few hundred bytes read once
     * in Application.onCreate, well under any ANR threshold.
     */
    fun load() {
        val p = runBlocking { store.data.first() }
        state.value = Values(
            baseUrl = p[BASE_URL],
            token = p[TOKEN],
            deviceName = p[DEVICE_NAME],
            lastNotificationId = p[LAST_NOTIFICATION_ID] ?: 0,
        )
    }

    /** Saves a successful pairing. */
    suspend fun savePairing(baseUrl: String, token: String, deviceName: String) {
        store.edit {
            it[BASE_URL] = baseUrl
            it[TOKEN] = token
            it[DEVICE_NAME] = deviceName
        }
        state.update { it.copy(baseUrl = baseUrl, token = token, deviceName = deviceName) }
    }

    /** Forgets the device token (the PC no longer knows it); keeps the address for re-pairing. */
    suspend fun clearToken() {
        state.update { it.copy(token = null) }
        store.edit { it.remove(TOKEN) }
    }

    suspend fun setLastNotificationId(id: Long) {
        store.edit { it[LAST_NOTIFICATION_ID] = id }
        state.update { it.copy(lastNotificationId = id) }
    }

    /** Unpair: forget the token and notification progress, keep address and name as hints. */
    suspend fun unpair() {
        state.update { it.copy(token = null, lastNotificationId = 0) }
        store.edit {
            it.remove(TOKEN)
            it.remove(LAST_NOTIFICATION_ID)
        }
    }

    private companion object {
        val BASE_URL = stringPreferencesKey("base_url")
        val TOKEN = stringPreferencesKey("device_token")
        val DEVICE_NAME = stringPreferencesKey("device_name")
        val LAST_NOTIFICATION_ID = longPreferencesKey("last_notification_id")
    }
}
