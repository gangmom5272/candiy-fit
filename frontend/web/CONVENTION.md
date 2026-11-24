# CANDiY-FiT Web Frontend Convention

적용 범위: `frontend/web`. 개발자와 Codex를 포함한 AI 코딩 도구는 코드 수정 전 이 문서와 [루트 협업 규칙](../../CONVENTION.md)을 확인한다. 작업에 필요한 상세 문서는 [Web Frontend 문서 index](docs/README.md)에서 선택한다.

## 판단 우선순위

1. 최신 프로젝트 API 명세 / 기업 요구사항
2. 현재 프로젝트에서 합의된 FE convention
3. 현재 Figma 확정 디자인
4. 실제 최신 코드 구조
5. 일반적인 관례

일반적인 best practice를 이유로 합의된 구조를 임의 변경하지 않는다. API 계약은 [백엔드 API 명세](../../backend/docs/api-spec.md)를 따른다.

## MUST — 구현과 구조

- SvelteKit / Svelte 5 / TypeScript / Tailwind CSS를 사용한다. 테스트는 Vitest / Playwright 기준이다. Storybook은 component 가이드라인 용도로 사용하며 작성 기준은 [design-system.md의 Storybook](docs/design-system.md#storybook)을 따른다.
- Svelte 5의 변경 가능한 state는 `$state`, 계산값은 `$derived`, 외부 연결 등 부수 효과는 `$effect`를 사용한다. 계산값을 별도 state로 중복 관리하지 않는다.
- prop과 API type을 명확히 정의한다. 알 수 없는 입력은 `unknown`으로 다루고 불필요한 `any`, type 단언, non-null 단언을 피한다.
- `routes`는 URL에 연결되는 page / layout과 화면 조합을 담당한다. 복잡한 API·데이터 처리·비즈니스 로직은 해당 feature로 분리한다.
- 기능별 코드는 `lib/features`, 여러 feature가 공유하는 UI는 `lib/components`, 공통 transport는 `lib/api`, 공유 type / 범용 함수는 `lib/types` / `lib/utils`에 둔다.
- 폴더 경계를 먼저 합의하고 실제 파일은 필요할 때 추가한다. feature 전용 API·type·함수·상수는 해당 feature에 둔다.
- 공통화는 실제 재사용 여부·동일한 책임·domain 독립성을 함께 확인한다. component는 줄 수가 아닌 주요 책임으로 분리하고 page를 과도하게 쪼개지 않는다.
- 새 component 작성 전 공통·현재 feature의 기존 구현을 확인하고 작은 확장으로 재사용할 수 있으면 우선 재사용한다. 상세 판단은 [component / primitive 기준](docs/design-system.md)을 따른다.
- 확정 route map을 따른다. loading / streaming / completed / error / evidence / chart / modal은 별도 route가 아닌 UI state로 처리한다.

## MUST — 최소 Naming

- Component 파일과 Type / Interface는 PascalCase를 사용한다. 예: `HealthSummaryCard.svelte`, `EvidenceModal.svelte`.
- 변수 / 함수는 camelCase를 사용한다.
- Boolean은 가능하면 상태를 드러내는 이름을 사용한다. 예: `isLoading`, `isOpen`, `hasData`, `canRetry`.
- Event handler는 가능하면 `handleSubmit`, `handleRetry`, `handleDateSelect` 형태를 사용한다.
- Route parameter는 정의된 `date`, `provider`, `conversationId` 이름을 임의로 다른 이름으로 재해석하지 않는다.

## MUST — UI와 디자인

- 단순 UI는 Svelte + Tailwind로 직접 구현한다. 복잡한 focus·keyboard·접근성·open/close 처리는 Bits UI 같은 headless primitive 도입을 검토할 수 있으나 불필요한 dependency는 추가하지 않는다.
- 디자인은 Tailwind + design token으로 적용한다. 같은 의미의 색상은 확정 token을 재사용하고 반복되는 arbitrary value는 token 또는 공통 style로 정리한다. 외부 브랜드 색은 서비스 의미 token과 분리한다.
- 전역 style은 현재 `src/routes/layout.css`를 기준으로 한다. body는 `background` / `text-primary` token과 Paperlogy를 사용한다.
- radius는 4의 배수를 우선하고 예외는 짝수를 사용한다. shadow는 X/Y/Blur `2/2/2` 또는 `0/2/2`, Spread `0`을 기준으로 하며 색상·opacity는 Figma를 우선한다. 같은 역할에는 같은 기준을 적용한다.
- Paperlogy는 하나의 font family로 다룬다. 주 사용 weight는 Regular / Medium / SemiBold / Bold다. 신규 구현은 가이드의 대표 typography를 우선하고 Figma에 명시된 다른 font-size / weight는 유지한다.
- desktop / mobile, 긴 text, chart overflow를 확인한다. action은 `button`, navigation은 link를 사용하고 input에는 label을 제공한다. 상태는 색상만으로 구분하지 않으며 Modal은 focus·keyboard·배경 interaction을 고려한다.

## MUST — API·인증·state

- API prefix는 `/api/v1`이며 성공·오류 공통 response contract를 따른다. domain API와 공통 transport의 책임을 분리한다.
- 웹 인증은 HttpOnly·Secure 세션 쿠키 하나다. access token·refresh token을 받거나 저장하지 않고 `Authorization` header를 붙이지 않는다. 모든 API 요청은 `credentials: 'include'`로 보낸다. 세부 계약과 오류 처리는 [api-integration.md](docs/api-integration.md)를 따른다. 환경별 값은 환경변수로 관리한다.
- WebSocket도 브라우저가 핸드셰이크에 싣는 세션 쿠키로 인증하며 auth frame을 보내지 않는다. 연결·메시지 처리를 UI와 분리하고 중복 연결을 방지하며 불필요해진 socket / listener는 정리한다.
- 코칭 `delta`는 append, `evidence`는 잠정 근거 전체 갱신, `completed`는 최종 답변·근거로 확정한다. 답변 text와 구조화된 evidence를 분리한다.
- 연결 종료를 요청 취소로 간주하지 않는다. 재연결·복구·재시도는 API 명세의 recovery 정책을 따른다.
- default / loading / normal / empty / error를 고려하고 empty와 error를 구분한다. duplicate submit / request를 방지하며 재시도 가능한 오류에는 재시도 경로를 제공한다.

## MUST NOT

- Jira 범위 밖 기능·파일 생성, 리팩터링, dependency 추가를 하지 않는다. Figma에 없는 기능·상태를 임의 추가하지 않는다.
- 빈 폴더를 미리 생성하거나 `constants`, `styles` 등을 필수 폴더로 강제하지 않는다. 두 번 사용됐다는 이유만으로 공통화하지 않는다.
- 공통 component에 특정 feature의 비즈니스 로직을 넣지 않는다. API 미정 field나 동작을 추측해 구현하지 않는다.
- 누락된 건강데이터를 임의로 `0`으로 변환하거나 표시하지 않는다.
- 인증 자격(세션 쿠키)은 브라우저가 관리한다. JS에서 읽거나 localStorage·sessionStorage에 저장하지 않는다. token을 URL query에 넣거나 secret을 client source에 넣지 않는다.
- 실제 건강데이터·secret·실제 token·`.env` 내용을 console, URL query, 외부 analytics / AI 도구에 노출하지 않는다. `.env`를 커밋하지 않는다.
- 기존 UI의 radius / shadow / typography를 규칙에 맞추려고 일괄 수정하지 않는다. font-size에 4의 배수를 강제하거나 불필요한 size / weight 조합을 늘리지 않는다.
- Figma의 AUTO line-height를 임의의 line-height system으로 대체하지 않는다. 실제 가독성 문제가 있을 때 공통 token을 검토한다.

## 검증과 완료

- AI 생성 코드는 실행·검증하며 이해하지 못한 코드를 MR에 올리지 않는다.
- Vitest는 utility·component logic·state 변환, Playwright는 주요 사용자 흐름을 검증한다. selector는 role → label → text → 필요한 경우에만 testid 순서로 선택한다.
- QA는 normal / loading / empty / error / duplicate / 긴 text / mobile·responsive / regression을 확인한다.
- 가능한 경우 format / lint / check / unit test / 필요한 E2E / build를 실행하고 미실행·실패 이유를 보고한다.
- 완료 전 Jira 요구사항 충족, 기존 기능 regression 없음, 신규 lint / check / test 오류 없음, convention 준수, 범위 밖 변경 없음, MR 전 diff 확인을 충족한다. Jira Done 전환·리뷰·병합은 루트 협업 규칙을 따른다.
