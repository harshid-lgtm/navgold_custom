import frappeUIPreset from 'frappe-ui/tailwind';

/** @type {import('tailwindcss').Config} */
export default {
	presets: [frappeUIPreset],
	content: [
		'./src/**/*.{html,jsx,tsx,vue,js,ts}',
		'./node_modules/frappe-ui/src/components/**/*.{vue,js,ts}',
	],
	safelist: ['animate-pulse', 'animate-ping'],
	theme: {
		extend: {},
	},
	plugins: [],
};
