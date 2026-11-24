package io.candiy.healthconnect.quickstart

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathBuilder
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 앱에서 쓰는 아이콘. 24 격자에 선 두께 2 로 직접 그린다.
 * 외부 아이콘 라이브러리(material-icons-extended)를 들이지 않으려고 필요한 것만 만들었다.
 * 색은 그릴 때 Icon(tint) 로 정하므로 여기서는 검정으로 둔다.
 */
object LinkIcons {
    private fun icon(name: String, filled: Boolean = false, block: PathBuilder.() -> Unit): ImageVector =
        ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).path(
            fill = if (filled) SolidColor(Color.Black) else null,
            stroke = if (filled) null else SolidColor(Color.Black),
            strokeLineWidth = 2f,
            strokeLineCap = StrokeCap.Round,
            strokeLineJoin = StrokeJoin.Round,
            pathBuilder = block,
        ).build()

    private fun PathBuilder.oval(cx: Float, cy: Float, rx: Float, ry: Float) {
        moveTo(cx - rx, cy)
        arcTo(rx, ry, 0f, false, true, cx + rx, cy)
        arcTo(rx, ry, 0f, false, true, cx - rx, cy)
        close()
    }

    private fun PathBuilder.heart() {
        moveTo(12f, 20.5f)
        curveTo(12f, 20.5f, 3.5f, 15.2f, 3.5f, 9.2f)
        curveTo(3.5f, 6.4f, 5.6f, 4.5f, 8f, 4.5f)
        curveTo(9.7f, 4.5f, 11.1f, 5.4f, 12f, 6.8f)
        curveTo(12.9f, 5.4f, 14.3f, 4.5f, 16f, 4.5f)
        curveTo(18.4f, 4.5f, 20.5f, 6.4f, 20.5f, 9.2f)
        curveTo(20.5f, 15.2f, 12f, 20.5f, 12f, 20.5f)
        close()
    }

    val Check = icon("Check") { moveTo(5f, 12.5f); lineTo(9.5f, 17f); lineTo(19f, 7.5f) }
    val ChevronRight = icon("ChevronRight") { moveTo(9.5f, 6f); lineTo(15.5f, 12f); lineTo(9.5f, 18f) }
    val ChevronLeft = icon("ChevronLeft") { moveTo(14.5f, 6f); lineTo(8.5f, 12f); lineTo(14.5f, 18f) }
    val Alert = icon("Alert") { moveTo(12f, 6.5f); lineTo(12f, 13.5f); moveTo(12f, 17.5f); lineTo(12f, 17.6f) }

    /** 걸음: 발자국 두 개 */
    val Steps = icon("Steps", filled = true) {
        oval(8.3f, 7.4f, 3.2f, 4.6f); oval(8.3f, 15.2f, 2.4f, 1.6f)
        oval(15.7f, 10.8f, 3.2f, 4.6f); oval(15.7f, 18.6f, 2.4f, 1.6f)
    }
    val Heart = icon("Heart") { heart() }
    /** 산소포화도: 물방울 */
    val Drop = icon("Drop") {
        moveTo(12f, 3.5f)
        curveTo(12f, 3.5f, 5.5f, 10.4f, 5.5f, 14.5f)
        curveTo(5.5f, 18.1f, 8.4f, 20.5f, 12f, 20.5f)
        curveTo(15.6f, 20.5f, 18.5f, 18.1f, 18.5f, 14.5f)
        curveTo(18.5f, 10.4f, 12f, 3.5f, 12f, 3.5f)
        close()
    }
    /** 수면: 초승달 */
    val Moon = icon("Moon") {
        moveTo(20f, 14.6f)
        curveTo(18.9f, 15.1f, 17.7f, 15.3f, 16.5f, 15.3f)
        curveTo(12f, 15.3f, 8.5f, 11.7f, 8.5f, 7.3f)
        curveTo(8.5f, 6f, 8.8f, 4.8f, 9.3f, 3.7f)
        curveTo(5.9f, 4.9f, 3.5f, 8.2f, 3.5f, 12f)
        curveTo(3.5f, 16.7f, 7.3f, 20.5f, 12f, 20.5f)
        curveTo(15.7f, 20.5f, 18.9f, 18.1f, 20f, 14.6f)
        close()
    }
    /** 활동: 번개 */
    val Bolt = icon("Bolt") {
        moveTo(13f, 3f); lineTo(5f, 13.5f); lineTo(11f, 13.5f); lineTo(10f, 21f); lineTo(18.5f, 10.5f); lineTo(12.5f, 10.5f); close()
    }
    /** 신체 측정: 사람 */
    val Body = icon("Body") {
        oval(12f, 8f, 3.5f, 3.5f)
        moveTo(5f, 20.5f)
        curveTo(5f, 16.9f, 8.1f, 14.5f, 12f, 14.5f)
        curveTo(15.9f, 14.5f, 19f, 16.9f, 19f, 20.5f)
    }
    val Sync = icon("Sync") {
        moveTo(19.5f, 10.5f); curveTo(18.5f, 7f, 15.5f, 4.5f, 12f, 4.5f); curveTo(9.2f, 4.5f, 6.8f, 6f, 5.5f, 8.3f)
        moveTo(5f, 4.5f); lineTo(5f, 8.8f); lineTo(9.3f, 8.8f)
        moveTo(4.5f, 13.5f); curveTo(5.5f, 17f, 8.5f, 19.5f, 12f, 19.5f); curveTo(14.8f, 19.5f, 17.2f, 18f, 18.5f, 15.7f)
        moveTo(19f, 19.5f); lineTo(19f, 15.2f); lineTo(14.7f, 15.2f)
    }
    val Watch = icon("Watch") {
        moveTo(9.5f, 6f); lineTo(9.5f, 3f); lineTo(14.5f, 3f); lineTo(14.5f, 6f)
        moveTo(9.5f, 18f); lineTo(9.5f, 21f); lineTo(14.5f, 21f); lineTo(14.5f, 18f)
        moveTo(9.5f, 6f); lineTo(14.5f, 6f); curveTo(15.9f, 6f, 17f, 7.1f, 17f, 8.5f); lineTo(17f, 15.5f)
        curveTo(17f, 16.9f, 15.9f, 18f, 14.5f, 18f); lineTo(9.5f, 18f); curveTo(8.1f, 18f, 7f, 16.9f, 7f, 15.5f)
        lineTo(7f, 8.5f); curveTo(7f, 7.1f, 8.1f, 6f, 9.5f, 6f); close()
        moveTo(12f, 9.5f); lineTo(12f, 12f); lineTo(13.5f, 13f)
    }
    val Logout = icon("Logout") {
        moveTo(14f, 4.5f); lineTo(6.5f, 4.5f); curveTo(5.4f, 4.5f, 4.5f, 5.4f, 4.5f, 6.5f); lineTo(4.5f, 17.5f)
        curveTo(4.5f, 18.6f, 5.4f, 19.5f, 6.5f, 19.5f); lineTo(14f, 19.5f)
        moveTo(10f, 12f); lineTo(20f, 12f); moveTo(16.5f, 8.5f); lineTo(20f, 12f); lineTo(16.5f, 15.5f)
    }

    // 하단 메뉴
    val Home = icon("Home") {
        moveTo(4f, 10.5f); lineTo(12f, 4f); lineTo(20f, 10.5f)
        moveTo(6f, 9f); lineTo(6f, 19f); curveTo(6f, 19.6f, 6.4f, 20f, 7f, 20f); lineTo(10f, 20f); lineTo(10f, 15f); lineTo(14f, 15f)
        lineTo(14f, 20f); lineTo(17f, 20f); curveTo(17.6f, 20f, 18f, 19.6f, 18f, 19f); lineTo(18f, 9f)
    }
    /** 기록: 시계 */
    val History = icon("History") { oval(12f, 12f, 8.5f, 8.5f); moveTo(12f, 7.5f); lineTo(12f, 12f); lineTo(15f, 14f) }
    /** AI 코치: 말풍선 */
    val Coach = icon("Coach") {
        moveTo(5f, 5f); lineTo(19f, 5f); curveTo(19.6f, 5f, 20f, 5.4f, 20f, 6f); lineTo(20f, 15f)
        curveTo(20f, 15.6f, 19.6f, 16f, 19f, 16f); lineTo(10f, 16f); lineTo(6f, 19.5f); lineTo(6f, 16f); lineTo(5f, 16f)
        curveTo(4.4f, 16f, 4f, 15.6f, 4f, 15f); lineTo(4f, 6f); curveTo(4f, 5.4f, 4.4f, 5f, 5f, 5f); close()
        moveTo(8.5f, 10.5f); lineTo(8.6f, 10.5f); moveTo(12f, 10.5f); lineTo(12.1f, 10.5f); moveTo(15.5f, 10.5f); lineTo(15.6f, 10.5f)
    }
    /** 대화 목록 열기: 왼쪽 칸이 있는 창(웹 ConversationListToggle 과 같은 모양) */
    val Panel = icon("Panel") {
        moveTo(5f, 4f); lineTo(19f, 4f); curveTo(20.1f, 4f, 21f, 4.9f, 21f, 6f); lineTo(21f, 18f); curveTo(21f, 19.1f, 20.1f, 20f, 19f, 20f)
        lineTo(5f, 20f); curveTo(3.9f, 20f, 3f, 19.1f, 3f, 18f); lineTo(3f, 6f); curveTo(3f, 4.9f, 3.9f, 4f, 5f, 4f); close()
        moveTo(9f, 4f); lineTo(9f, 20f)
    }
    val Plus = icon("Plus") { moveTo(12f, 5f); lineTo(12f, 19f); moveTo(5f, 12f); lineTo(19f, 12f) }
    /** 보내기: 종이비행기(웹 질문 보내기 버튼과 같은 모양) */
    val Send = icon("Send", filled = true) {
        moveTo(21.4f, 2.6f); curveTo(21.1f, 2.3f, 20.7f, 2.2f, 20.35f, 2.37f); lineTo(3.35f, 8.87f)
        curveTo(2.5f, 9.2f, 2.5f, 10.45f, 3.37f, 10.75f); lineTo(10.47f, 13.25f); lineTo(12.97f, 20.35f)
        curveTo(13.27f, 21.2f, 14.5f, 21.2f, 14.85f, 20.37f); lineTo(21.35f, 3.37f); curveTo(21.5f, 3.0f, 21.4f, 2.8f, 21.4f, 2.6f); close()
    }
    /** 설정: 조절 막대 세 줄 */
    val Settings = icon("Settings") {
        moveTo(4f, 7f); lineTo(20f, 7f); moveTo(4f, 12f); lineTo(20f, 12f); moveTo(4f, 17f); lineTo(20f, 17f)
        oval(9f, 7f, 2f, 2f); oval(15f, 12f, 2f, 2f); oval(8f, 17f, 2f, 2f)
    }
    val Battery = icon("Battery") {
        moveTo(5.5f, 7f); lineTo(16.5f, 7f); curveTo(17.3f, 7f, 18f, 7.7f, 18f, 8.5f); lineTo(18f, 15.5f)
        curveTo(18f, 16.3f, 17.3f, 17f, 16.5f, 17f); lineTo(5.5f, 17f); curveTo(4.7f, 17f, 4f, 16.3f, 4f, 15.5f); lineTo(4f, 8.5f)
        curveTo(4f, 7.7f, 4.7f, 7f, 5.5f, 7f); close()
        moveTo(21f, 10.5f); lineTo(21f, 13.5f); moveTo(7.5f, 10.5f); lineTo(7.5f, 13.5f); moveTo(11f, 10.5f); lineTo(11f, 13.5f)
    }
    /** 권한: 방패 */
    val Shield = icon("Shield") {
        moveTo(12f, 3.5f); lineTo(19f, 6.5f); lineTo(19f, 11.5f); curveTo(19f, 15.8f, 16f, 19.2f, 12f, 20.5f)
        curveTo(8f, 19.2f, 5f, 15.8f, 5f, 11.5f); lineTo(5f, 6.5f); close()
        moveTo(9f, 12f); lineTo(11.2f, 14.2f); lineTo(15.2f, 10.2f)
    }
    val Minus = icon("Minus") { moveTo(7f, 12f); lineTo(17f, 12f) }
}

