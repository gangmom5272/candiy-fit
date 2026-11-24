import type { Preview } from '@storybook/sveltekit';
// 실제 화면과 같은 폰트·색 토큰을 쓰도록 전역 CSS를 그대로 불러온다
import '../src/routes/layout.css';
import './docs.css';

const preview: Preview = {
	parameters: {
		controls: {
			matchers: {
				color: /(background|color)$/i,
				date: /Date$/i
			}
		},

		a11y: {
			// 'todo': 접근성 위반을 패널에만 표시한다. 'error'로 바꾸면 테스트에서 실패로 처리된다
			test: 'todo'
		}
	}
};

export default preview;
