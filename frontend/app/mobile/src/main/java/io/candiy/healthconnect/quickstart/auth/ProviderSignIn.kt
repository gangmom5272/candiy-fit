package io.candiy.healthconnect.quickstart.auth

import android.app.Activity
import androidx.credentials.CredentialManager
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import androidx.credentials.exceptions.GetCredentialCancellationException
import androidx.credentials.exceptions.GetCredentialException
import com.google.android.libraries.identity.googleid.GetSignInWithGoogleOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import com.google.android.libraries.identity.googleid.GoogleIdTokenParsingException
import com.kakao.sdk.auth.model.OAuthToken
import com.kakao.sdk.common.KakaoSdk
import com.kakao.sdk.common.model.ClientError
import com.kakao.sdk.common.model.ClientErrorCause
import com.kakao.sdk.user.UserApiClient
import io.candiy.healthconnect.quickstart.BuildConfig
import io.candiy.healthconnect.quickstart.FileLog
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * Google·카카오에서 ID 토큰을 받는 한 단계. 로그인 기능 중 외부 라이브러리를 쓰는 곳은 이 파일뿐이다.
 * 서버 호출·토큰 저장·갱신·화면은 직접 만들었다(AuthApi·SessionStorage·AuthSession·LoginFlow).
 *
 * 외부 라이브러리를 쓸 수밖에 없는 이유 (직접 구현을 먼저 검토했다)
 *
 *  - ID 토큰은 제공자 서버가 서명해 주는 신분증이라 앱이 만들 수 없다. 받는 통로가 필요하다.
 *  - 서버 계약(backend/docs/api-spec.md 3.1, S15P31A403-76)이 "Google Credential Manager·Kakao Android SDK 의
 *    OIDC ID 토큰"으로 정해져 있다. 서버는 토큰의 대상(aud)이 Google 웹 클라이언트 ID·카카오 네이티브 앱 키인지 본다.
 *  - Google: 브라우저로 로그인 화면을 띄우고 앱으로 돌아오는 방식(직접 구현 가능한 방식)은 Android 용 OAuth
 *    클라이언트에서 권장되지 않고, 앱 서명(SHA-1)으로 앱을 확인하는 일은 Google Play 서비스만 할 수 있다.
 *    Credential Manager 는 Android 공식 라이브러리(AndroidX)이고, googleid 는 그 결과를 읽는 부분이다.
 *  - 카카오: SDK 없이 하려면 REST API 키로 인가 코드를 토큰으로 바꿔야 하는데, 이 앱은 웹과 같은 카카오 앱을 쓰고
 *    웹은 클라이언트 시크릿을 쓴다. 시크릿을 앱에 넣으면 누구나 꺼낼 수 있어 안 된다. 네이티브 앱 키로 받는 방법은
 *    공개 문서가 SDK 사용으로만 안내하고, 카카오톡 앱으로 로그인하는 연결 방식도 공개되어 있지 않다.
 *
 * 그래서 라이브러리에는 "nonce 를 넣은 로그인 창 띄우기 → ID 토큰 받기"만 맡기고, 결과는 [SocialSignInResult] 로
 * 바꿔 넘긴다. 취소·실패 판단과 이후 순서는 [LoginFlow] 가 한다.
 */
class ProviderSignIn(private val activity: Activity) : SocialSignIn {

    override fun isConfigured(social: SocialType) = when (social) {
        SocialType.GOOGLE -> BuildConfig.GOOGLE_SERVER_CLIENT_ID.isNotBlank()
        SocialType.KAKAO -> BuildConfig.KAKAO_NATIVE_APP_KEY.isNotBlank()
    }

    override suspend fun signIn(social: SocialType, nonce: String): SocialSignInResult {
        if (!isConfigured(social)) return SocialSignInResult.NotConfigured
        return when (social) {
            SocialType.GOOGLE -> google(nonce)
            SocialType.KAKAO -> kakao(nonce)
        }.also {
            if (it is SocialSignInResult.Failed) FileLog.i("candiy", "social_signin_failed social=$social kind=${it.kind}")
        }
    }

