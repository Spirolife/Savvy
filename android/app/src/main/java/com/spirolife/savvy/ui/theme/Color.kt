package com.spirolife.savvy.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color

/**
 * alenna.com's "Gallery" palette (personal_website_v2, src/styles/global.css),
 * WCAG AA in both themes. Use these through [SavvyTheme.colors], never raw hex in
 * screens, so dark mode follows automatically.
 */
@Immutable
data class SavvyColors(
    val bg: Color,          // page background
    val band: Color,        // tinted strip / chip background
    val card: Color,        // raised surfaces
    val ink: Color,         // body text
    val muted: Color,       // secondary text (>= 5.5:1)
    val accent: Color,      // links, primary buttons, kickers
    val accentInk: Color,   // text on accent
    val deep: Color,        // headings, pressed chips
    val line: Color,        // dividers, input outlines
    val ok: Color,          // "connected", success
    val warn: Color,        // "signed out", needs attention (text)
    val warnBg: Color,      // warning card background
    val warnLine: Color,    // warning card outline
    val gradient: Brush,    // purple -> magenta (-> peach) highlight
    val isDark: Boolean,
)

val LightColors = SavvyColors(
    bg = Color(0xFFF2EEF4),
    band = Color(0xFFE8E0EE),
    card = Color(0xFFF9F6FA),
    ink = Color(0xFF2A2131),
    muted = Color(0xFF5A5163),
    accent = Color(0xFF71409B),
    accentInk = Color(0xFFFFFFFF),
    deep = Color(0xFF2A0F3A),
    line = Color(0xFFDCCDE6),
    ok = Color(0xFF1A7F47),
    warn = Color(0xFFA23D0C),
    warnBg = Color(0xFFFBEDE6),
    warnLine = Color(0xFFE3A27F),
    gradient = Brush.linearGradient(listOf(Color(0xFF71409B), Color(0xFF9A3F8F))),
    isDark = false,
)

val DarkColors = SavvyColors(
    bg = Color(0xFF16121A),
    band = Color(0xFF221A2A),
    card = Color(0xFF1E1824),
    ink = Color(0xFFECE6F1),
    muted = Color(0xFFA59FAE),
    accent = Color(0xFFC4A1E0),
    accentInk = Color(0xFF2A0F3A),
    deep = Color(0xFFE6D2F0),
    line = Color(0xFF352B3E),
    ok = Color(0xFF7FD4A0),
    warn = Color(0xFFF4B08A),
    warnBg = Color(0xFF2B1E22),
    warnLine = Color(0xFF5C3A2E),
    gradient = Brush.linearGradient(listOf(Color(0xFFC4A1E0), Color(0xFFEC8FC4))),
    isDark = true,
)

/** Calendar event colors, from the site's gradient: index by a stable hash of the calendar. */
fun eventColors(dark: Boolean): List<Color> = if (dark)
    listOf(Color(0xFFC4A1E0), Color(0xFFEC8FC4), Color(0xFFF4B08A), Color(0xFF9FC3E8))
else
    listOf(Color(0xFF71409B), Color(0xFF9A3F8F), Color(0xFFB5532C), Color(0xFF3F5E9A))

val LocalSavvyColors = staticCompositionLocalOf { LightColors }
