package io.candiy.healthconnect.quickstart.wear

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.lifecycleScope
import com.google.android.gms.wearable.MessageClient
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId

/**
 * 워치 앱 화면. 걸음·심박 등은 자동(패시브) 수집과 삼성 헬스가 맡으므로 사용자가 누를 것은 없다.
 * 스크롤 없이 한 화면에 폰 연결과 네 지표(걸음·심박·산소포화도·수면)의 전송 상태를 보여 준다(S15P31A403-360).
 *
 * 배치는 "상태 고리"다(S15P31A403-440 시안 1안). 손목을 들었을 때 궁금한 것은 "지금 잘 보내지고 있나" 하나라,
 * 가운데 한 줄이 전체 상태를 말하고("모두 보냈어요"·"1개 확인 필요"), 바깥 고리의 네 조각이 네 지표의 상태를 색으로 보인다.
 * 조각 자리는 읽는 순서와 같다: 왼쪽 위 걸음 · 오른쪽 위 심박 · 왼쪽 아래 산소포화도 · 오른쪽 아래 수면.
 * 처음에는 2×2 타일이었는데 네 칸을 다 읽어야 전체 상태를 알 수 있었다.
 * 크기는 모두 화면 폭의 비율로 잡아 갤럭시 워치 40mm·44mm 에서 같은 배치가 된다. 판단은 폰 앱 홈과 같다(폰이 보낸 상태를 그대로 그린다).
 *
 * 배터리: 화면이 열릴 때(onResume) 한 번만 폰에 묻고, 주기적으로 다시 읽거나 움직이는 그림을 두지 않는다.
 * 바탕은 어두운 와인빛 위에 서비스 코랄이 위쪽에서 번져 내려오는 그림이다(시안 배경 3). 사탕 로고 뒤가 밝아 로고가 살고,
 * 글자가 놓이는 가운데와 아래는 어두워 밤에도 눈부시지 않다. 움직이지 않는 그림이라 배터리 부담은 단색과 같다.
 */
class StatusActivity : ComponentActivity() {

    private lateinit var regular: Typeface
    private lateinit var bold: Typeface
    private lateinit var ring: RingView
    /** 지표 키(STEPS 등) → 그 지표의 상태 줄. 순서는 폰 앱 홈과 같다 */
    private val lines = linkedMapOf<String, TextView>()
    /** 지표 키 → 상태 종류(ok · bad · idle). 고리 조각의 색과 가운데 한 줄을 정한다 */
    private val kinds = linkedMapOf<String, String>()
    /** 가운데 큰 한 줄: 전체 상태 */
    private lateinit var headlineView: TextView
    /** 가운데 작은 줄: 폰 연결 · 상태를 받은 시각, 또는 안내 */
    private lateinit var subView: TextView
    /** 아래 한 줄: 워치 → 폰 전송. 누르면 지금 보낸다 */
    private lateinit var watchView: TextView
    /** 화면 폭(px). 둥근 화면이라 모든 크기를 이 값의 비율로 잡는다 */
    private val screen by lazy { resources.displayMetrics.widthPixels }

    private enum class Mode { WAITING, HINT, STATUS, PERMISSION }

    private var mode = Mode.WAITING
    private var phoneText = "폰 확인 중"
    private var checkedText = ""

    // 폰 응답은 화면이 보이는 동안만 받는다 (onResume 에 붙이고 onPause 에 뗀다)
    private val statusListener = MessageClient.OnMessageReceivedListener { event: MessageEvent ->
        if (event.path != STATUS_PATH) return@OnMessageReceivedListener
        val body = String(event.data)
        statusPrefs(this).edit().putString("last", body).apply()
        runOnUiThread { showStatus(body, fresh = true) }
    }

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { result ->
        FileLog.i(TAG, "permission_result granted=${result.count { it.value }}/${result.size}")
        if (REQUIRED.all { checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }) askBackground()
        else { mode = Mode.PERMISSION; renderCenter() }
    }

    // 앱이 꺼져 있어도 심박을 받으려면 "항상 허용"(백그라운드 신체 센서)이 따로 필요하다
    private val backgroundLauncher = registerForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        FileLog.i(TAG, "background_sensor_granted=$ok")
        startPassive()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        FileLog.init(this)
        // 폰 앱·웹과 같은 Paperlogy. 워치는 보통·굵게 두 굵기만 넣었다 (라이선스 assets/Paperlogy-OFL.txt)
        regular = resources.getFont(R.font.paperlogy_regular)
        bold = resources.getFont(R.font.paperlogy_bold)
        setContentView(buildPage())

