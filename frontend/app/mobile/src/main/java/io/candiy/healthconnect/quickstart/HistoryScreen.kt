package io.candiy.healthconnect.quickstart

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
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
import java.time.LocalDate
import java.time.format.DateTimeFormatter

/**
 * 전송 기록 (와이어프레임 A-05 · A-05-1). 동기화 회차마다 성공·새 기록 없음·일부 실패·실패와 건수를 보여 주고,
 * 누르면 지표별로 보낸 건수를 아래 시트로 연다. 건강 수치는 없고 건수만 있다.
 *
 * @param labels SDK 지표 이름(STEPS 등) → 화면 이름
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HistoryScreen(runs: List<SyncRun>, labels: Map<String, String>, loaded: Boolean = true) {
    var opened by remember { mutableStateOf<SyncRun?>(null) }
    opened?.let { run ->
        ModalBottomSheet(
            onDismissRequest = { opened = null },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
            containerColor = Color.White,
            tonalElevation = 0.dp,
        ) { RunDetail(run, labels) }
    }
    val today = LocalDate.now()
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(horizontal = 20.dp, vertical = 12.dp)) {
        item {
            Text("전송 기록", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = Link.Text)
            Spacer(Modifier.height(4.dp))
            Text("이 폰에서 서버로 보낸 결과예요. 15분마다 자동으로 보내요.", fontSize = 13.sp, color = Link.Muted)
            Spacer(Modifier.height(12.dp))
        }
        // 첫 읽기 전에는 "아직 보낸 기록이 없어요"가 잠깐 보였다 바뀌지 않게 목록 모양 스켈레톤을 둔다
        if (!loaded) {
            item { HistorySkeleton() }
        } else if (runs.isEmpty()) {
            item {
                Column(
                    Modifier.fillMaxWidth().background(Link.Surface, RoundedCornerShape(18.dp)).padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    IconTile(LinkIcons.History, Link.Muted, Color.White, size = 44.dp, iconSize = 24.dp)
                    Spacer(Modifier.height(10.dp))
                    Text("아직 보낸 기록이 없어요", fontSize = 15.sp, fontWeight = FontWeight.Bold, color = Link.Text)
                    Text("첫 동기화가 끝나면 여기에 쌓여요.", fontSize = 13.sp, color = Link.Muted)
                }
            }
        }
        runs.groupBy { it.at.take(5) }.forEach { (day, dayRuns) ->
            item(key = "day-$day") {
                Text(dayLabel(day, today), fontSize = 15.sp, fontWeight = FontWeight.Bold, color = Link.Sub,
                    modifier = Modifier.padding(top = 14.dp, bottom = 6.dp))
            }
            item(key = "box-$day") {
                Column(Modifier.fillMaxWidth().background(Link.Surface, RoundedCornerShape(18.dp))) {
                    dayRuns.forEachIndexed { i, run ->
                        if (i > 0) HorizontalDivider(color = Color.White, modifier = Modifier.padding(horizontal = 14.dp))
                        RunRow(run) { opened = run }
                    }
                }
            }
        }
        item {
            Spacer(Modifier.height(16.dp))
            Column(Modifier.fillMaxWidth().background(Link.Pink, RoundedCornerShape(16.dp)).padding(16.dp)) {
                Text("다시 보내기 버튼은 없어요", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text)
                Text("보내지 못한 기록은 다음 주기에 자동으로 다시 보내요.", fontSize = 13.sp, color = Link.Sub)
            }
        }
    }
}

/** 날짜 제목 + 회차 줄(RunRow) 세 개짜리 상자와 같은 자리의 스켈레톤 */
@Composable
private fun HistorySkeleton() {
    Column(Modifier.semantics { contentDescription = "전송 기록을 불러오는 중" }) {
        LinkSkeleton(Modifier.padding(top = 14.dp, bottom = 6.dp).width(56.dp).height(18.dp))
        Column(Modifier.fillMaxWidth().background(Link.Surface, RoundedCornerShape(18.dp))) {
            repeat(3) { i ->
                if (i > 0) HorizontalDivider(color = Color.White, modifier = Modifier.padding(horizontal = 14.dp))
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CardSkeleton(Modifier.size(34.dp), CircleShape)
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        CardSkeleton(Modifier.fillMaxWidth(0.45f).height(16.dp))
                        Spacer(Modifier.height(5.dp))
                        CardSkeleton(Modifier.fillMaxWidth(0.7f).height(12.dp))
                    }
                    Spacer(Modifier.width(12.dp))
                    CardSkeleton(Modifier.width(36.dp).height(14.dp))
                }
            }
        }
    }
}

