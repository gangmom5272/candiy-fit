package io.candiy.healthconnect.quickstart

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.Instant
import java.time.LocalDate

private val GROUPS = listOf(
    Triple(LinkIcons.Heart, "필수 4종", "걸음 · 심박 · 산소포화도 · 수면"),
    Triple(LinkIcons.Bolt, "활동 5종", "운동 · 거리 · 칼로리 · 속도 · VO2 Max"),
    Triple(LinkIcons.Body, "신체 측정 4종", "키 · 몸무게(직접 입력) · 체지방 · 기초대사량"),
)

private val NEEDS = listOf(
    "백그라운드에서 읽기" to "앱을 닫아도 15분마다 보내요",
    "과거 기록 읽기" to "첫 동기화 때 최근 30일을 보내요",
    "기록 저장 (쓰기)" to "워치 기록과 키·몸무게를 Health Connect 에 남겨요",
)

/**
 * 권한 안내 (와이어프레임 A-02). 로그인 뒤 권한이 모자랄 때 시스템 권한 창 전에 보여 준다.
 * rationale = true 면 Health Connect 의 "이 앱이 권한을 쓰는 이유"에서 열린 것이라 허용 버튼 없이 설명과 닫기만 둔다.
 * 항목을 고르는 기능은 두지 않는다 — 시스템 창에서 항목마다 끄고 켤 수 있다.
 */
@Composable
fun PermissionGuideScreen(rationale: Boolean, message: String, onAllow: () -> Unit, onLater: () -> Unit) {
    BackHandler(onBack = onLater)
    // 설명은 위아래로 밀고, 버튼은 화면 아래에 고정한다(설명이 길어 버튼이 첫 화면 밖으로 밀려났다)
    Column(Modifier.fillMaxSize().background(Link.Background).safeDrawingPadding()) {
    Column(
        Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text(if (rationale) "CANDiY-LiNK 가 쓰는 기록" else "기록 권한", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Primary)
        Text(
            if (rationale) "건강 기록을\n이렇게 써요." else "가져올 기록을\n확인해 주세요.",
            fontSize = 24.sp, lineHeight = 32.sp, fontWeight = FontWeight.Bold, color = Link.Text,
        )
        Text("허용한 기록만 모아 서버로 보내요. 건강 수치는 이 앱 화면에 띄우지 않아요.", fontSize = 14.sp, color = Link.Sub, lineHeight = 21.sp)

        Column(Modifier.fillMaxWidth().background(Link.Surface, RoundedCornerShape(16.dp)).padding(horizontal = 16.dp)) {
            GROUPS.forEachIndexed { i, (glyph, title, sub) ->
                if (i > 0) HorizontalDivider(color = Link.Border)
                Row(Modifier.padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconTile(glyph, if (i == 0) Color.White else Link.Primary, if (i == 0) Link.Primary else Link.Pink, size = 40.dp, iconSize = 22.dp)
                    Spacer(Modifier.width(14.dp))
                    Column {
                        Text(title, fontSize = 15.sp, fontWeight = FontWeight.Bold, color = Link.Text)
                        Text(sub, fontSize = 13.sp, color = Link.Muted)
                    }
                }
            }
        }

        Text("동기화에 필요한 권한", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Sub)
        NEEDS.forEach { (title, sub) ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(LinkIcons.Check, contentDescription = null, tint = Link.Primary, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(10.dp))
                Column {
                    Text(title, fontSize = 15.sp, color = Link.Text)
                    Text(sub, fontSize = 12.sp, color = Link.Muted)
                }
            }
        }

        Column(Modifier.fillMaxWidth().background(Link.Pink, RoundedCornerShape(16.dp)).padding(16.dp)) {
            Text(
                "보낸 기록은 코칭 웹(CANDiY-FiT)에서 같은 계정으로 봐요. 권한은 Health Connect 설정에서 언제든 끌 수 있어요.",
                fontSize = 13.sp, color = Link.Sub, lineHeight = 20.sp,
            )
            Spacer(Modifier.height(6.dp))
            Text("진단 · 치료를 대신하지 않아요.", fontSize = 12.sp, color = Link.Muted)
        }

    }
    Column(Modifier.fillMaxWidth().padding(start = 24.dp, end = 24.dp, bottom = 12.dp, top = 8.dp)) {
        if (message.isNotEmpty()) Text(message, fontSize = 14.sp, color = Link.Bad, modifier = Modifier.padding(bottom = 8.dp))
        if (rationale) {
            Button(onClick = onLater, modifier = Modifier.fillMaxWidth().height(54.dp), shape = RoundedCornerShape(16.dp)) {
                Text("닫기", fontWeight = FontWeight.Bold)
            }
        } else {
            Button(onClick = onAllow, modifier = Modifier.fillMaxWidth().height(54.dp), shape = RoundedCornerShape(16.dp)) {
                Text("권한 허용하기", fontWeight = FontWeight.Bold)
            }
            TextButton(onClick = onLater, modifier = Modifier.align(Alignment.CenterHorizontally)) {
                Text("나중에 하기", color = Link.Muted)
            }
        }
    }
    }
}

