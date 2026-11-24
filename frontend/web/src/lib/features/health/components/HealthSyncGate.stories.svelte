<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import HealthSyncGateExample from '#lib/testing/HealthSyncGateExample.svelte';
	const { Story } = defineMeta({
		title: 'Features/health/HealthSyncGate',
		component: HealthSyncGateExample
	});
</script>

<Story name="갱신 요청 대기" args={{ outcome: 'LOADING' }} />

<Story name="갱신 완료 후 조회" args={{ outcome: 'SYNCED' }} />
<Story name="최근 시도로 생략" args={{ outcome: 'SKIPPED_RECENT' }} />
<Story name="다른 작업 진행 중" args={{ outcome: 'BUSY' }} />
<Story name="응답 대기 종료" args={{ outcome: 'TIMEOUT' }} />
<Story name="합성 회원" args={{ outcome: 'SYNTHETIC' }} />
<Story name="수집 비활성화" args={{ outcome: 'DISABLED' }} />
<Story name="수집 비활성화 제목 행" args={{ outcome: 'DISABLED', showHeader: true }} />
<Story name="앱 연동 확인" args={{ outcome: 'NOT_LINKED' }} />
<Story name="동기화 실패" args={{ outcome: 'UNAVAILABLE' }} />
<Story name="연결 실패" args={{ outcome: 'NETWORK' }} />
<Story name="응답 형식 오류" args={{ outcome: 'INVALID' }} />
