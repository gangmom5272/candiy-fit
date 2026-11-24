package io.candiy.healthconnect.quickstart

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.LocalDate

/**
 * 설정 (와이어프레임 A-06 · A-06-1). 계정 · 수집 상태 · 프로필 · 로그아웃 · 버전.
 * 와이어프레임의 수집 끄기 토글과 주기 선택(15·30·60분)은 아직 앱에 기능이 없어 버튼 대신 지금 상태만 보여 준다.
 * 사용자 ID(UUID)는 화면에 띄우지 않는다.
 */
@Composable
fun SettingsScreen(
    state: DashState,
    busy: Boolean,
    onOpenBody: () -> Unit,
    onOpenPermissions: () -> Unit,
    onOpenBattery: () -> Unit,
    onLogout: () -> Unit,
) {
    var confirmLogout by remember { mutableStateOf(false) }
    if (confirmLogout) LogoutDialog(onConfirm = { confirmLogout = false; onLogout() }, onDismiss = { confirmLogout = false })
    val home = HomeStatus.state(
        syncOk = state.statusOk,
        nextSyncAt = state.nextSyncAt,
        missingPermissions = (state.permTotal - state.permGranted).coerceAtLeast(0),
        watchNearby = state.watchNearby,
        nowMs = System.currentTimeMillis(),
    )
    val missing = (state.permTotal - state.permGranted).coerceAtLeast(0)

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text("설정", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = Link.Text)

        // 계정
        Row(
            Modifier.fillMaxWidth().background(Link.Surface, RoundedCornerShape(18.dp)).padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(44.dp).background(Link.Pink, CircleShape), contentAlignment = Alignment.Center) {
                Text(state.account.take(1).ifBlank { "?" }, fontSize = 18.sp, fontWeight = FontWeight.ExtraBold, color = Link.Primary)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(state.account.ifBlank { "로그인한 계정" }, fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Link.Text)
                Text("코칭 웹(CANDiY-FiT)과 같은 계정이에요", fontSize = 12.sp, color = Link.Muted)
                // 목업·수집 전용 모드는 debug 빌드에서만 켜진다
                if (BuildConfig.DEBUG && state.modeLabel.isNotBlank()) Text("개발용 · ${state.modeLabel}", fontSize = 11.sp, color = Link.Disabled)
            }
        }

        Section("수집")
        Group {
            SettingRow(
                LinkIcons.Sync, "자동 동기화",
                when (home) {
                    HomeState.OK, HomeState.WATCH, HomeState.PERMISSION -> "켜짐 · 15분마다"
                    HomeState.LATE -> "켜짐 · 예정 시각이 지났어요"
                    HomeState.PREPARING -> "등록하는 중"
                    HomeState.FAILED -> "시작하지 못했어요 · 홈에서 다시 시도"
                },
                subColor = if (home == HomeState.FAILED) Link.Bad else if (home == HomeState.LATE) Link.Amber else Link.Muted,
            )
            Divider()
            SettingRow(
                LinkIcons.Shield, "Health Connect 권한",
                if (state.permTotal == 0) "확인하는 중" else if (missing == 0) "모두 허용 · ${state.permTotal}개" else "${missing}개 꺼짐 · 눌러서 다시 허용",
                subColor = if (missing > 0) Link.Bad else Link.Muted, onClick = onOpenPermissions,
            )
            Divider()
            val (watchLine, watchWarn) = HomeStatus.watchSetting(state.watchNearby, state.watchLastReceived, LocalDate.now())
            SettingRow(LinkIcons.Watch, "워치", watchLine, subColor = if (watchWarn) Link.Amber else Link.Muted)
            Divider()
            SettingRow(
                LinkIcons.Battery, "배터리 사용",
                "'제한 없음'으로 두면 15분 주기가 잘 지켜져요", onClick = onOpenBattery,
            )
        }

        Section("프로필")
        Group {
            SettingRow(
                LinkIcons.Body, "키 · 몸무게",
                state.bodySavedOn?.let { "마지막 저장 $it" } ?: "아직 입력하지 않았어요",
                subColor = if (state.bodySavedOn == null) Link.Amber else Link.Muted, onClick = onOpenBody,
            )
        }

        if (state.canLogout) {
            Spacer(Modifier.height(4.dp))
            Group {
                Row(
                    Modifier.fillMaxWidth().clickable(enabled = !busy) { confirmLogout = true }.padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(LinkIcons.Logout, contentDescription = null, tint = Link.Bad, modifier = Modifier.size(20.dp))
                    Spacer(Modifier.width(10.dp))
                    Text(if (busy) "로그아웃 중…" else "로그아웃", fontSize = 15.sp, fontWeight = FontWeight.Bold, color = Link.Bad)
                }
            }
        }

        Text(
            "앱 ${BuildConfig.VERSION_NAME} · CANDiY SDK 0.1.9",
            fontSize = 12.sp, color = Link.Disabled, modifier = Modifier.padding(top = 8.dp, start = 4.dp),
        )
    }
}

@Composable
private fun Section(title: String) {
    Text(title, fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Sub, modifier = Modifier.padding(top = 8.dp, start = 4.dp))
}

@Composable
private fun Group(content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().background(Link.Surface, RoundedCornerShape(18.dp))) { content() }
}

@Composable
private fun Divider() = HorizontalDivider(color = Color.White, modifier = Modifier.padding(horizontal = 14.dp))

@Composable
private fun SettingRow(icon: ImageVector, title: String, sub: String, subColor: Color = Link.Muted, onClick: (() -> Unit)? = null) {
    Row(
        Modifier.fillMaxWidth().let { if (onClick != null) it.clickable(onClick = onClick) else it }.padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconTile(icon, Link.Primary, Color.White, size = 34.dp, iconSize = 18.dp)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = Link.Text)
            Text(sub, fontSize = 12.sp, color = subColor)
        }
        if (onClick != null) Icon(LinkIcons.ChevronRight, contentDescription = null, tint = Link.Disabled, modifier = Modifier.size(18.dp))
    }
}
