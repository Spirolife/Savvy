package com.spirolife.savvy.ui.chat

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Text another screen wants waiting in the chat box (the thread screen's
 * "Ask Savvy to reply"). Chat takes it once and clears it.
 */
object ChatPrefill {
    private val _text = MutableStateFlow<String?>(null)
    val text: StateFlow<String?> = _text.asStateFlow()

    fun set(text: String) { _text.value = text }

    fun take(): String? = _text.value.also { _text.value = null }
}
