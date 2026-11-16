package com.spirolife.savvy.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.spirolife.savvy.ui.theme.CardShape
import com.spirolife.savvy.ui.theme.PillShape
import com.spirolife.savvy.ui.theme.SavvyTheme

/** The site's card: 18dp corners, soft plum shadow, no outline. */
@Composable
fun SavvyCard(
    modifier: Modifier = Modifier,
    color: Color = SavvyTheme.colors.card,
    contentPadding: PaddingValues = PaddingValues(16.dp),
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier.shadow(if (SavvyTheme.colors.isDark) 6.dp else 3.dp, CardShape,
            ambientColor = Color(0x302A0F3A), spotColor = Color(0x402A0F3A)),
        shape = CardShape,
        color = color,
    ) {
        Column(Modifier.padding(contentPadding), verticalArrangement = Arrangement.spacedBy(8.dp), content = content)
    }
}

/** Uppercase mono label above a heading or inside a card ("NEXT UP · 10:00"). */
@Composable
fun Kicker(text: String, modifier: Modifier = Modifier, color: Color = SavvyTheme.colors.accent) {
    Text(text.uppercase(), modifier = modifier, color = color,
        style = androidx.compose.material3.MaterialTheme.typography.labelSmall)
}

@Composable
fun ScreenTitle(text: String, modifier: Modifier = Modifier) {
    Text(text, modifier = modifier, color = SavvyTheme.colors.deep,
        style = androidx.compose.material3.MaterialTheme.typography.headlineMedium)
}

/** Primary pill button (accent). Min height 48dp for touch. */
@Composable
fun PillButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    Button(onClick = onClick, enabled = enabled, shape = PillShape,
        modifier = modifier.heightIn(min = 48.dp),
        colors = ButtonDefaults.buttonColors(containerColor = SavvyTheme.colors.accent, contentColor = SavvyTheme.colors.accentInk)) {
        Text(text, style = androidx.compose.material3.MaterialTheme.typography.labelLarge)
    }
}

/** Secondary pill button (outline). */
@Composable
fun PillOutlineButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    OutlinedButton(onClick = onClick, enabled = enabled, shape = PillShape,
        modifier = modifier.heightIn(min = 48.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, SavvyTheme.colors.line),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = SavvyTheme.colors.ink)) {
        Text(text, style = androidx.compose.material3.MaterialTheme.typography.labelLarge)
    }
}

@Composable
fun LoadingBox(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(color = SavvyTheme.colors.accent)
    }
}

/** A friendly error with a retry, for any screen whose load failed. */
@Composable
fun ErrorBox(message: String, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(message, color = SavvyTheme.colors.muted, textAlign = TextAlign.Center)
        PillOutlineButton("Try again", onRetry)
    }
}

/** Stand-in for a screen another part of the build fills in. */
@Composable
fun PlaceholderScreen(name: String) {
    Box(Modifier.fillMaxSize().background(SavvyTheme.colors.bg), contentAlignment = Alignment.Center) {
        Text(name, color = SavvyTheme.colors.muted)
    }
}
