package io.candiy.healthconnect.quickstart

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.Instant
import java.time.LocalDate

/** 화면에 그릴 값 묶음. 건강 수치 대신 기록 건수·시각·전송 결과만 담는다. */
data class DashState(
    /** 로그인한 회원 표시(닉네임 · 로그인 종류). 사용자 ID(UUID)는 화면에 띄우지 않는다 */
    val account: String,
    val modeLabel: String,
    /** 수집 전용 모드에서는 false — 로그아웃이 기기 기록을 지우기 때문에 버튼을 두지 않는다 */
    val canLogout: Boolean = true,
    /** SDK 시작 콜백 문구. 실패했을 때 상단 카드에 그대로 보여 준다 */
    val status: String = "권한을 확인하는 중",
    val statusOk: Boolean? = null,
    val lastSync: FileLog.LastSync? = null,
    val nextSyncAt: Long? = null,
    val permGranted: Int = 0,
    val permTotal: Int = 0,
    val watchNearby: Int? = null,
    /** 워치 자동 수집분을 마지막으로 받아 Health Connect 에 저장한 시각(ms). 받은 적 없으면 null */
    val watchLastReceived: Long? = null,
    /** 13종. 앞 네 개가 와이어프레임 홈의 핵심 지표(HomeStatus.CORE) */
    val metrics: List<MetricStatus> = emptyList(),
    val mockResult: String = "",
    val bodySavedOn: String? = null,
    val bodyNotice: String = "",
    /** 동기화 회차 (전송 기록 탭, 와이어프레임 A-05). 최근 회차부터 */
    val history: List<SyncRun> = emptyList(),
    /** 앱을 열고 첫 읽기(refresh)가 끝났는지. 끝나기 전에는 지표·전송 기록 자리에 스켈레톤을 둔다 */
    val loaded: Boolean = false,
    /** 앱 연동 완료를 서버에 알렸는지(S15P31A403-366). 연결 완료 화면은 REPORTED 일 때만 완료로 보여 준다 */
    val connection: ConnectionReport = ConnectionReport.IDLE,
)

