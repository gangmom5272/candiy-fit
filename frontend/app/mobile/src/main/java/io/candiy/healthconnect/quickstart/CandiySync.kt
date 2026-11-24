package io.candiy.healthconnect.quickstart

import android.content.Context
import androidx.health.connect.client.records.BasalMetabolicRateRecord
import androidx.health.connect.client.records.BodyFatRecord
import androidx.health.connect.client.records.DistanceRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.HeightRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.SpeedRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.TotalCaloriesBurnedRecord
import androidx.health.connect.client.records.Vo2MaxRecord
import androidx.health.connect.client.records.WeightRecord
import com.candiy.candiyhc.ClientCredentials
import com.candiy.candiyhc.HealthConnectManager
import com.candiy.candiyhc.data.enums.Connections
import com.candiy.candiyhc.data.enums.DataTypes
import kotlin.reflect.KClass

/**
 * 실데이터 모드의 CANDiY 동기화 등록. MainActivity 와 워치 수신 서비스가 같은 설정으로 부른다.
 */
object CandiySync {

    // 실데이터 모드 수집 타입과 읽을 레코드 — CANDiY 서버가 받는 13종 전부(SDK 전송 대상과 같다).
    // 칼로리는 ACTIVE_CALORIES_BURNED 가 아니라 CALORIES_BURNED 여야 서버로 간다.
    val realTypes: Map<DataTypes, KClass<out Record>> = linkedMapOf(
        // 명세서 필수 지표
        DataTypes.STEPS to StepsRecord::class,
        DataTypes.HEART_RATE to HeartRateRecord::class,
        DataTypes.OXYGEN_SATURATION to OxygenSaturationRecord::class,
        DataTypes.SLEEP to SleepSessionRecord::class,
        // 활동
        DataTypes.EXERCISE_SESSION to ExerciseSessionRecord::class,
        DataTypes.DISTANCE_WALKED to DistanceRecord::class,
        DataTypes.CALORIES_BURNED to TotalCaloriesBurnedRecord::class,
        DataTypes.SPEED to SpeedRecord::class,
        DataTypes.VO2_MAX to Vo2MaxRecord::class,
        // 신체 측정 (삼성 헬스에 입력·측정된 경우)
        DataTypes.WEIGHT to WeightRecord::class,
        DataTypes.BODY_FAT to BodyFatRecord::class,
        DataTypes.BASAL_METABOLIC_RATE to BasalMetabolicRateRecord::class,
        DataTypes.HEIGHT to HeightRecord::class,
    )

    // 워치 앱(Health Services)이 보낸 걷기 기록·15분 자동 수집분을 이 앱이 Health Connect 에 쓸 때 필요한 쓰기 대상.
    // 걸음·거리·칼로리·심박은 워치 앱이, 키·몸무게는 직접 입력이 맡는다. 삼성 헬스는 수면·산소포화도만 맡는다(10-19).
    val watchWriteTypes: List<KClass<out Record>> = listOf(
        ExerciseSessionRecord::class, StepsRecord::class, DistanceRecord::class,
        TotalCaloriesBurnedRecord::class, HeartRateRecord::class,
        HeightRecord::class, WeightRecord::class,
    )

    /**
     * 지금 바로 한 번 동기화. 이미 등록된 상태에서 [start] 만 다시 부르면 SDK 는 아무것도 하지 않는다
     * (`이미 동기화 중` · ALREADY_REGISTERED — 포털 문서 「데이터 동기화 — 결과 콜백」).
     * 그래서 등록을 먼저 해제하고 다시 등록해 첫 작업이 곧바로(약 0.5초 뒤) 돌게 한다.
     * stopRealtime 은 알람·감시 작업만 끄고 변경 추적 위치는 그대로 두므로, 새로 들어온 기록만 읽는다.
     */
    fun startNow(context: Context, onResult: (Boolean, String) -> Unit) {
        if (UserIdStore.get(context) == null) return start(context, onResult = onResult)
        HealthConnectManager(context.applicationContext, baseUrl = BuildConfig.CANDIY_BASE_URL).stopRealtime(Connections.BLE)
        start(context, onResult = onResult)
    }

    fun start(context: Context, dataTypes: Set<DataTypes> = realTypes.keys, onResult: (Boolean, String) -> Unit) {
        FileLog.init(context)
        // 사용자 ID 는 로그인 때 저장한 값만 쓴다. 없으면(로그아웃·로그인 전) 전송하지 않는다
        val endUserId = UserIdStore.get(context) ?: run {
            FileLog.i("candiy", "sync_skipped reason=no_user_id")
            onResult(false, "로그인하지 않아 전송하지 않습니다")
            return
        }
        HealthConnectManager(context.applicationContext, baseUrl = BuildConfig.CANDIY_BASE_URL).syncHealthData(
            type = Connections.BLE,
            dataTypes = dataTypes,
            endUserId = endUserId,
            credentials = ClientCredentials(
                clientId = BuildConfig.CANDIY_CLIENT_ID,
                clientSecret = BuildConfig.CANDIY_CLIENT_SECRET,
            ),
            intervalMinutes = 15,
        ) { ok, message ->
            FileLog.i("candiy", "sync_registered ok=$ok types=${dataTypes.size} msg=${LogRedaction.freeText(message)}")
            onResult(ok, message)
        }
    }
}
