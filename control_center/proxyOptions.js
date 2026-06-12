import { existsSync, readFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

const current_dir = dirname(fileURLToPath(import.meta.url));
const config_path = resolve(
	current_dir,
	'../../../sites/common_site_config.json',
);

let webserver_port = 8000;

if (existsSync(config_path)) {
	const common_site_config = JSON.parse(readFileSync(config_path, 'utf-8'));
	webserver_port = common_site_config.webserver_port || 8000;
}

export default {
	'^/(app|api|assets|files|private)': {
		target: `http://127.0.0.1:${webserver_port}`,
		ws: true,
		router: (req) => {
			const site_name = req.headers.host.split(':')[0];
			return `http://${site_name}:${webserver_port}`;
		},
	},
};