/**
 * 홈 (와이어프레임 A-04). 스크롤 없이 한 화면에 들어가게 둔다(작은 화면에서만 밀린다).
 * 상태 카드 → 핵심 4종 → 나머지 9종·키몸무게 진입 줄 → 지금 동기화.
 * 기록이 Health Connect 에 들어왔는지(건수·마지막 측정)와 서버로 갔는지(지표별 마지막 전송)를 본다.
 * 나머지 9종은 아래에서 올라오는 시트로, 키·몸무게 입력은 별도 화면(와이어프레임 A-06 프로필 자리)으로 연다.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Dashboard(
    state: DashState,
    busy: Boolean,
    onSyncNow: () -> Unit,
    onRefresh: () -> Unit,
    onRequestPermission: () -> Unit,
    onOpenBody: () -> Unit,
) {
    var showOthers by remember { mutableStateOf(false) }
    val today = LocalDate.now()
    val home = HomeStatus.state(
        syncOk = state.statusOk,
        nextSyncAt = state.nextSyncAt,
        missingPermissions = (state.permTotal - state.permGranted).coerceAtLeast(0),
        watchNearby = state.watchNearby,
        nowMs = System.currentTimeMillis(),
    )
    val core = state.metrics.filter { it.key in HomeStatus.CORE }
    val others = state.metrics.filter { it.key !in HomeStatus.CORE }
    if (showOthers) {
        // 반만 열리면 마지막 지표가 가려져 처음부터 다 펼친다
        ModalBottomSheet(
            onDismissRequest = { showOthers = false },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
            containerColor = Color.White,
            tonalElevation = 0.dp,
        ) {
            OthersSheet(others, today)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Link.Background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 6.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        // 머리말: 로고와 지금 동기화. 계정·로그아웃은 설정 탭(와이어프레임 A-06)에 있다.
        // 하단 메뉴가 생겨 아래쪽 큰 버튼은 첫 화면 밖으로 밀려 머리말 오른쪽 작은 버튼으로 옮겼다
        Row(Modifier.padding(top = 6.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            LinkLogo(Modifier.weight(1f))
            Row(
                Modifier.background(if (busy) Link.Surface else Link.Pink, RoundedCornerShape(50))
                    .clickable(enabled = !busy, onClick = onSyncNow).padding(horizontal = 12.dp, vertical = 7.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(LinkIcons.Sync, contentDescription = null, tint = if (busy) Link.Disabled else Link.Primary, modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(5.dp))
                Text(if (busy) "보내는 중" else "지금 동기화", fontSize = 13.sp, fontWeight = FontWeight.Bold,
                    color = if (busy) Link.Disabled else Link.Primary)
            }
        }

        // 첫 읽기 전에는 다음 동기화 예정·워치 연결을 아직 몰라 "준비 중"·"아직 전송한 기록이 없어요"가 잠깐 보였다 바뀐다.
        // 실제 상태가 아닌 문구가 번쩍이지 않게 제목·상태 카드도 같은 자리의 스켈레톤으로 둔다(시작 실패는 바로 알린다)
        val waiting = !state.loaded && state.statusOk != false
        Column {
            Text(HomeStatus.dateLine(today), fontSize = 13.sp, color = Link.Muted)
            Spacer(Modifier.height(4.dp))
            if (waiting) {
                Column(Modifier.semantics { contentDescription = "기록 상태를 확인하는 중" }) {
                    LinkSkeleton(Modifier.padding(vertical = 4.dp).width(150.dp).height(21.dp))
                    LinkSkeleton(Modifier.padding(vertical = 4.dp).width(190.dp).height(21.dp))
                }
            } else {
                Text(TITLES.getValue(home), fontSize = 22.sp, lineHeight = 29.sp, fontWeight = FontWeight.Bold, color = Link.Text)
            }
        }

        if (waiting) StatusCardSkeleton() else StatusCard(home, state, today, onSyncNow, onRefresh, onRequestPermission)

        // 핵심 4종
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.Bottom) {
                Text("오늘 기록", fontSize = 18.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.weight(1f))
                Text("최근 24시간 · Health Connect 기준", fontSize = 11.sp, color = Link.Muted)
            }
            if (!state.loaded) {
                // Health Connect 를 쓸 수 없어 읽기를 시작하지 못하면 위 상태 카드가 이유를 알려 준다
                if (state.statusOk == false) Text("기록을 읽지 못했어요", fontSize = 13.sp, color = Link.Muted)
                else CoreSkeleton()
            }
            core.chunked(2).forEach { pair ->
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    pair.forEach { MetricCard(it, today, Modifier.weight(1f)) }
                    if (pair.size == 1) Spacer(Modifier.weight(1f))
                }
            }
        }

        // 나머지 9종 · 키몸무게 — 한 상자에 진입 줄 두 개
        Column(Modifier.fillMaxWidth().border(1.5.dp, Link.Border, RoundedCornerShape(16.dp))) {
            if (others.isNotEmpty()) {
                val withData = others.count { (it.count24h ?: 0) > 0 }
                val trouble = others.count { !it.permitted || it.transfer?.ok == false }
                EntryRow(
                    icon = LinkIcons.Bolt,
                    title = "나머지 ${others.size}종",
                    sub = "활동 · 신체 측정 · 기록 있음 ${withData}종" + if (trouble > 0) " · 확인 필요 ${trouble}종" else " · 전송 문제 없음",
                    subColor = if (trouble > 0) Link.Bad else Link.Muted,
                    onClick = { showOthers = true },
                )
                HorizontalDivider(color = Link.Surface)
            } else if (!state.loaded && state.statusOk != false) {
                EntryRowSkeleton()
                HorizontalDivider(color = Link.Surface)
            }
            EntryRow(
                icon = LinkIcons.Body,
                title = "키 · 몸무게",
                sub = state.bodySavedOn?.let { "마지막 저장 $it · 바뀌면 다시 입력" } ?: "워치로는 잴 수 없어 한 번 입력해 주세요",
                subColor = if (state.bodySavedOn == null) Link.Amber else Link.Muted,
                onClick = onOpenBody,
            )
        }
    }
}

private val TITLES = mapOf(
    HomeState.OK to "기록을 자동으로\n보내고 있어요.",
    HomeState.PREPARING to "기록 연결을\n준비하고 있어요.",
    HomeState.LATE to "동기화가\n늦어지고 있어요.",
    HomeState.PERMISSION to "권한이 꺼진 기록이\n있어요.",
    HomeState.WATCH to "워치와 연결이\n끊겨 있어요.",
    HomeState.FAILED to "동기화를\n시작하지 못했어요.",
)

/** 상단 상태 카드 (와이어프레임 A-04 · A-04-1 ~ 4 · A-03-4) */
@Composable
private fun StatusCard(
    home: HomeState,
    state: DashState,
    today: LocalDate,
    onSyncNow: () -> Unit,
    onRefresh: () -> Unit,
    onRequestPermission: () -> Unit,
) {
    val next = state.nextSyncAt?.let { HomeStatus.clock(Instant.ofEpochMilli(it), today) }
    // 보낼 새 기록이 없던 정상 회차를 "성공 · 0건"으로 쓰면 실패처럼 읽혀 따로 쓴다
    val last = state.lastSync?.let {
        val at = HomeStatus.logTime(it.time, today)
        when {
            !it.ok -> "마지막 전송 · $at 실패 · 다음 주기에 다시"
            it.sent == 0 -> "마지막 확인 · $at · 새 기록 없음"
            else -> "마지막 전송 · $at 성공 · ${it.sent}건"
        }
    } ?: "아직 전송한 기록이 없어요"
    val watch = when (state.watchNearby) { null -> null; 0 -> "워치 연결 안 됨" to Link.Disabled; else -> "워치 연결됨" to Link.Green }

    if (home == HomeState.OK) {
        Column(
            Modifier.fillMaxWidth().background(Link.Pink, RoundedCornerShape(20.dp)).padding(horizontal = 18.dp, vertical = 14.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(32.dp).background(Link.Primary, CircleShape), contentAlignment = Alignment.Center) {
                    Icon(LinkIcons.Check, contentDescription = null, tint = Color.White, modifier = Modifier.size(18.dp))
                }
                Spacer(Modifier.width(12.dp))
                Column {
                    Text("자동 동기화 켜짐", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Link.Text)
                    Text("15분마다 · 다음 예정 ${next ?: "-"}", fontSize = 13.sp, color = Link.Sub)
                }
            }
            HorizontalDivider(Modifier.padding(vertical = 10.dp), color = Link.Coral)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(last, fontSize = 12.sp, color = Link.Muted, modifier = Modifier.weight(1f))
                watch?.let { (text, color) ->
                    Icon(LinkIcons.Watch, contentDescription = null, tint = color, modifier = Modifier.size(15.dp))
                    Spacer(Modifier.width(3.dp))
                    Text(text, fontSize = 12.sp, fontWeight = FontWeight.Bold, color = color)
                }
            }
        }
        return
    }

    data class Look(val bg: Color, val fg: Color, val head: String, val sub: String, val action: String?, val onAction: () -> Unit)
    val missing = (state.permTotal - state.permGranted).coerceAtLeast(0)
    val look = when (home) {
        HomeState.FAILED -> Look(Link.BadBg, Link.Bad, state.status.ifBlank { "서버에 연결하지 못했어요" },
            "네트워크를 확인하고 다시 시도해 주세요.", "다시 시도", onSyncNow)
        HomeState.PERMISSION -> Look(Link.BadBg, Link.Bad, "권한 ${missing}개가 꺼져 있어요",
            "다시 허용해야 그 기록을 보내요.", "권한 다시 허용", onRequestPermission)
        HomeState.LATE -> Look(Link.AmberBg, Link.Amber, "예정 ${next ?: "-"} · 아직 실행 전",
            "절전 때문에 밀렸을 수 있어요. 배터리 사용을 '제한 없음'으로 두면 좋아요.", "지금 동기화", onSyncNow)
        HomeState.WATCH -> Look(Link.AmberBg, Link.Amber, "워치 기록을 받지 못하고 있어요",
            "워치에 보관했다가 다시 연결되면 보내요.", "다시 확인", onRefresh)
        else -> Look(Link.Surface, Link.Sub, state.status.ifBlank { "동기화를 등록하는 중" },
            "보통 몇 초 안에 끝나요. 첫 동기화는 최근 30일을 보내요.", null) {}
    }
    Column(Modifier.fillMaxWidth().background(look.bg, RoundedCornerShape(20.dp)).padding(horizontal = 18.dp, vertical = 14.dp)) {
        Text(look.head, fontSize = 16.sp, fontWeight = FontWeight.Bold, color = look.fg)
        Spacer(Modifier.height(2.dp))
        Text(look.sub, fontSize = 13.sp, color = Link.Sub, lineHeight = 18.sp)
        if (look.action != null) {
            Spacer(Modifier.height(10.dp))
            Box(
                Modifier.fillMaxWidth().background(Color.White, RoundedCornerShape(10.dp)).clickable(onClick = look.onAction)
                    .padding(vertical = 9.dp),
                contentAlignment = Alignment.Center,
            ) { Text(look.action, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = if (home == HomeState.WATCH) Link.Primary else look.fg) }
        }
        Spacer(Modifier.height(8.dp))
        Text(last, fontSize = 12.sp, color = Link.Muted)
    }
}

