app_name = "navgold_custom"
app_title = "Navgold Custom"
app_publisher = "developers@buildwithhussain.com"
app_description = "Custom Frappe App for Navgold LTD"
app_email = "developers@buildwithhussain.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "navgold_custom",
# 		"logo": "/assets/navgold_custom/logo.png",
# 		"title": "Navgold Custom",
# 		"route": "/navgold_custom",
# 		"has_permission": "navgold_custom.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
app_include_css = "/assets/navgold_custom/css/navgold_custom.css"
app_include_js = ["/assets/navgold_custom/js/utils.js", "/assets/navgold_custom/js/packing_utils.js"]

# include js, css files in header of web template
# web_include_css = "/assets/navgold_custom/css/navgold_custom.css"
# web_include_js = "/assets/navgold_custom/js/navgold_custom.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "navgold_custom/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "navgold_custom/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "navgold_custom.utils.jinja_methods",
# 	"filters": "navgold_custom.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "navgold_custom.install.before_install"
# after_install = "navgold_custom.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "navgold_custom.uninstall.before_uninstall"
# after_uninstall = "navgold_custom.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "navgold_custom.utils.before_app_install"
# after_app_install = "navgold_custom.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "navgold_custom.utils.before_app_uninstall"
# after_app_uninstall = "navgold_custom.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "navgold_custom.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

fixtures = [
	{"dt": "Stock Entry Type", "filters": [["name", "=", "FBA Transfer"]]},
	{"dt": "Role", "filters": [["name", "like", "Navgold%"]]},
	{
		"dt": "Custom DocPerm",
		"filters": [
			[
				"parent",
				"in",
				[
					"Batch",
					"Buying Settings",
					"Company",
					"Currency",
					"Item",
					"Item Group",
					"Item Tax Template",
					"Material Request",
					"Packing Slip",
					"Pick List",
					"Price List",
					"Proxy SKU",
					"Purchase Order",
					"Purchase Receipt",
					"Purchase Taxes and Charges Template",
					"Sales Order",
					"Stock Entry",
					"Stock Entry Type",
					"Stock Ledger Entry",
					"Stock Reconciliation",
					"Stock Settings",
					"Supplier",
					"Supplier Group",
					"UOM",
					"Warehouse",
				],
			]
		],
	},
]

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"navgold_custom.tasks.all"
# 	],
# 	"daily": [
# 		"navgold_custom.tasks.daily"
# 	],
# 	"hourly": [
# 		"navgold_custom.tasks.hourly"
# 	],
# 	"weekly": [
# 		"navgold_custom.tasks.weekly"
# 	],
# 	"monthly": [
# 		"navgold_custom.tasks.monthly"
# 	],
# }

# Testing
# -------

before_tests = "navgold_custom.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "navgold_custom.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "navgold_custom.task.get_dashboard_data"
# }

override_doctype_dashboards = {
	"Item": "navgold_custom.navgold_custom.custom_script.item.item_dashboard.get_data",
	"Supplier": "navgold_custom.navgold_custom.custom_script.supplier.supplier_dashboard.get_data",
}

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["navgold_custom.utils.before_request"]
# after_request = ["navgold_custom.utils.after_request"]

# Job Events
# ----------
# before_job = ["navgold_custom.utils.before_job"]
# after_job = ["navgold_custom.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"navgold_custom.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

doctype_js = {
	"Sales Order": ["navgold_custom/custom_script/sales_order/sales_order.js"],
	"Purchase Order": ["navgold_custom/custom_script/purchase_order/purchase_order.js"],
	"Purchase Receipt": ["navgold_custom/custom_script/purchase_receipt/purchase_receipt.js"],
	"Pick List": ["navgold_custom/custom_script/pick_list/pick_list.js"],
	"Packing Slip": ["navgold_custom/custom_script/packing_slip/packing_slip.js"],
	"Supplier": ["navgold_custom/custom_script/supplier/supplier.js"],
	"Item": ["navgold_custom/custom_script/item/item.js"],
}

