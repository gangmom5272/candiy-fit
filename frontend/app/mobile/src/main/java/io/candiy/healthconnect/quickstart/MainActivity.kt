package io.candiy.healthconnect.quickstart

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.BasalMetabolicRateRecord
import androidx.health.connect.client.records.BodyFatRecord
import androidx.health.connect.client.records.DistanceRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeightRecord
import androidx.health.connect.client.records.SpeedRecord
import androidx.health.connect.client.records.TotalCaloriesBurnedRecord
import androidx.health.connect.client.records.Vo2MaxRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import androidx.lifecycle.lifecycleScope
import com.candiy.candiyhc.ClientCredentials
import com.candiy.candiyhc.HealthConnectManager
import com.candiy.candiyhc.data.enums.Connections
import com.candiy.candiyhc.data.enums.DataTypes
import com.google.android.gms.wearable.Wearable
import io.candiy.healthconnect.quickstart.coaching.CoachingApi
import io.candiy.healthconnect.quickstart.coaching.CoachingController
import io.candiy.healthconnect.quickstart.coaching.CoachingScreen
import io.candiy.healthconnect.quickstart.coaching.EvidencePreviewScreen
import io.candiy.healthconnect.quickstart.coaching.CoachingSocket
import io.candiy.healthconnect.quickstart.auth.ApiResult
import io.candiy.healthconnect.quickstart.auth.Auth
import io.candiy.healthconnect.quickstart.auth.AuthUser
import io.candiy.healthconnect.quickstart.auth.LoginFlow
import io.candiy.healthconnect.quickstart.auth.LoginResult
import io.candiy.healthconnect.quickstart.auth.LoginStep
import io.candiy.healthconnect.quickstart.auth.ProfileForm
import io.candiy.healthconnect.quickstart.auth.ProviderSignIn
import io.candiy.healthconnect.quickstart.auth.RefreshOutcome
import io.candiy.healthconnect.quickstart.auth.Session
import io.candiy.healthconnect.quickstart.auth.SignupStep
import io.candiy.healthconnect.quickstart.auth.SocialType
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withContext
import java.time.Duration
import java.time.Instant
import kotlin.reflect.KClass

/** 지금 보여 줄 화면. 로그인 전에는 Health Connect·SDK 를 건드리지 않는다 */
private sealed interface Screen {
    data object Loading : Screen
    data class Login(val message: String = "") : Screen
    data class Signup(
        val pending: LoginResult.SignupRequired,
        val errors: Map<String, String> = emptyMap(),
        val message: String = "",
    ) : Screen
    data object Dashboard : Screen
    /** 권한 안내(A-02). 권한이 모자랄 때 시스템 권한 창 전에 */
    data class PermissionGuide(val message: String = "") : Screen
    /** Health Connect 의 "권한을 쓰는 이유"에서 열렸을 때. 설명과 닫기만 */
    data object Rationale : Screen
    /** 연결 완료(A-03-2). 안내 화면에서 권한을 허용한 직후 한 번. 서버 완료 보고가 끝나야 완료로 보인다 */
    data object Connected : Screen
    /** 키·몸무게 입력(와이어프레임 A-06 프로필 자리). 홈의 진입 줄에서 연다 */
    data object BodyInfo : Screen
    /** debug 빌드 전용: 지어낸 값으로 근거 상세 화면 모양만 본다(coaching/EvidencePreview.kt) */
    data class EvidencePreview(val kind: String) : Screen
}

/**
 * 로그아웃 정리를 화면(Activity)과 상관없이 끝까지 돌린다. 화면을 닫거나 돌려도 취소되지 않아,
 * 서버 세션은 폐기했는데 SDK 기록 정리가 중간에 멈추는 일이 없다. 결과는 로그인 화면에 띄울 문구(없으면 빈 문자열)
 */
private object LogoutCleanup {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var running: Deferred<String>? = null

    /** 아직 끝나지 않은 정리. 없으면 null */
    val pending: Deferred<String>? get() = running?.takeIf { it.isActive }

    fun start(block: suspend () -> String) {
        running = scope.async {
            runCatching { block() }.getOrElse {
                FileLog.e("candiy", "logout_failed", it)
                "로그아웃을 마치지 못했어요. 앱을 다시 열어 주세요."
            }
        }
    }
}

/**
 * CANDiY HC SDK 연동 샘플. 앱을 켜면 로그인 상태를 확인하고, 로그인돼 있으면 Health Connect 권한을 요청한 뒤 동기화를 등록한다.
 * 서버 주소와 API 키는 local.properties 에서 읽는다. 사용자 ID 는 로그인 응답의 endUserId 를 쓴다(UserIdStore).
 *
 * candiy.mockData=true 이면(디버그 빌드만) 연동 테스트 모드로 동작한다. 걸음 수·심박수·산소포화도·수면
 * 목업 7일치를 Health Connect 에 쓰고, 다시 읽어 개수를 확인한 뒤 네 지표를 동기화한다.
 * false 이면 실데이터 모드로, CANDiY 서버가 받는 13종(필수 4종 + 활동 5종 + 신체 측정 4종)을 모두 동기화한다.
 *
 * 화면(Dashboard, 와이어프레임 A-04)에는 동기화 상태·다음 예정·워치 연결과, 13종 지표마다 최근 24시간 기록 수·
 * 마지막 측정 시각·마지막 전송 결과를 보여 준다. 건강 수치는 보여 주지 않고 건수·시각·상태만 보여 준다.
 * 로그는 앱 내부 파일에도 남긴다(FileLog).
 */
