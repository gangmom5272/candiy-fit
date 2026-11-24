package io.candiy.healthconnect.quickstart

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.records.HeightRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.records.metadata.Metadata
import androidx.health.connect.client.units.Length
import androidx.health.connect.client.units.Mass
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/**
 * 키·몸무게 직접 입력. 워치로 잴 수 없는 값이라 처음(가입 무렵) 한 번 입력받아 Health Connect 에 쓴다.
 * 그러면 CANDiY SDK 가 다른 지표처럼 서버(heights · weights)로 보낸다. 출처(app)는 이 앱이다.
 *
 * 같은 날 다시 저장하면 clientRecordId 가 같아 덮어써진다(하루 1건). 저장한 날짜만 폰에 남기고 값은 남기지 않는다.
 * 체지방·기초대사량은 체성분 측정(BIA)이 필요해 입력받지 않는다 — 삼성 헬스에서 측정한 경우에만 들어온다.
 */
object BodyInfo {
    private const val PREFS = "candiy_body"

    fun validHeightCm(v: Double) = v in 100.0..250.0
    fun validWeightKg(v: Double) = v in 20.0..300.0

    suspend fun save(context: Context, heightCm: Double?, weightKg: Double?) {
        val now = Instant.now()
        val offset = ZoneId.systemDefault().rules.getOffset(now)
        val day = LocalDate.now(ZoneId.systemDefault())
        val records = buildList {
            heightCm?.let {
                add(HeightRecord(time = now, zoneOffset = offset, height = Length.meters(it / 100.0),
                    metadata = Metadata.manualEntry(clientRecordId = "candiy-height-$day")))
            }
            weightKg?.let {
                add(WeightRecord(time = now, zoneOffset = offset, weight = Mass.kilograms(it),
                    metadata = Metadata.manualEntry(clientRecordId = "candiy-weight-$day")))
            }
        }
        if (records.isEmpty()) return
        HealthConnectClient.getOrCreate(context).insertRecords(records)
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("savedOn", day.toString()).apply()
        FileLog.i("candiy", "body_info_saved height=${heightCm != null} weight=${weightKg != null}")
    }

    fun savedOn(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("savedOn", null)
}