private val CORE_LABELS = mapOf("STEPS" to "걸음 수", "HEART_RATE" to "심박수", "OXYGEN_SATURATION" to "산소포화도", "SLEEP" to "수면")

/** 핵심 4종 아이콘과 구분색 */
private data class MetricLook(val icon: ImageVector, val tint: Color, val bg: Color)
private val CORE_LOOKS = mapOf(
    "STEPS" to MetricLook(LinkIcons.Steps, Link.Steps, Link.StepsBg),
    "HEART_RATE" to MetricLook(LinkIcons.Heart, Link.HeartRate, Link.HeartRateBg),
    "OXYGEN_SATURATION" to MetricLook(LinkIcons.Drop, Link.Oxygen, Link.OxygenBg),
    "SLEEP" to MetricLook(LinkIcons.Moon, Link.Sleep, Link.SleepBg),
)

/** 핵심 지표 카드: 이름과 기록 수 한 줄 · 마지막 측정 · 전송 결과 */
@Composable
private fun MetricCard(m: MetricStatus, today: LocalDate, modifier: Modifier) {
    val (line, kind) = HomeStatus.transferLine(m, today)
    val look = CORE_LOOKS[m.key]
    Column(modifier.background(Link.Surface, RoundedCornerShape(18.dp)).padding(horizontal = 14.dp, vertical = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (look != null) {
                IconTile(look.icon, look.tint, look.bg, size = 28.dp, iconSize = 16.dp)
                Spacer(Modifier.width(8.dp))
            }
            Text(CORE_LABELS[m.key] ?: m.label, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = Link.Sub, modifier = Modifier.weight(1f))
            if (!m.permitted) {
                Text("—", fontSize = 20.sp, fontWeight = FontWeight.ExtraBold, color = Link.Disabled)
            } else {
                Text(m.count24h?.toString() ?: "—", fontSize = 20.sp, fontWeight = FontWeight.ExtraBold,
                    color = if ((m.count24h ?: 0) > 0) Link.Text else Link.Disabled)
                Text("건", fontSize = 12.sp, color = Link.Muted, modifier = Modifier.padding(start = 2.dp, bottom = 3.dp))
            }
        }
        // 권한이 없으면 기록을 읽지 못한 것이라 "기록 없음"과 구분한다
        Text(
            when {
                !m.permitted -> "권한이 없어 읽지 못해요"
                m.lastMeasured != null -> "측정 ${HomeStatus.clock(m.lastMeasured, today)}"
                else -> "30일 기록 없음"
            },
            fontSize = 12.sp, color = if (m.permitted && m.lastMeasured == null) Link.Amber else Link.Muted,
        )
        Spacer(Modifier.height(5.dp))
        TransferChip(line, kind)
    }
}