        if (REQUIRED.all { checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }) askBackground()
        else permissionLauncher.launch(REQUIRED)
    }

    override fun onResume() {
        super.onResume()
        Wearable.getMessageClient(this).addListener(statusListener)
        val cached = statusPrefs(this).getString("last", null)
        if (cached != null) showStatus(cached, fresh = false) else showStatusWaiting()
        watchView.text = PassiveCollector.summary(this)
        lifecycleScope.launch {
            // 폰과 블루투스로 직접 붙어 있을 때만 묻는다. 끊겨 있어도 기록은 워치에 보관됐다가 나중에 전달된다
            val nearby = runCatching {
                Wearable.getNodeClient(this@StatusActivity).connectedNodes.await().filter { it.isNearby }
            }.getOrNull()
            showPhone(nearby?.size)
            // 받은 적이 없는데 물어볼 폰도 없으면 기다리지 않고 안내로 돌린다. 물었으면 답을 잠깐만 기다린다
            if (cached == null) {
                if (nearby.isNullOrEmpty()) showStatusHint()
                else subView.postDelayed({ if (mode == Mode.WAITING) showStatusHint() }, STATUS_WAIT_MS)
            }
            nearby?.forEach { node ->
                runCatching { Wearable.getMessageClient(this@StatusActivity).sendMessage(node.id, STATUS_REQUEST_PATH, ByteArray(0)).await() }
                    .onFailure { FileLog.e(TAG, "status_request_failed", it) }
            }
        }
    }

    override fun onPause() {
        Wearable.getMessageClient(this).removeListener(statusListener)
        super.onPause()
    }

    private fun askBackground() {
        if (checkSelfPermission(Manifest.permission.BODY_SENSORS_BACKGROUND) == PackageManager.PERMISSION_GRANTED) startPassive()
        else backgroundLauncher.launch(Manifest.permission.BODY_SENSORS_BACKGROUND)
    }

    /** 자동(패시브) 수집 등록. 걸음·거리·칼로리는 백그라운드 권한 없이도 받는다. 여러 번 불러도 안전하다. */
    private fun startPassive() = lifecycleScope.launch {
        try {
            PassiveCollector.start(this@StatusActivity)
        } catch (e: Exception) {
            FileLog.e(TAG, "passive_start_failed", e)
        }
        watchView.text = PassiveCollector.summary(this@StatusActivity)
    }

    private fun showPhone(nearby: Int?) {
        phoneText = when {
            nearby == null -> "폰 확인 중"
            nearby > 0 -> "폰 연결됨"
            else -> "폰 연결 안 됨"
        }
        renderCenter()
    }

    /** 폰이 보낸 네 지표 상태를 넣는다. [fresh]가 아니면 지난번 받은 값이라 받은 시각을 같이 쓴다. */
    private fun showStatus(body: String, fresh: Boolean) {
        val json = runCatching { JSONObject(body) }.getOrNull() ?: return
        val metrics = json.optJSONArray("metrics") ?: return
        for (i in 0 until metrics.length()) {
            val m = metrics.optJSONObject(i) ?: continue
            // 키가 없는 예전 응답은 이름으로 맞춘다
            val key = m.optString("key").takeIf { it in lines } ?: LABELS.entries.firstOrNull { it.value == m.optString("label") }?.key ?: continue
            setMetric(key, m.optString("line"), m.optString("kind"))
        }
        val at = Instant.ofEpochMilli(json.optLong("at")).atZone(ZoneId.systemDefault()).toLocalTime().toString().take(5)
        checkedText = if (fresh) "방금" else at
        mode = Mode.STATUS
        renderCenter()
    }

    /** 폰 응답을 기다리는 동안. 배터리를 아끼려고 움직이는 그림 없이 글자만 둔다 */
    private fun showStatusWaiting() {
        LABELS.keys.forEach { setMetric(it, "확인 중", "idle") }
        checkedText = ""
        mode = Mode.WAITING
        renderCenter()
    }

    /** 폰 응답이 없을 때(폰이 멀거나 폰 앱이 아직 답하지 않음) */
    private fun showStatusHint() {
        LABELS.keys.forEach { setMetric(it, "확인 전", "idle") }
        mode = Mode.HINT
        renderCenter()
    }

    /** 지표 한 칸의 상태 줄과 고리 조각. 색 구분은 폰 홈 지표 카드와 같다(보냄 초록 · 문제 빨강 · 그 밖 회색) */
    private fun setMetric(key: String, line: String, kind: String) {
        val view = lines[key] ?: return
        view.text = line
        view.setTextColor(when (kind) { "ok" -> OK; "bad" -> BAD; else -> MUTED })
        kinds[key] = kind
        ring.set(LABELS.keys.indexOf(key), kind)
    }

    /**
     * 가운데 두 줄. 큰 줄은 전체 상태 한마디, 작은 줄은 폰 연결과 상태를 받은 시각이다.
     * 문제가 하나라도 있으면 몇 개인지 먼저 말하고, 모두 보냈으면 그렇게 말한다.
     * 문제는 없지만 "새 기록 없음"처럼 보낼 것이 없던 지표가 섞여 있으면 "문제 없어요"다
     */
    private fun renderCenter() {
        val bad = kinds.values.count { it == "bad" }
        val (title, color) = when (mode) {
            Mode.PERMISSION -> "권한이 필요해요" to BAD
            Mode.WAITING -> "확인 중" to TEXT
            Mode.HINT -> "확인 전" to TEXT
            Mode.STATUS -> when {
                bad > 0 -> "${bad}개 확인 필요" to BAD
                kinds.isNotEmpty() && kinds.values.all { it == "ok" } -> "모두 보냈어요" to TEXT
                else -> "문제 없어요" to TEXT
            }
        }
        headlineView.text = title
        headlineView.setTextColor(color)
        subView.text = when (mode) {
            Mode.PERMISSION -> "앱을 다시 열어 허용해 주세요"
            Mode.HINT -> "폰 앱을 열면 상태가 보여요"
            else -> listOf(phoneText, checkedText).filter { it.isNotEmpty() }.joinToString(" · ")
        }
    }

    // ----- 화면 -----

    /**
     * 한 화면 배치(스크롤 없음). 자리는 시안(432×432)에서 잰 값을 화면 폭의 비율로 옮겼다.
     * 위에서부터: 사탕 로고 · 걸음과 심박 · 가운데 두 줄 · 산소포화도와 수면 · 워치 전송 한 줄.
     * 둥근 화면이라 위아래로 갈수록 가로 폭이 줄어든다. 가장 넓은 가운데에 큰 글자를 두고, 좁은 맨 위와 맨 아래에는 짧은 것만 둔다
     */
    private fun buildPage(): View {
        fun px(value: Float) = (screen * value / DESIGN).toInt()

        ring = RingView(this)
        val root = FrameLayout(this)
        root.addView(ring, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        root.addView(ImageView(this).apply {
            setImageResource(R.drawable.candy)
            contentDescription = "LiNK"
        }, FrameLayout.LayoutParams(px(68f), px(37f), Gravity.CENTER_HORIZONTAL).apply { topMargin = px(40f) })

        // 네 지표: 윗줄 왼쪽·오른쪽, 아랫줄 왼쪽·오른쪽. 고리 조각과 같은 자리다
        val spots = listOf(58f to 92f, 224f to 92f, 58f to 270f, 224f to 270f)
        LABELS.entries.forEachIndexed { index, (key, label) ->
            root.addView(metric(key, label, ICONS.getValue(key), ICON_COLORS.getValue(key)), FrameLayout.LayoutParams(px(150f), ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                leftMargin = px(spots[index].first)
                topMargin = px(spots[index].second)
            })
        }

        headlineView = text("확인 중", 33f, TEXT, bold).apply { letterSpacing = -0.015f }
        subView = text("", 18f, MUTED).apply {
            maxLines = 1
            // "폰 앱을 열면 상태가 보여요"처럼 긴 안내도 한 줄에 들어오게 줄인다
            setAutoSizeTextTypeUniformWithConfiguration(px(13f), px(18f), 1, TypedValue.COMPLEX_UNIT_PX)
        }
        root.addView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            addView(headlineView)
            addView(subView, LinearLayout.LayoutParams(px(300f), px(26f)).apply { topMargin = px(2f) })
        }, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = px(170f) })

        // 워치 → 폰 전송. 누르면 15분을 기다리지 않고 지금 보낸다(확인용)
        watchView = text("", 17f, MUTED).apply {
            maxLines = 1
            // "전송 실패 · N건 다음에 다시"처럼 긴 결과도 고리 안쪽 한 줄에 들어오게 줄인다
            setAutoSizeTextTypeUniformWithConfiguration(px(12f), px(17f), 1, TypedValue.COMPLEX_UNIT_PX)
            setOnClickListener {
                text = "보내는 중…"
                lifecycleScope.launch { PassiveCollector.flush(this@StatusActivity); text = PassiveCollector.summary(this@StatusActivity) }
            }
        }
        // 누르는 곳은 글자보다 넉넉하게 잡는다(높이 40). 글자는 그 안에서 위쪽에 둔다
        root.addView(watchView, FrameLayout.LayoutParams(px(224f), px(40f), Gravity.CENTER_HORIZONTAL).apply { topMargin = px(344f) })
        return root
    }

    /** 지표 한 칸: 윗줄에 아이콘과 이름, 아랫줄에 짧은 전송 상태(예: 보냄 14:20) */
    private fun metric(key: String, label: String, icon: Int, tint: Int): View {
        fun px(value: Float) = (screen * value / DESIGN).toInt()
        val line = text("확인 중", 18f, MUTED).apply {
            maxLines = 1
            setAutoSizeTextTypeUniformWithConfiguration(px(13f), px(18f), 1, TypedValue.COMPLEX_UNIT_PX)
        }
        lines[key] = line
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            // 아이콘과 이름 줄은 칸 폭을 다 차지하므로(세로 묶음의 기본값) 그 안에서 가운데로 모은다.
            // 세로 가운데만 맞췄더니 이름이 칸 왼쪽에 붙어 아래 상태 줄과 어긋났다(실기기에서 확인)
            addView(LinearLayout(this@StatusActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER
                addView(ImageView(this@StatusActivity).apply {
                    setImageResource(icon)
                    imageTintList = ColorStateList.valueOf(tint)
                }, LinearLayout.LayoutParams(px(22f), px(22f)).apply { marginEnd = px(6f) })
                addView(text(label, 23f, TEXT, bold).apply { maxLines = 1 })
            })
            addView(line, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, px(25f)))
        }
    }

    /** 글자 크기는 시안의 px 값을 화면 폭 비율로 옮긴다(40mm 에서 이름 약 11sp · 상태 약 8.5sp). 둥근 한 화면이라 글자 배율 설정은 따르지 않는다 */
    private fun text(value: String, size: Float, color: Int, face: Typeface = regular) = TextView(this).apply {
        text = value
        setTextSize(TypedValue.COMPLEX_UNIT_PX, screen * size / DESIGN)
        typeface = face
        setTextColor(color)
        gravity = Gravity.CENTER
        includeFontPadding = false
    }

    /**
     * 바탕과 상태 고리. 고리는 네 조각이고 조각마다 지표 하나의 상태 색을 띤다.
     * 각도는 12시가 0도, 시계 방향이다: 걸음 278~352 · 심박 8~82 · 산소포화도 188~262 · 수면 98~172
     */
    private class RingView(context: Context) : View(context) {
        private val kinds = arrayOf("idle", "idle", "idle", "idle")
        private val base = Paint()
        private val glow = Paint(Paint.ANTI_ALIAS_FLAG)
        private val arc = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND }
        private val box = RectF()

        fun set(index: Int, kind: String) {
            if (index !in kinds.indices || kinds[index] == kind) return
            kinds[index] = kind
            invalidate()
        }

        override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
            base.shader = LinearGradient(0f, 0f, 0f, h.toFloat(), BG_TOP, BG_BOTTOM, Shader.TileMode.CLAMP)
            // 코랄 빛은 화면 위 바깥(높이의 8% 위)에서 시작해 타원으로 번지고, 반지름의 66% 에서 사라진다
            glow.shader = RadialGradient(0f, 0f, h * GLOW_HEIGHT, intArrayOf(GLOW, GLOW_END), floatArrayOf(0f, 0.66f), Shader.TileMode.CLAMP)
            val radius = w * 201f / DESIGN
            box.set(w / 2f - radius, h / 2f - radius, w / 2f + radius, h / 2f + radius)
            arc.strokeWidth = w * 11f / DESIGN
        }

        override fun onDraw(canvas: Canvas) {
            canvas.drawPaint(base)
            canvas.save()
            canvas.translate(width / 2f, -0.08f * height)
            // 타원: 가로 반지름은 폭의 115%, 세로 반지름은 높이의 85%
            canvas.scale(GLOW_WIDTH * width / (GLOW_HEIGHT * height), 1f)
            canvas.drawCircle(0f, 0f, height * GLOW_HEIGHT, glow)
            canvas.restore()
            STARTS.forEachIndexed { index, start ->
                arc.color = when (kinds[index]) { "ok" -> OK; "bad" -> BAD; else -> RING_IDLE }
                canvas.drawArc(box, start - 90f, SWEEP, false, arc)
            }
        }

        private companion object {
            val STARTS = floatArrayOf(278f, 8f, 188f, 98f)
            const val SWEEP = 74f
            const val GLOW_WIDTH = 1.15f
            const val GLOW_HEIGHT = 0.85f
        }
    }

    companion object {
        private const val TAG = "CandiyWear"
        // 폰 앱 WatchWalkReceiver 와 같은 경로
        private const val STATUS_REQUEST_PATH = "/candiy/status/request"
        private const val STATUS_PATH = "/candiy/status"
        // 심박(신체 센서)·걸음(신체 활동) 둘 다 필요
        private val REQUIRED = arrayOf(Manifest.permission.BODY_SENSORS, Manifest.permission.ACTIVITY_RECOGNITION)
        /** 시안을 그린 화면 폭(px). 갤럭시 워치6 40mm */
        private const val DESIGN = 432f
        // 웹·폰 앱(LinkTheme)과 같은 계열을 어두운 바탕에 맞게 옮긴 값.
        // 바탕은 브랜드 레드를 아주 어둡게 가라앉힌 와인빛이고, 그 위에 서비스 코랄(coral-main #EDA18B)이 위에서 번진다.
        // 글자는 웹 primary-foreground(따뜻한 흰색). 보조 글자·보냄·문제 색은 코랄 빛이 가장 밝은 곳에서도 읽히게 조금 밝혔다.
        // 글자 대비(코랄 빛이 가장 밝은 곳 #6B4A43 기준): 흰 글자 7.5:1 · 보조 글자 4.9:1 · 보냄 4.9:1 · 문제 4.9:1
        private val BG_TOP = Color.parseColor("#2B1E21")
        private val BG_BOTTOM = Color.parseColor("#241A1D")
        private val GLOW = Color.argb(158, 237, 161, 139)
        private val GLOW_END = Color.argb(0, 237, 161, 139)
        private val TEXT = Color.parseColor("#FCF9F6")
        private val MUTED = Color.parseColor("#D9C8C6")
        private val OK = Color.parseColor("#9CDCB0")
        private val BAD = Color.parseColor("#FFBDB5")
        /** 보낼 것이 없거나 아직 확인하지 않은 지표의 고리 조각(보조 글자색을 42% 로 옅게) */
        private val RING_IDLE = Color.argb(107, 217, 200, 198)
        /** 지표 순서와 이름. 폰 앱 홈의 네 지표(HomeStatus.CORE)와 같다 */
        private val LABELS = linkedMapOf("STEPS" to "걸음", "HEART_RATE" to "심박", "OXYGEN_SATURATION" to "산소포화도", "SLEEP" to "수면")
        private val ICONS = mapOf(
            "STEPS" to R.drawable.ic_metric_steps, "HEART_RATE" to R.drawable.ic_metric_heart,
            "OXYGEN_SATURATION" to R.drawable.ic_metric_oxygen, "SLEEP" to R.drawable.ic_metric_sleep,
        )
        /** 폰 앱의 지표 색(걸음 #3F9A6B · 심박 #EE4E57 · 산소포화도 #3A7BD5 · 수면 #6F5BD8)을 어두운 바탕에서 읽히게 밝힌 값 */
        private val ICON_COLORS = mapOf(
            "STEPS" to Color.parseColor("#6FCF9B"), "HEART_RATE" to Color.parseColor("#FF7A82"),
            "OXYGEN_SATURATION" to Color.parseColor("#7FB0F5"), "SLEEP" to Color.parseColor("#A796F5"),
        )
        /** 폰에 지표 상태를 물은 뒤 기다리는 시간. 넘으면 안내로 돌린다 */
        private const val STATUS_WAIT_MS = 4_000L

        private fun statusPrefs(c: Context) = c.getSharedPreferences("candiy_status", Context.MODE_PRIVATE)
    }
}