class MainActivity : ComponentActivity() {

    // 데이터를 보낼 서버 주소 — local.properties 의 candiy.baseUrl
    private val manager by lazy { HealthConnectManager(this, baseUrl = BuildConfig.CANDIY_BASE_URL) }

    // 목업 기록은 운영 서버에서 실제 기록과 구별되지 않는다. release 빌드에서는 gradle 에서도 꺼 두지만 여기서도 막는다
    private val mock = BuildConfig.DEBUG && BuildConfig.CANDIY_MOCK_DATA

    /**
     * 수집 전용 모드(local.properties 의 candiy.collectorMode=true, 디버그 빌드만). 로그인 기능을 만드는 동안에도
     * 개발자 폰의 워치 기록이 끊기지 않게, 로그인 화면 없이 기존 사용자 ID 로 권한·동기화만 한다.
     * 워치 앱은 패키지명·서명이 같은 폰 앱에만 기록을 넘길 수 있어 별도 앱 대신 이 앱 안의 모드로 둔다.
     */
    private val collectorMode = BuildConfig.DEBUG && BuildConfig.COLLECTOR_MODE

    // Health Connect 를 쓸 수 있는 기기인지 (onCreate 에서 확인). false 면 Health Connect·SDK 를 건드리지 않는다
    private var hcReady = false

    // 테스트 모드에서 다루는 네 지표
    private val mockTypes: Map<DataTypes, KClass<out Record>> = mapOf(
        DataTypes.STEPS to StepsRecord::class,
        DataTypes.HEART_RATE to HeartRateRecord::class,
        DataTypes.OXYGEN_SATURATION to OxygenSaturationRecord::class,
        DataTypes.SLEEP to SleepSessionRecord::class,
    )

    // 실데이터 모드 수집 타입. 워치 수신 서비스와 같은 설정을 쓰도록 CandiySync 에 둔다.
    private val types = if (mock) mockTypes else CandiySync.realTypes

    // 요청할 권한 — AndroidManifest.xml 의 선언과 짝을 맞춘다
    private val permissions = buildSet {
        add(HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND)
        add(HealthPermission.PERMISSION_READ_HEALTH_DATA_HISTORY)
        types.values.forEach { add(HealthPermission.getReadPermission(it)) }
        if (mock) mockTypes.values.forEach { add(HealthPermission.getWritePermission(it)) }
        // 워치 앱(Health Services)이 보낸 걷기 기록을 이 앱이 Health Connect 에 쓴다
        else CandiySync.watchWriteTypes.forEach { add(HealthPermission.getWritePermission(it)) }
    }

    /**
     * 동기화 대상 지표의 읽기 권한. 연동으로 치는 기준은 이 중 하나 이상 허용이다(S15P31A403-366).
     * 쓰기·백그라운드·과거 기록 권한만으로는 건강 기록을 읽을 수 없다
     */
    private val readPermissions = types.values.map { HealthPermission.getReadPermission(it) }.toSet()

    private val initialDash = DashState(account = "", modeLabel = if (mock) "목업 테스트 모드" else "실데이터 모드")
    private var dash by mutableStateOf(initialDash)

    private val auth by lazy { Auth.session(this) }
    // local.properties 에 google.serverClientId·kakao.nativeAppKey 가 없으면 그 버튼은 설정 없음을 안내한다
    private val loginFlow by lazy { LoginFlow(auth, ProviderSignIn(this)) }
    private var screen by mutableStateOf<Screen>(Screen.Loading)
    /** AI 코치 탭(웹 코칭과 같은 백엔드). 탭을 오가도 대화가 남게 액티비티가 들고 있다 */
    private val coaching: CoachingController by lazy {
        CoachingController(
            scope = lifecycleScope,
            auth = auth,
            api = CoachingApi(BuildConfig.BACKEND_BASE_URL, auth),
            socket = CoachingSocket(BuildConfig.BACKEND_BASE_URL),
            onSignedOut = {
                coaching.reset()
                screen = Screen.Login("로그인이 끝났어요. 다시 로그인해 주세요.")
            },
        )
    }
    /** 로그인 뒤 하단 메뉴에서 고른 탭. 키·몸무게 화면에서 돌아와도 그 탭으로 온다 */
    private var tab by mutableStateOf(Tab.HOME)
    // 로그인·가입·로그아웃 요청이 진행 중인지. 버튼을 두 번 눌러 같은 요청이 겹치지 않게 한다
    private var busy by mutableStateOf(false)
    // 로그인 화면에서 진행 중일 때 버튼 아래에 보일 문구(로그인 중 / 이전 계정 정리 중)
    private var busyText by mutableStateOf(LOGIN_BUSY)
    // 동기화 결과가 온 뒤 화면을 다시 읽을 예약. 결과가 연달아 오면 앞 예약은 취소한다
    private var pendingRefresh: Job? = null
    // debug 빌드의 홈 미리보기(dashboardPreview)인지. 미리보기에서는 로그아웃이 실제 기록·ID 를 지우지 않게 막는다
    private var previewOnly = false
    // 권한 요청이 안내 화면(A-02)에서 시작됐는지. 그렇다면 허용 뒤 연결 완료 화면을 보여 준다
    private var fromGuide = false

