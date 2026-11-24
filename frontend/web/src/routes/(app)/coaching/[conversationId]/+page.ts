import type { PageLoad } from './$types';

// 지난 메시지(명세 6.3)는 화면에서 불러오고, 진행 중인 질문은 코칭 layout의 연결에서 가져온다.
export const load: PageLoad = ({ params }) => ({ conversationId: params.conversationId });
