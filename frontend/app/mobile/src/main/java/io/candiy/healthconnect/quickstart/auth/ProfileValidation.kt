package io.candiy.healthconnect.quickstart.auth

import java.text.Normalizer
import java.time.Year
import java.time.ZoneId

/** 가입 입력칸 값. 숫자 칸도 입력한 글자 그대로 둔다. gender 는 MALE·FEMALE, 고르지 않았으면 빈 문자열 */
data class ProfileForm(
    val displayName: String = "",
    val birthYear: String = "",
    val gender: String = "",
    val heightCm: String = "",
    val weightKg: String = "",
)

sealed interface ProfileCheck {
    data class Ok(val profile: SignupProfile) : ProfileCheck
    /** 칸 이름(명세 필드명) → 안내 문구 */
    data class Invalid(val errors: Map<String, String>) : ProfileCheck
}

/**
 * 가입 프로필 검사(명세 4.3). 웹(frontend/web/src/lib/features/profile/validation.ts)과 같은 규칙·문구를 쓴다.
 * 서버가 최종 판정하고, 여기서는 보내기 전에 미리 알려 준다.
 */
object ProfileValidation {
    val FIELDS = listOf("displayName", "birthYear", "gender", "heightCm", "weightKg")

    private const val DISPLAY_NAME_MAX = 12
    private const val MIN_BIRTH_YEAR = 1900
    private val MAX_VALUE = mapOf("heightCm" to 300.0, "weightKg" to 500.0)
    private val UNIT_LABEL = mapOf("heightCm" to "키", "weightKg" to "몸무게")
    private val CONTROL = Regex("""\p{Cc}""")
    private val MEASURE = Regex("""^\d+(\.\d+)?$""")
    private val DIGITS = Regex("""^\d+$""")

    /** 태어난 연도 상한. 요청 시점의 KST 올해 */
    fun kstYear(): Int = Year.now(ZoneId.of("Asia/Seoul")).value

    /** 명세 1.2 details.reason 을 입력칸 문구로. 서버 422 와 화면 검사가 같은 문구를 쓴다 */
    fun reasonMessage(field: String, reason: String, kstYear: Int = kstYear()): String = when (field) {
        "displayName" -> when (reason) {
            "too_long" -> "닉네임은 ${DISPLAY_NAME_MAX}자까지 쓸 수 있어요."
            "invalid_character" -> "줄바꿈이나 제어 문자는 쓸 수 없어요."
            else -> "닉네임을 입력해 주세요."
        }
        "birthYear" ->
            if (reason == "out_of_range") "${MIN_BIRTH_YEAR}년부터 ${kstYear}년 사이로 입력해 주세요."
            else "태어난 연도를 숫자 4자리로 입력해 주세요."
        "gender" -> "성별을 선택해 주세요."
        else -> {
            val label = UNIT_LABEL[field] ?: field
            when (reason) {
                "out_of_range" -> "${label}는 0보다 크고 ${MAX_VALUE[field]?.toInt()} 이하로 입력해 주세요."
                "decimal_places" -> "${label}는 소수 둘째 자리까지 입력할 수 있어요."
                else -> "${label}를 숫자로 입력해 주세요."
            }
        }
    }

    /**
     * 입력하는 동안 칸마다 들어갈 수 있는 글자만 남긴다. 범위·형식의 최종 검사는 [check] 가 한다.
     * 닉네임은 코드 포인트 12자, 연도는 숫자 4자리, 키·몸무게는 정수 3자리·소수 둘째 자리까지.
     */
    fun limitInput(field: String, raw: String): String = when (field) {
        "displayName" -> raw.codePoints().limit(DISPLAY_NAME_MAX.toLong())
            .collect(::StringBuilder, StringBuilder::appendCodePoint, StringBuilder::append).toString()
        "birthYear" -> raw.filter(Char::isDigit).take(4)
        else -> {
            val parts = raw.filter { it.isDigit() || it == '.' }.split('.')
            val head = parts[0].take(3)
            if (parts.size > 1) "$head.${parts.drop(1).joinToString("").take(2)}" else head
        }
    }

    fun check(form: ProfileForm, kstYear: Int = kstYear()): ProfileCheck {
        val errors = linkedMapOf<String, String>()

        val name = checkDisplayName(form.displayName)
        name.reason?.let { errors["displayName"] = reasonMessage("displayName", it, kstYear) }

        val yearText = form.birthYear.trim()
        val year = yearText.toIntOrNull()
        when {
            !DIGITS.matches(yearText) || year == null -> errors["birthYear"] = reasonMessage("birthYear", "invalid_type", kstYear)
            year < MIN_BIRTH_YEAR || year > kstYear -> errors["birthYear"] = reasonMessage("birthYear", "out_of_range", kstYear)
        }

        if (form.gender != "MALE" && form.gender != "FEMALE") errors["gender"] = reasonMessage("gender", "missing", kstYear)

        val height = checkMeasure(form.heightCm, MAX_VALUE.getValue("heightCm"))
        height.reason?.let { errors["heightCm"] = reasonMessage("heightCm", it, kstYear) }
        val weight = checkMeasure(form.weightKg, MAX_VALUE.getValue("weightKg"))
        weight.reason?.let { errors["weightKg"] = reasonMessage("weightKg", it, kstYear) }

        if (errors.isNotEmpty()) return ProfileCheck.Invalid(errors)
        return ProfileCheck.Ok(
            SignupProfile(name.value!!, year!!, form.gender, height.value!!, weight.value!!)
        )
    }

    private class Checked<T>(val value: T? = null, val reason: String? = null)

    /** 줄바꿈·제어문자는 trim 전에 보고, 길이·공백은 NFC 정규화와 앞뒤 공백 제거 뒤 코드 포인트로 센다 */
    private fun checkDisplayName(raw: String): Checked<String> {
        if (CONTROL.containsMatchIn(raw)) return Checked(reason = "invalid_character")
        val value = Normalizer.normalize(raw, Normalizer.Form.NFC).trim { Character.isWhitespace(it) || Character.isSpaceChar(it) }
        // 공백·서식 문자(보이지 않는 문자)만 있는 이름은 비어 있는 것으로 본다
        val invisible = value.codePoints().allMatch {
            Character.isWhitespace(it) || Character.isSpaceChar(it) || Character.getType(it) == Character.FORMAT.toInt()
        }
        if (value.isEmpty() || invisible) return Checked(reason = "blank")
        if (value.codePointCount(0, value.length) > DISPLAY_NAME_MAX) return Checked(reason = "too_long")
        return Checked(value)
    }

    /** 165.250 은 허용, 165.251 은 거절한다. 반올림하지 않는다 */
    private fun checkMeasure(raw: String, max: Double): Checked<Double> {
        val text = raw.trim()
        if (!MEASURE.matches(text)) return Checked(reason = "invalid_type")
        val value = text.toDouble()
        if (!(value > 0 && value <= max)) return Checked(reason = "out_of_range")
        val decimals = text.substringAfter('.', "").trimEnd('0')
        if (decimals.length > 2) return Checked(reason = "decimal_places")
        return Checked(value)
    }
}