@Composable
private fun TransferChip(text: String, kind: String) {
    val (bg, fg) = when (kind) { "ok" -> Link.GreenBg to Link.Green; "bad" -> Link.BadBg to Link.Bad; else -> Color.White to Link.Muted }
    Text(
        text, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, color = fg, lineHeight = 15.sp, maxLines = 1,
        modifier = Modifier.background(bg, RoundedCornerShape(8.dp)).padding(horizontal = 8.dp, vertical = 3.dp),
    )
}

@Composable
private fun EntryRow(icon: ImageVector, title: String, sub: String, subColor: Color, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 14.dp, vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconTile(icon, Link.Primary, Link.Pink, size = 34.dp, iconSize = 18.dp)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 15.sp, fontWeight = FontWeight.Bold, color = Link.Text)
            Text(sub, fontSize = 12.sp, color = subColor, maxLines = 1)
        }
        Icon(LinkIcons.ChevronRight, contentDescription = null, tint = Link.Disabled, modifier = Modifier.size(20.dp))
    }
}

/** 핵심 4종 카드(MetricCard)와 같은 크기·자리의 스켈레톤 2×2. 회색 카드 안이라 글줄은 Border 색이다 */
@Composable
private fun CoreSkeleton() {
    Column(
        Modifier.semantics { contentDescription = "오늘 기록을 불러오는 중" },
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        repeat(2) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                repeat(2) {
                    Column(
                        Modifier.weight(1f).background(Link.Surface, RoundedCornerShape(18.dp))
                            .padding(horizontal = 14.dp, vertical = 12.dp),
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            CardSkeleton(Modifier.size(28.dp), CircleShape)
                            Spacer(Modifier.width(8.dp))
                            CardSkeleton(Modifier.weight(1f).height(14.dp))
                            Spacer(Modifier.width(18.dp))
                            CardSkeleton(Modifier.width(26.dp).height(22.dp))
                        }
                        Spacer(Modifier.height(6.dp))
                        CardSkeleton(Modifier.fillMaxWidth(0.7f).height(12.dp))
                        Spacer(Modifier.height(8.dp))
                        CardSkeleton(Modifier.fillMaxWidth(0.8f).height(20.dp))
                    }
                }
            }
        }
    }
}

