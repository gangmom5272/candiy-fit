// Storybook에는 SvelteKit 3의 경로 초기화 데이터가 없다.
// Header에서 사용하는 정적 경로를 base path 없는 Storybook URL로 해석한다.
export function resolve(path: string) {
	return `/${path
		.split('/')
		.filter((segment) => segment && !segment.startsWith('('))
		.join('/')}`;
}
