package io.candiy.healthconnect.quickstart

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 인터넷 연결 판단. 와이파이에 붙어 있어도 실제 접속이 확인되지 않으면 끊긴 것으로 본다(11-12 실기기 상황) */
class ConnectivityTest {
    @Test fun `네트워크가 있고 인터넷용이며 접속이 확인돼야 연결로 본다`() {
        assertTrue(Connectivity.isOnline(hasNetwork = true, internet = true, validated = true))
        // 와이파이에는 붙었지만 인증이 풀렸거나 막힌 망
        assertFalse(Connectivity.isOnline(hasNetwork = true, internet = true, validated = false))
        assertFalse(Connectivity.isOnline(hasNetwork = true, internet = false, validated = false))
        assertFalse(Connectivity.isOnline(hasNetwork = false, internet = false, validated = false))
    }
}
