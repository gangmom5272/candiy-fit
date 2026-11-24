import { describe, expect, it } from 'vitest';
import {
	getKstYear,
	applyLimit,
	getProfileChanges,
	limitInput,
	toFormValues,
	validateProfile,
	type ProfileFormValues
} from './validation';

// KST로는 이미 2027년이다
const NOW = new Date('2026-12-31T15:30:00Z');

const valid: ProfileFormValues = {
	displayName: '  테스트  ',
	birthYear: '2000',
	gender: 'FEMALE',
	heightCm: '165.250',
	weightKg: '55'
};

function errorsOf(patch: Partial<ProfileFormValues>) {
	const result = validateProfile({ ...valid, ...patch }, NOW);
	return result.ok ? {} : result.errors;
}

describe('validateProfile', () => {
	it('정규화한 값을 숫자로 바꿔 돌려준다', () => {
		expect(validateProfile(valid, NOW)).toEqual({
			ok: true,
			profile: {
				displayName: '테스트',
				birthYear: 2000,
				gender: 'FEMALE',
				heightCm: 165.25,
				weightKg: 55
			}
		});
	});

	it('닉네임은 NFC·trim 뒤 코드 포인트 12자까지다', () => {
		expect(errorsOf({ displayName: '가'.repeat(12) })).toEqual({});
		expect(errorsOf({ displayName: '😀'.repeat(12) })).toEqual({});
		expect(errorsOf({ displayName: '가'.repeat(13) }).displayName).toContain('12자');
		// 조합형 한글 13 코드 포인트(ㄱ+ㅏ)는 NFC 뒤 1자다
		expect(errorsOf({ displayName: '가'.repeat(12) })).toEqual({});
	});

	it.each([
		['빈 값', '   ', '입력해'],
		['보이지 않는 문자만', '​　', '입력해'],
		['줄바꿈', '테\n스트', '줄바꿈'],
		['앞뒤 제어문자', '\t테스트', '줄바꿈']
	])('닉네임 %s은 거절한다', (_, displayName, message) => {
		expect(errorsOf({ displayName }).displayName).toContain(message);
	});

	it('태어난 연도는 1900~KST 올해 정수다', () => {
		expect(getKstYear(NOW)).toBe(2027);
		expect(errorsOf({ birthYear: '2027' })).toEqual({});
		expect(errorsOf({ birthYear: '1899' }).birthYear).toContain('1900년부터 2027년');
		expect(errorsOf({ birthYear: '2028' }).birthYear).toContain('사이');
		expect(errorsOf({ birthYear: '2000.5' }).birthYear).toContain('숫자 4자리');
		expect(errorsOf({ birthYear: '' }).birthYear).toContain('숫자 4자리');
	});

	it('성별은 선택해야 한다', () => {
		expect(errorsOf({ gender: '' }).gender).toBe('성별을 선택해 주세요.');
	});

	it.each([
		['heightCm', '300', undefined],
		['heightCm', '300.01', '0보다 크고 300 이하'],
		['heightCm', '0', '0보다 크고'],
		['heightCm', '165.251', '소수 둘째 자리'],
		['heightCm', '-1', '숫자로'],
		['heightCm', '1e2', '숫자로'],
		['weightKg', '500', undefined],
		['weightKg', '500.1', '500 이하']
	] as const)('%s %s', (field, value, message) => {
		const error = errorsOf({ [field]: value })[field];
		if (message) expect(error).toContain(message);
		else expect(error).toBeUndefined();
	});
});

describe('limitInput', () => {
	it.each([
		['displayName', '직장인,,,,,,,,,,,,,,,', '직장인,,,,,,,,,'],
		['displayName', '😀'.repeat(13), '😀'.repeat(12)],
		['birthYear', '1992,,,,,,', '1992'],
		['birthYear', '19a9-2', '1992'],
		['birthYear', '199212', '1992'],
		['heightCm', '175,,,,', '175'],
		['heightCm', '175.5', '175.5'],
		['heightCm', '165.251', '165.25'],
		['heightCm', '1.2.3', '1.23'],
		['heightCm', '1750', '175'],
		['weightKg', '74kg', '74'],
		['weightKg', '-74', '74']
	] as const)('%s "%s" → "%s"', (field, raw, expected) => {
		expect(limitInput(field, raw)).toBe(expected);
	});
});

describe('applyLimit', () => {
	const full = '가나다라마바사아자차카타';

	it('닉네임이 넘치면 커서 앞에 방금 넣은 글자를 지운다', () => {
		// "가" 뒤에 "X"를 넣은 상태, 커서는 X 뒤
		expect(applyLimit('displayName', `가X${full.slice(1)}`, 2)).toEqual({ value: full, caret: 1 });
	});

	it('닉네임 끝에 붙여 넣으면 넘친 부분만 지운다', () => {
		expect(applyLimit('displayName', `${full}하늘`)).toEqual({ value: full, caret: full.length });
	});

	it('이모지는 1자로 센다', () => {
		const emoji = '😀'.repeat(12);
		expect(applyLimit('displayName', `${emoji}😀`)).toEqual({ value: emoji, caret: emoji.length });
	});

	it('숫자 칸은 지운 글자만큼 커서를 당긴다', () => {
		expect(applyLimit('birthYear', '19a2', 3)).toEqual({ value: '192', caret: 2 });
		expect(applyLimit('heightCm', '17,5', 3)).toEqual({ value: '175', caret: 2 });
	});
});

describe('getProfileChanges', () => {
	const saved = {
		displayName: '테스트',
		birthYear: 2000,
		gender: 'FEMALE',
		heightCm: 165.25,
		weightKg: 55
	} as const;

	it('바뀐 필드만 돌려준다', () => {
		expect(getProfileChanges({ ...saved, weightKg: 56.5 }, saved)).toEqual({ weightKg: 56.5 });
	});

	it('입력값을 정규화해 같아지면 바뀐 것이 없다', () => {
		const result = validateProfile(
			{ ...toFormValues(saved), displayName: ' 테스트 ', heightCm: '165.250' },
			NOW
		);
		expect(result.ok && getProfileChanges(result.profile, saved)).toEqual({});
	});
});
