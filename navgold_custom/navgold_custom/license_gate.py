import frappe
from frappe.integrations.utils import make_get_request
from frappe.utils import cint

LICENSE_URL = "https://hq.bwh.tech/api/v2/method/purchase_flow"
CACHE_KEY = "navgold_license_status"
REFRESH_LOCK = "navgold_license_refreshing"
CACHE_TTL = 6 * 60 * 60
DEFAULT_INTERVAL = 120


def check_license():
	try:
		response = make_get_request(LICENSE_URL)
		warn, nag_count, interval = parse_flags(response)
	except Exception:
		frappe.log_error("License check failed", "Navgold License Gate")
		return

	status = {"warn": warn, "nag_count": nag_count, "warn_interval": interval}
	frappe.cache().set_value(CACHE_KEY, status, expires_in_sec=CACHE_TTL)


def parse_flags(response):
	payload = frappe.parse_json(response) if isinstance(response, str) else response
	if not isinstance(payload, dict):
		return False, 1, DEFAULT_INTERVAL

	nag_count = cint(payload["nag_count"]) if "nag_count" in payload else 1
	interval = cint(payload["warn_interval"]) if "warn_interval" in payload else DEFAULT_INTERVAL
	return bool(payload.get("warn")), nag_count, interval


def get_status():
	status = frappe.cache().get_value(CACHE_KEY)
	if not status:
		request_refresh()
		return {"warn": False, "nag_count": 0, "interval": DEFAULT_INTERVAL}

	return {
		"warn": bool(status.get("warn")),
		"nag_count": cint(status.get("nag_count", 1)),
		"interval": cint(status.get("warn_interval")) or DEFAULT_INTERVAL,
	}


def request_refresh():
	if frappe.cache().get_value(REFRESH_LOCK):
		return
	frappe.cache().set_value(REFRESH_LOCK, 1, expires_in_sec=120)
	frappe.enqueue(
		"navgold_custom.navgold_custom.license_gate.check_license",
		queue="short",
		enqueue_after_commit=True,
	)


def get_bootinfo(bootinfo):
	status = get_status()
	bootinfo.navgold_payment_warn = status["warn"]
	bootinfo.navgold_payment_nag_count = status["nag_count"]
	bootinfo.navgold_payment_warn_interval = status["interval"]
