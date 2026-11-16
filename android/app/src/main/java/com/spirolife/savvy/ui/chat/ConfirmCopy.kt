package com.spirolife.savvy.ui.chat

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** What the confirm sheet says for one tool call. */
data class ConfirmCopy(
    val kicker: String,
    val title: String,
    val fields: List<Pair<String, String>>,
    val body: String?,
    val denyLabel: String,
    val allowLabel: String,
)

private val EMAIL_FIELDS = listOf("to" to "To", "cc" to "Cc", "bcc" to "Bcc", "account" to "From", "subject" to "Subject")

/** Readable copy for a `confirm` event: email tools read like an email, anything else as key: value. */
fun confirmCopy(name: String, level: Int, args: JsonObject): ConfirmCopy {
    val isEmail = name in setOf("send_email", "draft_email", "send_draft", "reply_email") ||
        (name.contains("email") && "to" in args)
    if (isEmail) {
        val send = name.startsWith("send") || name.startsWith("reply")
        val fields = EMAIL_FIELDS.mapNotNull { (k, label) -> args[k]?.readable()?.takeIf { it.isNotBlank() }?.let { label to it } }
        val known = EMAIL_FIELDS.map { it.first }.toSet() + "body"
        val rest = args.filterKeys { it !in known }.mapNotNull { (k, v) -> v.readable().takeIf { it.isNotBlank() }?.let { label(k) to it } }
        return ConfirmCopy(
            kicker = if (send) "Needs your OK · sends email" else "Needs your OK · email draft",
            title = when {
                name == "send_draft" -> "Send this draft?"
                send -> "Send this email?"
                else -> "Save this draft?"
            },
            fields = fields + rest,
            body = args["body"]?.readable()?.takeIf { it.isNotBlank() },
            denyLabel = if (send) "Don't send" else "Don't save",
            allowLabel = if (send) "Send" else "Save draft",
        )
    }
    val fields = args.mapNotNull { (k, v) -> v.readable().takeIf { it.isNotBlank() }?.let { label(k) to it } }
    return ConfirmCopy(
        kicker = if (level >= 2) "Needs your OK · reaches outside" else "Needs your OK",
        title = "Allow ${label(name).lowercase()}?",
        fields = fields,
        body = null,
        denyLabel = "Don't allow",
        allowLabel = "Allow",
    )
}

private fun label(key: String) = key.replace('_', ' ').trim().replaceFirstChar { it.uppercase() }

private fun JsonElement.readable(): String = when (this) {
    is JsonNull -> ""
    is JsonPrimitive -> content
    is JsonArray -> joinToString(", ") { it.readable() }
    is JsonObject -> entries.joinToString(", ") { (k, v) -> "${label(k)}: ${v.readable()}" }
}
