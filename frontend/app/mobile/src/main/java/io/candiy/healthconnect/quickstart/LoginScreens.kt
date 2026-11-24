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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.candiy.healthconnect.quickstart.auth.LoginFlow
import io.candiy.healthconnect.quickstart.auth.ProfileForm
import io.candiy.healthconnect.quickstart.auth.ProfileValidation
import io.candiy.healthconnect.quickstart.auth.SocialType

private val Hint = Color(0xFF757575)
private val Error = Color(0xFFC62828)
private val KakaoYellow = Color(0xFFFEE500)

/** 로그인 상태를 확인하는 동안 */
@Composable
fun LoadingScreen() {
    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background), contentAlignment = Alignment.Center) {
        // 진행 표시는 꾸밈이라 브랜드 레드로 둔다
        CircularProgressIndicator(color = Link.Red)
    }
}

/** 와이어프레임 A-01-1. 기존 회원은 바로 로그인, 신규 회원은 가입 화면으로 간다 */
@Composable
fun LoginScreen(busy: Boolean, busyText: String, message: String, onLogin: (SocialType) -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .safeDrawingPadding()
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Spacer(Modifier.weight(1f))
        // 런처 아이콘과 같은 앱 마크로 시작해 설치한 앱과 같은 앱임을 알린다
        LinkMark(56.dp)
        Spacer(Modifier.height(4.dp))
        Text("CANDiY LiNK 계정", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
        Text("내 기록과 코칭을\n하나의 계정으로.", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text("코칭 웹과 같은 계정으로 로그인하세요.\n처음이면 로그인 뒤 기본 정보만 받아요.", color = Hint)
        Spacer(Modifier.weight(1f))
        if (message.isNotEmpty()) Text(message, color = Error, fontSize = 14.sp)
        Button(
            onClick = { onLogin(SocialType.KAKAO) },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth().height(52.dp),
            colors = ButtonDefaults.buttonColors(containerColor = KakaoYellow, contentColor = Color.Black),
        ) { Text("카카오 로그인", fontWeight = FontWeight.SemiBold) }
        OutlinedButton(
            onClick = { onLogin(SocialType.GOOGLE) },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth().height(52.dp),
        ) { Text("Google로 계속하기", fontWeight = FontWeight.SemiBold) }
        Text(
            if (busy) busyText else "가입과 로그인을 한 번에 진행해요.",
            color = Hint, fontSize = 13.sp, modifier = Modifier.align(Alignment.CenterHorizontally),
        )
    }
}

/** 와이어프레임 A-01-2. 소셜 로그인 뒤 신규 회원만. 다섯 칸 모두 필수(명세 3.4) */
@Composable
fun SignupScreen(
    social: SocialType,
    busy: Boolean,
    errors: Map<String, String>,
    message: String,
    onSubmit: (ProfileForm) -> Unit,
    onCancel: () -> Unit,
) {
    // 뒤로 가기는 앱을 닫지 않고 로그인 화면으로 돌아간다(가입 중이던 자격은 버린다)
    BackHandler(enabled = !busy, onBack = onCancel)
    var form by remember { mutableStateOf(ProfileForm()) }
    // 오류를 받은 뒤 고친 칸. 고친 칸의 지난 오류는 숨기고, 다시 제출할 때 새로 검사한다
    var edited by remember(errors) { mutableStateOf(emptySet<String>()) }
    val shown = errors.filterKeys { it !in edited }
    fun edit(field: String, raw: String) {
        edited = edited + field
        val value = ProfileValidation.limitInput(field, raw)
        form = when (field) {
            "displayName" -> form.copy(displayName = value)
            "birthYear" -> form.copy(birthYear = value)
            "heightCm" -> form.copy(heightCm = value)
            else -> form.copy(weightKg = value)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("${LoginFlow.label(social)} 계정 확인 완료 · 신규 회원", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
        Text("기본 정보를\n알려 주세요.", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text("코칭에 쓰여요. 코칭 웹에서 바꿀 수 있어요.", color = Hint)

        Field("닉네임 (1~12자)", form.displayName, shown["displayName"]) { edit("displayName", it) }
        Field("태어난 연도", form.birthYear, shown["birthYear"], KeyboardType.Number) { edit("birthYear", it) }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            for ((code, label) in listOf("MALE" to "남성", "FEMALE" to "여성")) {
                val selected = form.gender == code
                if (selected) Button(onClick = {}, modifier = Modifier.weight(1f)) { Text(label) }
                else OutlinedButton(
                    onClick = { edited = edited + "gender"; form = form.copy(gender = code) },
                    modifier = Modifier.weight(1f),
                ) { Text(label) }
            }
        }
        shown["gender"]?.let { Text(it, color = Error, fontSize = 12.sp) }

        Field("키 (cm)", form.heightCm, shown["heightCm"], KeyboardType.Decimal) { edit("heightCm", it) }
        Field("몸무게 (kg)", form.weightKg, shown["weightKg"], KeyboardType.Decimal) { edit("weightKg", it) }

        Text("10분 안에 입력하지 않으면 로그인부터 다시 해요.", color = Hint, fontSize = 13.sp)
        if (message.isNotEmpty()) Text(message, color = Error, fontSize = 14.sp)
        Button(onClick = { edited = emptySet(); onSubmit(form) }, enabled = !busy, modifier = Modifier.fillMaxWidth().height(52.dp)) {
            Text(if (busy) "가입하는 중…" else "가입하고 시작하기", fontWeight = FontWeight.SemiBold)
        }
        OutlinedButton(onClick = onCancel, enabled = !busy, modifier = Modifier.fillMaxWidth()) { Text("다른 계정으로 로그인") }
    }
}

@Composable
private fun Field(
    label: String,
    value: String,
    error: String?,
    keyboard: KeyboardType = KeyboardType.Text,
    onChange: (String) -> Unit,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        singleLine = true,
        isError = error != null,
        supportingText = error?.let { { Text(it) } },
        keyboardOptions = KeyboardOptions(keyboardType = keyboard),
        colors = linkFieldColors(),
        modifier = Modifier.fillMaxWidth(),
    )
}
