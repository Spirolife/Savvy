package com.spirolife.savvy.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp

/**
 * The site's type: Bricolage Grotesque (display, 800, tight), Atkinson
 * Hyperlegible (body), JetBrains Mono (uppercase kickers).
 *
 * OWNER: app-shell agent replaces these three families with the bundled fonts
 * in res/font (OFL). Everything else references only these names.
 */
object SavvyFonts {
    var display: FontFamily = FontFamily.SansSerif
    var body: FontFamily = FontFamily.SansSerif
    var mono: FontFamily = FontFamily.Monospace
}

fun savvyTypography(): Typography {
    val d = SavvyFonts.display
    val b = SavvyFonts.body
    return Typography(
        displaySmall = TextStyle(fontFamily = d, fontWeight = FontWeight.ExtraBold, fontSize = 34.sp, lineHeight = 36.sp, letterSpacing = (-0.035).em),
        headlineMedium = TextStyle(fontFamily = d, fontWeight = FontWeight.ExtraBold, fontSize = 28.sp, lineHeight = 32.sp, letterSpacing = (-0.02).em),
        headlineSmall = TextStyle(fontFamily = d, fontWeight = FontWeight.ExtraBold, fontSize = 22.sp, lineHeight = 26.sp, letterSpacing = (-0.02).em),
        titleLarge = TextStyle(fontFamily = d, fontWeight = FontWeight.Bold, fontSize = 20.sp, lineHeight = 24.sp),
        titleMedium = TextStyle(fontFamily = b, fontWeight = FontWeight.Bold, fontSize = 16.sp, lineHeight = 22.sp),
        titleSmall = TextStyle(fontFamily = b, fontWeight = FontWeight.Bold, fontSize = 15.sp, lineHeight = 20.sp),
        bodyLarge = TextStyle(fontFamily = b, fontSize = 16.sp, lineHeight = 24.sp),
        bodyMedium = TextStyle(fontFamily = b, fontSize = 15.sp, lineHeight = 22.sp),
        bodySmall = TextStyle(fontFamily = b, fontSize = 13.sp, lineHeight = 18.sp),
        labelLarge = TextStyle(fontFamily = b, fontWeight = FontWeight.Bold, fontSize = 15.sp),
        labelMedium = TextStyle(fontFamily = b, fontWeight = FontWeight.Bold, fontSize = 13.sp),
        labelSmall = TextStyle(fontFamily = SavvyFonts.mono, fontWeight = FontWeight.SemiBold, fontSize = 12.sp, letterSpacing = 0.04.em),
    )
}