/** 둥근 네모 안에 아이콘. 지표·묶음 표시에 쓴다 */
@Composable
fun IconTile(icon: ImageVector, tint: Color, background: Color, size: Dp = 36.dp, iconSize: Dp = 20.dp) {
    Box(Modifier.size(size).background(background, RoundedCornerShape(size * 0.32f)), contentAlignment = Alignment.Center) {
        Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(iconSize))
    }
}

/**
 * 런처 아이콘·시작 화면과 같은 앱 마크: 연분홍 바탕(웹 primary-soft)에 웹 파비콘과 같은 사탕.
 * 아이콘을 누르고 시작 화면을 거쳐 홈까지 사탕과 바탕색이 바뀌지 않게 한다
 */
@Composable
fun LinkMark(size: Dp = 40.dp) {
    Box(Modifier.size(size).background(Link.Pink, RoundedCornerShape(size * 0.3f)), contentAlignment = Alignment.Center) {
        Image(painterResource(R.drawable.candy), contentDescription = null, modifier = Modifier.width(size * 0.8f))
    }
}

/** 와이어프레임 로고: 앱 마크 · CANDiY · 분홍 알약 안의 LiNK */
@Composable
fun LinkLogo(modifier: Modifier = Modifier) {
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        LinkMark(28.dp)
        Spacer(Modifier.width(8.dp))
        Text("CANDiY", fontSize = 19.sp, fontWeight = FontWeight.ExtraBold, color = Link.Text, letterSpacing = (-0.3).sp)
        Spacer(Modifier.width(6.dp))
        Text(
            "LiNK", fontSize = 12.sp, fontWeight = FontWeight.ExtraBold, color = Link.Primary,
            modifier = Modifier.background(Link.Pink, RoundedCornerShape(50)).padding(horizontal = 9.dp, vertical = 2.dp),
        )
    }
}
