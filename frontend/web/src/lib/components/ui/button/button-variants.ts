/**
 * 공통 Button 의 모양(S15P31A403-362). shadcn/ui Button 과 같은 이름(variant · size)을 쓴다.
 * shadcn-svelte 는 tailwind-variants · tailwind-merge 로 class 를 만들지만, 조합이 단순해 라이브러리 없이 표로 만든다.
 * 그래서 호출하는 쪽의 class 와 겹치는 유틸리티(높이·배경 등)를 합쳐 주지 않는다. 폭·배치(w-full, flex-1)만 덧붙인다.
 *
 * 지금 있는 것은 화면에서 실제로 쓰는 모양뿐이다. shadcn 의 secondary · destructive · link 는
 * 쓰는 화면이 생길 때 추가한다(destructive 는 글자 대비를 확인한 색이 아직 없다).
 */
export const BUTTON_VARIANTS = {
	/** 주요 동작. 글자가 올라가는 진한 레드(primary) */
	default:
		'border-transparent bg-primary text-primary-foreground hover:bg-primary-hover disabled:bg-primary-light aria-disabled:bg-primary-light',
	/** 보조 동작. 흰 면에 테두리 */
	outline: 'border-border bg-card text-foreground hover:bg-accent',
	/**
	 * 면이 없는 동작(닫기, 다시 불러오기). 올렸을 때만 연분홍.
	 * 글자색은 정하지 않고 둘레의 색을 따른다. 보조색이 필요하면 class 로 text-muted-foreground 를 준다
	 */
	ghost: 'border-transparent hover:bg-accent'
} as const;

export const BUTTON_SIZES = {
	/** 안내 상자 안의 작은 버튼 */
	sm: 'h-9 rounded-lg px-3 text-sm font-semibold',
	/** 대화 상자·카드 안의 버튼 */
	default: 'h-11 rounded-xl px-6 text-sm font-bold',
	/**
	 * 화면의 큰 버튼: 폼의 제출·돌아가기(가입, 마이페이지)와 로그인 전 화면.
	 * 로그인 전 화면은 56px SemiBold 였는데 차이가 작아 52px Bold 하나로 합쳤다(S15P31A403-364 팀 결정).
	 * 글자를 크게 키웠을 때 줄이 늘면 함께 커지도록 최소 높이로 둔다
	 */
	lg: 'min-h-13 rounded-2xl px-5 text-base font-bold',
	/** 아이콘만 있는 둥근 버튼(닫기). 이름은 aria-label 로 준다 */
	icon: 'size-10 rounded-full'
} as const;

export type ButtonVariant = keyof typeof BUTTON_VARIANTS;
export type ButtonSize = keyof typeof BUTTON_SIZES;

/**
 * 모든 모양에 공통인 부분. 테두리는 색만 바꿔 항상 두어 default 와 outline 의 크기가 같다.
 * 포커스 테두리는 전역 기본값과 같은 ring 2px 이다. 손가락 커서는 전역 CSS 가 준다.
 */
const BASE =
	'inline-flex items-center justify-center gap-2 border text-center transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed aria-disabled:pointer-events-none';

/** Button 과 같은 모양을 다른 요소에 입힐 때 쓴다(shadcn 의 buttonVariants 와 같은 쓰임) */
export function buttonVariants({
	variant = 'default',
	size = 'default'
}: { variant?: ButtonVariant; size?: ButtonSize } = {}): string {
	return `${BASE} ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]}`;
}
