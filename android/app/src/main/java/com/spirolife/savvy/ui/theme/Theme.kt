package com.spirolife.savvy.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

/** Card radius from the site (18px); buttons and chips are pills. */
val CardShape = RoundedCornerShape(18.dp)
val PillShape = RoundedCornerShape(percent = 50)

@Composable
fun SavvyTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val c = if (dark) DarkColors else LightColors
    val scheme = if (dark) darkColorScheme(
        primary = c.accent, onPrimary = c.accentInk, background = c.bg, onBackground = c.ink,
        surface = c.card, onSurface = c.ink, surfaceVariant = c.band, onSurfaceVariant = c.muted,
        outline = c.line, secondary = c.deep, error = c.warn,
    ) else lightColorScheme(
        primary = c.accent, onPrimary = c.accentInk, background = c.bg, onBackground = c.ink,
        surface = c.card, onSurface = c.ink, surfaceVariant = c.band, onSurfaceVariant = c.muted,
        outline = c.line, secondary = c.deep, error = c.warn,
    )
    CompositionLocalProvider(LocalSavvyColors provides c) {
        MaterialTheme(
            colorScheme = scheme,
            typography = savvyTypography(),
            shapes = Shapes(small = RoundedCornerShape(12.dp), medium = CardShape, large = CardShape),
            content = content,
        )
    }
}

object SavvyTheme {
    val colors: SavvyColors
        @Composable @ReadOnlyComposable get() = LocalSavvyColors.current
}