    // ── 앱 연동 완료 보고(S15P31A403-366, AppConnection.kt) ──
    private val reporter by lazy { AppConnectionReporter(auth, UrlAppConnectionTransport(BuildConfig.BACKEND_BASE_URL)) }
    /** 로그인한 회원. 수집 전용 모드·미리보기에는 회원이 없어 보고하지 않는다 */
    private var member: AuthUser? = null
    /** 로그인·로그아웃마다 올린다. 계정을 바꾼 뒤 늦게 온 동기화 결과·보고 응답을 이 번호로 걸러 낸다 */
    private var connectAttempt = 0
    /** 이번 실행에서 보고를 마친 회원. 동기화 결과가 여러 번 와도 한 번만 보고한다 */
    private var reportedMember: String? = null
    private var reportJob: Job? = null

    private val permissionLauncher = registerForActivityResult(
        PermissionController.createRequestPermissionResultContract()
    ) { granted ->
        val ok = granted.intersect(permissions)
        FileLog.i("candiy", "permission_result granted=${ok.size}/${permissions.size}")
        // 읽기 권한이 하나라도 허용되면 동기화를 시작한다(포털 문서 「권한 요청」). SDK 가 매 작업마다 승인 상태를 다시 보고
        // 허용된 타입만 수집하며, 나중에 더 허용하면 다음 작업부터 자동으로 포함된다.
        // 빠진 권한 수는 화면의 권한 항목·「권한 허용하기」 버튼으로 안내한다.
        // 쓰기·백그라운드 권한만 허용한 경우는 읽을 기록이 없어 시작하지 않는다(완료 보고 기준과 같다)
        val guided = fromGuide
        fromGuide = false
        if (AppConnection.hasReadPermission(granted, readPermissions)) {
            onPermissionsReady()
            // 연결 완료 화면은 "준비 중"으로 열리고, 동기화 등록과 서버 완료 보고가 끝난 뒤에 완료로 바뀐다
            if (guided) screen = Screen.Connected
        } else if (guided) {
            screen = Screen.PermissionGuide("권한을 허용하지 않으면 기록을 보낼 수 없어요. 다시 눌러 허용해 주세요.")
        } else {
            dash = dash.copy(status = "권한이 없어 동기화를 시작할 수 없습니다", statusOk = false)
        }
        refresh()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        FileLog.init(this)
        // 지난 실행 이후 SDK 가 남긴 로그(15분 주기 동기화 등)를 파일로 떠 둔다. logcat 을 끝까지 읽어 느려서 IO 스레드에서
        lifecycleScope.launch(Dispatchers.IO) { FileLog.snapshotSdkLog() }
        setContent {
            LinkTheme {
                // 인터넷 연결 상태. 끊겼다가 돌아오면 못 불러온 대화 목록·홈 값을 다시 읽는다
                val online by rememberOnline()
                LaunchedEffect(online) { if (online) onBackOnline() }
                when (val s = screen) {
                    Screen.Loading -> LoadingScreen()
                    is Screen.EvidencePreview -> EvidencePreviewScreen(s.kind)
                    is Screen.Login -> LoginScreen(busy = busy, busyText = busyText, message = s.message, onLogin = ::login)
                    is Screen.Signup -> SignupScreen(
                        social = s.pending.socialType,
                        busy = busy,
                        errors = s.errors,
                        message = s.message,
                        onSubmit = ::signup,
                        onCancel = { screen = Screen.Login() },
                    )
                    Screen.Dashboard -> MainTabs(tab, online = online || previewOnly, onTab = { tab = it }) { current ->
                        when (current) {
                            Tab.HOME -> Dashboard(
                                state = dash,
                                busy = busy,
                                onSyncNow = ::syncNow,
                                onRefresh = ::refresh,
                                onRequestPermission = { if (hcReady) permissionLauncher.launch(permissions) },
                                onOpenBody = ::openBody,
                            )
                            Tab.COACH -> CoachingScreen(coaching)
                            Tab.HISTORY -> HistoryScreen(dash.history, dash.metrics.associate { it.key to it.label }, loaded = dash.loaded)
                            Tab.SETTINGS -> SettingsScreen(
                                state = dash,
                                busy = busy,
                                onOpenBody = ::openBody,
                                onOpenPermissions = ::openPermissionSettings,
                                onOpenBattery = ::openBatterySettings,
                                onLogout = ::logout,
                            )
                        }
                    }
                    Screen.BodyInfo -> BodyInfoScreen(
                        savedOn = dash.bodySavedOn,
                        notice = dash.bodyNotice,
                        onSave = ::saveBody,
                        onBack = { screen = Screen.Dashboard },
                    )
                    is Screen.PermissionGuide -> PermissionGuideScreen(
                        rationale = false,
                        message = s.message,
                        onAllow = ::requestFromGuide,
                        onLater = { screen = Screen.Dashboard; refresh() },
                    )
                    Screen.Rationale -> PermissionGuideScreen(rationale = true, message = "", onAllow = {}, onLater = ::finish)
                    Screen.Connected -> ConnectedScreen(
                        state = dash,
                        onHome = { screen = Screen.Dashboard; refresh() },
                        onRetry = ::syncNow,
                        onRetryReport = ::reportConnection,
                    )
                }
            }
        }
        // Health Connect 설정의 "이 앱이 권한을 쓰는 이유"로 열렸으면 설명만 보여 준다(로그인·동기화를 건드리지 않는다)
        if (intent.action in RATIONALE_ACTIONS) {
            screen = Screen.Rationale
            return
        }
        lifecycleScope.launch { restoreSession() }
    }

