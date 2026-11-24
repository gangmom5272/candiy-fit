import { describe, expect, it } from 'vitest';
import {
	readHealthReferenceDate,
	readHealthReturn,
	readMetricReturn,
	readSleepReturn,
	shouldHandleHealthLink,
	type HealthReturnContext
} from './health-navigation';

const today = '2026-11-12';
const context: HealthReturnContext = {
	referenceDate: '2026-11-09',
	section: 'steps',
	date: '2026-11-03',
	scrollY: 420.5,
	chartScrollLeft: { steps: 0, exercises: 12.5, sleeps: 80, heart_rates: 100 }
};

function read(overrides: Record<string, unknown> = {}) {
	return readHealthReturn({ healthReturn: { ...context, ...overrides } }, today);
}

describe('건강 지표 링크의 기준일', () => {
	it.each([today, '2026-10-25', '2025-12-31', '2024-02-29', '2000-02-29', '0001-01-01'])(
		'오늘 또는 과거의 실제 날짜 %s를 그대로 보존한다',
		(value) => {
			expect(readHealthReferenceDate(value, today)).toBe(value);
		}
	);

	it.each([
		undefined,
		null,
		'',
		' ',
		false,
		0,
		20261025,
		[],
		['2026-10-25'],
		{},
		new Date('2026-10-25T00:00:00Z'),
		'2026-1-05',
		'2026-10-5',
		'20261025',
		'2026/10/25',
		'2026-10-25T00:00:00Z',
		' 2026-10-25',
		'2026-10-25 ',
		'2026-02-30',
		'2025-02-29',
		'1900-02-29',
		'2026-00-25',
		'2026-13-01',
		'2026-10-00',
		'0000-01-01',
		'10000-01-01',
		'/health/activity?date=2026-10-25',
		'2026-10-25#exercise'
	])('부재·다른 타입·형식 오류·존재하지 않는 날짜 %j는 기준일로 사용하지 않는다', (value) => {
		expect(readHealthReferenceDate(value, today)).toBeNull();
	});

	it.each(['2026-11-13', '2027-01-01', '9999-12-31'])(
		'미래의 실제 날짜 %s를 현재 기간으로 해석하지 않는다',
		(value) => {
			expect(readHealthReferenceDate(value, today)).toBeNull();
		}
	);

	it('연말과 윤년 경계에서도 주어진 오늘을 기준으로 판단한다', () => {
		expect(readHealthReferenceDate('2026-12-31', '2027-01-01')).toBe('2026-12-31');
		expect(readHealthReferenceDate('2027-01-01', '2026-12-31')).toBeNull();
		expect(readHealthReferenceDate('2024-02-29', '2024-02-29')).toBe('2024-02-29');
		expect(readHealthReferenceDate('2024-02-29', '2024-02-28')).toBeNull();
	});

	it.each(['invalid', '2026-02-30', '0000-01-01'])(
		'판단 기준인 오늘 %s가 유효하지 않으면 날짜를 보정하지 않는다',
		(invalidToday) => {
			expect(readHealthReferenceDate('2026-10-25', invalidToday)).toBeNull();
		}
	);
});