/** 상단 상태 카드(StatusCard)와 비슷한 높이의 스켈레톤. 연분홍 카드 안이라 흰색에 가까운 Border 색 글줄이다 */
@Composable
private fun StatusCardSkeleton() {
    Column(
        Modifier.fillMaxWidth().background(Link.Pink, RoundedCornerShape(20.dp)).padding(horizontal = 18.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            CardSkeleton(Modifier.size(32.dp), CircleShape)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                CardSkeleton(Modifier.fillMaxWidth(0.45f).height(16.dp))
                Spacer(Modifier.height(6.dp))
                CardSkeleton(Modifier.fillMaxWidth(0.7f).height(12.dp))
            }
        }
        CardSkeleton(Modifier.fillMaxWidth().height(12.dp))
    }
}

/** 진입 줄(EntryRow)과 같은 높이의 스켈레톤. 흰 상자 안이라 Surface 색이다 */
@Composable
private fun EntryRowSkeleton() {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        LinkSkeleton(Modifier.size(34.dp), CircleShape)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            LinkSkeleton(Modifier.fillMaxWidth(0.4f).height(16.dp))
            Spacer(Modifier.height(5.dp))
            LinkSkeleton(Modifier.fillMaxWidth(0.75f).height(12.dp))
        }
    }
}

/** 나머지 9종 (활동 5 · 신체 측정 4) 시트 */
@Composable
private fun OthersSheet(others: List<MetricStatus>, today: LocalDate) {
    Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(bottom = 12.dp)) {
        Text("나머지 ${others.size}종", fontSize = 18.sp, fontWeight = FontWeight.Bold, color = Link.Text,
            modifier = Modifier.padding(horizontal = 20.dp))
        Text("최근 24시간 기록 수 · 마지막 전송", fontSize = 12.sp, color = Link.Muted,
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
        others.forEach { m ->
            HorizontalDivider(color = Link.Surface)
            val (line, kind) = HomeStatus.transferLine(m, today)
            Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(8.dp).background(if ((m.count24h ?: 0) > 0) Link.Green else Link.Line, CircleShape))
                Spacer(Modifier.width(10.dp))
                Text(m.label, fontSize = 14.sp, color = Link.Text, modifier = Modifier.width(80.dp))
                Text(if (m.permitted) "${m.count24h ?: "-"}건" else "—", fontSize = 13.sp, fontWeight = FontWeight.Bold,
                    color = Link.Text, modifier = Modifier.width(48.dp))
                Text(line, fontSize = 12.sp, color = when (kind) { "ok" -> Link.Green; "bad" -> Link.Bad; else -> Link.Muted },
                    modifier = Modifier.weight(1f))
            }
        }
    }
}