    /** 다른 앱(Health Connect·워치)에 다녀오면 값이 바뀌었을 수 있어 홈을 다시 읽는다. 새로고침 버튼 대신 */
    override fun onResume() {
        super.onResume()
        if (screen == Screen.Dashboard && !previewOnly) refresh()
    }

    /** 연결이 돌아왔을 때. 처음 화면을 열 때도 한 번 불리지만 다시 읽을 것이 없으면 아무 일도 하지 않는다 */
    private fun onBackOnline() {
        if (screen != Screen.Dashboard || previewOnly) return
        coaching.retryFailedLoads()
        refresh()
    }

    private fun openBody() {
        dash = dash.copy(bodyNotice = "")
        screen = Screen.BodyInfo
    }

    /** 설정 탭: Health Connect 의 이 앱 권한 화면. 못 열면 기존처럼 시스템 권한 창을 띄운다 */
    private fun openPermissionSettings() {
        if (previewOnly) return
        val opened = runCatching { startActivity(HealthConnectClient.getHealthConnectManageDataIntent(this)) }.isSuccess
        if (!opened && hcReady) permissionLauncher.launch(permissions)
    }

    /** 설정 탭: 배터리 최적화 목록. 앱이 직접 예외를 요청하지 않고 사용자가 고르게 둔다 */
    private fun openBatterySettings() {
        if (previewOnly) return
        runCatching { startActivity(Intent(android.provider.Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }
    }

    /** 안내 화면(A-02)의 "권한 허용하기". 결과는 permissionLauncher 에서 받는다 */
    private fun requestFromGuide() {
        if (!hcReady || previewOnly) return
        fromGuide = true
        permissionLauncher.launch(permissions)
    }

    /**
     * 저장된 로그인 상태로 시작한다. 시작할 때 한 번 갱신해 회원 정보를 새로 받는다(정상 갱신은 7일 활동으로 쳐진다).
     * 연결이 안 되는 등 나중에 다시 할 수 있는 실패면 저장된 상태로 계속한다 — 동기화는 SDK 자체 자격으로 돈다.
     */
    private suspend fun restoreSession() {
        // 디버그 빌드 전용: 소셜 로그인 설정 없이 가입 화면을 연다. 가짜 가입 자격이라 제출하면 서버가 401 로 거절한다
        // adb shell am start -n io.candiy.healthconnect.quickstart/.MainActivity --ez signupPreview true
        if (BuildConfig.DEBUG && intent.getBooleanExtra("signupPreview", false)) {
            screen = Screen.Signup(LoginResult.SignupRequired("preview", Instant.now().plusSeconds(600), SocialType.KAKAO))
            return
        }
        // 로그인·서버 없이 AI 코치 근거 상세의 차트·달력 모양만 본다(지어낸 값, coaching/EvidencePreview.kt).
        // debug 빌드와, -PevidencePreview=true 로 만든 release 빌드에서만 열린다(실기기 확인용). 로그인 상태는 건드리지 않는다
        // adb shell am start -n io.candiy.healthconnect.quickstart/.MainActivity --ez evidencePreview true --es previewState SLEEP
        if ((BuildConfig.DEBUG || BuildConfig.EVIDENCE_PREVIEW) && intent.getBooleanExtra("evidencePreview", false)) {
            previewOnly = true
            screen = Screen.EvidencePreview(intent.getStringExtra("previewState").orEmpty())
            return
        }
        // 디버그 빌드 전용: 로그인·Health Connect·SDK 없이 화면 모양만 본다(지어낸 건수). 버튼은 동작하지 않는다
        // adb shell am start -n io.candiy.healthconnect.quickstart/.MainActivity --ez dashboardPreview true --es previewState LATE
        // previewState: 홈 상태(OK·PREPARING·LATE·PERMISSION·WATCH·FAILED) 또는 GUIDE(권한 안내)·CONNECTED(연결 완료)
        //   LOADING·HISTORY_LOADING: 첫 읽기 전(스켈레톤) 홈·전송 기록
        if (BuildConfig.DEBUG && intent.getBooleanExtra("dashboardPreview", false)) {
            previewOnly = true
            val kind = intent.getStringExtra("previewState").orEmpty().uppercase()
            // 미리보기의 연결 완료는 서버 보고까지 끝난 모습으로 보여 준다
            dash = DashboardPreview.state(kind).let { if (kind == "CONNECTED") it.copy(connection = ConnectionReport.REPORTED) else it }
            tab = when (kind) { "HISTORY", "HISTORY_LOADING" -> Tab.HISTORY; "SETTINGS" -> Tab.SETTINGS; else -> Tab.HOME }
            screen = when (kind) {
                "GUIDE" -> Screen.PermissionGuide()
                "CONNECTED" -> Screen.Connected
                else -> Screen.Dashboard
            }
            return
        }
        if (collectorMode) return enterCollector()
        if (waitForLogout()) return
        val saved = auth.current() ?: run { screen = Screen.Login(); return }
        val outcome = auth.refresh()
        // 코드는 서버 오류 코드 또는 NETWORK·NO_RESPONSE. 토큰 값은 남기지 않는다
        FileLog.i("candiy", "session_restore outcome=${outcome::class.simpleName} code=${
            (outcome as? RefreshOutcome.SignedOut)?.code ?: (outcome as? RefreshOutcome.TryLater)?.code ?: "-"
        }")
        when (outcome) {
            is RefreshOutcome.Refreshed -> enter(outcome.session)
            is RefreshOutcome.TryLater -> enter(saved)
            is RefreshOutcome.SignedOut -> screen = Screen.Login("로그인이 끝났어요. 다시 로그인해 주세요.")
            RefreshOutcome.NoSession -> screen = Screen.Login()
        }
    }

    private fun login(social: SocialType) {
        if (busy || LogoutCleanup.pending != null) return
        busy = true
        busyText = LOGIN_BUSY
        screen = Screen.Login()
        lifecycleScope.launch {
            val step = loginFlow.login(social)
            busy = false
            FileLog.i("candiy", "login_result social=$social step=${step::class.simpleName} reason=${(step as? LoginStep.Failed)?.reason ?: "-"}")
            when (step) {
                is LoginStep.LoggedIn -> enter(step.session)
                is LoginStep.NeedsSignup -> screen = Screen.Signup(step.pending)
                LoginStep.Cancelled -> screen = Screen.Login()
                is LoginStep.Failed -> screen = Screen.Login(step.message)
            }
        }
    }

    private fun signup(form: ProfileForm) {
        val current = screen as? Screen.Signup ?: return
        if (busy) return
        busy = true
        lifecycleScope.launch {
            val step = loginFlow.signup(current.pending, form)
            busy = false
            FileLog.i("candiy", "signup_result step=${step::class.simpleName}")
            when (step) {
                is SignupStep.LoggedIn -> enter(step.session)
                is SignupStep.Invalid -> screen = current.copy(errors = step.errors, message = "")
                is SignupStep.Restart -> screen = Screen.Login(step.message)
                is SignupStep.Failed -> screen = current.copy(errors = emptyMap(), message = step.message)
            }
        }
    }

    /**
     * 로그인된 회원으로 대시보드를 연다. 사용자 ID 가 바뀌었으면(다른 계정·예전에 손으로 넣은 ID) 기존 동기화를 멈추고
     * 새 ID 로 다시 등록한다. SDK 는 ID 가 바뀌면 동기화 상태를 새로 시작해 이전 사람 기록을 새 ID 로 보내지 않는다.
     */
    private fun enter(session: Session) {
        member = session.user
        connectAttempt++
        dash = dash.copy(account = "${session.user.displayName} · ${LoginFlow.label(session.user.socialType)}", canLogout = true)
        startCollecting(session.user.endUserId, "login_ready")
    }

    /**
     * 수집 전용 모드 시작. 폰에 저장된 ID(예전에 입력한 ID)를 그대로 쓰고, 없으면 local.properties 의 candiy.endUserId.
     * 로그아웃은 기기 기록을 지우므로 이 모드에서는 버튼을 두지 않는다.
     */
    private fun enterCollector() {
        val id = UserIdStore.get(this)
            ?: BuildConfig.CANDIY_END_USER_ID.trim().takeIf { it.isNotEmpty() && it != "user-123" }
        if (id == null) {
            screen = Screen.Login("수집 전용 모드인데 사용자 ID 가 없어요. local.properties 에 candiy.endUserId 를 넣고 다시 빌드해 주세요.")
            return
        }
        dash = dash.copy(account = "수집 전용 모드 · 로그인 없음", canLogout = false)
        startCollecting(id, "collector_ready")
    }

    private fun startCollecting(id: String, event: String) {
        val before = UserIdStore.get(this)
        val changed = before != null && before != id
        UserIdStore.set(this, id)
        screen = Screen.Dashboard
        FileLog.i("candiy", "$event user_changed=$changed")

        // Health Connect 를 못 쓰는 기기에서는 getOrCreate()·HealthConnectManager 가 예외를 던지므로 가장 먼저 확인한다
        if (!checkHealthConnect()) return
        if (changed) manager.stopRealtime(Connections.BLE)
        checkPermissionsAndSync()
    }

    /**
     * 로그아웃: 서버 세션 폐기 → 수집을 멈추고 이 폰의 SDK 기록 정리(clearLocalData) → 사용자 ID 삭제(와이어프레임 A-01).
     * 서버에 알리지 못해도 이 폰에서는 로그아웃한다.
     *
     * SDK 는 전송 중이면 정리를 전송이 끝날 때까지 미룬다. 로그인 직후 첫 전송(30일치)은 1분 넘게 걸려 그동안 화면이
     * 멈춘 것처럼 보였다. 그래서 바로 로그인 화면으로 넘기고 "정리 중"을 보여 주며, 정리는 [LogoutCleanup] 에서 화면과
     * 상관없이 끝까지 돌린다. 정리가 끝나기 전에는 새 로그인을 막는다(이전 계정 기록 정리와 새 계정 등록이 섞이지 않게).
     */
    private fun logout() {
        if (busy || previewOnly) return
        val session = auth
        val sdk = if (hcReady) manager else null
        val app = applicationContext
        dash = initialDash
        coaching.reset()
        // 이전 계정의 동기화 결과·보고 응답이 뒤늦게 와도 쓰지 않게 끊는다
        member = null
        reportedMember = null
        connectAttempt++
        reportJob?.cancel()
        FileLog.i("candiy", "logout_started")
        LogoutCleanup.start {
            val started = System.currentTimeMillis()
            val result = session.logout()
            if (sdk != null) runCatching { sdk.clearLocalData() }.onFailure { FileLog.e("candiy", "clear_local_failed", it) }
            UserIdStore.clear(app)
            FileLog.i("candiy", "logout server=${result::class.simpleName} elapsedMs=${System.currentTimeMillis() - started}")
            if (result is ApiResult.Ok) "" else "서버에 로그아웃을 알리지 못했지만 이 폰에서는 로그아웃했어요."
        }
        lifecycleScope.launch { waitForLogout() }
    }

    /** 진행 중인 로그아웃 정리가 있으면 로그인 화면에서 끝나기를 기다린다. 화면을 다시 열었을 때도 이어서 기다린다 */
    private suspend fun waitForLogout(): Boolean {
        val cleanup = LogoutCleanup.pending ?: return false
        busy = true
        busyText = "이전 계정 기록을 정리하는 중… 보내던 기록이 있으면 끝날 때까지(1~2분) 기다려요."
        screen = Screen.Login()
        val message = cleanup.await()
        busy = false
        busyText = LOGIN_BUSY
        screen = Screen.Login(message)
        return true
    }

    /**
     * Health Connect 사용 가능 여부(포털 문서 「설치와 권한 — 권한 요청」). Android 14 미만은 앱 설치가 필요하고,
     * 설치·업데이트가 필요하면 Play 스토어의 Health Connect 화면을 연다. 사용할 수 있을 때만 true.
     */
    private fun checkHealthConnect(): Boolean {
        val status = HealthConnectClient.getSdkStatus(this)
        hcReady = status == HealthConnectClient.SDK_AVAILABLE
        if (hcReady) return true
        FileLog.i("candiy", "health_connect_unavailable status=$status")
        if (status == HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED) {
            dash = dash.copy(status = "Health Connect 설치·업데이트가 필요합니다. 설치한 뒤 앱을 다시 열어 주세요", statusOk = false)
            runCatching {
                startActivity(
                    Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$HC_PACKAGE&url=healthconnect%3A%2F%2Fonboarding"))
                        .setPackage("com.android.vending")
                )
            }
        } else {
            dash = dash.copy(status = "이 기기에서는 Health Connect 를 쓸 수 없어 건강 데이터를 수집할 수 없습니다", statusOk = false)
        }
        return false
    }

    private fun checkPermissionsAndSync() {
        if (!hcReady) return
        lifecycleScope.launch {
            val granted = HealthConnectClient.getOrCreate(this@MainActivity).permissionController.getGrantedPermissions()
            // 읽기 권한이 하나도 없으면 시스템 권한 창 전에 안내 화면(A-02)을 보여 준다.
            // 일부만 허용한 사람은 다시 열 때마다 안내로 돌아가지 않고 바로 시작하며, 빠진 권한은 홈의 권한 줄에서 알린다(S15P31A403-366)
            if (AppConnection.hasReadPermission(granted, readPermissions)) onPermissionsReady() else screen = Screen.PermissionGuide()
            refresh()
        }
    }

    /** 키·몸무게 저장. 둘 중 하나만 넣어도 된다. 범위를 벗어나면 저장하지 않는다. */
    private fun saveBody(heightText: String, weightText: String) {
        val h = heightText.trim().toDoubleOrNull()
        val w = weightText.trim().toDoubleOrNull()
        when {
            h == null && w == null -> { dash = dash.copy(bodyNotice = "키나 몸무게를 숫자로 입력해 주세요"); return }
            h != null && !BodyInfo.validHeightCm(h) -> { dash = dash.copy(bodyNotice = "키는 100~250cm 사이로 입력해 주세요"); return }
            w != null && !BodyInfo.validWeightKg(w) -> { dash = dash.copy(bodyNotice = "몸무게는 20~300kg 사이로 입력해 주세요"); return }
        }
        if (!hcReady) return
        lifecycleScope.launch {
            try {
                BodyInfo.save(this@MainActivity, h, w)
                dash = dash.copy(bodyNotice = "저장했어요. 다음 동기화 때 서버로 보내요.", bodySavedOn = BodyInfo.savedOn(this@MainActivity))
            } catch (e: Exception) {
                FileLog.e("candiy", "body_info_failed", e)
                dash = dash.copy(bodyNotice = "저장하지 못했어요 · 권한을 확인해 주세요")
            }
        }
    }

    /**
     * 화면 값 새로 읽기. 건강 수치는 읽지 않고 지표마다 기록 건수·마지막 측정 시각만 읽는다.
     * 서버로 갔는지는 SDK 로그의 지표별 전송 결과(FileLog.transfers)로 본다.
     */
    private fun refresh() {
        if (!hcReady) return
        lifecycleScope.launch {
            val client = HealthConnectClient.getOrCreate(this@MainActivity)
            val granted = runCatching { client.permissionController.getGrantedPermissions() }.getOrDefault(emptySet())
            val nearby = runCatching {
                Wearable.getNodeClient(this@MainActivity).connectedNodes.await().count { it.isNearby }
            }.getOrNull()
            // 등록된 알람 예정 시각. 첫 값만 읽는다(SDK 는 1초마다 다시 내보낸다)
            val nextAt = runCatching { manager.syncAlarmNextAt().first() }.getOrNull()
            val since = Instant.now().minus(Duration.ofHours(24))
            // logcat 덤프·로그 파일 읽기는 느려서 IO 스레드에서 한다
            val (lastSync, transfers, history) = withContext(Dispatchers.IO) {
                FileLog.snapshotSdkLog()
                Triple(FileLog.lastSync(), FileLog.transfers(), FileLog.history())
            }
            val metrics = types.map { (type, cls) ->
                val permitted = HealthPermission.getReadPermission(cls) in granted
                MetricStatus(
                    key = type.name,
                    label = LABELS[type].orEmpty(),
                    count24h = if (permitted) runCatching { countSince(client, cls, since) }.getOrNull() else null,
                    lastMeasured = if (permitted) runCatching { latestTime(client, cls) }.getOrNull() else null,
                    permitted = permitted,
                    transfer = transfers[type.name],
                )
            }
            dash = dash.copy(
                permGranted = granted.intersect(permissions).size,
                permTotal = permissions.size,
                watchNearby = nearby,
                watchLastReceived = WatchWalkReceiver.lastReceived(this@MainActivity),
                nextSyncAt = nextAt,
                metrics = metrics,
                lastSync = lastSync,
                history = history,
                bodySavedOn = BodyInfo.savedOn(this@MainActivity),
                loaded = true,
            )
        }
    }

    /** 최근 30일 중 가장 늦게 측정된 기록의 시각(구간 기록은 끝, 시점 기록은 그 시각). 없으면 null */
    private suspend fun latestTime(client: HealthConnectClient, cls: KClass<out Record>): Instant? {
        val from = Instant.now().minus(Duration.ofDays(30))
        val r = client.readRecords(ReadRecordsRequest(cls, TimeRangeFilter.after(from), ascendingOrder = false, pageSize = 1))
            .records.firstOrNull() ?: return null
        // 구간·시점 기록의 공통 타입(IntervalRecord·InstantRecord)이 라이브러리 내부용이라 13종을 직접 나눈다
        return when (r) {
            is StepsRecord -> r.endTime
            is HeartRateRecord -> r.endTime
            is SleepSessionRecord -> r.endTime
            is ExerciseSessionRecord -> r.endTime
            is DistanceRecord -> r.endTime
            is TotalCaloriesBurnedRecord -> r.endTime
            is SpeedRecord -> r.endTime
            is OxygenSaturationRecord -> r.time
            is Vo2MaxRecord -> r.time
            is WeightRecord -> r.time
            is BodyFatRecord -> r.time
            is BasalMetabolicRateRecord -> r.time
            is HeightRecord -> r.time
            else -> null
        }
    }

    private suspend fun countSince(client: HealthConnectClient, cls: KClass<out Record>, since: Instant): Int {
        var n = 0
        var token: String? = null
        do {
            val res = client.readRecords(ReadRecordsRequest(cls, TimeRangeFilter.after(since), pageToken = token))
            n += res.records.size
            token = res.pageToken
        } while (token != null)
        return n
    }

    /**
     * 지금 바로 한 번 동기화. stopRealtime 은 알람만 끄고 변경 추적 토큰은 그대로 두므로,
     * 다시 등록하면 "지난 동기화 이후 새로 들어온 기록"만 읽는 동기화가 몇 초 뒤 한 번 돈다.
     */
    private fun syncNow() {
        if (!hcReady) return
        manager.stopRealtime(Connections.BLE)
        startSync()
    }

    private fun onPermissionsReady() {
        if (!mock) return startSync()
        // adb shell am start -n io.candiy.healthconnect.quickstart/.MainActivity --ez late true
        if (intent.getBooleanExtra("late", false)) return simulateLateArrival()
        lifecycleScope.launch {
            try {
                val client = HealthConnectClient.getOrCreate(this@MainActivity)
                dash = dash.copy(status = "목업 데이터를 쓰는 중", statusOk = null)
                val written = MockHealthData.write(client)
                Log.d("candiy", "mock_written records=$written")
                dash = dash.copy(mockResult = MockHealthData.readBack(client, packageName))
                startSync()
            } catch (e: Exception) {
                Log.e("candiy", "mock_failed", e)
                dash = dash.copy(status = "목업 데이터 쓰기 실패 — ${e.message}", statusOk = false)
            }
        }
    }

    /**
     * 늦게 도착한 데이터 체험. 과거 시각의 운동 기록을 지금 Health Connect 에 쓰고 바로 다음 동기화를 돌린다.
     */
    private fun simulateLateArrival() {
        lifecycleScope.launch {
            try {
                val desc = MockHealthData.writeLateWorkout(HealthConnectClient.getOrCreate(this@MainActivity))
                Log.d("candiy", "mock_late_written $desc")
                dash = dash.copy(mockResult = desc)
                syncNow()
            } catch (e: Exception) {
                Log.e("candiy", "mock_late_failed", e)
                dash = dash.copy(status = "늦은 기록 쓰기 실패 — ${e.message}", statusOk = false)
            }
        }
    }

    private fun startSync() {
        val endUserId = UserIdStore.get(this) ?: return
        dash = dash.copy(status = "동기화를 등록하는 중", statusOk = null)
        manager.syncHealthData(
            type = Connections.BLE,
            dataTypes = types.keys,
            // 로그인 응답으로 받은 사용자 ID (UserIdStore)
            endUserId = endUserId,
            // local.properties 의 candiy.clientId · candiy.clientSecret
            credentials = ClientCredentials(
                clientId = BuildConfig.CANDIY_CLIENT_ID,
                clientSecret = BuildConfig.CANDIY_CLIENT_SECRET,
            ),
            intervalMinutes = 15,
        ) { ok, message ->
            FileLog.i("candiy", "sync_started ok=$ok msg=${LogRedaction.freeText(message)}")
            // 콜백은 백그라운드 스레드에서 오고, 첫 동기화 때는 지표마다 여러 번 온다
            runOnUiThread {
                dash = dash.copy(status = message, statusOk = ok)
                if (ok) {
                    loadNextSync()
                    reportConnection()
                }
                // 첫 동기화는 알람으로 몇 초 뒤에 돈다. 끝날 즈음 화면을 다시 읽되, 여러 번 와도 마지막 한 번만 읽는다
                pendingRefresh?.cancel()
                pendingRefresh = lifecycleScope.launch { delay(20_000); refresh() }
            }
        }
    }

    /**
     * 앱 연동 완료를 서버에 알린다(S15P31A403-366). 동기화 등록이 성공했을 때와 연결 완료 화면의 "다시 시도"에서 부른다.
     * 등록 결과는 뒤늦게, 여러 번 올 수 있어 보내기 전에 회원·권한·시도 번호를 다시 확인한다.
     * 보고만 다시 할 때 동기화 등록(stopRealtime·syncHealthData)은 건드리지 않는다.
     */
    private fun reportConnection() {
        val user = member
        // 목업·수집 전용 모드는 실제 회원이 아니라 보고하지 않는다. 연결 완료 화면이 기다리지 않게 완료로 둔다
        if (user == null || mock || collectorMode || previewOnly) {
            dash = dash.copy(connection = ConnectionReport.REPORTED)
            return
        }
        if (!hcReady || reportedMember == user.id || reportJob?.isActive == true) return
        val attempt = connectAttempt
        reportJob = lifecycleScope.launch {
            // 동기화에 등록한 사용자 ID 가 지금 로그인한 회원의 것인지
            val session = auth.current()
            if (session?.user?.id != user.id || !AppConnection.sameMember(session, UserIdStore.get(this@MainActivity))) {
                FileLog.i("candiy", "app_connection skipped=account")
                return@launch
            }
            val granted = runCatching {
                HealthConnectClient.getOrCreate(this@MainActivity).permissionController.getGrantedPermissions()
            }.getOrDefault(emptySet())
            if (attempt != connectAttempt) return@launch
            if (!AppConnection.hasReadPermission(granted, readPermissions)) {
                FileLog.i("candiy", "app_connection skipped=permission")
                dash = dash.copy(connection = ConnectionReport.PENDING)
                return@launch
            }
            dash = dash.copy(connection = ConnectionReport.SENDING)
            val result = reporter.report(user.id) { attempt == connectAttempt && member?.id == user.id }
            FileLog.i("candiy", "app_connection result=${result.code()}")
            if (attempt != connectAttempt) return@launch
            when (result) {
                ReportResult.Reported -> {
                    reportedMember = user.id
                    dash = dash.copy(connection = ConnectionReport.REPORTED)
                }
                is ReportResult.Pending -> dash = dash.copy(connection = ConnectionReport.PENDING)
                ReportResult.Stale -> dash = dash.copy(connection = ConnectionReport.IDLE)
                ReportResult.SignedOut -> {
                    coaching.reset()
                    screen = Screen.Login("로그인이 끝났어요. 다시 로그인해 주세요.")
                }
            }
        }
    }

    /** 다음 동기화 예정 시각. SDK 가 알람을 거는 데 조금 걸려 몇 초 동안 다시 읽는다 */
    private fun loadNextSync() {
        lifecycleScope.launch {
            repeat(10) {
                val at = runCatching { manager.syncAlarmNextAt().first() }.getOrNull()
                if (at != null) { dash = dash.copy(nextSyncAt = at); return@launch }
                delay(500)
            }
        }
    }

    companion object {
        // Health Connect 권한 근거 화면 요청 (AndroidManifest.xml 의 intent-filter 와 짝)
        private val RATIONALE_ACTIONS = setOf(
            "androidx.health.ACTION_SHOW_PERMISSIONS_RATIONALE",
            "android.intent.action.VIEW_PERMISSION_USAGE",
        )
        private const val HC_PACKAGE = "com.google.android.apps.healthdata"
        private const val LOGIN_BUSY = "로그인하는 중…"

        private val LABELS = mapOf(
            DataTypes.STEPS to "걸음",
            DataTypes.HEART_RATE to "심박",
            DataTypes.OXYGEN_SATURATION to "산소포화도",
            DataTypes.SLEEP to "수면",
            DataTypes.EXERCISE_SESSION to "운동",
            DataTypes.DISTANCE_WALKED to "거리",
            DataTypes.CALORIES_BURNED to "칼로리",
            DataTypes.SPEED to "속도",
            DataTypes.VO2_MAX to "VO2 Max",
            DataTypes.WEIGHT to "체중",
            DataTypes.BODY_FAT to "체지방",
            DataTypes.BASAL_METABOLIC_RATE to "기초대사량",
            DataTypes.HEIGHT to "키",
        )
    }
}