describe('건강 메인 복귀 상태', () => {
	it('명시한 UI 필드만 복사하고 원본이나 결과의 변경을 서로 공유하지 않는다', () => {
		const stored = {
			...context,
			chartScrollLeft: { ...context.chartScrollLeft },
			data: { synthetic: true },
			userId: 'synthetic-user',
			token: 'synthetic-token',
			url: 'https://example.invalid/'
		};
		const result = readHealthReturn({ healthReturn: stored, unrelated: true }, today);
		expect(result).toEqual(context);
		expect(result).not.toBe(stored);
		expect(result?.chartScrollLeft).not.toBe(stored.chartScrollLeft);
		if (!result) throw new Error('유효한 합성 UI 상태가 필요합니다.');
		stored.chartScrollLeft.steps = 50;
		expect(result.chartScrollLeft.steps).toBe(0);
		result.chartScrollLeft.exercises = 70;
		expect(stored.chartScrollLeft.exercises).toBe(12.5);
		expect(stored.data).toEqual({ synthetic: true });
	});

	it.each(['steps', 'exercises', 'sleeps', 'heart_rates'] as const)(
		'%s 섹션과 0 스크롤·빈 차트 위치를 보존한다',
		(section) => {
			expect(read({ section, scrollY: 0, chartScrollLeft: {} })).toEqual({
				...context,
				section,
				scrollY: 0,
				chartScrollLeft: {}
			});
		}
	);

	it.each([
		null,
		undefined,
		false,
		0,
		'state',
		[],
		new Date(),
		{},
		{ healthReturn: null },
		{ healthReturn: [] }
	])('복귀 정보가 없는 값이나 잘못된 컨테이너 %j는 거부한다', (state) => {
		expect(readHealthReturn(state, today)).toBeNull();
	});

	it.each(['referenceDate', 'section', 'date', 'scrollY', 'chartScrollLeft'] as const)(
		'필수 UI 필드 %s가 빠지면 거부한다',
		(key) => {
			const partial: Record<string, unknown> = { ...context };
			delete partial[key];
			expect(readHealthReturn({ healthReturn: partial }, today)).toBeNull();
		}
	);

	it.each([
		'2026-2-03',
		'2026-02-30',
		'2025-02-29',
		'0000-01-01',
		'10000-01-01',
		'2026-11-13',
		'',
		20261109,
		null,
		'https://example.invalid/',
		'2026-11-09?token=synthetic'
	])('잘못되거나 미래인 기준일 %j는 거부한다', (referenceDate) => {
		expect(read({ referenceDate })).toBeNull();
	});

	it.each(['2026-02-30', '0000-01-01', '2026-11-13', '2026-11-10', '2026-11-02', '', null])(
		'잘못되거나 출발 화면의 7일 범위를 벗어난 선택일 %j는 거부한다',
		(date) => {
			expect(read({ date })).toBeNull();
		}
	);

	it.each([
		{ referenceDate: '2026-11-12', date: '2026-11-12' },
		{ referenceDate: '2026-11-12', date: '2026-11-06' },
		{ referenceDate: '2026-01-03', date: '2025-12-28' },
		{ referenceDate: '2024-03-01', date: '2024-02-29' },
		{ referenceDate: '0001-01-01', date: '0001-01-01' }
	])('오늘·7일 경계·연도 경계·윤년·최소 연도를 검증한다: %j', (dates) => {
		expect(read(dates)).toEqual({ ...context, ...dates });
	});

	it.each(['invalid', '0000-01-01', '2026-02-30'])(
		'기준 KST 오늘 %s가 유효하지 않으면 복원하지 않는다',
		(invalidToday) => {
			expect(readHealthReturn({ healthReturn: context }, invalidToday)).toBeNull();
		}
	);

	it.each([
		'exercise',
		'sleep',
		'heart',
		'oxygen_saturations',
		'__proto__',
		'',
		'/health',
		null,
		0
	])('API 섹션이 아닌 값 %j는 거부한다', (section) => {
		expect(read({ section })).toBeNull();
	});

	it.each([-1, NaN, Infinity, -Infinity, '0', null, undefined, {}])(
		'잘못된 세로 스크롤 %j는 거부한다',
		(scrollY) => {
			expect(read({ scrollY })).toBeNull();
		}
	);

	it.each([
		null,
		[],
		0,
		'positions',
		new Date(),
		{ steps: -1 },
		{ steps: NaN },
		{ exercises: Infinity },
		{ sleeps: '0' },
		{ heart_rates: null },
		{ oxygen_saturations: 0 },
		JSON.parse('{"__proto__":0}')
	])('허용하지 않은 키·숫자·형태를 가진 차트 위치 %j는 거부한다', (chartScrollLeft) => {
		expect(read({ chartScrollLeft })).toBeNull();
	});

	it('입력 객체를 변경하지 않고 유효한 큰 위치도 임의로 반올림하거나 자르지 않는다', () => {
		const stored = Object.freeze({
			...context,
			scrollY: Number.MAX_VALUE,
			chartScrollLeft: Object.freeze({ steps: 0.25 })
		});
		expect(readHealthReturn(Object.freeze({ healthReturn: stored }), today)).toEqual(stored);
	});

	it('prototype에 들어 있는 복귀 정보나 인스턴스는 복원하지 않는다', () => {
		expect(readHealthReturn(Object.create({ healthReturn: context }), today)).toBeNull();
		expect(read({ chartScrollLeft: Object.create({ steps: 1 }) })).toBeNull();
	});

	it('속성 접근이 실패하는 비정상 state는 화면 오류로 전파하지 않는다', () => {
		const state = Object.defineProperty({}, 'healthReturn', {
			get() {
				throw new Error('synthetic invalid state');
			}
		});
		expect(readHealthReturn(state, today)).toBeNull();
	});
});

