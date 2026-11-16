package com.spirolife.savvy.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.spirolife.savvy.R

/**
 * The site's type, bundled in res/font (SIL OFL 1.1, see assets/licenses/OFL.txt):
 * Bricolage Grotesque (display, 800, tight), Atkinson Hyperlegible (body),
 * JetBrains Mono (uppercase kickers). Bricolage and JetBrains Mono are variable
 * fonts, so each declared weight pins the wght axis (supported from API 26).
 */
@OptIn(ExperimentalTextApi::class)
object SavvyFonts {
    private fun bricolage(weight: Int) = Font(
        R.font.bricolage_grotesque, FontWeight(weight),
        variationSettings = FontVariation.Settings(FontVariation.weight(weight), FontVariation.Setting("opsz", 32f)),
    )

    private fun jetbrains(weight: Int) = Font(
        R.font.jetbrains_mono, FontWeight(weight),
        variationSettings = FontVariation.Settings(FontVariation.weight(weight)),
    )

    val display: FontFamily = FontFamily(bricolage(600), bricolage(700), bricolage(800))
    val body: FontFamily = FontFamily(
        Font(R.font.atkinson_hyperlegible_regular, FontWeight.Normal),
        Font(R.font.atkinson_hyperlegible_bold, FontWeight.Bold),
    )
    val mono: FontFamily = FontFamily(jetbrains(400), jetbrains(500), jetbrains(600))
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
