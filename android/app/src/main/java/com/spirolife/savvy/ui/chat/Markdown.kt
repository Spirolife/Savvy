package com.spirolife.savvy.ui.chat

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle

/** Colors the renderer needs; pass them from SavvyTheme.colors. */
data class MarkdownColors(val link: Color, val codeBackground: Color)

/**
 * The small subset of markdown Savvy's replies use, as an [AnnotatedString]:
 * **bold**, *italic* / _italic_, `code`, ``` fenced blocks ```, [links](url) and bare
 * URLs (clickable through LinkAnnotation), "- " / "* " bullets, numbered lists and
 * "#" headings (shown bold). Anything else passes through as text.
 */
object Markdown {
    private val linkRe = Regex("""^\[([^\]\n]+)]\(([^)\s]+)\)""")
    private val bulletRe = Regex("""^(\s*)[-*+•]\s+(.*)$""")
    private val numberRe = Regex("""^(\s*)(\d+)[.)]\s+(.*)$""")
    private val headingRe = Regex("""^#{1,6}\s+(.*)$""")
    private val codeFont = FontFamily.Monospace

    fun render(text: String, colors: MarkdownColors, mono: FontFamily = codeFont): AnnotatedString = buildAnnotatedString {
        val codeStyle = SpanStyle(fontFamily = mono, background = colors.codeBackground)
        val lines = text.replace("\r\n", "\n").split("\n")
        var inFence = false
        var first = true
        for (line in lines) {
            if (line.trimStart().startsWith("```")) {
                inFence = !inFence
                continue
            }
            if (!first) append("\n")
            first = false
            if (inFence) {
                withStyle(codeStyle) { append(line) }
                continue
            }
            val bullet = bulletRe.matchEntire(line)
            val number = numberRe.matchEntire(line)
            val heading = headingRe.matchEntire(line)
            when {
                // "**bold**" at the start of a line is emphasis, not a bullet.
                bullet != null && !line.trimStart().startsWith("**") -> {
                    append(bullet.groupValues[1].replace("\t", "  "))
                    append("• ")
                    inlineSpans(bullet.groupValues[2], colors, codeStyle)
                }
                number != null -> {
                    append(number.groupValues[1])
                    append(number.groupValues[2]); append(". ")
                    inlineSpans(number.groupValues[3], colors, codeStyle)
                }
                heading != null -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) {
                    inlineSpans(heading.groupValues[1], colors, codeStyle)
                }
                else -> inlineSpans(line, colors, codeStyle)
            }
        }
    }

    private fun AnnotatedString.Builder.inlineSpans(s: String, colors: MarkdownColors, codeStyle: SpanStyle) {
        var i = 0
        val plain = StringBuilder()
        fun flush() { if (plain.isNotEmpty()) { append(plain.toString()); plain.clear() } }

        while (i < s.length) {
            val c = s[i]
            // `code`
            if (c == '`') {
                val end = s.indexOf('`', i + 1)
                if (end > i + 1) {
                    flush()
                    withStyle(codeStyle) { append(s.substring(i + 1, end)) }
                    i = end + 1
                    continue
                }
            }
            // **bold** or __bold__
            if ((c == '*' || c == '_') && s.startsWith("$c$c", i)) {
                val end = s.indexOf("$c$c", i + 2)
                if (end > i + 2) {
                    flush()
                    withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { inlineSpans(s.substring(i + 2, end), colors, codeStyle) }
                    i = end + 2
                    continue
                }
            }
            // *italic* or _italic_ (an underscore inside a word, like snake_case, is text)
            if ((c == '*' || c == '_') && i + 1 < s.length && !s[i + 1].isWhitespace() &&
                !(c == '_' && i > 0 && s[i - 1].isLetterOrDigit())) {
                val end = findClosing(s, c, i + 1)
                if (end > i + 1) {
                    flush()
                    withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { inlineSpans(s.substring(i + 1, end), colors, codeStyle) }
                    i = end + 1
                    continue
                }
            }
            // [text](url)
            if (c == '[') {
                val m = linkRe.find(s.substring(i))
                if (m != null) {
                    flush()
                    link(m.groupValues[2], colors) { inlineSpans(m.groupValues[1], colors, codeStyle) }
                    i += m.value.length
                    continue
                }
            }
            // bare URL
            if ((c == 'h') && (i == 0 || !s[i - 1].isLetterOrDigit()) &&
                (s.startsWith("https://", i) || s.startsWith("http://", i))) {
                var end = i
                while (end < s.length && !s[end].isWhitespace()) end++
                while (end > i && s[end - 1] in ".,;:!?)'\"") end--
                flush()
                val url = s.substring(i, end)
                link(url, colors) { append(url) }
                i = end
                continue
            }
            plain.append(c)
            i++
        }
        flush()
    }

    private fun findClosing(s: String, c: Char, from: Int): Int {
        var j = s.indexOf(c, from)
        while (j != -1) {
            val before = s[j - 1]
            val after = s.getOrNull(j + 1)
            val doubled = after == c
            val wordAfter = c == '_' && after != null && after.isLetterOrDigit()
            if (!before.isWhitespace() && !doubled && !wordAfter) return j
            j = s.indexOf(c, j + if (doubled) 2 else 1)
        }
        return -1
    }

    private fun AnnotatedString.Builder.link(url: String, colors: MarkdownColors, block: AnnotatedString.Builder.() -> Unit) {
        val style = TextLinkStyles(SpanStyle(color = colors.link, textDecoration = TextDecoration.Underline))
        withLink(LinkAnnotation.Url(url, style)) { block() }
    }
}
