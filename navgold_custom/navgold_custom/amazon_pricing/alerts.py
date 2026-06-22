# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe.integrations.utils import make_get_request, make_post_request
from frappe.utils.data import add_to_date, cint, flt, fmt_money, get_datetime, now_datetime

LOSS_WINDOW_CACHE_KEY = "navgold_below_min_loss_events"
LOSS_WINDOW_MINUTES = 15

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

SEVERITY_ICON = {
	"critical": "🔴",
	"warning": "🟠",
	"info": "🔵",
	"ok": "🟢",
}


def get_alert_config() -> dict:
	settings = frappe.get_cached_doc("Navgold Settings")
	return {
		"enabled": cint(settings.get("enable_telegram_alerts")),
		"token": settings.get_password("telegram_bot_token", raise_exception=False)
		if settings.get("telegram_bot_token")
		else None,
		"chat_id": settings.get("telegram_chat_id"),
	}


def send_telegram(text: str, config: dict | None = None) -> dict | None:
	# Build defensively: a failure to alert must never break the calling business flow.
	config = config or get_alert_config()
	if not (config.get("enabled") and config.get("token") and config.get("chat_id")):
		return None

	url = TELEGRAM_API.format(token=config["token"], method="sendMessage")
	try:
		return make_post_request(
			url,
			data={
				"chat_id": config["chat_id"],
				"text": text,
				"parse_mode": "HTML",
				"disable_web_page_preview": "true",
			},
		)
	except Exception as exception:
		frappe.log_error(f"Telegram alert failed: {exception!s}", "Navgold Telegram Alert")
		return None


def notify(
	title: str,
	lines: list[str],
	severity: str = "critical",
	dedupe_key: str | None = None,
	dedupe_seconds: int = 600,
) -> None:
	config = get_alert_config()
	# Require a fully-wired channel before touching the dedupe cache — otherwise a key set
	# while the chat id is missing would silently suppress the alert once it is connected.
	if not (config.get("enabled") and config.get("token") and config.get("chat_id")):
		return

	if dedupe_key:
		cache_key = f"navgold_alert_sent:{dedupe_key}"
		if frappe.cache().get_value(cache_key):
			return
		frappe.cache().set_value(cache_key, "1", expires_in_sec=dedupe_seconds)

	icon = SEVERITY_ICON.get(severity, "🔔")
	body = "\n".join(lines)
	text = f"{icon} <b>{frappe.utils.escape_html(title)}</b>\n{body}"
	send_telegram(text, config)


def money(amount) -> str:
	return fmt_money(flt(amount), currency="GBP")


# ---------------------------------------------------------------------------
# Instant event hooks (called from the live code paths)
# ---------------------------------------------------------------------------


def alert_feed_error(log_name: str, error: str, channel: str = "Amazon") -> None:
	notify(
		f"{channel} feed FAILED",
		[f"Log: <code>{log_name}</code>", f"Error: {frappe.utils.escape_html(str(error)[:300])}"],
		severity="critical",
		dedupe_key=f"feed_error:{log_name}",
	)


def alert_blocked_skus(channel: str, blocked_count: int, sample: list[str] | None = None) -> None:
	if not blocked_count:
		return
	lines = [f"{blocked_count} SKU(s) blocked from the {channel} push."]
	if sample:
		lines.append("e.g. " + ", ".join(sample[:5]))
	notify(
		f"{channel} push blocked SKUs",
		lines,
		severity="warning",
		dedupe_key=f"blocked:{channel}:{blocked_count}",
		dedupe_seconds=300,
	)


def alert_freeze_toggled(frozen: int) -> None:
	if frozen:
		notify(
			"Amazon outbound FROZEN",
			["All Amazon/Repricer writes are halted."],
			severity="critical",
			dedupe_key="freeze:on",
			dedupe_seconds=60,
		)
	else:
		notify(
			"Amazon outbound released",
			["Outbound writes are live again."],
			severity="ok",
			dedupe_key="freeze:off",
			dedupe_seconds=60,
		)


def alert_below_min_sale(sales_order: str, proxy_sku: str, rate, min_price, qty) -> None:
	loss = max(flt(min_price) - flt(rate), 0) * flt(qty)
	notify(
		"Below-min sale",
		[
			f"SKU <b>{frappe.utils.escape_html(proxy_sku)}</b> sold {money(rate)} (min {money(min_price)})",
			f"{frappe.utils.escape_html(sales_order)} · qty {flt(qty):g} · est. loss {money(loss)}",
		],
		severity="critical",
		dedupe_key=f"belowmin:{sales_order}:{proxy_sku}",
		dedupe_seconds=86400,
	)


