# Copyright (c) 2026, developers@buildwithhussain.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.integrations.utils import make_get_request, make_post_request
from frappe.utils.data import add_to_date, cint, cstr, flt, fmt_money, get_datetime, now_datetime

from navgold_custom.navgold_custom.report.below_min_price_sales.below_min_price_sales import (
	get_min_price_map,
)

LOSS_WINDOW_CACHE_KEY = "navgold_below_min_loss_events"
LOSS_WINDOW_MINUTES = 15

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# Per-severity Telegram treatment: (title prefix with colour key, send silently without a ping).
SEVERITY_TELEGRAM = {
	"critical": ("🔴 CRITICAL — ", False),
	"warning": ("🟡 WARNING — ", False),
	"info": ("🔵 ", True),
	"ok": ("🟢 ", True),
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


def send_telegram(text: str, config: dict | None = None, silent: bool = False) -> dict | None:
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
				"disable_notification": "true" if silent else "false",
			},
		)
	except Exception as exception:
		frappe.log_error(f"Telegram alert failed: {exception!s}", "Navgold Telegram Alert")
		return None


def alert_gate_open(config: dict, dedupe_key: str | None = None, dedupe_seconds: int = 600) -> bool:
	# Require a fully-wired channel before touching the dedupe cache — otherwise a key set
	# while the chat id is missing would silently suppress the alert once it is connected.
	if not (config.get("enabled") and config.get("token") and config.get("chat_id")):
		return False

	if dedupe_key:
		cache_key = f"navgold_alert_sent:{dedupe_key}"
		if frappe.cache().get_value(cache_key):
			return False
		frappe.cache().set_value(cache_key, "1", expires_in_sec=dedupe_seconds)

	return True


def notify(
	title: str,
	lines: list[str],
	severity: str = "critical",
	dedupe_key: str | None = None,
	dedupe_seconds: int = 600,
) -> None:
	config = get_alert_config()
	if not alert_gate_open(config, dedupe_key, dedupe_seconds):
		return

	prefix, silent = SEVERITY_TELEGRAM.get(severity, ("", False))
	body = "\n".join(lines)
	text = f"{prefix}<b>{frappe.utils.escape_html(title)}</b>\n{body}"
	send_telegram(text, config, silent=silent)


@frappe.whitelist()
def send_telegram_message(
	message: str | list,
	title: str | None = None,
	severity: str = "info",
	dedupe_key: str | None = None,
	dedupe_seconds: int = 600,
) -> None:
	# Whitelisted so Server Scripts can enqueue it — the sandbox only allows whitelisted methods.
	lines = message if isinstance(message, list) else [cstr(message)]
	if title:
		notify(
			title,
			lines,
			severity=severity,
			dedupe_key=dedupe_key,
			dedupe_seconds=cint(dedupe_seconds) or 600,
		)
		return

	config = get_alert_config()
	if not alert_gate_open(config, dedupe_key, cint(dedupe_seconds) or 600):
		return
	_, silent = SEVERITY_TELEGRAM.get(severity, ("", False))
	send_telegram("\n".join(lines), config, silent=silent)


def format_money(amount) -> str:
	return fmt_money(flt(amount), currency="GBP")


def alert_price_approval_required(strategy_name: str) -> None:
	strategy = frappe.db.get_value(
		"Amazon Product Strategy",
		strategy_name,
		[
			"proxy_sku",
			"strategy_name",
			"approved_min_price",
			"approved_max_price",
			"calculated_min_price",
			"calculated_max_price",
		],
		as_dict=True,
	)
	if not strategy:
		return

	notify(
		"Pricing approval required",
		[
			f"SKU <b>{frappe.utils.escape_html(cstr(strategy.proxy_sku))}</b> · {frappe.utils.escape_html(cstr(strategy.strategy_name))}",
			f"Approved: {format_money(strategy.approved_min_price)} – {format_money(strategy.approved_max_price)}",
			f"Proposed: {format_money(strategy.calculated_min_price)} – {format_money(strategy.calculated_max_price)}",
			f"Review strategy <code>{frappe.utils.escape_html(strategy_name)}</code> in ERPNext.",
		],
		severity="warning",
		dedupe_key=f"price_approval:{strategy_name}:{flt(strategy.calculated_min_price, 2)}:{flt(strategy.calculated_max_price, 2)}",
		dedupe_seconds=86400,
	)


