package io.candiy.healthconnect.quickstart

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** 하단 메뉴 (와이어프레임 A-04 하단 내비게이션). 와이어프레임의 "연결"은 뜻이 바로 읽히도록 "홈"으로 부른다 */
enum class Tab(val label: String, val icon: ImageVector) {
    HOME("홈", LinkIcons.Home),
    HISTORY("기록", LinkIcons.History),
    // 멘토링(11-05) 뒤 앱 통합 첫 단계: 웹 AI 코치를 앱에서도 쓴다
    COACH("AI 코치", LinkIcons.Coach),
    SETTINGS("설정", LinkIcons.Settings),
}

/**
 * 로그인 뒤 본 화면: (연결이 끊겼으면 안내 한 줄) 위에 고른 탭의 내용, 아래에 하단 메뉴. 홈이 아닌 탭에서 뒤로 가면 홈으로 온다.
 * 키보드가 떠 있는 동안(AI 코치 질문 입력)은 하단 메뉴를 숨겨 대화 칸을 넓힌다. 메뉴까지 키보드 위로 올라오면
 * 휴대폰에서 대화가 몇 줄밖에 안 보였다(11-06 실기기)
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MainTabs(tab: Tab, online: Boolean, onTab: (Tab) -> Unit, content: @Composable (Tab) -> Unit) {
    BackHandler(enabled = tab != Tab.HOME) { onTab(Tab.HOME) }
    val typing = WindowInsets.isImeVisible
    Column(Modifier.fillMaxSize().background(Link.Background).safeDrawingPadding()) {
        // 연결이 끊기면 어느 탭에서든 위에 이유를 알린다. 목록·기록이 비어 보이는 까닭을 알 수 있게
        if (!online) OfflineBanner()
        Box(Modifier.weight(1f).fillMaxWidth()) { content(tab) }
        if (typing) return@Column
        HorizontalDivider(color = Link.Surface)
        // 창 여백은 바깥 Column 이 이미 뺐다
        NavigationBar(containerColor = Color.White, tonalElevation = 0.dp, windowInsets = WindowInsets(0)) {
            Tab.entries.forEach { item ->
                val on = item == tab
                NavigationBarItem(
                    selected = on,
                    onClick = { onTab(item) },
                    icon = { Icon(item.icon, contentDescription = null, modifier = Modifier.size(22.dp)) },
                    label = { Text(item.label, fontSize = 12.sp, fontWeight = if (on) FontWeight.Bold else FontWeight.Medium) },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = Link.Primary,
                        selectedTextColor = Link.Text,
                        indicatorColor = Link.Pink,
                        unselectedIconColor = Link.Muted,
                        unselectedTextColor = Link.Muted,
                    ),
                )
            }
        }
    }
}
