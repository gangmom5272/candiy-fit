package io.candiy.healthconnect.quickstart

import android.content.Context

/**
 * 이 폰의 사용자 ID(CANDiY end_user_id). 로그인 응답의 user.endUserId(= 백엔드 users.id)를 저장해 SDK 에 넘긴다.
 * 웹과 같은 회원 ID 라 코칭 웹이 이 폰이 보낸 기록을 본다. 손으로 입력받지 않는다.
 *
 * 로그인 상태(auth.KeystoreSessionStorage)와 따로 두는 이유: 워치 기록을 받아 화면 없이 깨어난 경우(CandiySync)에도
 * 키 복호화 없이 ID 만 읽으면 되기 때문이다. ID 자체는 비밀값이 아니다.
 */
object UserIdStore {
    private const val PREFS = "candiy_user"
    private const val KEY = "end_user_id"

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun get(context: Context): String? = prefs(context).getString(KEY, null)

    fun set(context: Context, id: String) {
        prefs(context).edit().putString(KEY, id).apply()
    }

    /** 로그아웃 때. 지우면 워치 기록 수신도 전송을 건너뛴다 */
    fun clear(context: Context) {
        prefs(context).edit().remove(KEY).apply()
    }
}
