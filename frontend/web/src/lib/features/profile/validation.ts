import type { Gender, SignupProfile } from '#lib/features/auth/types.js';

/** 입력창 값. 숫자 필드도 사용자가 입력한 문자열 그대로 둔다. */
export type ProfileFormValues = {
	displayName: string;
	birthYear: string;
	gender: Gender | '';
	heightCm: string;
	weightKg: string;
};

export type ProfileField = keyof ProfileFormValues;
export type ProfileErrors = Partial<Record<ProfileField, string>>;

export type ProfileValidation =
	{ ok: true; profile: SignupProfile } | { ok: false; errors: ProfileErrors };

export const PROFILE_FIELDS: ProfileField[] = [
	'displayName',
	'birthYear',
	'gender',
	'heightCm',
	'weightKg'
];

const DISPLAY_NAME_MAX = 12;
const MIN_BIRTH_YEAR = 1900;
const MAX_VALUE: Record<'heightCm' | 'weightKg', number> = { heightCm: 300, weightKg: 500 };
const UNIT_LABEL: Record<'heightCm' | 'weightKg', string> = { heightCm: '키', weightKg: '몸무게' };

/** 명세 4.3 태어난 연도 상한. 요청 시점의 KST 올해다. */
export function getKstYear(now: Date = new Date()): number {
	return Number(
		new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Seoul', year: 'numeric' }).format(now)
	);
}

/** 명세 1.2 `details.reason`을 입력칸 문구로 바꾼다. 서버 422와 화면 검사가 같은 문구를 쓴다. */
export function getReasonMessage(field: ProfileField, reason: string, now = new Date()): string {
	if (field === 'displayName') {
		if (reason === 'too_long') return `닉네임은 ${DISPLAY_NAME_MAX}자까지 쓸 수 있어요.`;
		if (reason === 'invalid_character') return '줄바꿈이나 제어 문자는 쓸 수 없어요.';
		return '닉네임을 입력해 주세요.';
	}
	if (field === 'birthYear') {
		if (reason === 'out_of_range') {
			return `${MIN_BIRTH_YEAR}년부터 ${getKstYear(now)}년 사이로 입력해 주세요.`;
		}
		return '태어난 연도를 숫자 4자리로 입력해 주세요.';
	}
	if (field === 'gender') return '성별을 선택해 주세요.';

	const label = UNIT_LABEL[field];
	if (reason === 'out_of_range')
		return `${label}는 0보다 크고 ${MAX_VALUE[field]} 이하로 입력해 주세요.`;
	if (reason === 'decimal_places') return `${label}는 소수 둘째 자리까지 입력할 수 있어요.`;
	return `${label}를 숫자로 입력해 주세요.`;
}

/**
 * 닉네임 규칙(명세 4.3). 줄바꿈·제어문자는 trim 전에 검사하고,
 * 길이·공백은 NFC 정규화와 앞뒤 공백 제거 뒤 코드 포인트로 센다.
 */
function checkDisplayName(raw: string): { value: string } | { reason: string } {
	if (/\p{Cc}/u.test(raw)) return { reason: 'invalid_character' };
	const value = raw.normalize('NFC').trim();
	if (value === '' || /^[\p{White_Space}\p{Default_Ignorable_Code_Point}]+$/u.test(value)) {
		return { reason: 'blank' };
	}
	if (Array.from(value).length > DISPLAY_NAME_MAX) return { reason: 'too_long' };
	return { value };
}

export type TextProfileField = Exclude<ProfileField, 'gender'>;

/**
 * 입력하는 동안 칸마다 들어갈 수 있는 글자만 남긴다. 범위·형식의 최종 검사는 validateProfile이 한다.
 * - 닉네임: 코드 포인트 12자까지(이모지도 1자)
 * - 태어난 연도: 숫자 4자리까지
 * - 키·몸무게: 숫자와 소수점 하나, 정수 3자리·소수 둘째 자리까지 (상한 300·500이 3자리)
 */
export function limitInput(field: TextProfileField, raw: string): string {
	if (field === 'displayName') return Array.from(raw).slice(0, DISPLAY_NAME_MAX).join('');
	if (field === 'birthYear') return raw.replace(/\D/g, '').slice(0, 4);

	const [integer = '', ...rest] = raw.replace(/[^\d.]/g, '').split('.');
	const head = integer.slice(0, 3);
	return rest.length ? `${head}.${rest.join('').slice(0, 2)}` : head;
}