def alert_price_approvals_summary(strategy_names: list[str], source: str) -> None:
	strategy_names = list(dict.fromkeys(strategy_names))
	if not strategy_names:
		return

	rows = frappe.get_all(
		"Amazon Product Strategy",
		filters={"name": ["in", strategy_names], "approval_status": "Pending Approval"},
		fields=[
			"name",
			"proxy_sku",
			"strategy_name",
			"approved_max_price",
			"calculated_max_price",
		],
	)
	if not rows:
		return

	rows.sort(
		key=lambda row: abs(flt(row.calculated_max_price) - flt(row.approved_max_price)),
		reverse=True,
	)
	lines = [f"{len(rows)} strategy price change(s) require review after {frappe.utils.escape_html(source)}."]
	for row in rows[:5]:
		lines.append(
			f"{frappe.utils.escape_html(cstr(row.proxy_sku))} · "
			f"{frappe.utils.escape_html(cstr(row.strategy_name))}: "
			f"{format_money(row.approved_max_price)} → {format_money(row.calculated_max_price)}"
		)
	if len(rows) > 5:
		lines.append(f"…and {len(rows) - 5} more.")

	notify(
		"Pricing approvals required",
		lines,
		severity="warning",
		dedupe_key=f"price_approval_batch:{source}:{len(rows)}",
		dedupe_seconds=900,
	)


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
			f"SKU <b>{frappe.utils.escape_html(proxy_sku)}</b> sold {format_money(rate)} (min {format_money(min_price)})",
			f"{frappe.utils.escape_html(sales_order)} · qty {flt(qty):g} · est. loss {format_money(loss)}",
		],
		severity="critical",
		dedupe_key=f"belowmin:{sales_order}:{proxy_sku}",
		dedupe_seconds=86400,
	)


def alert_orders_canceled(amazon_order_ids: list[str]) -> None:
	if not amazon_order_ids:
		return

	lines = [f"{len(amazon_order_ids)} order(s) cancelled on Amazon and closed in ERPNext."]
	lines.append(", ".join(frappe.utils.escape_html(order_id) for order_id in amazon_order_ids[:10]))
	if len(amazon_order_ids) > 10:
		lines.append(f"…and {len(amazon_order_ids) - 10} more.")
	lines.append("Check stock reservations and any linked Material Request.")

	notify(
		"Amazon orders cancelled",
		lines,
		severity="warning",
		dedupe_key=f"ordercancel:{','.join(sorted(amazon_order_ids))[:100]}",
		dedupe_seconds=3600,
	)


def alert_status_refresh_truncated(updated_count: int, page_cap: int) -> None:
	notify(
		"Amazon status refresh hit its page cap",
		[
			f"Stopped after {page_cap} pages with {updated_count} order(s) updated.",
			"The window was not advanced, so the same batch retries next run — investigate if this repeats.",
		],
		severity="warning",
		dedupe_key="status_refresh_truncated",
		dedupe_seconds=3600,
	)


def alert_status_refresh_gap(skipped_hours: float, resuming_from) -> None:
	skipped_line = f"The watermark was too stale to walk back, so {skipped_hours:.0f}h of status changes were passed over."
	resume_line = f"Resuming from {cstr(resuming_from)}. Rewind After Date on Amazon SP API Settings to re-read the gap."

	notify(
		"Amazon status refresh skipped a gap",
		[skipped_line, resume_line],
		severity="critical",
		dedupe_key="status_refresh_gap",
		dedupe_seconds=3600,
	)


def alert_material_request_failed(sales_order: str, error: str) -> None:
	# Called from inside an exception handler on Sales Order submit, where anything raised would
	# abort the order the sync has just written — the alert must never become the bigger failure.
	missing_line = (
		f"Sales Order {cstr(sales_order)} was submitted without a Material Request, so its shortage "
		"never reaches To Purchase and nobody buys the stock."
	)
	reason_line = f"<code>{frappe.utils.escape_html(cstr(error)[:300])}</code>"
	suppression_line = "Further failures within the hour are suppressed — check the Error Log for the rest."

	try:
		notify(
			"Auto Material Request failed",
			[missing_line, reason_line, suppression_line],
			severity="critical",
			dedupe_key="auto_material_request_failed",
			dedupe_seconds=3600,
		)
	except Exception as exception:
		frappe.log_error(f"Material Request alert failed: {exception!s}", "Navgold Telegram Alert")