/**
 * 키·몸무게 입력 (와이어프레임 A-06 설정 → 프로필 자리). 워치로 잴 수 없어 직접 입력받아 Health Connect 에 쓴다.
 * 저장한 날짜만 보여 주고 값은 화면에 다시 띄우지 않는다.
 */
@Composable
fun BodyInfoScreen(savedOn: String?, notice: String, onSave: (String, String) -> Unit, onBack: () -> Unit) {
    BackHandler(onBack = onBack)
    var height by remember { mutableStateOf("") }
    var weight by remember { mutableStateOf("") }
    Column(
        Modifier.fillMaxSize().background(Link.Background).safeDrawingPadding().padding(horizontal = 24.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        TextButton(onClick = onBack, modifier = Modifier.padding(start = 0.dp)) {
            Icon(LinkIcons.ChevronLeft, contentDescription = null, tint = Link.Sub, modifier = Modifier.size(20.dp))
            Text("홈", color = Link.Sub, fontSize = 15.sp)
        }
        Text("키 · 몸무게", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = Link.Text)
        Text(
            "워치로는 잴 수 없어 직접 입력해요. Health Connect 에 기록되고 다음 동기화 때 서버로 보내요.",
            fontSize = 14.sp, color = Link.Sub, lineHeight = 21.sp,
        )
        Text(savedOn?.let { "마지막 저장 · $it · 바뀌면 다시 입력하세요." } ?: "아직 저장한 적이 없어요.", fontSize = 13.sp,
            color = if (savedOn == null) Link.Amber else Link.Muted)
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedTextField(value = height, onValueChange = { height = it }, singleLine = true,
                label = { Text("키 cm") }, colors = linkFieldColors(), modifier = Modifier.weight(1f))
            OutlinedTextField(value = weight, onValueChange = { weight = it }, singleLine = true,
                label = { Text("몸무게 kg") }, colors = linkFieldColors(), modifier = Modifier.weight(1f))
        }
        if (notice.isNotEmpty()) Text(notice, color = if (notice.startsWith("저장")) Link.Green else Link.Bad, fontSize = 13.sp)
        Button(
            onClick = { onSave(height, weight); height = ""; weight = "" },
            modifier = Modifier.fillMaxWidth().height(52.dp), shape = RoundedCornerShape(16.dp),
        ) { Text("저장", fontWeight = FontWeight.Bold) }
        Spacer(Modifier.weight(1f))
        Column(Modifier.fillMaxWidth().background(Link.Pink, RoundedCornerShape(16.dp)).padding(16.dp)) {
            Text("하나만 넣어도 돼요. 같은 날 다시 저장하면 그날 값이 바뀌어요.", fontSize = 13.sp, color = Link.Sub)
            Spacer(Modifier.height(4.dp))
            Text("체지방·기초대사량은 삼성 헬스에서 체성분을 측정한 경우에만 들어와요.", fontSize = 12.sp, color = Link.Muted)
        }
    }
}

/** 로그아웃 확인. 아직 보내지 못한 기록도 이 폰에서 지워지므로 미리 알린다(와이어프레임 A-06-1) */
@Composable
internal fun LogoutDialog(onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Color.White,
        title = { Text("로그아웃할까요?", fontWeight = FontWeight.ExtraBold) },
        text = {
            Text("기록 수집을 멈추고 이 폰에 모아 둔 기록을 지워요. 아직 보내지 못한 기록은 보내지 않아요. 서버에 이미 보낸 기록은 그대로 남아요.",
                color = Link.Sub)
        },
        confirmButton = { Button(onClick = onConfirm) { Text("로그아웃") } },
        dismissButton = { OutlinedButton(onClick = onDismiss) { Text("취소", color = Link.Sub) } },
    )
}