private data class RunLook(val icon: ImageVector, val tint: Color, val bg: Color, val title: String, val titleColor: Color)

private fun look(run: SyncRun): RunLook = when (run.kind) {
    SyncRun.Kind.SENT -> RunLook(LinkIcons.Check, Link.Green, Link.GreenBg, "성공 · ${run.sent}건", Link.Text)
    SyncRun.Kind.EMPTY -> RunLook(LinkIcons.Minus, Link.Muted, Color.White, "새 기록 없음", Link.Text)
    SyncRun.Kind.PARTIAL -> RunLook(LinkIcons.Alert, Link.Amber, Link.AmberBg, "일부 실패 · ${run.sent}건 보냄", Link.Amber)
    SyncRun.Kind.FAILED -> RunLook(LinkIcons.Alert, Link.Bad, Link.BadBg, "실패", Link.Bad)
}

private fun sub(run: SyncRun): String = when (run.kind) {
    SyncRun.Kind.EMPTY -> "보낼 변경분이 없었어요"
    SyncRun.Kind.FAILED, SyncRun.Kind.PARTIAL -> "다음 주기에 다시 보내요"
    SyncRun.Kind.SENT -> "읽음 ${run.read} · 보냄 ${run.sent} · 실패 ${run.failed}"
}

@Composable
private fun RunRow(run: SyncRun, onClick: () -> Unit) {
    val l = look(run)
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconTile(l.icon, l.tint, l.bg, size = 34.dp, iconSize = 18.dp)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(l.title, fontSize = 15.sp, fontWeight = FontWeight.Bold, color = l.titleColor)
            Text(sub(run), fontSize = 12.sp, color = Link.Muted)
        }
        Text(run.at.drop(6), fontSize = 13.sp, color = Link.Muted)
        Spacer(Modifier.width(4.dp))
        Icon(LinkIcons.ChevronRight, contentDescription = null, tint = Link.Disabled, modifier = Modifier.size(18.dp))
    }
}

/** 회차 상세 (와이어프레임 A-05-1) */
@Composable
private fun RunDetail(run: SyncRun, labels: Map<String, String>) {
    val l = look(run)
    Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 20.dp).padding(bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconTile(l.icon, l.tint, l.bg, size = 40.dp, iconSize = 22.dp)
            Spacer(Modifier.width(12.dp))
            Column {
                Text("${run.at.drop(6)} · ${l.title}", fontSize = 19.sp, fontWeight = FontWeight.ExtraBold, color = l.titleColor)
                Text(sub(run), fontSize = 13.sp, color = Link.Muted)
            }
        }
        Row(Modifier.fillMaxWidth().background(Link.Surface, RoundedCornerShape(16.dp)).padding(vertical = 14.dp)) {
            listOf("읽음" to run.read, "보냄" to run.sent, "실패" to run.failed).forEach { (name, n) ->
                Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("$n", fontSize = 22.sp, fontWeight = FontWeight.ExtraBold,
                        color = if (name == "실패" && n > 0) Link.Bad else Link.Text)
                    Text(name, fontSize = 12.sp, color = Link.Muted)
                }
            }
        }
        if (run.sentByType.isNotEmpty()) {
            Text("지표별로 보낸 건수", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Sub)
            Column(Modifier.fillMaxWidth().background(Link.Surface, RoundedCornerShape(16.dp)).padding(horizontal = 16.dp, vertical = 4.dp)) {
                run.sentByType.entries.sortedByDescending { it.value }.forEach { (type, n) ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 9.dp)) {
                        Text(labels[type] ?: type, fontSize = 14.sp, color = Link.Text, modifier = Modifier.weight(1f))
                        Text("${n}건", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text)
                    }
                }
            }
        }
        Text("건강 수치는 담지 않고 건수만 보여 줘요.", fontSize = 12.sp, color = Link.Muted)
    }
}

private val DAY = DateTimeFormatter.ofPattern("MM-dd")

/** "MM-dd" → 오늘 · 어제 · 11월 1일 */
private fun dayLabel(day: String, today: LocalDate): String = when (day) {
    today.format(DAY) -> "오늘"
    today.minusDays(1).format(DAY) -> "어제"
    else -> day.split("-").let { (m, d) -> "${m.toInt()}월 ${d.toInt()}일" }
}