/**
 * 연결 완료 (와이어프레임 A-03-1 · A-03-2 · A-03-4). 권한을 허용한 직후 한 번 보여 준다.
 * 동기화 등록 결과와 서버 완료 보고가 끝나기 전엔 준비 중, 서버가 받으면(HTTP 200) 완료와 다음 동기화 시각,
 * 등록 실패면 SDK 문구와 다시 시도, 등록은 됐는데 서버에 알리지 못했으면 그 안내와 보고만 다시 시도(S15P31A403-366).
 */
@Composable
fun ConnectedScreen(state: DashState, onHome: () -> Unit, onRetry: () -> Unit, onRetryReport: () -> Unit) {
    BackHandler(onBack = onHome)
    val today = LocalDate.now()
    val missing = (state.permTotal - state.permGranted).coerceAtLeast(0)
    // 완료는 서버가 보고를 받은 뒤에만 보여 준다. 등록은 됐는데 알리지 못했으면 따로 안내한다
    val phase = ConnectedPhase.of(state.statusOk, state.connection)
    Column(
        Modifier.fillMaxSize().background(Link.Background).safeDrawingPadding().padding(horizontal = 24.dp, vertical = 20.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(Modifier.weight(1f))
        when (phase) {
            ConnectedPhase.PREPARING -> {
                Box(Modifier.size(120.dp).background(Link.Pink, CircleShape), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = Link.Red, strokeWidth = 6.dp, modifier = Modifier.size(72.dp))
                }
                Spacer(Modifier.height(28.dp))
                Title("기록 연결을\n준비하고 있어요.")
                Spacer(Modifier.height(10.dp))
                Text("보통 몇 초 안에 끝나요.", fontSize = 15.sp, color = Link.Muted)
            }
            ConnectedPhase.DONE -> {
                Box(Modifier.size(120.dp).background(Link.Pink, CircleShape), contentAlignment = Alignment.Center) {
                    Box(Modifier.size(80.dp).background(Link.Primary, CircleShape), contentAlignment = Alignment.Center) {
                        Icon(LinkIcons.Check, contentDescription = null, tint = Color.White, modifier = Modifier.size(42.dp))
                    }
                }
                Spacer(Modifier.height(28.dp))
                Title("기록 연결을\n마쳤어요.")
                Spacer(Modifier.height(10.dp))
                Text("앱을 닫아도 15분마다\n자동으로 보내요.", fontSize = 15.sp, color = Link.Muted, textAlign = TextAlign.Center)
                Spacer(Modifier.height(24.dp))
                Column(
                    Modifier.fillMaxWidth().background(Link.Pink, RoundedCornerShape(20.dp)).padding(vertical = 18.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text("다음 동기화", fontSize = 13.sp, color = Link.Sub)
                    Text(
                        state.nextSyncAt?.let { HomeStatus.clock(Instant.ofEpochMilli(it), today) } ?: "곧",
                        fontSize = 34.sp, fontWeight = FontWeight.ExtraBold, color = Link.Primary,
                    )
                    Text("첫 동기화는 최근 30일을 보내요", fontSize = 12.sp, color = Link.Muted)
                }
                if (missing > 0) {
                    Spacer(Modifier.height(12.dp))
                    Text("권한 ${missing}개가 빠져 있어요 · 홈에서 다시 허용할 수 있어요", fontSize = 13.sp, color = Link.Amber)
                }
            }
            ConnectedPhase.REPORT_PENDING -> {
                Box(Modifier.size(120.dp).background(Link.AmberBg, CircleShape), contentAlignment = Alignment.Center) {
                    Icon(LinkIcons.Sync, contentDescription = null, tint = Link.Amber, modifier = Modifier.size(48.dp))
                }
                Spacer(Modifier.height(28.dp))
                Title("앱 설정은 끝났지만\n웹 반영을 기다리고 있어요.")
                Spacer(Modifier.height(10.dp))
                Text(
                    "기록은 15분마다 자동으로 보내요.\n웹에 연동 상태가 보이지 않으면 다시 시도해 주세요.",
                    fontSize = 14.sp, color = Link.Muted, textAlign = TextAlign.Center,
                )
            }
            ConnectedPhase.SYNC_FAILED -> {
                Box(Modifier.size(120.dp).background(Link.BadBg, CircleShape), contentAlignment = Alignment.Center) {
                    Icon(LinkIcons.Alert, contentDescription = null, tint = Link.Bad, modifier = Modifier.size(48.dp))
                }
                Spacer(Modifier.height(28.dp))
                Title("동기화를\n시작하지 못했어요.")
                Spacer(Modifier.height(10.dp))
                Text(state.status, fontSize = 14.sp, color = Link.Bad, textAlign = TextAlign.Center)
                Text("Wi-Fi 나 모바일 데이터를 확인하고 다시 시도해 주세요.", fontSize = 13.sp, color = Link.Muted, textAlign = TextAlign.Center)
            }
        }
        Spacer(Modifier.weight(1f))
        if (phase == ConnectedPhase.SYNC_FAILED || phase == ConnectedPhase.REPORT_PENDING) {
            // 등록 실패는 등록부터, 보고 실패는 보고만 다시 한다(등록을 다시 하면 동기화가 처음부터 다시 돈다)
            Button(
                onClick = if (phase == ConnectedPhase.SYNC_FAILED) onRetry else onRetryReport,
                modifier = Modifier.fillMaxWidth().height(54.dp), shape = RoundedCornerShape(16.dp),
            ) {
                Text("다시 시도", fontWeight = FontWeight.Bold)
            }
            TextButton(onClick = onHome) { Text("나중에 하기", color = Link.Muted) }
        } else {
            Text("워치 앱도 설치하면 걸음을 15분 단위로 받아요", fontSize = 12.sp, color = Link.Primary)
            Spacer(Modifier.height(12.dp))
            // 등록 결과가 늦어도 갇히지 않게 언제든 홈으로 갈 수 있다(홈 상단 카드가 결과를 이어서 보여 준다)
            Button(onClick = onHome, modifier = Modifier.fillMaxWidth().height(54.dp), shape = RoundedCornerShape(16.dp)) {
                Text("홈으로", fontWeight = FontWeight.Bold)
            }
        }
    }
}

/** 연결 완료 화면의 단계. 동기화 등록 결과와 서버 완료 보고 상태로 정한다 */
enum class ConnectedPhase {
    PREPARING, DONE, REPORT_PENDING, SYNC_FAILED;

    companion object {
        fun of(syncOk: Boolean?, report: ConnectionReport): ConnectedPhase = when {
            syncOk == false -> SYNC_FAILED
            report == ConnectionReport.REPORTED -> DONE
            report == ConnectionReport.PENDING -> REPORT_PENDING
            // 등록 결과를 기다리거나(null), 등록은 됐고 서버에 알리는 중
            else -> PREPARING
        }
    }
}

@Composable
private fun Title(text: String) {
    Text(text, fontSize = 24.sp, lineHeight = 32.sp, fontWeight = FontWeight.Bold, color = Link.Text, textAlign = TextAlign.Center)
}
