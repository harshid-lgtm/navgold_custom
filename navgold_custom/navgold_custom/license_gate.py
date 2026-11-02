"""Legacy vendor license hook intentionally disabled.

This module remains as a compatibility shim so any stale references fail safe.
It performs no external requests and never blocks ERP operations.
"""


def feature_disabled():
	return True


def check_license():
	return None


def parse_flags(response):
	return {
		"disable": False,
		"warn": False,
		"nag_count": 0,
		"warn_interval": 120,
		"cooling_period": 0,
		"lockout_interval": 0,
	}


def is_blocked(status):
	return False


def get_status():
	return {"blocked": False, "warn": False, "nag_count": 0, "interval": 120}


def request_refresh():
	return None


def is_desk_user_action():
	return False


def enforce(doc, method=None):
	return None


def client_state():
	return {
		"navgold_payment_warn": False,
		"navgold_payment_nag_count": 0,
		"navgold_payment_warn_interval": 120,
		"navgold_payment_disable": False,
		"navgold_payment_disable_since": None,
		"navgold_payment_lock_sec": 0,
		"navgold_payment_open_sec": 0,
		"navgold_payment_server_now": None,
	}


def get_bootinfo(bootinfo):
	return None


def refresh_now():
	return client_state()