def alert_stale_draft_orders(draft_count: int, oldest_hours: float) -> None:
	notify(
		"Amazon orders stuck in draft",
		[
			f"{draft_count} Amazon Sales Order(s) still in draft, oldest {oldest_hours:.0f}h old.",
			"These are paid customer orders whose promotion never completed — they reserve no stock.",
		],
		severity="critical",
		dedupe_key="stale_draft_orders",
		dedupe_seconds=3600,
	)


def check_sales_order_below_min(doc, method=None) -> None:
	# Detection runs regardless of Telegram config — the circuit breaker depends on it.
	if not doc.get("amazon_order_id"):
		return

	pairs = [(item.item_code, item.uom) for item in doc.items]
	min_price_map = get_min_price_map(pairs)
	order_loss = flt(0)
	for item in doc.items:
		mapped = min_price_map.get((item.item_code, item.uom))
		if not mapped:
			continue
		if 0 < flt(item.rate) < flt(mapped["min_price"]):
			alert_below_min_sale(doc.name, mapped["proxy_sku"], item.rate, mapped["min_price"], item.qty)
			order_loss += max(flt(mapped["min_price"]) - flt(item.rate), 0) * flt(item.qty)

	if order_loss > 0:
		window_loss = record_loss_event(order_loss)
		trip_circuit_breaker_if_needed(window_loss)


def record_loss_event(loss: float) -> float:
	# Redis-backed sliding 15-minute window: reacts within a second, no per-order DB scan.
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
		return False

	frappe.db.set_single_value("Navgold Settings", "freeze_amazon_outbound", 1)
	frappe.clear_cache(doctype="Navgold Settings")

	# Log even when Telegram is off — the freeze itself is the safety action.
	message = (
		f"Circuit breaker tripped: below-min loss {fmt_money(window_loss, currency='GBP')} in "
		f"{LOSS_WINDOW_MINUTES} min (limit {fmt_money(threshold, currency='GBP')}). Outbound frozen."
	)
	frappe.log_error(message, "Navgold Circuit Breaker")
	notify(
		"CIRCUIT BREAKER TRIPPED — outbound FROZEN",
		[
			f"Below-min loss hit {format_money(window_loss)} in {LOSS_WINDOW_MINUTES} min (limit {format_money(threshold)}).",
			"All Amazon/Repricer writes are halted. Investigate before releasing the freeze.",
		],
		severity="critical",
		dedupe_key="circuit_breaker",
		dedupe_seconds=300,
	)
	return True


def sweep_circuit_breaker() -> None:
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


def get_activity_rollup(minutes: int = 30) -> dict:
	cutoff = add_to_date(now_datetime(), minutes=-minutes)
	pushed = ["Submitted", "Completed"]
	settings = frappe.get_cached_doc("Navgold Settings")
	last_fba = settings.get("last_afn_stock_synced_at")
	return {
		"minutes": minutes,
		"orders_synced": frappe.db.count(
			"Sales Order", {"amazon_order_id": ["is", "set"], "creation": [">=", cutoff]}
		),
		"prices_pushed": frappe.db.count(
			"Amazon Pricing Submission Log",
			{"submission_type": "Amazon", "status": ["in", pushed], "creation": [">=", cutoff]},
		),
		"repricer_pushed": frappe.db.count(
			"Amazon Pricing Submission Log",
			{"submission_type": "Repricer", "status": ["in", pushed], "creation": [">=", cutoff]},
		),
		"fba_proxy_count": cint(settings.get("last_afn_stock_proxy_count")),
		"fba_minutes_ago": cint((now_datetime() - get_datetime(last_fba)).total_seconds() / 60)
		if last_fba
		else None,
		"errors": frappe.db.count(
			"Amazon Pricing Submission Log", {"status": "Error", "creation": [">=", cutoff]}
		)
		+ frappe.db.count("Amazon Failed Sync Record", {"status": "Failed", "creation": [">=", cutoff]}),
	}