def check_sales_order_below_min(doc, method=None) -> None:
	# Fires the instant the Amazon order lands in ERP — the direct money-bleed tripwire.
	# Detection runs regardless of Telegram config because the circuit breaker depends on it;
	# only the alert message itself is gated by the alert settings.
	if not doc.get("amazon_order_id"):
		return

	from navgold_custom.navgold_custom.report.below_min_price_sales.below_min_price_sales import (
		get_min_price_map,
	)

	pairs = [(item.item_code, item.uom) for item in doc.items]
	min_price_map = get_min_price_map(pairs)
	order_loss = flt(0)
	for item in doc.items:
		mapped = min_price_map.get((item.item_code, item.uom))
		if not mapped:
			continue
		if flt(item.rate) < flt(mapped["min_price"]):
			alert_below_min_sale(doc.name, mapped["proxy_sku"], item.rate, mapped["min_price"], item.qty)
			order_loss += max(flt(mapped["min_price"]) - flt(item.rate), 0) * flt(item.qty)

	if order_loss > 0:
		window_loss = record_loss_event(order_loss)
		trip_circuit_breaker_if_needed(window_loss)


# ---------------------------------------------------------------------------
# Loss circuit breaker — auto-freeze when the bleed rate crosses a threshold
# ---------------------------------------------------------------------------


def record_loss_event(loss: float) -> float:
	# Redis-backed sliding 15-minute window so the breaker reacts within a second of a bad sale,
	# without a per-order database scan.
	now = now_datetime()
	cutoff = add_to_date(now, minutes=-LOSS_WINDOW_MINUTES)
	events = frappe.cache().get_value(LOSS_WINDOW_CACHE_KEY) or []
	events = [event for event in events if get_datetime(event[0]) >= cutoff]
	events.append([str(now), flt(loss)])
	frappe.cache().set_value(LOSS_WINDOW_CACHE_KEY, events, expires_in_sec=LOSS_WINDOW_MINUTES * 60 + 60)
	return flt(sum(event[1] for event in events), 2)


def trip_circuit_breaker_if_needed(window_loss: float) -> bool:
	settings = frappe.get_cached_doc("Navgold Settings")
	if not cint(settings.get("enable_loss_circuit_breaker")):
		return False

	threshold = flt(settings.get("loss_circuit_breaker_threshold"))
	if threshold <= 0 or window_loss < threshold:
		return False

	if cint(settings.get("freeze_amazon_outbound")):
		return False  # already frozen — nothing more to do

	frappe.db.set_single_value("Navgold Settings", "freeze_amazon_outbound", 1)
	frappe.clear_cache(doctype="Navgold Settings")

	# Always leave an audit trail, even if Telegram is off — the freeze itself is the safety action.
	message = (
		f"Circuit breaker tripped: below-min loss {fmt_money(window_loss, currency='GBP')} in "
		f"{LOSS_WINDOW_MINUTES} min (limit {fmt_money(threshold, currency='GBP')}). Outbound frozen."
	)
	frappe.log_error(message, "Navgold Circuit Breaker")
	notify(
		"CIRCUIT BREAKER TRIPPED — outbound FROZEN",
		[
			f"Below-min loss hit {money(window_loss)} in {LOSS_WINDOW_MINUTES} min "
			f"(limit {money(threshold)}).",
			"All Amazon/Repricer writes are halted. Investigate before releasing the freeze.",
		],
		severity="critical",
		dedupe_key="circuit_breaker",
		dedupe_seconds=300,
	)
	return True


def sweep_circuit_breaker() -> None:
	# DB-authoritative backup to the cache window: recompute the 15-minute below-min loss and trip.
	settings = frappe.get_cached_doc("Navgold Settings")
	if not cint(settings.get("enable_loss_circuit_breaker")) or cint(settings.get("freeze_amazon_outbound")):
		return

	from navgold_custom.navgold_custom.amazon_pricing.control_center import compute_loss_velocity
	from navgold_custom.navgold_custom.report.below_min_price_sales.below_min_price_sales import (
		get_data as get_below_min_data,
	)

	below_min_rows = get_below_min_data({"days_back": 1, "only_below_min": 1})
	window_loss = (compute_loss_velocity(below_min_rows).get("15m") or {}).get("loss", 0)
	trip_circuit_breaker_if_needed(flt(window_loss))


# ---------------------------------------------------------------------------
# Scheduled: 15-minute digest + 1-minute safety sweep
# ---------------------------------------------------------------------------