    /**
     * 서버 GOOGLE_CLIENT_ID(웹 클라이언트)를 serverClientId 로 넣어야 토큰 aud 가 서버 기대값과 같다.
     * 이 앱의 Android 클라이언트(패키지명+SHA-1)는 Google 쪽에서 앱을 확인하는 데 쓰이고 azp 로 들어간다.
     */
    private suspend fun google(nonce: String): SocialSignInResult {
        val option = GetSignInWithGoogleOption.Builder(BuildConfig.GOOGLE_SERVER_CLIENT_ID)
            .setNonce(nonce)
            .build()
        val request = GetCredentialRequest.Builder().addCredentialOption(option).build()
        return try {
            val credential = CredentialManager.create(activity).getCredential(activity, request).credential
            if (credential is CustomCredential && credential.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL) {
                val idToken = GoogleIdTokenCredential.createFrom(credential.data).idToken
                if (BuildConfig.DEBUG) logClientIds(idToken)
                SocialSignInResult.Success(idToken)
            } else {
                SocialSignInResult.Failed("unexpected_credential")
            }
        } catch (_: GetCredentialCancellationException) {
            SocialSignInResult.Cancelled
        } catch (e: GetCredentialException) {
            // 구글은 "[28444] Developer console is not set up correctly" 처럼 오류 번호를 문구 앞에 담는다.
            // 설정 문제를 찾는 데는 번호면 충분해 번호만 남긴다. 문구는 형식을 몰라 남기지 않는다(로그 규칙: 종류·코드만)
            val code = CODE.find(e.errorMessage?.toString().orEmpty())?.groupValues?.get(1) ?: "-"
            SocialSignInResult.Failed("${e.javaClass.simpleName} type=${e.type} code=$code")
        } catch (_: GoogleIdTokenParsingException) {
            SocialSignInResult.Failed("id_token_parse")
        }
    }

    /**
     * 카카오톡이 있으면 카카오톡으로, 없거나 카카오톡 로그인이 안 되면 카카오 계정(브라우저)으로 로그인한다.
     * 카카오톡에서 사용자가 취소했으면 계정 로그인으로 넘어가지 않는다. ID 토큰은 카카오 앱에서 OpenID Connect 를
     * 켜야 나온다 — 꺼져 있으면 토큰은 오는데 idToken 이 비어 있다.
     */
    private suspend fun kakao(nonce: String): SocialSignInResult {
        KakaoSdk.init(activity.applicationContext, BuildConfig.KAKAO_NATIVE_APP_KEY)
        val client = UserApiClient.instance
        if (client.isKakaoTalkLoginAvailable(activity)) {
            val talk = kakaoCall { callback -> client.loginWithKakaoTalk(activity, nonce = nonce, callback = callback) }
            if (talk !is SocialSignInResult.Failed) return talk
        }
        return kakaoCall { callback -> client.loginWithKakaoAccount(activity, nonce = nonce, callback = callback) }
    }

    /**
     * 디버그 빌드 전용: 서버가 400 으로 거절할 때 원인을 가르기 위해 토큰의 aud(받을 대상)·azp(요청한 앱)만 남긴다.
     * 서버는 aud 를 GOOGLE_CLIENT_ID, azp 를 GOOGLE_ANDROID_CLIENT_IDS 와 비교한다. 둘 다 공개용 클라이언트 ID 이고,
     * 이메일·이름 같은 다른 값과 토큰 원문은 남기지 않는다
     */
    private fun logClientIds(idToken: String) {
        val claims = runCatching {
            org.json.JSONObject(String(android.util.Base64.decode(idToken.split('.')[1], android.util.Base64.URL_SAFE)))
        }.getOrNull() ?: return
        FileLog.i("candiy", "google_token aud=${claims.optString("aud")} azp=${claims.optString("azp")}")
    }

    private companion object {
        /** 구글 오류 문구 앞의 [숫자] */
        val CODE = Regex("""^\s*\[(\d+)]""")
    }

    /** 카카오 SDK 의 콜백을 한 번만 받아 결과로 바꾼다 */
    private suspend fun kakaoCall(start: ((OAuthToken?, Throwable?) -> Unit) -> Unit): SocialSignInResult =
        suspendCancellableCoroutine { continuation ->
            start { token, error ->
                if (!continuation.isActive) return@start
                val result = when {
                    error is ClientError && error.reason == ClientErrorCause.Cancelled -> SocialSignInResult.Cancelled
                    error is ClientError -> SocialSignInResult.Failed("kakao_${error.reason.name}")
                    error != null -> SocialSignInResult.Failed(error.javaClass.simpleName)
                    token?.idToken.isNullOrBlank() -> SocialSignInResult.Failed("kakao_no_id_token")
                    else -> SocialSignInResult.Success(token!!.idToken!!)
                }
                continuation.resume(result)
            }
        }
}
