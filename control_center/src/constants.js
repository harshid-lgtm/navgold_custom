// frappe-charts palette, shared across every chart in the control center.
export const CHART_COLORS = {
	green: '#10b981',
	blue: '#3b82f6',
	amber: '#f59e0b',
	red: '#ef4444',
	purple: '#8b5cf6',
};

export const CHART_PALETTE = [
	CHART_COLORS.green,
	CHART_COLORS.blue,
	CHART_COLORS.amber,
	CHART_COLORS.red,
	CHART_COLORS.purple,
];

// Single source of truth for the Navgold Settings feature flags surfaced in the UI.
// StatusHeader renders compact badges, Emergency renders the full toggle list — both
// read from here so a new flag only needs adding in one place.
export const FLAG_DEFINITIONS = [
	{ key: 'enable_pricing_sync', label: 'Pricing Sync' },
	{ key: 'repricer_enabled', label: 'Repricer' },
	{ key: 'freeze_amazon_outbound', label: 'Freeze Amazon outbound' },
	{ key: 'pause_strategy_refresh', label: 'Strategy Refresh' },
	{ key: 'enable_afn_stock_sync', label: 'AFN Stock Sync' },
	{ key: 'amazon_publishing_mode', label: 'Publishing Mode' },
	{ key: 'enable_sync', label: 'Sync' },
];
