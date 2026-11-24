package io.candiy.healthconnect.quickstart

import java.time.Duration
import java.time.Instant

/**
 * debug 빌드 전용 화면 미리보기 값. 로그인·Health Connect·SDK 없이 홈 화면 모양만 확인한다(에뮬레이터·디자인 점검).
 * 숫자는 모두 지어낸 기록 건수·시각이다. 건강 수치는 없다.
 *
 *   adb shell am start -n io.candiy.healthconnect.quickstart/.MainActivity --ez dashboardPreview true --es previewState PERMISSION
 *
 * previewState: OK(기본) · PREPARING · LATE · PERMISSION · WATCH · FAILED, 탭은 HISTORY · SETTINGS
 * LOADING · HISTORY_LOADING 은 첫 읽기 전(지표·기록 없음) 모양이라 스켈레톤이 보인다
 */
object DashboardPreview {
    fun state(kind: String?): DashState {
        if (kind.orEmpty().uppercase().endsWith("LOADING")) {
            return DashState(account = "캔디 · 카카오", modeLabel = "미리보기", status = "권한을 확인하는 중")
        }
        val now = Instant.now()
        val home = runCatching { HomeState.valueOf(kind.orEmpty().uppercase()) }.getOrDefault(HomeState.OK)
        val today = java.time.LocalDate.now()
        fun log(minutesAgo: Long): String =
            java.time.LocalDateTime.now().minusMinutes(minutesAgo).format(java.time.format.DateTimeFormatter.ofPattern("MM-dd HH:mm"))
        val sent = TypeTransfer(log(3), true, log(3), 4)
        val quiet = TypeTransfer(log(3), true, log(240), 1)
        val rows = listOf(
            Triple("STEPS", "걸음", 96), Triple("HEART_RATE", "심박", 96), Triple("OXYGEN_SATURATION", "산소포화도", 0),
            Triple("SLEEP", "수면", 1), Triple("EXERCISE_SESSION", "운동", 1), Triple("DISTANCE_WALKED", "거리", 96),
            Triple("CALORIES_BURNED", "칼로리", 96), Triple("SPEED", "속도", 0), Triple("VO2_MAX", "VO2 Max", 0),
            Triple("WEIGHT", "체중", 1), Triple("BODY_FAT", "체지방", 0), Triple("BASAL_METABOLIC_RATE", "기초대사량", 0),
            Triple("HEIGHT", "키", 1),
        )
        val metrics = rows.map { (key, label, n) ->
            val permitted = !(home == HomeState.PERMISSION && key == "HEART_RATE")
            MetricStatus(
                key = key, label = label, count24h = if (permitted) n else null,
                lastMeasured = if (permitted && n > 0) now.minus(Duration.ofMinutes(if (key == "SLEEP") 400 else 12)) else null,
                permitted = permitted,
                transfer = when {
                    home == HomeState.FAILED -> TypeTransfer(log(3), false, null, 0)
                    n == 0 -> TypeTransfer(log(3), true, null, 0)
                    key == "SLEEP" || key == "WEIGHT" || key == "HEIGHT" || key == "EXERCISE_SESSION" -> quiet
                    else -> sent
                },
            )
        }
        val next = when (home) {
            HomeState.PREPARING -> null
            HomeState.LATE -> now.minus(Duration.ofMinutes(23)).toEpochMilli()
            else -> now.plus(Duration.ofMinutes(12)).toEpochMilli()
        }
        return DashState(
            account = "캔디 · 카카오",
            modeLabel = "미리보기",
            status = if (home == HomeState.FAILED) "서버에 연결하지 못했어요" else if (home == HomeState.PREPARING) "동기화를 등록하는 중" else "동기화 시작 성공",
            statusOk = when (home) { HomeState.FAILED -> false; HomeState.PREPARING -> null; else -> true },
            lastSync = if (home == HomeState.PREPARING) null else FileLog.LastSync(log(3), home != HomeState.FAILED, "success", 38, 0),
            nextSyncAt = next,
            permGranted = if (home == HomeState.PERMISSION) 21 else 22,
            permTotal = 22,
            watchNearby = if (home == HomeState.WATCH) 0 else 1,
            metrics = metrics,
            bodySavedOn = today.minusDays(4).toString(),
            loaded = true,
            history = listOf(
                SyncRun(log(3), true, "success", 38, 38, 0, mapOf("STEPS" to 12, "HEART_RATE" to 12, "DISTANCE_WALKED" to 7, "CALORIES_BURNED" to 7)),
                SyncRun(log(18), true, "no_data", 0, 0, 0, emptyMap()),
                SyncRun(log(33), true, "partial", 14, 12, 2, mapOf("STEPS" to 6, "HEART_RATE" to 6)),
                SyncRun(log(48), true, "success", 12, 12, 0, mapOf("STEPS" to 6, "HEART_RATE" to 6)),
                SyncRun(log(63), false, "network", 9, 0, 9, emptyMap()),
                SyncRun(log(60 * 20), true, "success", 52, 52, 0, mapOf("SLEEP" to 1, "OXYGEN_SATURATION" to 24, "HEART_RATE" to 27)),
            ),
        )
    }
}
