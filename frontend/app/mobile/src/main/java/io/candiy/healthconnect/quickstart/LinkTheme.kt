package io.candiy.healthconnect.quickstart

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight

/**
 * 웹(frontend/web/src/routes/layout.css)과 같은 색 이름·값을 쓴다(S15P31A403-377).
 * 빨강은 쓰임에 따라 둘로 나눈다(웹 S15P31A403-358 과 같은 기준):
 * - Primary(진한 레드): 글자·아이콘이 올라가는 면(주요 버튼, 전송 버튼, 완료 원)과 흰 면 위의 강조 글자·아이콘.
 *   흰 글자와의 대비 5.0:1, 흰 면 위 글자 대비 5.0:1 로 본문 기준(4.5:1)을 지킨다. 옛 강조 글자색(#EE4E57)은 3.6:1 이었다.
 * - Red(브랜드 레드): 글자가 올라가지 않는 꾸밈(차트 막대, 진행 표시, 입력칸 포커스 테두리·커서, 고른 항목의 왼쪽 막대).
 *   흰 글자와의 대비가 3.2:1 이라 버튼 면으로 쓰지 않는다.
 * 상태 글자색(초록·호박·빨강)은 칩 견본을 글자 대비에 맞게 어둡게 한 값이다.
 */
object Link {
    val Primary = Color(0xFFCC383E)    // 웹 primary · 글자가 올라가는 면, 강조 글자·아이콘
    val OnPrimary = Color(0xFFFCF9F6)  // 웹 primary-foreground · Primary 면 위의 글자
    val Red = Color(0xFFF85A5A)        // 웹 brand · 글자 없는 꾸밈(막대·진행 표시·포커스)
    val CoralMain = Color(0xFFEDA18B)  // 웹 coral-main · 차트 보통 막대, "수집 중" 글자
    val CoralSoft = Color(0xFFF9E0DD)  // 웹 coral-soft · 차트 최저 막대
    val Coral = Color(0xFFF5C7BC)      // 구분선 등
    val Pink = Color(0xFFFFF4F3)       // 상태 카드 배경
    val Text = Color(0xFF2C2E35)
    val Sub = Color(0xFF6D7280)
    val Muted = Color(0xFF8A8F9C)
    val Disabled = Color(0xFFA7ACB8)
    val Line = Color(0xFFD3D9E3)
    val Border = Color(0xFFE8EDF5)
    val Surface = Color(0xFFF3F6FB)    // 카드
    val Background = Color(0xFFFFFFFF)
    val Green = Color(0xFF4F8A5C); val GreenBg = Color(0xFFEDF5EF)
    val Amber = Color(0xFF9A7230); val AmberBg = Color(0xFFFBF4DE)
    val Bad = Color(0xFFD2504B); val BadBg = Color(0xFFF8E7E6)

    // 지표 구분색. 건강 앱처럼 지표마다 아이콘 색을 달리해 한눈에 구분한다(와이어프레임 v_3 에는 없는 값).
    // 글자가 아니라 아이콘에만 쓰고, 연한 배경과 함께 쓴다
    val Steps = Color(0xFF3F9A6B); val StepsBg = Color(0xFFE8F5EE)
    val HeartRate = Color(0xFFEE4E57); val HeartRateBg = Color(0xFFFDECEC)
    val Oxygen = Color(0xFF3A7BD5); val OxygenBg = Color(0xFFEAF2FC)
    val Sleep = Color(0xFF6F5BD8); val SleepBg = Color(0xFFF0EEFC)
}

/**
 * 웹(캔디핏)과 같은 Paperlogy 글꼴. 웹 woff2를 TTF로 바꾸고 한글 11,172자·영문·기호만 남겨 굵기마다 약 0.5MB로 줄였다.
 * 앱에서 쓰는 굵기(보통·Medium·SemiBold·Bold·ExtraBold)만 넣었다. 라이선스는 SIL OFL 1.1 (assets/Paperlogy-OFL.txt)
 */
val Paperlogy = FontFamily(
    Font(R.font.paperlogy_regular, FontWeight.Normal),
    Font(R.font.paperlogy_medium, FontWeight.Medium),
    Font(R.font.paperlogy_semibold, FontWeight.SemiBold),
    Font(R.font.paperlogy_bold, FontWeight.Bold),
    Font(R.font.paperlogy_extrabold, FontWeight.ExtraBold),
)

// Material 기본 글자 크기·간격은 그대로 두고 글꼴만 바꾼다. 스타일 없이 쓴 Text도 bodyLarge를 따라 Paperlogy가 된다
private val LinkTypography = Typography().let { base ->
    Typography(
        displayLarge = base.displayLarge.copy(fontFamily = Paperlogy),
        displayMedium = base.displayMedium.copy(fontFamily = Paperlogy),
        displaySmall = base.displaySmall.copy(fontFamily = Paperlogy),
        headlineLarge = base.headlineLarge.copy(fontFamily = Paperlogy),
        headlineMedium = base.headlineMedium.copy(fontFamily = Paperlogy),
        headlineSmall = base.headlineSmall.copy(fontFamily = Paperlogy),
        titleLarge = base.titleLarge.copy(fontFamily = Paperlogy),
        titleMedium = base.titleMedium.copy(fontFamily = Paperlogy),
        titleSmall = base.titleSmall.copy(fontFamily = Paperlogy),
        bodyLarge = base.bodyLarge.copy(fontFamily = Paperlogy),
        bodyMedium = base.bodyMedium.copy(fontFamily = Paperlogy),
        bodySmall = base.bodySmall.copy(fontFamily = Paperlogy),
        labelLarge = base.labelLarge.copy(fontFamily = Paperlogy),
        labelMedium = base.labelMedium.copy(fontFamily = Paperlogy),
        labelSmall = base.labelSmall.copy(fontFamily = Paperlogy),
    )
}

/**
 * 입력칸 색. 포커스 테두리와 커서는 꾸밈이라 브랜드 레드로 둔다(웹의 포커스 테두리 ring 과 같다).
 * 따로 주지 않으면 Material 이 테마 primary(진한 레드)를 쓴다
 */
@Composable
fun linkFieldColors() = OutlinedTextFieldDefaults.colors(focusedBorderColor = Link.Red, cursorColor = Link.Red)

@Composable
fun LinkTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        typography = LinkTypography,
        colorScheme = lightColorScheme(
            // Button·TextButton 등 Material 기본 위젯의 면과 글자가 이 값을 따른다
            primary = Link.Primary,
            onPrimary = Link.OnPrimary,
            secondary = Link.Primary,
            background = Link.Background,
            onBackground = Link.Text,
            surface = Link.Background,
            onSurface = Link.Text,
            surfaceVariant = Link.Surface,
            onSurfaceVariant = Link.Sub,
            outline = Link.Line,
            error = Link.Bad,
        ),
        content = content,
    )
}
