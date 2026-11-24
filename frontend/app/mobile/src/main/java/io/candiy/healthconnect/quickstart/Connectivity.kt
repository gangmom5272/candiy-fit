package io.candiy.healthconnect.quickstart

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/*
 * 인터넷 연결 안내. 연결이 끊기면 대화 목록·기록이 사라진 것처럼 보여(11-12 실기기) 화면 위에 이유를 알린다.
 * 웹의 OfflineBanner 와 같은 문구를 쓴다. 기기의 기본 네트워크 상태만 읽고(ACCESS_NETWORK_STATE) 서버에 따로 묻지 않는다.
 */
object Connectivity {
    /**
     * 인터넷을 쓸 수 있는 상태인지. 네트워크가 있고, 인터넷용이며, 기기가 실제 접속을 확인(VALIDATED)한 경우다.
     * 와이파이에 붙어 있어도 인증이 풀렸거나 막힌 망이면 확인이 되지 않아 끊긴 것으로 본다
     */
    fun isOnline(hasNetwork: Boolean, internet: Boolean, validated: Boolean): Boolean = hasNetwork && internet && validated

    private fun online(caps: NetworkCapabilities?): Boolean = isOnline(
        hasNetwork = caps != null,
        internet = caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true,
        validated = caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED) == true,
    )

    /** 지금 상태를 한 번 읽는다. 읽지 못하면 끊겼다고 단정하지 않는다(안내를 잘못 띄우지 않게) */
    fun current(context: Context): Boolean = runCatching {
        val manager = context.getSystemService(ConnectivityManager::class.java)
        online(manager.getNetworkCapabilities(manager.activeNetwork))
    }.getOrDefault(true)

    /** 기본 네트워크가 바뀔 때마다 알린다. 돌려주는 함수를 부르면 그만 듣는다 */
    fun observe(context: Context, onChange: (Boolean) -> Unit): () -> Unit {
        val manager = context.getSystemService(ConnectivityManager::class.java)
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) = onChange(online(caps))
            override fun onLost(network: Network) = onChange(false)
        }
        return runCatching {
            manager.registerDefaultNetworkCallback(callback)
            val stop: () -> Unit = { runCatching { manager.unregisterNetworkCallback(callback) } }
            stop
        }.getOrDefault({})
    }
}

/** 인터넷 연결 여부. 화면이 보이는 동안만 듣는다 */
@Composable
fun rememberOnline(): State<Boolean> {
    val context = LocalContext.current.applicationContext
    val online = remember { mutableStateOf(Connectivity.current(context)) }
    DisposableEffect(context) {
        val stop = Connectivity.observe(context) { online.value = it }
        onDispose(stop)
    }
    return online
}

/** 연결이 끊겼을 때 화면 위에 두는 한 줄 안내. 보조기기에도 바뀐 때 읽어 준다 */
@Composable
fun OfflineBanner() {
    Column(
        Modifier.fillMaxWidth().background(Link.AmberBg).padding(horizontal = 18.dp, vertical = 9.dp)
            .semantics { liveRegion = LiveRegionMode.Polite },
    ) {
        Text("인터넷 연결이 끊겼어요", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Text)
        Text("네트워크 연결을 확인해 주세요. 연결되면 다시 불러와요.", fontSize = 12.sp, color = Link.Sub)
    }
}