@frappe.whitelist()
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

	activity = get_activity_rollup(30)
	fba_pull = f"{activity['fba_minutes_ago']}m ago" if activity["fba_minutes_ago"] is not None else "never"

	lines = [
		f"Status: <b>{'GO' if is_go else 'NO-GO'}</b>",
		f"Bleed rate: <b>{format_money(loss_15m)}/15m</b> · {format_money(loss_1h)}/1h",
		f"Ready {readiness['ok_total']} / {readiness['total_active']} · would-block <b>{blockers}</b>",
		f"Below-min (1d): <b>{below['count']}</b> · est. loss {format_money(below['estimated_loss'])}",
		f"Feed errors: <b>{feeds.get('Error', 0)}</b> · pending {feeds.get('Pending', 0)}",
		f"Open failed syncs: {orders.get('Open Failed Syncs', {}).get('value', 0)}",
		f"Mins since last order: {orders.get('Minutes Since Last Amazon Order', {}).get('value', '—')}",
		f"Activity (30m): orders <b>{activity['orders_synced']}</b> · prices pushed <b>{activity['prices_pushed']}</b> · repricer <b>{activity['repricer_pushed']}</b> · FBA pull {fba_pull} ({activity['fba_proxy_count']} SKUs)",
		f"Errors (30m): <b>{activity['errors']}</b>",
	]
	notify("Activity digest", lines, severity="ok" if is_go else "warning")


def safety_sweep() -> None:
	# The circuit-breaker check runs first and is independent of the Telegram config.
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
		minutes = cint(
			(frappe.utils.now_datetime() - frappe.utils.get_datetime(last_order)).total_seconds() / 60
		)
		if minutes > 120:
			notify(
				"Amazon orders stalled",
				[f"No Amazon order synced for {minutes} minutes."],
				severity="warning",
				dedupe_key="sweep:orders_stalled",
				dedupe_seconds=1800,
			)

	check_stale_draft_orders()


def check_stale_draft_orders(max_age_hours: int = 6) -> None:
	cutoff = add_to_date(now_datetime(), hours=-max_age_hours)
	oldest = frappe.db.get_value(
		"Sales Order",
		{"amazon_order_id": ("is", "set"), "docstatus": 0, "creation": ("<", cutoff)},
		"creation",
		order_by="creation asc",
	)
	if not oldest:
		return

	draft_count = frappe.db.count(
		"Sales Order",
		{"amazon_order_id": ("is", "set"), "docstatus": 0, "creation": ("<", cutoff)},
	)
	oldest_hours = (now_datetime() - get_datetime(oldest)).total_seconds() / 3600
	alert_stale_draft_orders(draft_count, oldest_hours)


@frappe.whitelist()
def detect_chat_id() -> dict:
	settings = frappe.get_cached_doc("Navgold Settings")
	token = settings.get_password("telegram_bot_token", raise_exception=False)
	if not token:
		frappe.throw(_("Set the Telegram Bot Token first."))

	url = TELEGRAM_API.format(token=token, method="getUpdates")
	response = make_get_request(url)
	results = (response or {}).get("result") or []
	if not results:
		frappe.throw(
			_("No messages found. Add the bot to your group and send any message there, then retry.")
		)

	chats = []
	for update in results:
		container = update.get("message") or update.get("channel_post") or update.get("my_chat_member") or {}
		chat = container.get("chat")
		if chat:
			chats.append(chat)
	if not chats:
		frappe.throw(_("Could not read a chat from the bot updates."))

	# Prefer a group/supergroup/channel (team alerts) over a private DM; newest wins.
	group_chats = [chat for chat in chats if chat.get("type") in ("group", "supergroup", "channel")]
	chat = (group_chats or chats)[-1]

	chat_id = str(chat.get("id"))
	frappe.db.set_single_value("Navgold Settings", "telegram_chat_id", chat_id)
	frappe.clear_cache(doctype="Navgold Settings")
	return {"chat_id": chat_id, "chat_title": chat.get("title") or chat.get("username") or "private"}


@frappe.whitelist()
def send_test() -> None:
	config = get_alert_config()
	if not (config.get("token") and config.get("chat_id")):
		frappe.throw(_("Set the bot token and chat id first (use Detect Chat ID)."))
	# Bypass the enabled flag for an explicit test send.
	config["enabled"] = 1
	result = send_telegram("<b>Navgold test alert</b>\nTelegram alerting is wired correctly.", config)
	if not result or not result.get("ok"):
		frappe.throw(f"Telegram rejected the message: {frappe.as_json(result)}")
