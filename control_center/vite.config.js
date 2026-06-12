import path from 'path';
import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';
import proxyOptions from './proxyOptions';

// https://vitejs.dev/config/
export default defineConfig({
	plugins: [vue()],
	server: {
		port: 8080,
		host: '0.0.0.0',
		proxy: proxyOptions,
	},
	resolve: {
		alias: {
			'@': path.resolve(__dirname, 'src'),
		},
	},
	build: {
		outDir: '../navgold_custom/public/control_center',
		emptyOutDir: true,
		target: 'es2015',
	},
});
