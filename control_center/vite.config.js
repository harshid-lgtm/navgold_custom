import path from 'path';
import vue from '@vitejs/plugin-vue';
import frappeuiPlugin from 'frappe-ui/vite';
import { defineConfig } from 'vite';
import proxyOptions from './proxyOptions';

import fs from 'fs';

// doppio lives at apps/doppio, but this app may be built from the repo root or a
// git worktree at a different depth — walk upward until we find doppio/libs.
const find_doppio_root = () => {
	let dir = __dirname;
	for (let depth = 0; depth < 8; depth += 1) {
		dir = path.resolve(dir, '..');
		const candidate = path.join(dir, 'doppio');
		if (fs.existsSync(path.join(candidate, 'libs'))) return candidate;
	}
	throw new Error(`doppio app not found above ${__dirname}`);
};
const doppio_root = find_doppio_root();

// https://vitejs.dev/config/
export default defineConfig({
	plugins: [
		vue(),
		...frappeuiPlugin({
			lucideIcons: true,
			frappeProxy: false,
			jinjaBootData: false,
			buildConfig: false,
		}),
	],
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
