package io.candiy.healthconnect.quickstart.auth

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** 로그인 상태 보관. 단위 테스트에서는 메모리 구현으로 바꿔 끼운다 */
interface SessionStorage {
    fun load(): Session?
    fun save(session: Session)
    fun clear()
}

/**
 * 갱신 토큰은 30일까지 쓰는 자격이라 평문으로 두지 않는다.
 * Android Keystore 의 AES 키(기기 밖으로 못 꺼냄)로 암호화해 SharedPreferences 에 둔다.
 * 백업 복원·키 삭제로 풀 수 없게 되면 지우고 null — 다시 로그인하면 된다.
 */
class KeystoreSessionStorage(context: Context) : SessionStorage {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    override fun load(): Session? {
        val stored = prefs.getString(KEY_SESSION, null) ?: return null
        return try {
            AuthJson.decodeSession(decrypt(stored))
        } catch (_: Exception) {
            clear()
            null
        }
    }

    /** 갱신으로 바뀐 토큰을 잃으면 그 세션은 다시 못 쓰므로 commit 으로 바로 쓴다 */
    override fun save(session: Session) {
        check(prefs.edit().putString(KEY_SESSION, encrypt(AuthJson.encodeSession(session))).commit()) {
            "로그인 상태 저장에 실패했습니다."
        }
    }

    override fun clear() {
        prefs.edit().remove(KEY_SESSION).commit()
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE).run {
            init(
                KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build()
            )
            generateKey()
        }
    }

    /** 결과: base64(IV 12바이트 + 암호문) */
    private fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, key()) }
        return Base64.encodeToString(cipher.iv + cipher.doFinal(plain.toByteArray()), Base64.NO_WRAP)
    }

    private fun decrypt(stored: String): String {
        val bytes = Base64.decode(stored, Base64.NO_WRAP)
        val cipher = Cipher.getInstance(TRANSFORMATION).apply {
            init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes, 0, IV_BYTES))
        }
        return String(cipher.doFinal(bytes, IV_BYTES, bytes.size - IV_BYTES))
    }

    private companion object {
        const val PREFS = "candiy_auth"
        const val KEY_SESSION = "session"
        const val KEYSTORE = "AndroidKeyStore"
        const val KEY_ALIAS = "candiy_auth_session"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
    }
}
