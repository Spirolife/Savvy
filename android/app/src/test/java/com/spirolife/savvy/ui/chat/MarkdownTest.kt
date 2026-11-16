package com.spirolife.savvy.ui.chat

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownTest {
    private val colors = MarkdownColors(Color.Blue, Color.LightGray)
    private fun render(s: String) = Markdown.render(s, colors)

    @Test fun boldAndItalic() {
        val out = render("a **bold** and *it* and _also_")
        assertEquals("a bold and it and also", out.text)
        val bold = out.spanStyles.single { it.item.fontWeight == FontWeight.Bold }
        assertEquals("bold", out.text.substring(bold.start, bold.end))
        val italics = out.spanStyles.filter { it.item.fontStyle == FontStyle.Italic }.map { out.text.substring(it.start, it.end) }
        assertEquals(listOf("it", "also"), italics)
    }

    @Test fun snakeCaseStaysText() {
        assertEquals("move_event and send_email", render("move_event and send_email").text)
    }

    @Test fun linksAreClickable() {
        val out = render("See [the doc](https://example.com/a) or https://x.org/b.")
        assertEquals("See the doc or https://x.org/b.", out.text)
        val links = out.getLinkAnnotations(0, out.length).map { (it.item as LinkAnnotation.Url).url to out.text.substring(it.start, it.end) }
        assertEquals(listOf("https://example.com/a" to "the doc", "https://x.org/b" to "https://x.org/b"), links)
    }

    @Test fun bulletsNumbersAndCode() {
        val out = render("Plan:\n- one `x`\n* two\n2. three\n```\nval a = 1\n```")
        assertEquals("Plan:\n• one x\n• two\n2. three\nval a = 1", out.text)
        val code = out.spanStyles.filter { it.item.background == Color.LightGray }.map { out.text.substring(it.start, it.end) }
        assertEquals(listOf("x", "val a = 1"), code)
    }

    @Test fun unmatchedMarkersStay() {
        val out = render("2 * 3 = 6 and a lone ** here")
        assertEquals("2 * 3 = 6 and a lone ** here", out.text)
        assertTrue(out.spanStyles.isEmpty())
    }
}
