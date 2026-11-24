import { ResponseFormatError, isRecord } from './errors';

/**
 * feature API가 응답 `data`(unknown)를 명세 타입으로 바꿀 때 쓰는 검증 함수.
 * 형태가 다르면 어느 필드인지 담아 ResponseFormatError를 던진다.
 */

export function readRecord(value: unknown, field: string): Record<string, unknown> {
	if (!isRecord(value)) throw new ResponseFormatError(field);
	return value;
}

export function readArray(record: Record<string, unknown>, field: string): unknown[] {
	const value = record[field];
	if (!Array.isArray(value)) throw new ResponseFormatError(field);
	return value;
}

export function readString(record: Record<string, unknown>, field: string): string {
	const value = record[field];
	if (typeof value !== 'string') throw new ResponseFormatError(field);
	return value;
}

export function readNumber(record: Record<string, unknown>, field: string): number {
	const value = record[field];
	if (typeof value !== 'number' || !Number.isFinite(value)) throw new ResponseFormatError(field);
	return value;
}

export function readBoolean(record: Record<string, unknown>, field: string): boolean {
	const value = record[field];
	if (typeof value !== 'boolean') throw new ResponseFormatError(field);
	return value;
}

export function readEnum<T extends string>(
	record: Record<string, unknown>,
	field: string,
	values: readonly T[]
): T {
	const match = values.find((item) => item === record[field]);
	if (match === undefined) throw new ResponseFormatError(field);
	return match;
}

/** null이 허용된 필드. 키가 없거나 null이면 null, 있으면 reader로 검증한다. */
export function readNullable<T>(
	record: Record<string, unknown>,
	field: string,
	reader: (record: Record<string, unknown>, field: string) => T
): T | null {
	return record[field] === null || record[field] === undefined ? null : reader(record, field);
}
