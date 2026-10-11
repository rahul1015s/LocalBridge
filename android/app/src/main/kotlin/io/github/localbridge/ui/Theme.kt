package io.github.localbridge.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.shape.RoundedCornerShape
import io.github.localbridge.data.ThemeMode

// Teal brand, neutral surfaces. Brand identity is fixed (no dynamic color) so the app looks the same
// everywhere; contrast pairs below meet WCAG AA for body text.
private val Light = lightColorScheme(
    primary = Color(0xFF0B7A70), onPrimary = Color.White,
    primaryContainer = Color(0xFFD3F2EE), onPrimaryContainer = Color(0xFF00201D),
    secondary = Color(0xFF4A6360), onSecondary = Color.White,
    secondaryContainer = Color(0xFFCCE8E3), onSecondaryContainer = Color(0xFF05201D),
    background = Color(0xFFF5F8F7), onBackground = Color(0xFF151D1C),
    surface = Color(0xFFFFFFFF), onSurface = Color(0xFF151D1C),
    surfaceVariant = Color(0xFFE6EDEC), onSurfaceVariant = Color(0xFF3F4948),
    outline = Color(0xFF6F7978), outlineVariant = Color(0xFFD0DAD8),
    error = Color(0xFFB3261E), errorContainer = Color(0xFFF9DEDC), onErrorContainer = Color(0xFF410E0B),
)
private val Dark = darkColorScheme(
    primary = Color(0xFF4FD1C5), onPrimary = Color(0xFF003733),
    primaryContainer = Color(0xFF0B4F49), onPrimaryContainer = Color(0xFFB4F0EA),
    secondary = Color(0xFFB0CCC8), onSecondary = Color(0xFF1B3532),
    secondaryContainer = Color(0xFF334B48), onSecondaryContainer = Color(0xFFCCE8E3),
    background = Color(0xFF0E1413), onBackground = Color(0xFFDDE5E3),
    surface = Color(0xFF151C1B), onSurface = Color(0xFFDDE5E3),
    surfaceVariant = Color(0xFF232C2B), onSurfaceVariant = Color(0xFFBEC9C7),
    outline = Color(0xFF889391), outlineVariant = Color(0xFF3F4948),
    error = Color(0xFFF2B8B5), errorContainer = Color(0xFF8C1D18), onErrorContainer = Color(0xFFF9DEDC),
)

private val AppTypography = Typography(
    headlineLarge = TextStyle(fontSize = 30.sp, lineHeight = 36.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.5).sp),
    headlineMedium = TextStyle(fontSize = 24.sp, lineHeight = 30.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.3).sp),
    titleLarge = TextStyle(fontSize = 20.sp, lineHeight = 26.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 22.sp, fontWeight = FontWeight.SemiBold),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 16.sp),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
)

private val AppShapes = Shapes(
    small = RoundedCornerShape(10.dp), medium = RoundedCornerShape(16.dp), large = RoundedCornerShape(24.dp),
)

@Composable
fun LocalBridgeTheme(mode: ThemeMode, content: @Composable () -> Unit) {
    val dark = when (mode) {
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
        ThemeMode.LIGHT -> false
        ThemeMode.DARK -> true
    }
    val scheme: ColorScheme = if (dark) Dark else Light
    MaterialTheme(colorScheme = scheme, typography = AppTypography, shapes = AppShapes, content = content)
}
