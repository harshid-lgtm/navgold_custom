// Shared formatting helpers. Frappe desk globals (format_currency/cint) are not
// available on window in this standalone doppio SPA, so these are the single
// rounding/coercion path for the whole control center.

export function format_currency(value) {
	if (value === null || value === undefined || value === '') return '£0.00';
	const number = Number(value);
	if (Number.isNaN(number)) return '£0.00';
	return `£${number.toLocaleString('en-GB', {
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	})}`;
}

export function to_int(value, fallback = 0) {
	const number = Number.parseInt(value, 10);
	return Number.isNaN(number) ? fallback : number;
}