/**
 * [limitInput]을 입력창에 적용한 값과 커서 위치(UTF-16 위치).
 * 닉네임이 넘치면 뒤 글자가 아니라 커서 앞에 방금 넣은 글자를 지운다(가운데 입력이 끝 글자를 밀어내지 않게).
 */
export function applyLimit(
	field: TextProfileField,
	raw: string,
	caret = raw.length
): { value: string; caret: number } {
	if (field === 'displayName') {
		const chars = Array.from(raw);
		const over = chars.length - DISPLAY_NAME_MAX;
		const at = Array.from(raw.slice(0, caret)).length;
		if (over <= 0 || at < over) {
			const value = limitInput(field, raw);
			return { value, caret: Math.min(caret, value.length) };
		}
		const before = chars.slice(0, at - over).join('');
		return { value: before + chars.slice(at).join(''), caret: before.length };
	}
	const value = limitInput(field, raw);
	return { value, caret: Math.min(limitInput(field, raw.slice(0, caret)).length, value.length) };
}

/** 165.250은 허용, 165.251은 거절한다. 반올림하지 않는다. */
function checkMeasure(raw: string, max: number): { value: number } | { reason: string } {
	const text = raw.trim();
	if (!/^\d+(\.\d+)?$/.test(text)) return { reason: 'invalid_type' };
	const value = Number(text);
	if (!(value > 0 && value <= max)) return { reason: 'out_of_range' };
	const decimals = text.split('.')[1]?.replace(/0+$/, '') ?? '';
	if (decimals.length > 2) return { reason: 'decimal_places' };
	return { value };
}

export function validateProfile(
	values: ProfileFormValues,
	now: Date = new Date()
): ProfileValidation {
	const errors: ProfileErrors = {};

	const displayName = checkDisplayName(values.displayName);
	if ('reason' in displayName) {
		errors.displayName = getReasonMessage('displayName', displayName.reason, now);
	}

	const yearText = values.birthYear.trim();
	const birthYear = Number(yearText);
	if (!/^\d+$/.test(yearText)) {
		errors.birthYear = getReasonMessage('birthYear', 'invalid_type', now);
	} else if (birthYear < MIN_BIRTH_YEAR || birthYear > getKstYear(now)) {
		errors.birthYear = getReasonMessage('birthYear', 'out_of_range', now);
	}

	if (values.gender === '') errors.gender = getReasonMessage('gender', 'missing', now);

	const heightCm = checkMeasure(values.heightCm, MAX_VALUE.heightCm);
	if ('reason' in heightCm) errors.heightCm = getReasonMessage('heightCm', heightCm.reason, now);

	const weightKg = checkMeasure(values.weightKg, MAX_VALUE.weightKg);
	if ('reason' in weightKg) errors.weightKg = getReasonMessage('weightKg', weightKg.reason, now);

	if (
		errors.birthYear ||
		'reason' in displayName ||
		values.gender === '' ||
		'reason' in heightCm ||
		'reason' in weightKg
	) {
		return { ok: false, errors };
	}
	return {
		ok: true,
		profile: {
			displayName: displayName.value,
			birthYear,
			gender: values.gender,
			heightCm: heightCm.value,
			weightKg: weightKg.value
		}
	};
}

export function isProfileField(field: string): field is ProfileField {
	return (PROFILE_FIELDS as string[]).includes(field);
}

/** 저장된 회원 정보를 입력창 값으로 바꾼다. */
export function toFormValues(user: SignupProfile): ProfileFormValues {
	return {
		displayName: user.displayName,
		birthYear: String(user.birthYear),
		gender: user.gender,
		heightCm: String(user.heightCm),
		weightKg: String(user.weightKg)
	};
}

/** 명세 4.2는 바꾼 필드만 보낸다. 정규화한 값이 저장값과 같으면 보내지 않는다. */
export function getProfileChanges(
	profile: SignupProfile,
	saved: SignupProfile
): Partial<SignupProfile> {
	const changes: Partial<SignupProfile> = {};
	if (profile.displayName !== saved.displayName) changes.displayName = profile.displayName;
	if (profile.birthYear !== saved.birthYear) changes.birthYear = profile.birthYear;
	if (profile.gender !== saved.gender) changes.gender = profile.gender;
	if (profile.heightCm !== saved.heightCm) changes.heightCm = profile.heightCm;
	if (profile.weightKg !== saved.weightKg) changes.weightKg = profile.weightKg;
	return changes;
}