def send_digest() -> None:
	config = get_alert_config()
	if not config.get("enabled"):
		return

	from navgold_custom.navgold_custom.amazon_pricing.control_center import get_control_center_data

	data = get_control_center_data(below_min_days=1)
	readiness = data["readiness"]
	below = data["below_min"]
	feeds = data["feeds"]["status_counts"]
	orders = {row["label"]: row for row in data["orders"]["summary"]}

	blockers = readiness["blocked_total"]
	velocity = below.get("velocity") or {}
	loss_15m = (velocity.get("15m") or {}).get("loss", 0)
	loss_1h = (velocity.get("1h") or {}).get("loss", 0)
	is_go = blockers == 0 and below["count"] == 0 and feeds.get("Error", 0) == 0

	lines = [
		f"Status: <b>{'GO ✅' if is_go else 'NO-GO ⛔'}</b>",
		f"Bleed rate: <b>{money(loss_15m)}/15m</b> · {money(loss_1h)}/1h",
		f"Ready {readiness['ok_total']} / {readiness['total_active']} · would-block <b>{blockers}</b>",
		f"Below-min (1d): <b>{below['count']}</b> · est. loss {money(below['estimated_loss'])}",
		f"Feed errors: <b>{feeds.get('Error', 0)}</b> · pending {feeds.get('Pending', 0)}",
		f"Open failed syncs: {orders.get('Open Failed Syncs', {}).get('value', 0)}",
		f"Mins since last order: {orders.get('Minutes Since Last Amazon Order', {}).get('value', '—')}",
	]
	notify("15-min digest", lines, severity="info" if is_go else "warning")


def safety_sweep() -> None:
	# Runs every minute as the net under the instant hooks.
	# The circuit-breaker check runs first and is independent of the Telegram config — the
	# auto-freeze must work even when alerting is switched off.
	sweep_circuit_breaker()

	config = get_alert_config()
	if not config.get("enabled"):
		return

	error_feeds = frappe.db.count("Amazon Pricing Submission Log", {"status": "Error"})
	if error_feeds:
		notify(
			"Feed errors present",
			[f"{error_feeds} submission log(s) in Error."],
			severity="warning",
			dedupe_key=f"sweep:feed_errors:{error_feeds}",
			dedupe_seconds=900,
		)

	open_failed = frappe.db.count("Amazon Failed Sync Record", {"status": "Failed"})
	if open_failed:
		notify(
			"Open failed order syncs",
			[f"{open_failed} unresolved failed Amazon order sync(s)."],
			severity="warning",
			dedupe_key=f"sweep:failed_sync:{open_failed}",
			dedupe_seconds=1800,
		)

	last_order = frappe.db.get_value(
		"Sales Order", {"amazon_order_id": ("is", "set")}, "creation", order_by="creation desc"
	)
	if last_order:
		mins = cint(
			(frappe.utils.now_datetime() - frappe.utils.get_datetime(last_order)).total_seconds() / 60
		)
		if mins > 120:
			notify(
				"Amazon orders stalled",
				[f"No Amazon order synced for {mins} minutes."],
				severity="warning",
				dedupe_key="sweep:orders_stalled",
				dedupe_seconds=1800,
			)


# ---------------------------------------------------------------------------
# Setup helpers (whitelisted, used by the Navgold Settings buttons)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def detect_chat_id() -> dict:
	settings = frappe.get_cached_doc("Navgold Settings")
	token = settings.get_password("telegram_bot_token", raise_exception=False)
	if not token:
		frappe.throw("Set the Telegram Bot Token first.")

	url = TELEGRAM_API.format(token=token, method="getUpdates")
	response = make_get_request(url)
	results = (response or {}).get("result") or []
	if not results:
		frappe.throw("No messages found. Add the bot to your group and send any message there, then retry.")

	chats = []
	for update in results:
		container = update.get("message") or update.get("channel_post") or update.get("my_chat_member") or {}
		chat = container.get("chat")
		if chat:
			chats.append(chat)
	if not chats:
		frappe.throw("Could not read a chat from the bot updates.")

	# Prefer a group/supergroup/channel (team alerts) over a private DM; newest wins.
	group_chats = [chat for chat in chats if chat.get("type") in ("group", "supergroup", "channel")]
	chat = (group_chats or chats)[-1]

	chat_id = str(chat.get("id"))
	frappe.db.set_single_value("Navgold Settings", "telegram_chat_id", chat_id)
	frappe.clear_cache(doctype="Navgold Settings")
	return {"chat_id": chat_id, "chat_title": chat.get("title") or chat.get("username") or "private"}


@frappe.whitelist()
def send_test() -> dict:
	config = get_alert_config()
	if not (config.get("token") and config.get("chat_id")):
		frappe.throw("Set the bot token and chat id first (use Detect Chat ID).")
	# Bypass the enabled flag for an explicit test send.
	config["enabled"] = 1
	result = send_telegram("🟢 <b>Navgold test alert</b>\nTelegram alerting is wired correctly.", config)
	if not result or not result.get("ok"):
		frappe.throw(f"Telegram rejected the message: {frappe.as_json(result)}")
	return {"ok": True}
