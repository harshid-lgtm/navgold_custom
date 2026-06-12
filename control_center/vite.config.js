import path from 'path';
import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';
import proxyOptions from './proxyOptions';

// doppio lives at apps/doppio. The relative path from the worktree control_center/ directory
// to the bench apps/ directory is 5 levels up (no extra /apps segment needed).
const BENCH_APPS = path.resolve(__dirname, '../../../../../');
const doppio_root = path.resolve(BENCH_APPS, 'doppio');

// https://vitejs.dev/config/
export default defineConfig({
	plugins: [vue()],
	server: {
		port: 8080,
		host: '0.0.0.0',
		proxy: proxyOptions,
	},
	resolve: {
		alias: [
			{ find: '@', replacement: path.resolve(__dirname, 'src') },
			// map the relative-doppio imports that appear in main.js / router etc.
			{
				find: '../../../doppio/libs/controllers/auth',
				replacement: path.resolve(doppio_root, 'libs/controllers/auth.js'),
			},
			{
				find: '../../../doppio/libs/controllers/call',
				replacement: path.resolve(doppio_root, 'libs/controllers/call.js'),
			},
			{
				find: '../../../doppio/libs/controllers/socket',
				replacement: path.resolve(doppio_root, 'libs/controllers/socket.js'),
			},
			{
				find: '../../../doppio/libs/resourceManager',
				replacement: path.resolve(doppio_root, 'libs/resourceManager/index.js'),
			},
		],
	},
	build: {
		outDir: '../navgold_custom/public/control_center',
		emptyOutDir: true,
		target: 'es2015',
	},
});