doc_events = {
	"Purchase Order": {
		"before_validate": "navgold_custom.navgold_custom.custom_script.purchase_order.purchase_order.set_supplier_part_no",
	},
	"Supplier": {
		"validate": "navgold_custom.navgold_custom.custom_script.supplier.supplier.validate_lead_time",
		"on_update": "navgold_custom.navgold_custom.custom_script.supplier.supplier.propagate_sfp_eligibility",
	},
	"Sales Order": {
		"on_submit": [
			"navgold_custom.navgold_custom.custom_script.sales_order.sales_order.sales_order_on_submit",
			# Real-time below-min loss detection + loss circuit breaker (auto-freeze).
			"navgold_custom.navgold_custom.amazon_pricing.alerts.check_sales_order_below_min",
		],
	},
	"Item": {
		"validate": "navgold_custom.navgold_custom.custom_script.item.item.set_default_uom_conversion_factor"
	},
	"Packing Slip": {
		"before_validate": "navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip.before_validate",
		"on_submit": "navgold_custom.navgold_custom.custom_script.packing_slip.packing_slip.on_submit",
	},
	"Pick List": {
		"validate": [
			"navgold_custom.navgold_custom.custom_script.pick_list.pick_list.snap_locations_to_whole_stock_units",
		],
		"before_save": [
			"navgold_custom.navgold_custom.custom_script.pick_list.pick_list.block_partial_pick",
			"navgold_custom.navgold_custom.custom_script.pick_list.pick_list.sort_item_locations_by_walk_route",
		],
		"on_submit": [
			"navgold_custom.navgold_custom.custom_script.pick_list.pick_list.on_submit",
		],
	},
	"Delivery Note": {
		"validate": "navgold_custom.navgold_custom.custom_script.delivery_note.delivery_note.align_consolidated_bundle_packed_warehouse",
		"on_submit": "navgold_custom.navgold_custom.custom_script.delivery_note.delivery_note.on_submit",
		"on_cancel": "navgold_custom.navgold_custom.custom_script.delivery_note.delivery_note.on_cancel",
	},
	"Stock Entry": {
		"on_submit": [
			"navgold_custom.navgold_custom.custom_script.stock_entry.stock_entry.update_proxy_sku_transfer_rates",
			"navgold_custom.navgold_custom.custom_script.stock_entry.stock_entry.create_afn_material_issue",
			"navgold_custom.navgold_custom.custom_script.stock_entry.stock_entry.release_transferred_shelf_claim",
		],
		"before_cancel": "navgold_custom.navgold_custom.custom_script.stock_entry.stock_entry.cancel_afn_material_issue",
		"on_cancel": "navgold_custom.navgold_custom.custom_script.stock_entry.stock_entry.release_transferred_shelf_claim",
	},
}

after_install = "navgold_custom.install.after_install"
after_migrate = "navgold_custom.setup.after_migrate"
before_uninstall = "navgold_custom.setup.before_uninstall"

scheduler_events = {
	"cron": {
		"*/15 * * * *": [
			"navgold_custom.navgold_custom.amazon_pricing.feed_status_poller.poll_pending_feed_submissions",
			# Proactive safety sweep: circuit-breaker re-check + alerts on feed errors,
			# failed syncs and stalled order sync. Idempotent and dedupe-guarded.
			"navgold_custom.navgold_custom.amazon_pricing.alerts.safety_sweep",
		],
		"*/30 * * * *": [
			"navgold_custom.navgold_custom.doctype.amazon_sp_api_settings.amazon_sp_api_settings.schedule_get_order_details",
			"navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings.schedule_bulk_fetch_afn_stock",
		],
		"10,40 * * * *": [
			"navgold_custom.navgold_custom.amazon_repository.schedule_refresh_order_statuses",
		],
		# strategy refresh and pricing submits are deliberately offset so a feed is never
		# built from half-refreshed strategy prices
		"0,30 * * * *": [
			"navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings.schedule_bulk_refresh_strategies",
		],
		"15,45 * * * *": [
			"navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings.schedule_submit_active_pricing",
			"navgold_custom.navgold_custom.doctype.navgold_settings.navgold_settings.schedule_submit_active_repricer",
		],
		# Activity digest: routine order/price/stock/FBA rollup. Offset to run just after the
		# :00/:30 order + AFN sync so it reports fresh counts. Problems alert in real time.
		"5,35 * * * *": [
			"navgold_custom.navgold_custom.amazon_pricing.alerts.send_digest",
		],
		# Mirror Amazon's feed history into the ledger so the push monitor can show feeds
		# submitted directly to Amazon by external sources (e.g. Repricer), not just ours.
		# Offset off the sync cluster to spread SP-API load.
		"20 * * * *": [
			"navgold_custom.navgold_custom.amazon_pricing.feed_ledger.sync_amazon_feed_ledger",
		],
		"40 * * * *": [
			"navgold_custom.navgold_custom.doctype.supplier_stock_import.clf_stock_api.schedule_clf_fetch",
		],
		"25 * * * *": [
			"navgold_custom.navgold_custom.custom_script.material_request.material_request.reconcile_purchase_demand_job",
		],
	},
	"daily": [
		"navgold_custom.navgold_custom.doctype.proxy_sku.proxy_sku.schedule_bulk_fetch_selling_prices",
		"navgold_custom.navgold_custom.doctype.supplier_item_price_and_stock.supplier_item_price_and_stock.run_update_supplier_rank",
		"navgold_custom.navgold_custom.doctype.amazon_product_strategy.amazon_product_strategy.refresh_all_strategy_costs",
		"navgold_custom.navgold_custom.amazon_pricing.fba_fee_fetcher.bulk_fetch_fba_fees",
		"navgold_custom.navgold_custom.amazon_pricing.daily_order_stats.sync_daily_order_stats",
		# Morning known-good price capture the panic-restore rolls back to
		"navgold_custom.navgold_custom.doctype.amazon_pricing_snapshot.amazon_pricing_snapshot.daily_pricing_snapshot",
		# erpnext's Close button writes status through db_set, so no document hook sees it;
		# this sweep is what catches a closed order's demand
		"navgold_custom.navgold_custom.custom_script.material_request.material_request.retire_settled_demand",
	],
}

website_route_rules = [
	{"from_route": "/control_center/<path:app_path>", "to_route": "control_center"},
]