describe('하루 상세에서 직전 수면 상세로 복귀하는 상태', () => {
	it('오늘이나 7일 이전의 실제 출발 날짜와 0 스크롤을 보존한다', () => {
		for (const date of [today, '2026-10-01']) {
			expect(readSleepReturn({ sleepReturn: { date, scrollY: 0 } }, today)).toEqual({
				date,
				scrollY: 0
			});
		}
	});

	it('문맥이 없거나 객체 형태·필수 필드가 잘못되면 복원하지 않는다', () => {
		for (const state of [
			undefined,
			null,
			{},
			{ sleepReturn: null },
			{ sleepReturn: [] },
			{ sleepReturn: '2026-11-09' },
			{ sleepReturn: { date: today } },
			{ sleepReturn: { scrollY: 0 } }
		]) {
			expect(readSleepReturn(state, today)).toBeNull();
		}
	});

	it('미래 날짜·달력에 없는 날짜·0000년·URL을 출발 날짜로 해석하지 않는다', () => {
		for (const date of ['2026-11-13', '2026-02-30', '0000-01-01', '/health/sleep/2026-11-09']) {
			expect(readSleepReturn({ sleepReturn: { date, scrollY: 0 } }, today)).toBeNull();
		}
	});

	it('음수·무한대·NaN·문자 스크롤은 거부한다', () => {
		for (const scrollY of [-1, Infinity, NaN, '0']) {
			expect(readSleepReturn({ sleepReturn: { date: today, scrollY } }, today)).toBeNull();
		}
	});

	it('날짜와 소수 스크롤만 새 객체로 복사하고 추가 데이터를 보관하지 않는다', () => {
		const stored = {
			date: '2026-11-09',
			scrollY: 123.5,
			data: { synthetic: true },
			url: '/health'
		};
		const result = readSleepReturn({ sleepReturn: stored }, today);
		expect(result).toEqual({ date: '2026-11-09', scrollY: 123.5 });
		expect(result).not.toBe(stored);
		if (!result) throw new Error('유효한 합성 수면 복귀 상태가 필요합니다.');
		stored.scrollY = 900;
		expect(result.scrollY).toBe(123.5);
		result.date = '2026-11-01';
		expect(stored.date).toBe('2026-11-09');
	});

	it('건강 메인 문맥과 독립적으로 읽고 서로의 복귀 정보로 대체하지 않는다', () => {
		const sleepReturn = { date: '2026-10-01', scrollY: 25 };
		const state = { healthReturn: context, sleepReturn };
		expect(readSleepReturn(state, today)).toEqual(sleepReturn);
		expect(readHealthReturn(state, today)).toEqual(context);
		expect(readSleepReturn({ healthReturn: context }, today)).toBeNull();
		expect(readSleepReturn({ healthReturn: null, sleepReturn }, today)).toEqual(sleepReturn);
		expect(readHealthReturn({ healthReturn: context, sleepReturn: null }, today)).toEqual(context);
	});

	it('기준일이 잘못되거나 상태 속성 접근이 실패해도 오류를 전파하지 않는다', () => {
		expect(readSleepReturn({ sleepReturn: { date: today, scrollY: 0 } }, 'invalid')).toBeNull();
		const state = Object.defineProperty({}, 'sleepReturn', {
			get() {
				throw new Error('synthetic invalid state');
			}
		});
		expect(readSleepReturn(state, today)).toBeNull();
	});
});

describe('기존 활동·심박 상세 복귀 상태', () => {
	it('활동의 걸음·운동 영역과 심박의 기준일만 복사한다', () => {
		for (const metricReturn of [
			{ source: 'activity', referenceDate: '2026-10-25', exercise: false },
			{ source: 'activity', referenceDate: '2026-10-25', exercise: true },
			{ source: 'heart', referenceDate: today, exercise: false }
		]) {
			const stored = { ...metricReturn, url: 'https://example.invalid', scrollY: 99 };
			const result = readMetricReturn({ metricReturn: stored }, today);
			expect(result).toEqual(metricReturn);
			expect(result).not.toBe(stored);
		}
	});
	it('원래 지원하지 않는 출발 경로나 잘못된 기간·영역은 거부한다', () => {
		for (const metricReturn of [
			{ source: '/health/activity', referenceDate: today, exercise: false },
			{ source: 'sleep', referenceDate: today, exercise: false },
			{ source: 'activity', referenceDate: '2026-11-13', exercise: false },
			{ source: 'activity', referenceDate: '2026-02-30', exercise: false },
			{ source: 'activity', referenceDate: today },
			{ source: 'activity', referenceDate: today, exercise: 'false' },
			{ source: 'heart', referenceDate: today, exercise: true }
		])
			expect(readMetricReturn({ metricReturn }, today)).toBeNull();
	});
	it('다른 출발 화면의 정보로 대체하지 않고 비정상 상태를 안전하게 무시한다', () => {
		for (const state of [
			undefined,
			{},
			{ healthReturn: context },
			{ sleepReturn: { date: today, scrollY: 0 } },
			{ metricReturn: [] }
		])
			expect(readMetricReturn(state, today)).toBeNull();
		const state = Object.defineProperty({}, 'metricReturn', {
			get() {
				throw new Error('invalid');
			}
		});
		expect(readMetricReturn(state, today)).toBeNull();
		expect(
			readMetricReturn(
				{ metricReturn: { source: 'heart', referenceDate: today, exercise: false } },
				'invalid'
			)
		).toBeNull();
	});
});

describe('건강 상세 링크의 기본 브라우저 동작', () => {
	const plain = {
		button: 0,
		metaKey: false,
		ctrlKey: false,
		shiftKey: false,
		altKey: false,
		defaultPrevented: false
	};

	it('기본 클릭만 UI 복귀 상태와 함께 처리한다', () => {
		expect(shouldHandleHealthLink(plain)).toBe(true);
	});

	it.each(['metaKey', 'ctrlKey', 'shiftKey', 'altKey', 'defaultPrevented'] as const)(
		'%s가 있는 이벤트는 가로채지 않는다',
		(key) => {
			expect(shouldHandleHealthLink({ ...plain, [key]: true })).toBe(false);
		}
	);

	it.each([1, 2, -1, NaN])('기본 버튼이 아닌 %j 클릭은 가로채지 않는다', (button) => {
		expect(shouldHandleHealthLink({ ...plain, button })).toBe(false);
	});
});
