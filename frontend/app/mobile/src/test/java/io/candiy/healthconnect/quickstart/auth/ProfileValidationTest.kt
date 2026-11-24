package io.candiy.healthconnect.quickstart.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** 웹 validation.ts 와 같은 규칙인지 본다. 값은 모두 지어낸 것이다 */
class ProfileValidationTest {
    private val year = 2026
    private val valid = ProfileForm("핏디", "2000", "FEMALE", "165.25", "55")

    private fun errors(form: ProfileForm) = (ProfileValidation.check(form, year) as ProfileCheck.Invalid).errors

    @Test
    fun `올바른 값은 정리해서 돌려준다`() {
        val ok = ProfileValidation.check(valid.copy(displayName = "  핏디  ", heightCm = "165.250"), year) as ProfileCheck.Ok
        assertEquals(SignupProfile("핏디", 2000, "FEMALE", 165.25, 55.0), ok.profile)
    }

    @Test
    fun `빈 칸은 칸마다 문구를 준다`() {
        assertEquals(ProfileValidation.FIELDS.toSet(), errors(ProfileForm()).keys)
        assertEquals("성별을 선택해 주세요.", errors(valid.copy(gender = ""))["gender"])
    }

    @Test
    fun `닉네임은 코드 포인트 12자까지, 제어문자는 trim 전에 거절한다`() {
        assertTrue(ProfileValidation.check(valid.copy(displayName = "가".repeat(12)), year) is ProfileCheck.Ok)
        // 이모지는 UTF-16 으로 2칸이지만 1자다
        assertTrue(ProfileValidation.check(valid.copy(displayName = "😀".repeat(12)), year) is ProfileCheck.Ok)
        assertEquals("닉네임은 12자까지 쓸 수 있어요.", errors(valid.copy(displayName = "가".repeat(13)))["displayName"])
        assertEquals("줄바꿈이나 제어 문자는 쓸 수 없어요.", errors(valid.copy(displayName = "핏디\n"))["displayName"])
        assertEquals("닉네임을 입력해 주세요.", errors(valid.copy(displayName = " ​ "))["displayName"])
    }

    @Test
    fun `태어난 연도는 1900년부터 KST 올해까지`() {
        assertEquals("1900년부터 2026년 사이로 입력해 주세요.", errors(valid.copy(birthYear = "2027"))["birthYear"])
        assertEquals("1900년부터 2026년 사이로 입력해 주세요.", errors(valid.copy(birthYear = "1899"))["birthYear"])
        assertEquals("태어난 연도를 숫자 4자리로 입력해 주세요.", errors(valid.copy(birthYear = "20.5"))["birthYear"])
    }

    @Test
    fun `키·몸무게는 범위와 소수 둘째 자리를 본다`() {
        assertEquals("키는 0보다 크고 300 이하로 입력해 주세요.", errors(valid.copy(heightCm = "0"))["heightCm"])
        assertEquals("몸무게는 0보다 크고 500 이하로 입력해 주세요.", errors(valid.copy(weightKg = "500.01"))["weightKg"])
        assertEquals("키는 소수 둘째 자리까지 입력할 수 있어요.", errors(valid.copy(heightCm = "165.251"))["heightCm"])
        assertEquals("몸무게를 숫자로 입력해 주세요.", errors(valid.copy(weightKg = "-5"))["weightKg"])
    }

    @Test
    fun `입력 중에는 들어갈 수 있는 글자만 남긴다`() {
        assertEquals("2000", ProfileValidation.limitInput("birthYear", "2a0001"))
        assertEquals("165.25", ProfileValidation.limitInput("heightCm", "1655.259"))
        assertEquals("12.34", ProfileValidation.limitInput("weightKg", "12.3.4"))
        assertEquals(12, ProfileValidation.limitInput("displayName", "😀".repeat(15)).codePointCount(0, 24))
    }
}
