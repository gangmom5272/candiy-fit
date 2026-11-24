package io.candiy.healthconnect.quickstart

import android.animation.ValueAnimator
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/**
 * 불러오는 동안 자리만 잡는 회색 상자. 웹 스켈레톤(frontend/web Skeleton.svelte · .health-skeleton)과 같은 규칙이다:
 * 120ms 뒤 서서히 나타나 아주 짧은 로딩에는 깜빡이지 않고, 기기에서 애니메이션을 끄면 바로 보인다.
 * 흰 바탕 위에서는 Surface, 회색 카드(Surface) 안에서는 Border 색을 쓴다(onCard).
 * 읽어 줄 안내는 쓰는 쪽이 감싼 상자의 contentDescription 으로 한 번만 둔다. 라이브러리 없이 직접 그린다.
 */
@Composable
fun LinkSkeleton(modifier: Modifier, shape: Shape = RoundedCornerShape(8.dp), onCard: Boolean = false) {
    val animate = remember { ValueAnimator.areAnimatorsEnabled() }
    var shown by remember { mutableStateOf(!animate) }
    LaunchedEffect(Unit) {
        if (!shown) {
            delay(120)
            shown = true
        }
    }
    val alpha by animateFloatAsState(if (shown) 1f else 0f, tween(if (animate) 120 else 0), label = "skeleton")
    Box(modifier.alpha(alpha).background(if (onCard) Link.Border else Link.Surface, shape))
}

/** 회색 카드 안에서 쓰는 글줄 상자 */
@Composable
fun CardSkeleton(modifier: Modifier, shape: Shape = RoundedCornerShape(8.dp)) =
    LinkSkeleton(modifier, shape, onCard = true)
